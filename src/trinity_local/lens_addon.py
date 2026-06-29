"""Opt-in gate for the LENS ADD-ON — fusion is the free core; the lens is opt-in.

GTM (2026-06-16): a fresh install is pure cross-provider FUSION — the council
dispatches to the provider CLIs and the chairman synthesizes neutrally. NONE of
the lens machinery (the embedder download, the first lens build, the activity
refresh, the stale embed pass) fires until the user opts into the lens add-on
via `trinity-local lens setup` (or any explicit `lens` / `dream`). That keeps the
core install instant and dependency-light — no hundreds-of-MB embedder, no
background build — and makes the lens the deliberate upgrade that turns "a better
answer" into "YOUR answer".

The gate lives in ONE place so every caller (MCP first-tool-call, council launch)
inherits it: the four `maybe_kick_*` / `maybe_offer_*` entry points return early
when `lens_enabled()` is False.

Back-compat: a user who already has a built lens is treated as enabled, so
existing installs keep auto-refreshing without re-opting-in.
"""
from __future__ import annotations

import os
from pathlib import Path


def lens_enabled_marker_path() -> Path:
    """`~/.trinity/settings/lens_enabled` — the opt-in marker."""
    from .state_paths import state_dir

    return state_dir() / "settings" / "lens_enabled"


def _lens_already_built() -> bool:
    """Back-compat: a non-trivial lens.md means the user already has the add-on
    (an existing install) — keep auto-refresh working without re-opt-in."""
    try:
        from .state_paths import lens_path

        p = lens_path()
        return p.exists() and len(p.read_text(encoding="utf-8").strip()) > 50
    except Exception:
        return False


def lens_enabled() -> bool:
    """True once the user has opted into the lens add-on (the explicit marker)
    OR already has a built lens (existing install). `TRINITY_LENS_ENABLED=1`
    forces it on (tests / power users); `=0` forces it off."""
    forced = os.environ.get("TRINITY_LENS_ENABLED")
    if forced == "1":
        return True
    if forced == "0":
        return False
    try:
        if lens_enabled_marker_path().exists():
            return True
    except Exception:
        pass
    return _lens_already_built()


def enable_lens() -> bool:
    """Persist the opt-in (idempotent). Called by `lens setup` and by any
    explicit `lens` / `dream` run — explicitly building IS opting in. Returns
    True if the marker was newly written, False if it already existed / failed."""
    try:
        p = lens_enabled_marker_path()
        if p.exists():
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        from .utils import now_iso

        p.write_text(now_iso() + "\n", encoding="utf-8")
        return True
    except Exception:
        return False
