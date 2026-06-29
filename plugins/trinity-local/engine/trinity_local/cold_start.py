"""Cold-start auto-scan of local CLI transcripts on first MCP spawn.

The wow flow needs personalization on the first council, not a week
later. The four local-CLI parsers (Claude Code, Codex, Antigravity,
Cowork) all read from on-disk dirs the user already has — so the
moment Trinity's MCP child starts under a brand-new install, we can
auto-detect "no corpus + at least one CLI source present" and kick a
background scan. The server keeps serving tool calls immediately;
tool responses surface a `cold_start_scan` hint so the agent can tell
the user "I'm ingesting your CLI history…" while the scan runs.

Privacy invariant: same data path as the retired `seed-from-taste-terminal`
(replaced by `import-export` 2026-05-27). Only walks transcript dirs
the user already owns on this machine. No exports, no network, no
opt-in dialog. Same `incremental_ingest`
pipeline so dedup / cursors / parser fallthrough behavior is shared.

Disable for tests + CI with ``TRINITY_AUTOSCAN_DISABLED=1``; the
conftest autouse fixture sets it so tests never scan the developer's
real ``~/.claude/``.
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from pathlib import Path

from .state_paths import state_dir
from .utils import now_iso


COLD_START_SOURCES = ("claude", "codex", "gemini", "cowork")
DEFAULT_SCAN_DEADLINE_S = 300.0
HINT_FRESH_WINDOW_S = 600.0  # surface "scan complete" for 10 min after finish
# An in_progress scan older than this is DEAD — the process was killed (SIGKILL /
# OOM / crash) before _run_scan wrote a terminal state, which the thread's broad
# try/except can't catch. Past this window, treat it as re-scannable so a crash
# mid-scan can't wedge cold-start forever (well beyond DEFAULT_SCAN_DEADLINE_S).
_SCAN_STALE_S = DEFAULT_SCAN_DEADLINE_S * 2


def cold_start_state_path() -> Path:
    return state_dir() / "cold_start_scan.json"


def _autoscan_disabled() -> bool:
    return os.environ.get("TRINITY_AUTOSCAN_DISABLED", "").strip() not in ("", "0", "false", "False")


def detect_available_sources() -> list[str]:
    """Return the subset of local-CLI sources whose dirs exist on this
    machine. Empty dir counts as absent — a user who installed Claude
    Code but never ran it shouldn't trigger an empty cold-start scan."""
    from .watch_runtime import _iter_recent_paths, _source_root

    available: list[str] = []
    for source in COLD_START_SOURCES:
        try:
            root = _source_root(source)
        except ValueError:
            continue
        if not root.exists():
            continue
        # At least one matching transcript file present.
        if any(True for _ in _iter_recent_paths(source, 0.0)):
            available.append(source)
    return available


def _corpus_is_empty() -> bool:
    """True when no PromptNodes are on disk. Read directly from the
    JSONL file path to avoid pulling the full module + cache layer on
    the cold-start hot path."""
    from .state_paths import prompts_dir

    path = prompts_dir() / "prompt_nodes.jsonl"
    if not path.exists():
        return True
    try:
        return path.stat().st_size == 0
    except OSError:
        return True


def _scan_in_progress_but_dead(state: dict) -> bool:
    """True when state.status is in_progress but its started_at is older than
    _SCAN_STALE_S — a scan the process was killed mid-run, before it could write a
    terminal state. Such a state must NOT wedge cold-start forever, so it's treated
    as re-scannable. A missing/unparseable started_at on an in_progress is treated
    as dead too (can't prove it's live)."""
    if state.get("status") != "in_progress":
        return False
    started = state.get("started_at")
    if not started:
        return True
    try:
        from datetime import datetime, timezone

        ts = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() > _SCAN_STALE_S
    except (ValueError, TypeError):
        return True


def is_cold_start() -> bool:
    """Cold-start trigger: empty corpus AND no LIVE/finished prior scan AND at
    least one local CLI source present AND not disabled by env. A DEAD in_progress
    scan (process killed before writing a terminal state) does NOT count as prior
    state — otherwise a crash mid-scan would wedge cold-start forever."""
    if _autoscan_disabled():
        return False
    state = read_state()
    if state is not None and not _scan_in_progress_but_dead(state):
        # A completed/failed scan already ran, or an in_progress scan is still
        # within its window — either way, don't (re-)fire.
        return False
    if not _corpus_is_empty():
        return False
    return bool(detect_available_sources())


def read_state() -> dict | None:
    path = cold_start_state_path()
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # guard_shape_not_just_parse: a valid-JSON-but-non-dict state file (partial/
    # concurrent write, manual edit) must honor the `-> dict | None` contract or
    # every caller's `.get(...)` crashes the cold-start path.
    return obj if isinstance(obj, dict) else None


def _write_state(state: dict) -> None:
    from .utils import atomic_write_text
    atomic_write_text(cold_start_state_path(), json.dumps(state, indent=2))


def _run_scan(sources: list[str], deadline_s: float, start_iso: str) -> None:
    """The thread body. Runs the scan, rewrites the state file with the
    result. Wrapped in broad try/except: a parser blow-up in any source
    cannot leave the state file at status=in_progress forever (would
    block future cold-start triggers).

    The initial in_progress state file is written synchronously by
    ``kick_cold_start_scan`` BEFORE this thread starts, so the
    cross-process race (two MCP servers calling is_cold_start()
    simultaneously) closes via the existence-check on the state file.
    """
    from .incremental_ingest import ingest_recent

    started = time.monotonic()
    error: str | None = None
    added = 0
    scanned = 0
    try:
        result = ingest_recent(sources=sources, deadline_s=deadline_s)
        added = result.added
        scanned = result.scanned
    except Exception as exc:
        # TYPE only — the raw str(exc) leaks Python internals (a bare quoted
        # KeyError key, "Expecting value: line 1 column 1") and, worst, an
        # absolute FILESYSTEM PATH ("No such file or directory: '/Users/<name>/
        # .claude/...'") into cold_start_hint()'s agent-facing message. The hint
        # already carries the actionable recovery (`import-export <path>`); the
        # raw payload adds nothing the user can act on. (Same class as the
        # lens-health #140 / launchpad lens-build #141 raw-exc-into-a-user-
        # surface fixes.) The full str(exc) lives only in the debug marker below.
        error = type(exc).__name__

    # #242: decouple first-run VALUE from the (slow) embedding backfill. Anchors
    # are pure-text proper-noun recurrence — no embeddings, no basins, no lens —
    # so the instant the scan ingests prompts we can name the user's recurring
    # topics. Computed ONCE here (cold-start only fires on a fresh, small corpus)
    # and cached in the state file for cold_open_tension() to read cheaply.
    early_anchors = _compute_early_anchors() if not error else []

    _write_state({
        "status": "failed" if error else "complete",
        "started_at": start_iso,
        "finished_at": now_iso(),
        "sources_detected": list(sources),
        "added": added,
        "scanned": scanned,
        "early_anchors": early_anchors,
        "deadline_s": deadline_s,
        "duration_s": round(time.monotonic() - started, 2),
        "error": error,
    })


def _compute_early_anchors(top_n: int = 5) -> list[str]:
    """Pure-text top anchors (no embeddings) for the first-run insight (#242).

    Best-effort: any failure returns [] so the scan thread never dies on the
    cheap-insight path. min_threads=2 (looser than vocab's 3) because a fresh
    install has few threads but we still want SOME signal."""
    try:
        from .memory.store import iter_prompt_nodes
        from .vocabulary import find_anchors

        nodes = list(iter_prompt_nodes(limit=None))
        if not nodes:
            return []
        anchors = find_anchors(nodes, min_threads=2, top_n=top_n)
        return [phrase for phrase, _threads, _mentions in anchors]
    except Exception:
        return []


def kick_cold_start_scan(deadline_s: float = DEFAULT_SCAN_DEADLINE_S) -> dict | None:
    """Spawn the cold-start scan in a daemon thread. Returns the initial
    state dict (with status=in_progress), or None when no scan was kicked
    (autoscan disabled, corpus non-empty, prior scan present, or no
    available sources). Caller doesn't wait — the thread runs to deadline
    or completion and rewrites the state file.

    The initial in_progress state file is written SYNCHRONOUSLY before
    the daemon thread starts. This closes the cross-process race where
    two MCP servers (Claude Code + Codex CLI + Cursor + Antigravity all
    spawn on session start) call is_cold_start() simultaneously and
    both see an empty state — only the first to reach this function
    creates the state file; the rest hit the `is_cold_start()`
    state-file-exists short-circuit and return None.
    """
    if not is_cold_start():
        return None
    sources = detect_available_sources()
    if not sources:
        return None

    start_iso = now_iso()
    # Write the in_progress state BEFORE spawning the thread so the
    # second simultaneous caller's is_cold_start() check sees it and
    # bails. Within a single process, threading.Lock would be cheaper
    # but doesn't help across processes; the on-disk state file is
    # the cross-process serialization point.
    _write_state({
        "status": "in_progress",
        "started_at": start_iso,
        "finished_at": None,
        "sources_detected": list(sources),
        "added": 0,
        "scanned": 0,
        "deadline_s": deadline_s,
        "error": None,
    })

    thread = threading.Thread(
        target=_run_scan,
        args=(sources, deadline_s, start_iso),
        name="trinity-cold-start-scan",
        daemon=True,
    )
    thread.start()
    return read_state()


def cold_start_hint() -> dict | None:
    """For MCP tool responses. Returns a compact payload when the scan
    is running OR finished within ``HINT_FRESH_WINDOW_S``. The agent
    surfaces it inline so the user sees "I'm building your memory" without
    a launchpad detour. Returns None when no scan has ever fired (cold-
    start blocked or already-warm install) or when the scan is too old
    to be the agent's news."""
    state = read_state()
    if state is None:
        return None
    status = state.get("status")
    if status == "in_progress":
        return {
            "status": "in_progress",
            "message": (
                f"Trinity is ingesting your local CLI history "
                f"({', '.join(state.get('sources_detected', []))}). "
                f"Responses get more personal as it lands."
            ),
            "added_so_far": state.get("added", 0),
        }
    if status in ("complete", "failed"):
        # Only surface for a short window post-finish.
        finished_at = state.get("finished_at")
        if not finished_at:
            return None
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - ts).total_seconds()
        except (ValueError, TypeError):
            return None
        if age_s > HINT_FRESH_WINDOW_S:
            return None
        if status == "failed":
            return {
                "status": "failed",
                "message": (
                    "Cold-start ingest of your CLI history hit an error: "
                    f"{state.get('error') or 'unknown'}. "
                    "Run `trinity-local import-export <path>` to retry."
                ),
                "added": state.get("added", 0),
            }
        return {
            "status": "complete",
            "message": (
                f"Trinity finished ingesting {state.get('added', 0)} prompts "
                f"from {', '.join(state.get('sources_detected', []))}. "
                f"Memories will warm up over the next few councils."
            ),
            "added": state.get("added", 0),
        }
    return None


def maybe_kick_cold_start() -> dict | None:
    """Idempotent entry point for the MCP server startup hook. Wraps
    ``kick_cold_start_scan`` so thread-spawn failures cannot crash the
    MCP server."""
    try:
        return kick_cold_start_scan()
    except Exception:
        return None


# ── activity-gated lens refresh (Anthropic Auto-Dream pattern) ─────────
#
# Auto-Dream triggers on "24h elapsed AND 5 sessions" — NOT a wall-clock
# nightly cron. We mirror that: refresh an EXISTING lens when enough time
# has passed AND enough new conversation has accumulated, evaluated at MCP
# connect (a natural "session" event) so it runs inside an authenticated
# session — never a 3am cron with no provider auth, never a surprise spend
# on a quiet day. cold-start handles the FIRST build; this handles the
# keep-current refresh.

REFRESH_MIN_AGE_H = 24.0          # Auto-Dream's 24h floor
REFRESH_MIN_NEW_PROMPTS = 5       # Auto-Dream's "5 sessions" analog — enough new material
_REFRESH_COOLDOWN_S = 1800.0      # don't re-kick within 30 min (in-flight damping)
# A refresh lock older than this is presumed dead (its owner crashed mid-build
# and never released it). Bounds the cost of a single lost lock to one stale
# window. Comfortably larger than a real delta rebuild + a safety margin.
_REFRESH_LOCK_STALE_S = 3600.0


def lens_refresh_marker_path() -> Path:
    return state_dir() / "lens_refresh.json"


def lens_refresh_lock_path() -> Path:
    return state_dir() / "lens_refresh.lock"


def _hours_since(iso: str | None) -> float | None:
    if not iso or not isinstance(iso, str):
        return None
    import datetime as _dt
    try:
        ts = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 3600.0


def _fingerprint_count(fp: str) -> int:
    """Fingerprints are 'count:sha1'. Pull the leading count, or 0."""
    head = (fp or "").split(":", 1)[0]
    return int(head) if head.isdigit() else 0


def should_refresh_lens() -> tuple[bool, str]:
    """Activity gate: refresh iff a lens exists, ≥REFRESH_MIN_AGE_H since the
    last build, AND ≥REFRESH_MIN_NEW_PROMPTS new prompts have landed. Returns
    (should, human_reason). Pure read — never raises."""
    try:
        from .me_builder import _corpus_fingerprint, _lens_build_state_path, me_path

        if not me_path().exists():
            return False, "no lens yet (cold-start territory, not refresh)"
        sp = _lens_build_state_path()
        if not sp.exists():
            return False, "no build-state to age against"
        try:
            st = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, "unreadable build state"
        if not isinstance(st, dict):  # guard_shape_not_just_parse: .get below crashes on a non-dict
            return False, "unreadable build state"
        age_h = _hours_since(st.get("built_at"))
        if age_h is None:
            return False, "no built_at timestamp"
        if age_h < REFRESH_MIN_AGE_H:
            return False, f"last build {age_h:.1f}h ago (< {REFRESH_MIN_AGE_H:.0f}h floor)"
        prior_fp = st.get("fingerprint") or ""
        cur_fp = _corpus_fingerprint()
        if cur_fp == prior_fp:
            return False, "corpus unchanged since last build"
        new_prompts = _fingerprint_count(cur_fp) - _fingerprint_count(prior_fp)
        if new_prompts < REFRESH_MIN_NEW_PROMPTS:
            return False, f"only {new_prompts} new prompt(s) (< {REFRESH_MIN_NEW_PROMPTS})"
        return True, f"{new_prompts} new prompts, {age_h:.0f}h since last build"
    except Exception as exc:
        return False, f"gate error: {exc}"


def _refresh_marker_status(summary) -> str:
    """Map a build summary to an HONEST refresh-marker status. The build returns
    `ok:False` (+ `aborted`) on a chairman timeout/quota abort and
    `preserved_existing` when the clobber-guard kept the prior lens over a
    tension-less render — NEITHER advanced the lens, so recording "done" for them
    was the false-green that hid an 18-day freeze. A legit unchanged-corpus skip
    is a benign no-op too."""
    if not isinstance(summary, dict):
        return "done"
    if summary.get("ok") is False or summary.get("aborted"):
        return "failed"
    if summary.get("preserved_existing") or summary.get("skipped"):
        return "no-op"
    return "done"


def lens_freshness_status() -> tuple[str, str]:
    """Ground-truth lens-build freshness for surfacing on `status` — ('stale' |
    'current' | 'absent', human_reason).

    Reads `built_at` vs the live corpus fingerprint (via should_refresh_lens),
    NOT the lens_refresh.json marker. The marker can't be trusted on its own: a
    degenerate build that preserved the prior lens (clobber-guard) used to record
    status="done"/ok:true, which hid an 18-day-stale lens (677 new prompts
    unincorporated) on a real install 2026-06-29. A persistent 'stale' here means
    the activity gate is OPEN yet the auto-refresh build isn't LANDING. When stale,
    enrich the reason with the last refresh's failure cause (chairman timeout/
    quota) so the surface points at WHY, not just THAT.
    """
    try:
        from .me_builder import me_path

        if not me_path().exists():
            return "absent", "no lens built yet"
        due, reason = should_refresh_lens()
        if not due:
            return "current", reason
        # Stale → look up why the last build didn't land (now that the marker is honest).
        try:
            mk = json.loads(lens_refresh_marker_path().read_text(encoding="utf-8"))
            if isinstance(mk, dict) and mk.get("status") == "failed" and mk.get("error"):
                reason = f"{reason} · last build failed: {mk['error']}"
        except Exception:
            pass
        return "stale", reason
    except Exception as exc:  # pragma: no cover - never let a status read raise
        return "absent", f"freshness check error: {exc}"


def _recently_kicked() -> bool:
    """True if a refresh was kicked within the cooldown — damps re-kicks on
    repeated connects while one is still in flight."""
    p = lens_refresh_marker_path()
    if not p.exists():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        # guard_shape_not_just_parse: .get crashes (AttributeError, uncaught by the
        # OSError/ValueError clause) on a valid-JSON-but-non-dict marker.
        last = _hours_since(obj.get("last_kick_at") if isinstance(obj, dict) else None)
    except (OSError, ValueError):
        return False
    return last is not None and (last * 3600.0) < _REFRESH_COOLDOWN_S


def _write_refresh_marker(payload: dict) -> None:
    try:
        from .utils import atomic_write_text
        p = lens_refresh_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(p, json.dumps(payload))
    except Exception:
        pass


def _try_claim_refresh_lock() -> bool:
    """Atomic cross-process claim. Returns True iff THIS caller won the right
    to rebuild.

    Why this exists (#234): every CLI harness the user has connected (Claude
    Code + Codex CLI + Cursor + Antigravity) spawns its own MCP child, and
    they all hit `maybe_kick_lens_refresh()` on connect. `should_refresh_lens()`
    and `_recently_kicked()` are both pure reads, so without a lock all four
    children pass the gate in the same instant and each kicks a full chairman-
    driven rebuild — quadruple spend and four writers racing on the same
    ledger. The single-process cooldown marker can't close this: it's written
    AFTER the check, so concurrent racers all read it as absent.

    The lock is an `O_CREAT | O_EXCL` create — the OS guarantees exactly one
    creator even across processes. A lock older than `_REFRESH_LOCK_STALE_S`
    is treated as abandoned (owner crashed mid-build) and reclaimed, so a lost
    lock self-heals after one stale window instead of wedging refresh forever.
    """
    import os as _os

    path = lens_refresh_lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    # Reclaim a stale lock (crashed owner) before attempting the claim.
    try:
        age_s = time.time() - path.stat().st_mtime
        if age_s > _REFRESH_LOCK_STALE_S:
            path.unlink()
    except OSError:
        pass  # missing (fine) or unstattable (let the create attempt decide)

    try:
        fd = _os.open(str(path), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY, 0o644)
    except FileExistsError:
        return False  # another harness holds the lock — it owns this rebuild
    except OSError:
        return False
    try:
        _os.write(fd, f"{_os.getpid()}|{now_iso()}".encode("utf-8"))
    except OSError:
        pass
    finally:
        _os.close(fd)
    return True


def _release_refresh_lock() -> None:
    try:
        lens_refresh_lock_path().unlink()
    except OSError:
        pass


def maybe_kick_lens_refresh() -> dict | None:
    """If the activity gate is open and no refresh is in flight, background-
    kick a (cheap, delta) lens rebuild. Best-effort: every failure path is
    swallowed so it can't crash or block the MCP server. Returns the kick
    record, or None when skipped.

    #234: the actual in-flight guard is the cross-process lock claimed by
    `_try_claim_refresh_lock()` — only ONE concurrently-connecting harness
    wins it and rebuilds; the rest see the existing lock and bail. The
    `_recently_kicked()` cooldown stays as a cheap pre-filter so the common
    no-op connect doesn't even touch the lockfile."""
    from .lens_addon import lens_enabled
    if not lens_enabled():
        return None  # lens is an opt-in add-on; core fusion does no lens work
    if _autoscan_disabled():
        return None
    try:
        ok, reason = should_refresh_lens()
        if not ok or _recently_kicked():
            return None
        # Atomic check-and-set: only the lock winner proceeds. Closes the
        # TOCTOU window where N harnesses all pass the pure-read gate above
        # in the same instant and each spawns a rebuild.
        if not _try_claim_refresh_lock():
            return None
        _write_refresh_marker({"last_kick_at": now_iso(), "reason": reason, "status": "in_progress"})

        def _run():
            try:
                from .me_builder import build_me_via_lens_pipeline
                _path, summary = build_me_via_lens_pipeline()
                # Honest outcome: build_me returns ok:False on a chairman
                # timeout/quota abort and preserved_existing on a clobber-guard
                # preserve — NEITHER advanced the lens. Recording "done" for those
                # was the false-green that hid an 18-day freeze; map to failed/no-op
                # and carry the cause so the status surface can point at WHY.
                mstatus = _refresh_marker_status(summary)
                marker = {
                    "last_kick_at": now_iso(), "reason": reason, "status": mstatus,
                    "finished_at": now_iso(), "summary": summary,
                }
                if mstatus == "failed" and isinstance(summary, dict):
                    marker["error"] = str(
                        summary.get("reason") or summary.get("aborted") or "build did not land"
                    )[:200]
                _write_refresh_marker(marker)
            except Exception as exc:
                _write_refresh_marker({
                    "last_kick_at": now_iso(), "reason": reason, "status": "failed",
                    "finished_at": now_iso(), "error": str(exc)[:200],
                })
            finally:
                _release_refresh_lock()

        # Inherit the MCP active-sampling ContextVar (set in
        # handle_call_tool) so the build's Claude stages route through
        # `sampling/createMessage` — riding the user's Claude Code session
        # instead of burning `claude -p` quota. A bare Thread does NOT copy
        # ContextVars; copy_context() snapshots `_active` so the worker can
        # sample even after the triggering tool call clears the main context
        # (the snapshot is independent). Same primitive councils use.
        ctx = contextvars.copy_context()
        threading.Thread(
            target=lambda: ctx.run(_run), daemon=True, name="trinity-lens-refresh"
        ).start()
        return {"status": "kicked", "reason": reason}
    except Exception:
        return None


# ─── #242(a): auto-kick the FIRST lens build ────────────────────────────
#
# The activity-gated refresh above only keeps an EXISTING lens current — the
# very first build was a manual `trinity-local lens`. So a fresh user saw the
# #242 anchors ("…the deeper lens is still building") while nothing actually
# built it. This closes that: once the cold-start scan has ingested AND the
# corpus is embedded, the first build kicks in the background at MCP connect
# (an authenticated session — provider CLIs are live), writing live progress
# the launchpad shows + a cancel flag the user can trip. Same lock as refresh
# (one build at a time); same "never a surprise 3am cron" discipline.

def _no_lens_yet() -> bool:
    try:
        from .me_builder import me_path
        return not me_path().exists()
    except Exception:
        return False


def _corpus_has_embeddings(min_nodes: int = 20) -> bool:
    """Cheap probe: are there enough embedded prompt nodes for a REAL-embedding
    first build? Gates the auto-kick so we don't auto-produce a degraded TF-IDF
    lens before the embedder + backfill are ready (the deeper-memory card
    prompts the download in that window)."""
    try:
        from .embeddings import is_finite_embedding
        from .memory.store import iter_prompt_nodes
        n = 0
        for node in iter_prompt_nodes(limit=None):
            if is_finite_embedding(getattr(node, "embedding", None)):
                n += 1
                if n >= min_nodes:
                    return True
        return False
    except Exception:
        return False


def should_build_first_lens() -> tuple[bool, str]:
    """Gate for the auto first build: autoscan on, NO lens yet, the cold-start
    scan has completed, and the corpus is embedded. Pure read."""
    if _autoscan_disabled():
        return False, "autoscan disabled"
    if not _no_lens_yet():
        return False, "lens exists (refresh path, not first build)"
    st = read_state()
    if not st or st.get("status") != "complete":
        return False, "cold-start scan not complete"
    if not _corpus_has_embeddings():
        return False, "corpus not embedded yet (deeper-memory download pending)"
    return True, "first lens build (cold-start done, embeddings ready)"


def _lens_build_user_error(exc: BaseException) -> str:
    """A user-safe one-line failure message for the launchpad lens-build card.

    The card renders this string verbatim after "Lens build hit a snag —", so it
    must NOT carry a raw `str(exc)`: a KeyError stringifies to a bare quoted key
    ("'centroid'"), a JSONDecodeError to "Expecting value: line 1 column 1 …", an
    OSError to "[Errno 2] No such file or directory: '/Users/…/.trinity/…'" — all
    of which read as a broken stack trace (and leak a filesystem path) to a user
    on a fresh corpus, the exact state the auto-kicked first build fires in. Mirror
    the iter-140 lens-health fix: surface the exception TYPE (still useful in a bug
    report) inside plain, actionable English; never the exception payload."""
    return (
        f"Couldn't finish this run ({type(exc).__name__}). "
        "Retry below, or run trinity-local lens --force in your terminal."
    )


def maybe_kick_first_lens_build() -> dict | None:
    """Background-kick the first lens build when the gate is open and no build
    is in flight. Best-effort; returns the kick record or None when skipped.

    The build writes its own per-stage progress + a 'done' on success
    (`lens_progress`); this wrapper only records the canceled/failed terminal
    states and releases the shared build lock."""
    from .lens_addon import lens_enabled
    if not lens_enabled():
        return None  # lens is an opt-in add-on; core fusion does no lens work
    if _autoscan_disabled():
        return None
    try:
        ok, reason = should_build_first_lens()
        if not ok or _recently_kicked():
            return None
        if not _try_claim_refresh_lock():
            return None
        _write_refresh_marker({"last_kick_at": now_iso(), "reason": reason, "status": "in_progress"})

        def _run():
            from .lens_progress import LensBuildCanceled, write_progress
            try:
                from .me_builder import build_me_via_lens_pipeline
                build_me_via_lens_pipeline()  # per-stage progress + "done" on success
                _write_refresh_marker({
                    "last_kick_at": now_iso(), "reason": reason, "status": "done",
                    "finished_at": now_iso(),
                })
            except LensBuildCanceled:
                write_progress("canceled", status="canceled")
                _write_refresh_marker({
                    "last_kick_at": now_iso(), "reason": reason, "status": "canceled",
                    "finished_at": now_iso(),
                })
            except Exception as exc:
                # The launchpad's "Lens build hit a snag" card renders this `error`
                # verbatim (launchpad_template.py — `{{ lensBuild.error }}`). A raw
                # str(exc) leaks Python internals / a filesystem path into the UI
                # ("— 'centroid'", "— [Errno 2] No such file …/.trinity/…") — the
                # same raw-{exc!r}-into-a-user-surface defect as the lens-health
                # noise self-test (iter 140). Surface the exception TYPE (still
                # bug-report-useful) in a plain-English, actionable clause; keep the
                # full str(exc) only in the INTERNAL refresh marker (debug, not UI).
                write_progress(
                    "failed", status="failed", error=_lens_build_user_error(exc)
                )
                _write_refresh_marker({
                    "last_kick_at": now_iso(), "reason": reason, "status": "failed",
                    "finished_at": now_iso(), "error": str(exc)[:200],
                })
            finally:
                _release_refresh_lock()

        # Inherit the MCP active-sampling ContextVar so the first build's
        # Claude stages ride the user's session via sampling (see the
        # refresh kick above for the full rationale).
        ctx = contextvars.copy_context()
        threading.Thread(
            target=lambda: ctx.run(_run), daemon=True, name="trinity-first-lens-build"
        ).start()
        return {"status": "kicked", "reason": reason}
    except Exception:
        return None


def cold_open_tension() -> str | None:
    """The cold-start *aha* (#212 / Q2): ONE surprising, true thing about how
    the user decides — surfaced the instant their lens has any signal, before
    they've learned a single verb.

    It names the single highest-support decision *axis* from the lens (the
    tension they keep navigating), NOT a fixed winner — the lens models a
    both-defensible tension, so claiming "you always pick X" would overclaim.
    Returns None until a lens/registry tension exists (cold install). Pure
    read, fully best-effort: never raises into a startup/status path."""
    try:
        from .me.lens_registry import LOW_CONFIDENCE_BELOW, active_tensions_sorted

        tensions = active_tensions_sorted()
        if tensions:
            t = tensions[0]
            n = t.support_count
            # #254 signature+proof: when the cached taste adjectives exist, lead
            # with the three-word signature, then the dominant tension as proof.
            # The adjectives are read from a cheap cache (computed at lens-build);
            # no embedding on this paint path.
            try:
                from .me.correction_lens import load_taste_signature

                sig = load_taste_signature()
            except Exception:
                sig = None
            if sig and sig.get("adjectives"):
                words = ", ".join(sig["adjectives"][:3])
                # The tension is a BOTH-DEFENSIBLE axis: pole_a/pole_b are the
                # canonical (first-registered) phrasing, NOT a winner, and
                # support_count(n) backs the AXIS (both poles), not a tally of
                # times the user picked pole_a. Saying "you reached for pole_a
                # OVER pole_b {n} times" fabricates a directional pick-count the
                # data does not encode — the exact "you always pick X" overclaim
                # this function's docstring forbids, and it contradicts every
                # other rendering of the same tension (lens.md / taste card show
                # it symmetric: "pole_a ↔ pole_b"). The directional steer the
                # "in your voice" payoff rests on lives in the ADJECTIVES (the
                # correction lens — the pole the user steers toward), which are
                # legitimately directional. So: name the axis honestly (matching
                # the ↔ framing) and let the adjectives carry the direction.
                if n >= LOW_CONFIDENCE_BELOW:
                    proof = (
                        f"Across {n} decisions you keep navigating “{t.pole_a}” "
                        f"vs “{t.pole_b}” — the chairman reads that tension on "
                        f"every council, so answers come back in your voice."
                    )
                else:
                    proof = (
                        f"Your lens already surfaces the “{t.pole_a}” vs "
                        f"“{t.pole_b}” tension — the chairman reads it on every "
                        f"council."
                    )
                return f"Your taste in three words: {words}. {proof}"
            # No cached signature yet — the original tension-only aha.
            prov = f" — seen across {n} of your decisions" if n >= LOW_CONFIDENCE_BELOW else ""
            return (
                f"One axis your lens already surfaces: “{t.pole_a}” vs "
                f"“{t.pole_b}” — the tension you keep navigating{prov}."
            )
    except Exception:
        pass
    # Fallback for a pre-registry lens: the first accepted pair.
    try:
        from .me.pair_mining import load_lenses

        pairs = load_lenses()
        if pairs:
            p = pairs[0]
            return (
                f"One axis your lens already surfaces: “{p.pole_a}” vs "
                f"“{p.pole_b}” — the tension you keep navigating."
            )
    except Exception:
        pass
    # #242 first-run fallback: no lens yet (embeddings still backfilling), but
    # the cold-start scan cached pure-text anchors — name the user's recurring
    # topics so the FIRST paint after a fresh install has a real, true insight
    # instead of a blank, while the deeper lens builds in the background.
    try:
        state = read_state()
        if state:
            anchors = [a for a in (state.get("early_anchors") or []) if a]
            if anchors:
                shown = ", ".join(anchors[:4])
                return (
                    f"Your recurring topics so far: {shown} — the deeper lens "
                    f"is still building in the background."
                )
    except Exception:
        pass
    return None
