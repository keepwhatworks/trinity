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
    skipped_parse: int = 0
    sources: list[str] = field(default_factory=list)
    took_ms: int = 0
    deadline_hit: bool = False

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "added": self.added,
            "skipped_existing": self.skipped_existing,
            "skipped_parse": self.skipped_parse,
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


def _save_cursors(
    cursors: dict[str, float],
    drained: dict[str, tuple[str, int]] | None = None,
) -> None:
    from .utils import atomic_write_text
    path = ingest_cursors_path()
    drained = drained or {}
    payload: dict[str, dict] = {}
    for source, mtime in cursors.items():
        entry: dict = {"last_mtime": mtime}
        if source in drained:
            entry["drained_path"], entry["drained_size"] = drained[source]
        payload[source] = entry
    atomic_write_text(path, json.dumps(payload, indent=2))


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
    the latest-scanned path mtime so the next call resumes."""
    from .watch_runtime import _iter_recent_paths, _parse_source_path

    sources = list(sources or DEFAULT_SOURCES)
    cursors = _load_cursors()
    drained = _load_drained()
    existing_ids = _existing_prompt_node_ids()

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

        try:
            paths = list(_iter_recent_paths(source, last_mtime))
        except (OSError, ValueError):
            continue

        for path in paths:
            if time.monotonic() - started >= deadline_s:
                result.deadline_hit = True
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
                continue
            if boundary is None or file_mtime >= boundary[2]:
                boundary = (str(path), file_size, file_mtime)

            try:
                session = _parse_source_path(source, path)
            except Exception:
                result.skipped_parse += 1
                continue
            if session is None:
                result.skipped_parse += 1
                continue

            try:
                turns = list(iter_prompt_turns(session))
            except Exception:
                result.skipped_parse += 1
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
                    themes=[guess_task_type(turn.text)] if turn.text else [],
                )
                upsert_prompt_node(node)
                existing_ids.add(node_id)
                result.added += 1

        cursors[source] = max_mtime
        if boundary is not None:
            drained[source] = (boundary[0], boundary[1])

    _save_cursors(cursors, drained)
    result.took_ms = int((time.monotonic() - started) * 1000)
    return result
