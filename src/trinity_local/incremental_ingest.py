"""Tool-triggered incremental ingest into the memory index.

Runs on the MCP hot path (or from CLI). Walks transcripts newer than a
per-source cursor at ``~/.trinity/prompts/cursors.json`` and appends
``PromptNode`` records WITHOUT embeddings — embeddings are written by
``import-export`` (the one-shot bulk-ingest verb, which replaced the
retired seed-from-taste-terminal 2026-05-27) or recomputed lazily by
``lens-build`` / ``consolidate``. Per ``claude.md``: the read path stays
embedding-free, only bulk import and consolidation pay the embed cost.

Deadline-bounded: the caller passes ``deadline_s`` (default 2s) and we
persist the cursor at whichever path we got to so the next call resumes.
Designed to fire-and-forget at the start of MCP ``ask`` (and the
Chrome extension's ``ingest-recent`` action) so newly-typed prompts
become routable without a manual ``import-export`` rerun (or its
retired predecessor seed-from-taste-terminal).
(The ``search_prompts`` MCP tool that previously co-triggered this
was retired 2026-05-17 — substring + recency + replay-value
heuristics replaced it per retired_names.py.)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .ingest import iter_prompt_turns
from .ingest_helpers import existing_prompt_node_ids as _shared_existing_ids
from .memory import PromptNode, upsert_prompt_node
from .state_paths import ingest_cursors_path
from .task_types import guess_task_type
from .utils import now_iso, stable_id


DEFAULT_SOURCES = ("claude", "codex", "gemini", "antigravity", "cowork", "browser_claude", "browser_chatgpt", "browser_gemini")
DEFAULT_DEADLINE_S = 2.0


@dataclass
class IngestResult:
    scanned: int = 0
    added: int = 0
    skipped_existing: int = 0
    #: Files we could NOT read — the parser raised, or returned nothing and
    #: could not say the file was merely empty. This number means BREAKAGE.
    skipped_parse: int = 0
    #: Files that read fine and carry nothing to extract. Not breakage.
    #: Split out 2026-07-31: browser_gemini was reporting 2,740 "parse
    #: failures" over 4,322 live capture files, of which exactly 1 was
    #: unreadable — Gemini writes one capture per network frame and only
    #: about a third of them close out an assistant turn (1,582 ok /
    #: 2,739 empty, measured). A further 917 parsed into a session that
    #: yielded no user turn and were counted in NO bucket at all. A
    #: number that reads as breakage and isn't is worse than no number.
    skipped_empty: int = 0
    #: Files re-listed by the inclusive `>=` boundary and skipped WITHOUT
    #: being opened, because they are the same path at the same size we
    #: fully drained last pass. Also added 2026-07-31: these were counted
    #: in `scanned` and in nothing else, so the steady state of a quiet
    #: source read "scanned 1, added 0, skipped 0" — a file that simply
    #: vanished from its own accounting. That is the shape the live
    #: `gemini` source reports on EVERY pass. With this field the file
    #: buckets partition `scanned` exactly.
    skipped_unchanged: int = 0
    sources: list[str] = field(default_factory=list)
    took_ms: int = 0
    deadline_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "skipped_existing": self.skipped_existing,
            "skipped_parse": self.skipped_parse,
            "skipped_empty": self.skipped_empty,
            "skipped_unchanged": self.skipped_unchanged,
            "sources": list(self.sources),
            "took_ms": self.took_ms,
            "deadline_hit": self.deadline_hit,
        }


def _load_cursors() -> dict[str, float]:
    path = ingest_cursors_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    # guard_shape_not_just_parse: valid JSON that ISN'T a dict (a list/scalar/null
    # from a partial/concurrent write or a manual edit) would crash `.items()` with
    # an uncaught AttributeError — and this runs on every MCP tool call (the
    # continuous incremental ingest), so a wrong-shape cursors.json would break ALL
    # future ingestion until the file was hand-fixed. Degrade to a fresh cursor.
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for source, entry in raw.items():
        if isinstance(entry, (int, float)):
            out[source] = float(entry)
        elif isinstance(entry, dict):
            mtime = entry.get("last_mtime", 0.0)
            try:
                out[source] = float(mtime or 0.0)
            except (TypeError, ValueError):
                out[source] = 0.0
    return out


def _load_drained() -> dict[str, tuple[str, int]]:
    """Per-source ``{source: (drained_path, drained_size)}`` — the highest-mtime
    file fully processed last run. Lets a re-scan skip an unchanged boundary
    file instead of re-parsing it every call (the cost of the inclusive `>=`
    boundary on the 1s MCP path). Absent/legacy entries → no skip."""
    path = ingest_cursors_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):  # guard_shape_not_just_parse (see _load_cursors)
        return {}
    out: dict[str, tuple[str, int]] = {}
    for source, entry in raw.items():
        if isinstance(entry, dict):
            dp, ds = entry.get("drained_path"), entry.get("drained_size")
            if isinstance(dp, str) and isinstance(ds, int):
                out[source] = (dp, ds)
    return out


def _load_scanned_at() -> dict[str, float]:
    """Per-source ``{source: wall-clock epoch}`` of the last pass that walked
    the source to completion. See ``source_scan_ages`` for why this exists and
    why ``last_mtime`` cannot answer the same question."""
    path = ingest_cursors_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):  # guard_shape_not_just_parse (see _load_cursors)
        return {}
    out: dict[str, float] = {}
    for source, entry in raw.items():
        if isinstance(entry, dict):
            when = entry.get("scanned_at")
            if isinstance(when, (int, float)):
                out[source] = float(when)
    return out


def _save_cursors(
    cursors: dict[str, float],
    drained: dict[str, tuple[str, int]] | None = None,
    scanned_at: dict[str, float] | None = None,
) -> None:
    from .utils import atomic_write_text
    path = ingest_cursors_path()
    drained = drained or {}
    scanned_at = scanned_at or {}
    payload: dict[str, dict] = {}
    for source, mtime in cursors.items():
        entry: dict = {"last_mtime": mtime}
        if source in drained:
            entry["drained_path"], entry["drained_size"] = drained[source]
        if source in scanned_at:
            entry["scanned_at"] = scanned_at[source]
        payload[source] = entry
    atomic_write_text(path, json.dumps(payload, indent=2))


def _looked_at(source: str, cursors: dict[str, float], scanned: dict[str, float]) -> float:
    """When we last LOOKED at ``source`` (epoch seconds).

    ``scanned_at`` when we have it; otherwise the content watermark, which is
    what this code used before ``scanned_at`` existed — so a legacy
    cursors.json keeps exactly its old ordering until the first pass rewrites
    it. 0.0 (never seen) sorts first either way."""
    when = scanned.get(source)
    if when is not None:
        return when
    return cursors.get(source, 0.0)


def source_scan_ages(now: float | None = None) -> dict[str, float]:
    """``{source: seconds since we last walked it}`` — the honest freshness
    number for a source, and the one any staleness surface must read.

    ``last_mtime`` CANNOT answer this. It is a watermark over transcript
    mtimes, and the scan boundary is inclusive (`>=`, see
    ``watch_runtime._iter_recent_paths`` — batch-written siblings share an
    mtime and a strict `>` loses them). So a source whose newest file is old
    and fully drained parks its watermark on that file's mtime FOREVER: the
    file is returned on every pass, yields nothing, and cannot push the
    watermark past its own mtime. Measured 2026-07-31 on the real corpus, the
    `gemini` CLI source had exactly one file, drained, mtime == cursor to the
    microsecond, and read as "86.5 days behind" while being perfectly current.

    A number a user cannot clear by doing the right thing is worse than no
    number (see tests/test_advice_closure.py): `trinity-local ingest-recent`
    drives THIS clock to ~0 whether or not there was anything to ingest."""
    now = time.time() if now is None else now
    cursors = _load_cursors()
    scanned = _load_scanned_at()
    return {
        source: max(0.0, now - _looked_at(source, cursors, scanned))
        for source in set(cursors) | set(scanned)
    }


def _existing_prompt_node_ids() -> set[str]:
    # Thin alias for the consolidated helper. Kept under the
    # underscore-prefixed name so callers inside this module don't
    # need to update; the canonical implementation lives in
    # `ingest_helpers.existing_prompt_node_ids`.
    return _shared_existing_ids()


def ingest_recent(
    *,
    sources: list[str] | None = None,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> IngestResult:
    """Walk transcripts newer than the per-source cursor; append PromptNodes
    without embeddings. Bounded by ``deadline_s``; cursor is persisted at
    the latest-scanned path mtime so the next call resumes.

    Two clocks are written per source and they answer different questions:
    ``last_mtime`` is a WATERMARK over transcript mtimes (how far into the
    content we have read — it legitimately stops moving once a source stops
    producing files), and ``scanned_at`` is when we last WALKED the source
    (how fresh our knowledge of it is). Only the second is a freshness
    signal; see ``source_scan_ages``."""
    from .ingest import PARSE_EMPTY
    from .watch_runtime import _iter_recent_paths, _parse_source_path_classified

    sources = list(sources or DEFAULT_SOURCES)
    cursors = _load_cursors()
    drained = _load_drained()
    scanned_at = _load_scanned_at()
    existing_ids = _existing_prompt_node_ids()

    # STARVATION FIX (2026-07-27). This loop walks `sources` in order under a SHARED
    # deadline (60s from stale_pass) and breaks when it expires. With a fixed order the
    # sources at the front drain on every pass and the ones at the back never run: the
    # CLI transcripts are large, they are listed first, and DEFAULT_SOURCES puts the
    # three browser_* sources last. Measured 2026-07-26: claude/codex/antigravity
    # cursors 0 days behind while all three browser cursors sat 13 days behind, frozen
    # at the same minute — the timestamp of the last MANUAL ingest, not of any gated
    # pass. The usage gate was firing correctly the whole time; its budget just never
    # reached the tail.
    #
    # Ordering most-stale-first makes the budget follow the need. A starved source
    # takes the front of the queue until it catches up, then yields it back. Sources
    # never seen (no cursor) sort first — they have the most to gain — and the tie-break
    # on name keeps the order deterministic for tests.
    #
    # "Stale" is LAST LOOKED AT, not the content watermark (fixed 2026-07-31). The
    # watermark cannot move past a fully-drained boundary file — the boundary is
    # inclusive by design — so a source whose only file is old and drained pinned
    # itself at the head of this queue on every pass, forever, no matter how
    # recently it had been walked. Measured on the real corpus: the `gemini` CLI
    # source, one file, drained, watermark == its mtime exactly, ranked as "86.5
    # days behind" while being perfectly current. `scanned_at` is only written for
    # a source that finished its walk, so a source the deadline cut short KEEPS its
    # place at the front — which is the property the starvation fix bought.
    sources.sort(key=lambda s: (_looked_at(s, cursors, scanned_at), s))

    started = time.monotonic()
    result = IngestResult(sources=sources)

    for source in sources:
        if time.monotonic() - started >= deadline_s:
            result.deadline_hit = True
            break
        last_mtime = cursors.get(source, 0.0)
        max_mtime = last_mtime
        # Highest-mtime file fully processed this run → recorded so the next
        # scan can skip it if unchanged (the `>=` boundary would otherwise
        # re-parse it every call). (path_str, size, mtime).
        boundary: tuple[str, int, float] | None = None
        drained_path, drained_size = drained.get(source, ("", -1))
        # Did the deadline cut THIS source short? If so we did not finish
        # looking at it and must not stamp it as scanned — it keeps its place
        # at the front of the next pass's queue.
        truncated = False

        try:
            paths = list(_iter_recent_paths(source, last_mtime))
        except (OSError, ValueError):
            continue

        for path in paths:
            if time.monotonic() - started >= deadline_s:
                result.deadline_hit = True
                truncated = True
                break
            result.scanned += 1
            try:
                file_mtime = path.stat().st_mtime
                file_size = path.stat().st_size
            except OSError:
                continue
            max_mtime = max(max_mtime, file_mtime)
            # Skip a fully-drained, unchanged boundary file (same path + size).
            # A grown file has a different size → re-parsed; a sibling at the
            # same mtime has a different path → still scanned (equal-mtime
            # safety preserved). Track the highest-mtime file we processed.
            if str(path) == drained_path and file_size == drained_size:
                if boundary is None or file_mtime >= boundary[2]:
                    boundary = (str(path), file_size, file_mtime)
                result.skipped_unchanged += 1
                continue
            if boundary is None or file_mtime >= boundary[2]:
                boundary = (str(path), file_size, file_mtime)

            try:
                session, why = _parse_source_path_classified(source, path)
            except Exception:
                result.skipped_parse += 1
                continue
            if session is None:
                # BREAKAGE vs NOTHING-TO-SAY. Only a parser that actually
                # inspected the file may claim PARSE_EMPTY; everything else
                # still counts as unreadable (see _parse_source_path_classified).
                if why == PARSE_EMPTY:
                    result.skipped_empty += 1
                else:
                    result.skipped_parse += 1
                continue

            try:
                turns = list(iter_prompt_turns(session))
            except Exception:
                result.skipped_parse += 1
                continue

            if not turns:
                # Parsed fine, no user-facing turn in it (e.g. a gemini capture
                # whose assistant reply landed but whose prompt didn't). Was
                # counted NOWHERE before the split — silently invisible.
                result.skipped_empty += 1
                continue

            for turn in turns:
                node_id = stable_id(
                    "pnode", turn.transcript_id, str(turn.turn_index), turn.text[:200]
                )
                if node_id in existing_ids:
                    result.skipped_existing += 1
                    continue
                node = PromptNode(
                    id=node_id,
                    transcript_id=turn.transcript_id,
                    provider=turn.provider,
                    source_path=turn.source_path,
                    turn_index=turn.turn_index,
                    text=turn.text,
                    embedding=[],
                    created_at=now_iso(),
                    timestamp=turn.timestamp,
                    preceding_assistant_text=turn.preceding_assistant_text,
                    following_assistant_text=turn.following_assistant_text,
                    model=turn.model,
                    effort=turn.effort,
                    themes=[guess_task_type(turn.text)] if turn.text else [],
                )
                upsert_prompt_node(node)
                existing_ids.add(node_id)
                result.added += 1

        cursors[source] = max_mtime
        if boundary is not None:
            drained[source] = (boundary[0], boundary[1])
        if not truncated:
            scanned_at[source] = time.time()

    _save_cursors(cursors, drained, scanned_at)
    result.took_ms = int((time.monotonic() - started) * 1000)
    return result
