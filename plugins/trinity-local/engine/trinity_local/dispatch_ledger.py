"""Dispatch ledger — stop Trinity's own dispatches from re-entering the corpus.

Every prompt Trinity fires through a provider CLI (council members, chairman
synthesis, eval targets, eval judges) lands in that CLI's transcript directory
as a role=user turn — and the ingest pipeline, correctly trusting role=user,
indexes it as the founder's authored voice. The generated-shape filters
(#245/#248, generator-over-generated) can't catch the worst case: eval items
ARE the founder's original prompts replayed verbatim, so the replica passes
every voice filter and lands as a duplicate-content node under a new
transcript_id (verified 2026-06-09: one 42-item eval run put 40 verbatim
replicas of original prompts into the corpus — basin mass, recency, and
vocabulary stats all inflate, and it compounds with every eval/council run).

The fix exploits what no filter heuristic has: Trinity KNOWS which prompts it
dispatched. At dispatch time (providers.CLIProvider.run / CodexProvider.run —
the chokepoint every council member, chairman, eval target, and eval judge
call goes through) the prompt's normalized hash is appended here; at ingest
time the role=user gate (`ingest._is_user_facing_prompt`, the #260 single
barrier) drops turns whose hash matches a recent dispatch.

Privacy: the ledger stores ONLY sha1 hashes + timestamps — never prompt text.

Precision guards:
  * Prompts shorter than _MIN_RECORD_CHARS aren't recorded — a short turn
    ("fix this", "ok try again") the founder might genuinely retype must
    never be suppressed by hash collision with a dispatched one.
  * Entries expire after _LEDGER_TTL_DAYS, bounding the false-positive
    window: a founder who types text identical to something Trinity
    dispatched is only shadowed within the window, and only for NEW ingests
    (already-indexed originals keep their nodes — the gate runs before
    stable_id dedup, so it can only skip additions, never remove).
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .state_paths import prompts_dir
from .utils import now_iso

_LEDGER_TTL_DAYS = 7.0
_MIN_RECORD_CHARS = 40
# Normalize then bound: whitespace runs collapse (transcript round-trips can
# reflow), and hashing the first 2000 chars keeps one-token-different megaprompts
# distinct enough while bounding work.
_HASH_PREFIX_CHARS = 2000


def ledger_path() -> Path:
    return prompts_dir() / "dispatched_prompts.jsonl"


def _norm_hash(text: str) -> str:
    normalized = " ".join((text or "").split())[:_HASH_PREFIX_CHARS]
    return hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()


def record_dispatched_prompt(text: str) -> bool:
    """Append the dispatch hash. Best-effort: a ledger failure must never
    break a council/eval dispatch. Returns True when recorded."""
    try:
        if not text or len(text.strip()) < _MIN_RECORD_CHARS:
            return False
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"h": _norm_hash(text), "ts": now_iso()}) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


_CACHE_KEY: tuple[str, float, int] | None = None
_CACHE_SET: frozenset[str] = frozenset()


def dispatched_hash_set() -> frozenset[str]:
    """The TTL-live dispatch hashes. (mtime, size)-keyed module cache — the
    ingest gate consults this per user turn, so re-reading the file each call
    would dominate the 1s incremental budget."""
    global _CACHE_KEY, _CACHE_SET
    path = ledger_path()
    try:
        stat = path.stat()
    except OSError:
        return frozenset()
    key = (str(path), stat.st_mtime, stat.st_size)
    if key == _CACHE_KEY:
        return _CACHE_SET
    cutoff = time.time() - _LEDGER_TTL_DAYS * 86400
    from datetime import datetime, timezone

    live: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):  # guard_shape_not_just_parse
                continue
            h, ts = rec.get("h"), rec.get("ts")
            if not isinstance(h, str) or not isinstance(ts, str):
                continue
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if when.timestamp() >= cutoff:
                live.add(h)
    except OSError:
        return frozenset()
    _CACHE_KEY, _CACHE_SET = key, frozenset(live)
    return _CACHE_SET


def is_trinity_dispatched(text: str) -> bool:
    """True when this exact (normalized) text was dispatched by Trinity within
    the TTL window — the ingest gate's signal that a role=user turn is
    Trinity's own dispatch echoing back, not the founder typing."""
    if not text or len(text.strip()) < _MIN_RECORD_CHARS:
        return False
    hashes = dispatched_hash_set()
    if not hashes:
        return False
    return _norm_hash(text) in hashes
