"""Embedder-download wizard — the in-session "download the full memory engine" offer.

A plugin-only install (no `curl|bash`) bootstraps just the 3 base deps
(numpy/mcp/Pillow) into ``~/.trinity/venv`` (see ``plugins/.../bin/trinity-mcp``),
so Trinity runs on the lexical SHA-1 TF-IDF fallback. The REAL semantic memory
needs the embedding engine: the heavy runtime (``mlx-embeddings`` on Apple
Silicon, else ``sentence-transformers`` + ``torch``) plus the ~600 MB
``modernbert-embed-base`` weights. That's multi-hundred-MB and multi-minute, so
it must be OPT-IN — never part of the silent first-boot bootstrap.

This fires ONCE per server process, on the first MCP tool call (where a session
is registered, so MCP elicitation can ask), via ``_maybe_fire_lens_kicks``. It:

  1. gates (cheap, synchronous): only offers when the embedder isn't already
     live AND the user hasn't declined recently / opted out permanently;
  2. spawns a daemon thread (the elicit BLOCKS up to 5 min waiting for the human,
     so it must never sit on the tool-dispatch path) that:
     a. elicits a yes/no via ``mcp_features.elicit`` (no-op when the client lacks
        elicitation — most don't yet — so it silently re-checks next session);
     b. on accept, pip-installs the engine into ``sys.executable``'s environment
        and downloads the model (subprocess, ``HF_HUB_OFFLINE=0``), logging via
        ``mcp_log``;
     c. persists the decision so we don't nag.

Never blocks the tool call; never crashes the server. Trinity keeps working on
the lexical fallback throughout. The real embedder activates on the NEXT server
start — a running process already resolved ``embeddings._mlx_backend`` to the
fallback at import and can't hot-swap it.
"""
from __future__ import annotations

import contextvars
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .state_paths import telemetry_settings_dir
from .utils import now_iso

# The engine deps, mirrored EXACTLY from pyproject.toml's `[mlx]` extras (incl.
# the environment markers, so pip installs only what matches the platform —
# mlx/mlx-embeddings on arm64 macOS, the torch path everywhere). Duplicated here
# (not read from pyproject) because the VENDORED plugin engine ships without
# pyproject.toml; `tests/test_embedder_wizard.py` asserts this stays in sync with
# the real extras so the two can't drift.
ENGINE_DEPS: tuple[str, ...] = (
    "mlx>=0.20; platform_system == 'Darwin' and platform_machine == 'arm64'",
    "mlx-embeddings>=0.1; platform_system == 'Darwin' and platform_machine == 'arm64'",
    "sentence-transformers>=2.2",
    "einops",
    "torch>=2.0",
)

# Re-offer cooldown after a "Not now": long enough not to nag, short enough that
# a user who changes their mind doesn't wait forever.
_REOFFER_AFTER_DAYS = 14

# pip-install torch can be slow on a cold cache; the model download is ~600 MB.
_INSTALL_TIMEOUT_S = 30 * 60
_DOWNLOAD_TIMEOUT_S = 30 * 60


def _state_path() -> Path:
    return telemetry_settings_dir() / "embedder_wizard.json"


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _autoscan_disabled() -> bool:
    # Honour the same kill-switch as the lens kicks: the MCP-stdio safe recipe
    # and the whole test/CI suite set TRINITY_AUTOSCAN_DISABLED=1, so the wizard
    # (heavy background work) stays silent there.
    return os.environ.get("TRINITY_AUTOSCAN_DISABLED") == "1"


def _days_since(iso: str) -> float:
    """Whole days between `iso` and now, computed from now_iso() strings without
    importing datetime arithmetic helpers Trinity bans (Date.now-style). Returns
    a large number on any parse failure so a malformed timestamp re-offers rather
    than suppressing forever."""
    from datetime import datetime, timezone

    try:
        then = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        now = datetime.fromisoformat(now_iso()).replace(tzinfo=timezone.utc)
        return (now - then).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return float("inf")


def should_offer_embedder() -> tuple[bool, str]:
    """Cheap, synchronous gate. ``(False, reason)`` to skip; ``(True, reason)``
    to offer. No model load, no network — the heaviest probe is
    ``mlx_actually_loaded`` which returns False instantly when the backend module
    never imported (the exact plugin-only case)."""
    if _autoscan_disabled():
        return False, "autoscan disabled"
    try:
        from .embeddings import mlx_actually_loaded

        if mlx_actually_loaded():
            return False, "embedder already live"
    except Exception:
        # If we can't even probe, don't risk offering a download we can't verify.
        return False, "probe failed"

    state = _load_state()
    if state.get("opted_out"):
        return False, "user opted out"
    status = state.get("install_status")
    if status in {"in_progress", "done"}:
        return False, f"install {status}"
    declined_at = state.get("last_declined_at")
    if declined_at and _days_since(declined_at) < _REOFFER_AFTER_DAYS:
        return False, "within decline cooldown"
    return True, "offer"


def record_decision(choice: str) -> None:
    """Persist the user's elicitation answer."""
    state = _load_state()
    if choice == "decline":
        state["last_declined_at"] = now_iso()
    elif choice == "opt_out":
        state["opted_out"] = True
        state["last_declined_at"] = now_iso()
    elif choice == "accept":
        state["accepted_at"] = now_iso()
        state["install_status"] = "in_progress"
    _save_state(state)


def _mark_install(status: str, **extra) -> None:
    state = _load_state()
    state["install_status"] = status
    state[f"install_{status}_at"] = now_iso()
    state.update(extra)
    _save_state(state)


def _elicit_choice():
    """Ask the user. Returns 'accept' / 'decline' / 'opt_out', or None when the
    client doesn't support elicitation (so we re-check next session)."""
    from .mcp_features import elicit

    content = elicit(
        "Trinity is running on its lightweight lexical memory. Download the full "
        "embedding engine (~600 MB model + runtime, one-time) for sharper "
        "cross-provider memory and routing? It runs in the background — Trinity "
        "keeps working meanwhile, and the engine activates next time you reconnect.",
        {
            "type": "object",
            "title": "Download the full memory engine?",
            "properties": {
                "choice": {
                    "type": "string",
                    "title": "Choice",
                    "enum": ["download", "not_now", "dont_ask_again"],
                    "enumNames": [
                        "Download now (~600 MB, in the background)",
                        "Not now",
                        "Don't ask again",
                    ],
                }
            },
            "required": ["choice"],
        },
    )
    if not content:
        return None
    choice = content.get("choice")
    if choice == "download":
        return "accept"
    if choice == "dont_ask_again":
        return "opt_out"
    return "decline"


def _subprocess_env(*, allow_hub: bool) -> dict:
    """Inherit the server's env (preserves PYTHONPATH for the vendored-engine
    case) and, when downloading, flip OFF the HF offline pin main() set — the one
    moment Trinity is allowed to contact the Hub."""
    env = dict(os.environ)
    if allow_hub:
        env["HF_HUB_OFFLINE"] = "0"
        env["TRANSFORMERS_OFFLINE"] = "0"
    return env


def _run_install_and_download() -> None:
    """Worker body: pip-install the engine deps into the running interpreter's
    environment, then download the model via the existing `download-embedder`
    verb (subprocess so it picks up the just-installed deps and runs with the Hub
    reachable). Every failure is logged + recorded, never raised."""
    from .mcp_features import mcp_log

    def _log(level, msg):
        try:
            mcp_log(level, msg)
        except Exception:
            pass

    _log("info", "Trinity: installing the embedding engine (this runs once, in the background)…")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *ENGINE_DEPS],
            env=_subprocess_env(allow_hub=False),
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _mark_install("failed", error=f"deps install: {str(exc)[:200]}")
        _log("warning", "Trinity: embedding-engine install timed out; staying on lexical memory.")
        return
    if r.returncode != 0:
        _mark_install("failed", error=f"pip exit {r.returncode}: {(r.stderr or '')[:200]}")
        _log("warning", "Trinity: embedding-engine install failed; staying on lexical memory.")
        return

    _log("info", "Trinity: downloading the memory model (~600 MB)…")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "trinity_local", "download-embedder", "--json"],
            env=_subprocess_env(allow_hub=True),
            capture_output=True,
            text=True,
            timeout=_DOWNLOAD_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _mark_install("failed", error=f"model download: {str(exc)[:200]}")
        _log("warning", "Trinity: model download timed out; staying on lexical memory.")
        return

    ok = False
    try:
        ok = bool(json.loads(r.stdout or "{}").get("ok"))
    except json.JSONDecodeError:
        ok = r.returncode == 0
    if ok:
        _mark_install("done")
        _log(
            "info",
            "Trinity: embedding engine ready. Reconnect (or restart your harness) "
            "to switch from lexical to full semantic memory.",
        )
    else:
        _mark_install("failed", error=(r.stderr or r.stdout or "unknown")[:200])
        _log("warning", "Trinity: model download failed; staying on lexical memory.")


def _wizard_thread_body() -> None:
    """elicit → (on accept) install. Runs in a daemon thread so the elicit's
    up-to-5-min wait never blocks the triggering tool call."""
    try:
        choice = _elicit_choice()
    except Exception:
        return  # elicitation unsupported / errored — re-check next session
    if choice is None:
        return
    record_decision(choice)
    if choice != "accept":
        return
    _run_install_and_download()


def maybe_offer_embedder_download() -> dict | None:
    """Fire-and-forget entry point called from `_maybe_fire_lens_kicks`. Does the
    cheap gate synchronously, then hands the blocking elicit + heavy install to a
    daemon thread that inherits the MCP session via copy_context() (same
    primitive the lens kicks use to reach `sampling`/`elicit`). Returns a small
    record for logging/tests, or None when skipped."""
    try:
        from .lens_addon import lens_enabled
        if not lens_enabled():
            return None  # the embedder is part of the opt-in lens add-on, not core
        ok, reason = should_offer_embedder()
        if not ok:
            return None
        # Snapshot the active MCP session + request context so the worker thread
        # can send the elicitation request over the live connection.
        ctx = contextvars.copy_context()
        threading.Thread(
            target=lambda: ctx.run(_wizard_thread_body),
            daemon=True,
            name="trinity-embedder-wizard",
        ).start()
        return {"status": "offered", "reason": reason}
    except Exception:
        return None
