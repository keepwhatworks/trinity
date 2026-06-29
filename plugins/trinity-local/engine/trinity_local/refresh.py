from __future__ import annotations

from pathlib import Path


def refresh_launchpad(*, title: str = "Trinity · Ask all three") -> Path:
    from .launchpad_page import write_portal_html

    _backfill_thread_manifests()
    _reap_zombie_tasks()
    # write_portal_html publishes the vendored JS into
    # ~/.trinity/portal_pages/vendor/ as part of writing the page, so the
    # launchpad / memory viewer / council review pages have offline JS
    # available via ./vendor/* — same dir as the HTML they reference. (It
    # used to be published here separately, which left the public
    # write_portal_html able to emit a page that 404s its own scripts.)
    # Idempotent. Privacy: closes persona audit P06's "13 CDN JS contradicts
    # never-leaves-machine" objection.
    return write_portal_html(title=title)


def _reap_zombie_tasks(stale_after_minutes: int = 60) -> int:
    """Mark long-running tasks that never received a bundle_id as failed.

    Class of bug: user clicks "Launch Council" on the launchpad → task record
    is created in `running` state → the macOS Shortcut that should dispatch
    the actual council subprocess fails silently (Shortcut not installed,
    permission denied, etc.) → task stays `running` forever. Found 6 such
    tasks aged 8–10 days during the UI smoke loop (`a council is stuck?`).

    Reap criteria: status in {running/pending/dispatched/in_progress},
    no bundle_id assigned (never reached the council runner), older than
    `stale_after_minutes`. Anything WITH a bundle_id is left alone — those
    might be legit in-flight work we shouldn't abort blindly.
    """
    import json
    import time
    from datetime import datetime, timezone

    # Use the canonical helper so the legacy `tasks/` → `todos/` rename
    # is handled transparently. Local var still named `tasks_dir` so the
    # rest of this function reads identically.
    from .state_paths import tasks_dir as _todos_dir
    tasks_dir = _todos_dir()
    if not tasks_dir.is_dir():
        return 0
    now = time.time()
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    swept = 0
    for path in tasks_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") not in ("running", "pending", "dispatched", "in_progress"):
            continue
        if data.get("bundle_id") or data.get("status_token"):
            continue
        try:
            age_min = (now - path.stat().st_mtime) / 60
        except OSError:
            continue
        if age_min < stale_after_minutes:
            continue
        data["status"] = "failed"
        data["updated_at"] = now_iso
        data.setdefault("failure", {})
        data["failure"]["reason"] = "never_dispatched"
        data["failure"]["detail"] = (
            "Task was marked running but never received a bundle_id — likely the "
            "macOS Shortcut dispatch failed silently. Auto-reaped during portal-html refresh."
        )
        data["failure"]["reaped_at"] = now_iso
        try:
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            swept += 1
        except OSError:
            continue
    return swept


def _backfill_thread_manifests() -> int:
    """Safety net for the recurring stale-MCP bug: a long-running MCP server
    can save council outcomes via old code that didn't yet call
    `update_thread_manifest`. Result: `~/.trinity/council_outcomes/*.json`
    exists, but `_thread_<bundle>.js` does not — and the recent-council card
    on the launchpad 404s when clicked.

    Every `portal-html` regen scans for outcomes whose thread manifest is
    missing and writes one. Cheap (one fs stat per outcome); idempotent."""
    from .council_runtime import update_thread_manifest, load_council_outcome
    from .state_paths import council_outcomes_dir

    outcomes_dir = council_outcomes_dir()
    written = 0
    for outcome_path in outcomes_dir.glob("council_*.json"):
        try:
            outcome = load_council_outcome(outcome_path.stem)
        except Exception:
            continue
        chain_root = (outcome.metadata or {}).get("chain_root_id") or outcome.bundle_id
        if not chain_root:
            continue
        if (outcomes_dir / f"_thread_{chain_root}.js").exists():
            continue
        try:
            update_thread_manifest(outcome)
            written += 1
        except Exception:
            continue
    return written
