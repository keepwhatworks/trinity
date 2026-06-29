"""Disk-backed JSONL store for memory schemas (§8.4).

One JSONL file per type. Append-only for upserts (latest wins on read by id).
This is the bootstrap shape — switch to mmap'd vector blob + sqlite metadata
later if perf demands.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterator

from ..state_paths import (
    ingest_cursors_path,
    prompt_nodes_path,
    turn_windows_path,
)
from ..utils import now_iso
from .schemas import PromptNode, TurnWindow


_write_lock = threading.Lock()


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")


def upsert_prompt_node(node: PromptNode) -> None:
    _append_jsonl(prompt_nodes_path(), node.to_dict())


def upsert_turn_window(window: TurnWindow) -> None:
    _append_jsonl(turn_windows_path(), window.to_dict())


def _iter_jsonl_latest_by_id(path: Path, *, protect_field: str | None = None) -> Iterator[dict]:
    """Yield the latest record per id from a JSONL file (append-only upsert).

    `protect_field` guards against a later record clobbering a richer earlier
    one: when set, a new record whose `protect_field` is empty/falsy will NOT
    overwrite a stored record whose `protect_field` is non-empty. Used for
    `embedding` so an empty-embedding PromptNode (written cheaply by
    incremental ingest) can't shadow the same id's fully-embedded record —
    the dim-0 / lost-vector hazard the basin filter only papers over.

    NOTE for future writers: suppression is whole-record — when a later empty
    record is dropped, ANY non-embedding fields it carried are dropped with it.
    Harmless today (no writer upserts an empty-embedding metadata patch onto an
    existing id), but a future "patch metadata, keep embedding" writer would
    need a field-level merge here instead."""
    if not path.exists():
        return
    seen: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # guard_shape_not_just_parse: a valid-JSON-but-non-dict line (a manual
            # edit, a concatenation/partial-write bug) would crash `.get` with an
            # uncaught AttributeError — taking down the WHOLE corpus read (every
            # lens-build, search, ingest). Skip the junk line, keep the good ones.
            if not isinstance(record, dict):
                continue
            rid = record.get("id")
            if not rid:
                continue
            if protect_field and rid in seen:
                if not record.get(protect_field) and seen[rid].get(protect_field):
                    continue  # don't let an empty field shadow a populated one
            seen[rid] = record
    yield from seen.values()


# Cap the in-process search index at the most-recent N prompts. Beyond that,
# old prompts stay on disk but aren't loaded for search/autofill — recent
# history is what the user typically searches for. Override with the env var
# TRINITY_PROMPT_NODE_LIMIT for testing or larger working sets.
import os as _os
PROMPT_NODE_SEARCH_LIMIT = int(_os.environ.get("TRINITY_PROMPT_NODE_LIMIT", "5000"))

_PROMPT_NODE_CACHE: list[PromptNode] | None = None
_PROMPT_NODE_CACHE_KEY: tuple[float, int, int] | None = None


def _node_sort_key(node: PromptNode) -> str:
    """Stable sort key for newest-first ordering. Prefers `timestamp` (the
    original turn time) and falls back to `created_at` (the ingest time)."""
    return node.timestamp or node.created_at or ""


def tail_prompt_nodes_fast(limit: int = 10) -> list[PromptNode]:
    """Return the LAST N records from prompt_nodes.jsonl WITHOUT a full
    corpus parse. Reads the file from the end, walks back chunk by chunk,
    parses the trailing JSON lines, returns up to `limit` nodes.

    Use for sampling callers (doctor handoff_ready, drift surfaces, etc.)
    that need "K most-recent prompts" semantically and don't need:
      - per-ID dedup (the JSONL is append-upsert, so the last K lines are
        ALMOST always distinct in practice — and the few dup cases that
        slip through are harmless for sampling).
      - a global sort (file order is already roughly chronological).

    On the real 1GB corpus this is ~50ms vs iter_prompt_nodes's ~3.5s.
    DO NOT use for search/autofill or anything that needs the full
    deduplicated index — use iter_prompt_nodes for that.
    """
    if limit <= 0:
        return []
    path = prompt_nodes_path()
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if size == 0:
        return []

    # Read backwards in 64KB chunks; each PromptNode line is ~5-15KB
    # (the embedding field dominates), so 64KB normally covers 4-10
    # records. Grow until we have enough valid records or hit the start.
    CHUNK = 64 * 1024
    records: list[dict] = []
    buf = b""
    offset = size
    try:
        with path.open("rb") as fh:
            while offset > 0 and len(records) < limit:
                read_len = min(CHUNK, offset)
                offset -= read_len
                fh.seek(offset)
                buf = fh.read(read_len) + buf
                # Split on newlines; the first piece may be a partial
                # line (its start is mid-record) — drop it unless we're
                # at the file start.
                lines = buf.split(b"\n")
                if offset > 0:
                    head = lines[0]
                    rest = lines[1:]
                    buf = head  # carry partial back to next chunk
                else:
                    rest = lines
                    buf = b""
                # Parse from newest (end) to oldest (start) within rest.
                for raw in reversed(rest):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if len(records) >= limit:
                        break
    except OSError:
        return []

    nodes: list[PromptNode] = []
    for record in records:
        try:
            nodes.append(PromptNode.from_dict(record))
        except Exception:
            continue
    return nodes[:limit]


_UNSET = object()  # Sentinel so callers can distinguish "default cap" from "explicit None"

# Per-PromptNode the `embedding` field is a 768-element float array
# (~10KB serialized). For callers that never read it — anything in
# search/ranking that goes through token-jaccard or substring scoring,
# never vector cosine — paying ~1.85s of json.loads on the corpus per
# cold render is pure waste. The strip below replaces the array with
# an empty `[]` BEFORE json.loads, so PromptNode lands with
# embedding=[] and downstream code never knows. ~1.8s saved on the
# 1GB/38K-node corpus.
import re as _re
_EMBEDDING_STRIP_RE = _re.compile(r'"embedding":\s*\[[^\]]*\]')

# Parallel cache for the skinny variant — keyed on file mtime + size
# + limit, same as the full cache. Different cache to keep the full
# variant uncontaminated.
_PROMPT_NODE_SKINNY_CACHE: list[PromptNode] | None = None
_PROMPT_NODE_SKINNY_CACHE_KEY: tuple[str, float, int, int] | None = None


def iter_prompt_nodes_no_embedding(*, limit: object = _UNSET) -> Iterator[PromptNode]:
    """Like iter_prompt_nodes but skips embedding-array parsing.

    Use ONLY for callers that don't read PromptNode.embedding — empty-
    query search (token-jaccard ranking, no vectors) is the main one.
    Returns PromptNodes with embedding=[] always; do not feed these
    into vector-similarity code paths.

    Saves ~1.85s on the live 1GB corpus by regex-stripping the 768-
    element float array out of each JSON line BEFORE json.loads. The
    rest of the record (text, timestamps, council_run_ids, themes) is
    parsed normally.
    """
    global _PROMPT_NODE_SKINNY_CACHE, _PROMPT_NODE_SKINNY_CACHE_KEY
    path = prompt_nodes_path()
    try:
        stat = path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        mtime, size = 0.0, 0

    if limit is _UNSET:
        effective_limit: int | None = PROMPT_NODE_SEARCH_LIMIT
    else:
        effective_limit = limit  # type: ignore[assignment]

    signature = (str(path), mtime, size, effective_limit if effective_limit is not None else -1)
    if _PROMPT_NODE_SKINNY_CACHE is not None and _PROMPT_NODE_SKINNY_CACHE_KEY == signature:
        yield from _PROMPT_NODE_SKINNY_CACHE
        return

    if not path.exists():
        _PROMPT_NODE_SKINNY_CACHE = []
        _PROMPT_NODE_SKINNY_CACHE_KEY = signature
        return

    nodes: list[PromptNode] = []
    seen: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                stripped = _EMBEDDING_STRIP_RE.sub('"embedding":[]', line)
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):  # guard_shape_not_just_parse (see above)
                    continue
                rid = record.get("id")
                if rid:
                    seen[rid] = record
    except OSError:
        pass

    for record in seen.values():
        try:
            nodes.append(PromptNode.from_dict(record))
        except Exception:
            continue

    if effective_limit is not None and len(nodes) > effective_limit:
        nodes.sort(key=_node_sort_key, reverse=True)
        nodes = nodes[:effective_limit]

    _PROMPT_NODE_SKINNY_CACHE = nodes
    _PROMPT_NODE_SKINNY_CACHE_KEY = signature
    yield from nodes


def iter_prompt_nodes(*, limit: object = _UNSET) -> Iterator[PromptNode]:
    """Yield PromptNodes (newest first), capped at the configured search limit
    by default.

    - omit `limit`        → cap at PROMPT_NODE_SEARCH_LIMIT (search/autofill default)
    - `limit=None`        → NO CAP — full corpus scan (for ID lookups)
    - `limit=<int>`       → cap at that many

    Cached in-process by file mtime + size + effective limit. Parsing 18k JSONL
    records costs ~2.7s; with the cache, subsequent reads in the same process
    are ~5ms.
    """
    global _PROMPT_NODE_CACHE, _PROMPT_NODE_CACHE_KEY
    path = prompt_nodes_path()
    try:
        stat = path.stat()
        mtime, size = stat.st_mtime, stat.st_size
    except OSError:
        mtime, size = 0.0, 0

    if limit is _UNSET:
        effective_limit: int | None = PROMPT_NODE_SEARCH_LIMIT
    else:
        # `limit=None` explicitly means "no cap"; an int means "cap to that".
        effective_limit = limit  # type: ignore[assignment]

    # `path` is in the signature so monkeypatched TRINITY_HOME in tests
    # invalidates the cache automatically — without this the cache holds
    # the previous test's tmp-dir nodes and downstream tests see stale
    # data (caught real-corpus depth tests skipping silently in the full
    # suite while passing in isolation).
    signature = (str(path), mtime, size, effective_limit if effective_limit is not None else -1)

    if _PROMPT_NODE_CACHE is not None and _PROMPT_NODE_CACHE_KEY == signature:
        yield from _PROMPT_NODE_CACHE
        return

    nodes: list[PromptNode] = []
    for record in _iter_jsonl_latest_by_id(path, protect_field="embedding"):
        try:
            nodes.append(PromptNode.from_dict(record))
        except Exception:
            continue

    if effective_limit is not None and len(nodes) > effective_limit:
        # Sort newest-first, then truncate. Keeps search/autofill bounded as
        # ingest grows; users with deep history can raise the env var or pass
        # `limit=None` for full-corpus operations.
        nodes.sort(key=_node_sort_key, reverse=True)
        nodes = nodes[:effective_limit]

    _PROMPT_NODE_CACHE = nodes
    _PROMPT_NODE_CACHE_KEY = signature
    yield from nodes


def iter_turn_windows() -> Iterator[TurnWindow]:
    for record in _iter_jsonl_latest_by_id(turn_windows_path()):
        try:
            yield TurnWindow.from_dict(record)
        except Exception:
            continue


def load_prompt_node(node_id: str) -> PromptNode | None:
    """Look up a PromptNode by id with NO recency cap.

    `iter_prompt_nodes()` defaults to the recent-N working set (5000), which
    is right for search/autofill but wrong for ID lookup: `record_council_outcome`
    gets called with prompt IDs that may be older than the cap. Without
    `limit=None` this silently returned no-match for old prompts.
    """
    for node in iter_prompt_nodes(limit=None):
        if node.id == node_id:
            return node
    return None


def record_council_outcome(
    *,
    prompt_node_id: str,
    council_run_id: str,
    chairman_winner: str | None = None,
) -> bool:
    """Attach a council outcome to a PromptNode by re-upserting the record.

    Returns True if the node was found and updated.
    """
    node = load_prompt_node(prompt_node_id)
    if node is None:
        return False
    if council_run_id not in node.council_run_ids:
        node.council_run_ids = [*node.council_run_ids, council_run_id]
    if chairman_winner is not None:
        node.chairman_winner = chairman_winner
    node.last_replayed_at = now_iso()
    upsert_prompt_node(node)
    return True


def load_cursor(source: str) -> dict:
    """Cursor for incremental ingest. Returns {} if no cursor for this source."""
    path = ingest_cursors_path()
    if not path.exists():
        return {}
    try:
        all_cursors = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(all_cursors, dict):
        return {}
    cursor = all_cursors.get(source) or {}
    return cursor if isinstance(cursor, dict) else {}


def save_cursor(source: str, cursor: dict) -> None:
    """Persist cursor for a source. Cursor shape is source-specific."""
    path = ingest_cursors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        if path.exists():
            try:
                all_cursors = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(all_cursors, dict):
                    all_cursors = {}
            except (json.JSONDecodeError, OSError):
                all_cursors = {}
        else:
            all_cursors = {}
        all_cursors[source] = cursor
        from ..utils import atomic_write_text
        atomic_write_text(path, json.dumps(all_cursors, indent=2))
