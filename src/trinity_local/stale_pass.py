"""Auto-Dream-style stale pass: full ingest + embedding heal, piggybacked on
council runs (#251 — redesigned 2026-06-09, founder call: no separate cron).

The 1s incremental slices (`ingest_recent` on every MCP tool call) keep the
prompt index CURRENT but write nodes WITHOUT embeddings — and nothing healed
them: `flush_chunk` (bulk import) skips already-indexed ids, and lens-build
only *filters out* unembedded nodes (the incremental_ingest docstring's
"recomputed lazily by lens-build" claim was stale). Verified on the live
corpus: 2,904 of 30,129 nodes unembedded with no path back.

This module is the heal, triggered the way Anthropic's Auto-Dream triggers —
on USAGE, gated on staleness — never on a timer that burns cycles while the
tool sits idle:

  * ``maybe_kick_stale_pass(trigger)`` is called at council launch
    (run_council / run_consensus_round — auto_chain rides the latter) and from
    the MCP first-tool-call kick. Cost when not due: one marker read.
  * Due = marker missing or older than ``TRINITY_STALE_PASS_HOURS`` (24h,
    Auto-Dream's window). A cross-process lock file (same pattern as
    cold_start's lens_refresh.lock, #234) stops concurrent council launches
    from double-running; a lock older than _LOCK_STALE_S is presumed dead and
    taken over.
  * The pass runs in a daemon thread: ``ingest_recent`` with a generous
    deadline (catch up transcripts), then ``embed_backfill`` (heal unembedded
    nodes). The council it piggybacks on is never blocked.
  * ``embed_backfill`` ABSTAINS unless the real embedder model is downloaded
    (``require_embedder_ready``) — healing 768d vectors with the SHA-1 TF-IDF
    fallback would poison the vector space (the #106/#109 rule: a fallback
    that emits wrong output is worse than none). Texts are embedded with the
    SAME ``search_document:`` prefix as flush_chunk so healed vectors live in
    the same space as bulk-ingested ones; the node's created_at/timestamp are
    PRESERVED (healing must not make old prompts look recent).

Respects ``TRINITY_AUTOSCAN_DISABLED`` (the suite-wide kill switch) so tests
that exercise councils never ingest the developer's real transcripts.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
import time

from .state_paths import prompts_dir
from .utils import atomic_write_text, now_iso

DEFAULT_STALE_HOURS = 24.0
# A pass that has held the lock this long is presumed crashed; the next
# council launch takes the lock over. Generous: a full backfill of ~3k nodes
# is minutes on the torch fallback, seconds on Apple-MLX.
_LOCK_STALE_S = 45 * 60
# Bounds for the two phases. The ingest deadline is per-call (cursor resumes
# next pass); the backfill deadline bounds the embed loop between batches.
_INGEST_DEADLINE_S = 60.0
_BACKFILL_DEADLINE_S = 15 * 60.0
_EMBED_BATCH_SIZE = 64


def marker_path():
    return prompts_dir() / "last_stale_pass.json"


def lock_path():
    return prompts_dir() / "stale_pass.lock"


def _disabled() -> bool:
    return os.environ.get("TRINITY_AUTOSCAN_DISABLED", "").strip() not in ("", "0", "false", "False")


def _stale_hours() -> float:
    try:
        return float(os.environ.get("TRINITY_STALE_PASS_HOURS", DEFAULT_STALE_HOURS))
    except ValueError:
        return DEFAULT_STALE_HOURS


def stale_pass_is_due() -> tuple[bool, str]:
    """(due, reason). Due when the marker is missing, unreadable, wrong-shaped,
    or older than the staleness window. One small file read — cheap enough for
    every council launch."""
    path = marker_path()
    if not path.exists():
        return True, "no prior pass recorded"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True, "marker unreadable"
    if not isinstance(raw, dict):  # guard_shape_not_just_parse
        return True, "marker wrong shape"
    completed = raw.get("completed_at")
    if not isinstance(completed, str) or not completed:
        return True, "marker missing completed_at"
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return True, "marker timestamp unparseable"
    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600.0
    if age_h >= _stale_hours():
        return True, f"last pass {age_h:.1f}h ago (window {_stale_hours():.0f}h)"
    return False, f"fresh ({age_h:.1f}h ago)"


def _try_claim_lock() -> bool:
    """Atomic cross-process claim (O_CREAT|O_EXCL). A lock file older than
    _LOCK_STALE_S belonged to a crashed pass — take it over."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "claimed_at": now_iso()})
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > _LOCK_STALE_S:
                path.write_text(payload, encoding="utf-8")  # takeover
                return True
        except OSError:
            pass
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(payload)
    return True


def _release_lock() -> None:
    try:
        lock_path().unlink()
    except OSError:
        pass


def embed_backfill(*, deadline_s: float = _BACKFILL_DEADLINE_S) -> dict:
    """Heal PromptNodes that were ingested without embeddings.

    Re-upserts each healed node (the store is last-wins by id) with ONLY the
    embedding changed. Abstains entirely when the embedder model isn't
    downloaded — never writes TF-IDF-fallback vectors into a 768d space.
    """
    from .embeddings import DEFAULT_DIM, EmbedderNotReadyError, require_embedder_ready

    try:
        require_embedder_ready()
    except EmbedderNotReadyError as exc:
        return {"healed": 0, "remaining": -1, "skipped": f"embedder not ready: {exc}"}

    from .ingest_helpers import _embed_in_batches
    from .memory import upsert_prompt_node
    from .memory.store import iter_prompt_nodes

    # limit=None lifts the ~5000-node hot-path cap (mirrors compute_basins): the
    # unembedded tail is the OLDEST nodes (a stalled backfill leaves April/May
    # prompts dark), which sit BELOW the default cap. With the cap on, the heal
    # silently no-ops on exactly the nodes it exists to fix (#235 never closed).
    pending = [node for node in iter_prompt_nodes(limit=None) if not node.embedding]
    if not pending:
        return {"healed": 0, "remaining": 0}

    healed = 0
    started = time.monotonic()
    deadline_hit = False
    for i in range(0, len(pending), _EMBED_BATCH_SIZE):
        if time.monotonic() - started >= deadline_s:
            deadline_hit = True
            break
        batch = pending[i:i + _EMBED_BATCH_SIZE]
        # Same prefix as flush_chunk — healed vectors must live in the same
        # embedding space as bulk-ingested ones.
        texts = [f"search_document: {node.text}" for node in batch]
        vectors = _embed_in_batches(texts, dim=DEFAULT_DIM, batch_size=_EMBED_BATCH_SIZE)
        for node, vec in zip(batch, vectors):
            if not vec:
                continue
            upsert_prompt_node(dataclasses.replace(node, embedding=vec))
            healed += 1
    return {"healed": healed, "remaining": len(pending) - healed, "deadline_hit": deadline_hit}


def run_stale_pass(trigger: str = "manual") -> dict:
    """The pass itself, synchronous: transcript catch-up, then embedding heal.
    Writes the marker on completion (even partial — the deadline-bounded
    remainder resumes on the next stale window rather than hammering every
    council launch)."""
    from .incremental_ingest import ingest_recent

    summary: dict = {"trigger": trigger, "started_at": now_iso()}
    try:
        summary["ingest"] = ingest_recent(deadline_s=_INGEST_DEADLINE_S).to_dict()
    except Exception as exc:  # noqa: BLE001 — a broken source must not kill the heal
        summary["ingest"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        summary["backfill"] = embed_backfill()
    except Exception as exc:  # noqa: BLE001
        summary["backfill"] = {"error": f"{type(exc).__name__}: {exc}"}
    # Routing compounds AUTOMATICALLY (#316 journey-audit): rebuild picks.json
    # from the lens basins + accumulated council outcomes so `ask`'s basin
    # routing reflects recent winners WITHOUT a manual `consolidate`/`dream`.
    # This is the usage event that makes the README's "the right model picked
    # automatically / gets sharper as you use it" claim literally true — fired on
    # a council launch (the same trigger that kicks this pass), not a cron (the
    # founder's usage-gate rule). Cheap + LLM-FREE: a deterministic recency-
    # weighted tally over topics.json's live centroids. A no-op cold (no lens →
    # {} basins → save skipped, also dodging the clobber guard). Best-effort: a
    # routing rebuild must never break the heal it piggybacks on.
    try:
        from .cortex import save_routing_patterns
        from .lens_routing import consolidate_via_lens_basins

        routing = consolidate_via_lens_basins()
        if routing:
            save_routing_patterns(routing)
        summary["consolidate"] = {"routable_basins": len(routing)}
    except Exception as exc:  # noqa: BLE001
        summary["consolidate"] = {"error": f"{type(exc).__name__}: {exc}"}
    summary["completed_at"] = now_iso()
    try:
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker_path(), json.dumps(summary, indent=2))
    except OSError:
        pass
    return summary


def maybe_kick_stale_pass(trigger: str) -> bool:
    """The usage-gated entrypoint (call at council launch / MCP first call).
    Returns True when a pass was actually kicked. Never raises and never
    blocks the caller — the pass runs in a daemon thread."""
    try:
        from .lens_addon import lens_enabled
        if not lens_enabled():
            return False  # lens is an opt-in add-on; core fusion does no ingest/embed
        if _disabled():
            return False
        due, _reason = stale_pass_is_due()
        if not due:
            return False
        if not _try_claim_lock():
            return False

        def _run() -> None:
            try:
                run_stale_pass(trigger=trigger)
            finally:
                _release_lock()

        threading.Thread(target=_run, name="trinity-stale-pass", daemon=True).start()
        return True
    except Exception:  # noqa: BLE001 — the gate must never break a council launch
        return False
