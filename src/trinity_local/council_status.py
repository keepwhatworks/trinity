from __future__ import annotations

import json
import os
import time
from threading import Lock
from typing import Any

from .markdown_utils import render_markdown
from .state_paths import council_status_dir, council_status_js_path, council_status_json_path
from .utils import now_iso, safe_error_message


_STATUS_LOCK = Lock()

# Status files outlive their usefulness as soon as the council completes
# and the canonical outcome lands in council_outcomes/. But they stayed
# on disk forever — observed on a real install: 250 files / 5.8MB after
# ~30 days. Linear growth with council launches. Prune on launch (free
# rate-limiting via launch cadence) anything older than the polling
# window. 14 days is generous: in-flight councils complete in minutes,
# users re-polling a recent council have well under 14 days of recency.
_STATUS_FILE_MAX_AGE_S = 14 * 24 * 3600


def _prune_old_status_files() -> None:
    cutoff = time.time() - _STATUS_FILE_MAX_AGE_S
    try:
        for path in council_status_dir().iterdir():
            if not path.is_file():
                continue
            name = path.name
            # Only target our own naming convention — leave anything
            # else (manual notes, sibling state) alone.
            if not name.startswith("council_status_"):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


def _runner_is_alive(payload: dict[str, Any]) -> bool:
    pgid = payload.get("runner_pgid")
    pid = payload.get("runner_pid")
    try:
        if isinstance(pgid, int) and pgid > 0:
            os.killpg(pgid, 0)
            return True
    except OSError:
        pass
    try:
        if isinstance(pid, int) and pid > 0:
            os.kill(pid, 0)
            return True
    except OSError:
        pass
    return False


# A "running" council whose status hasn't been written in this long is dead. A live
# runner rewrites its status every ~1.5s, so long-staleness means the council ended —
# and pid-liveness ALONE can't catch it, because the dead runner's pid can be REUSED by
# an unrelated live process (observed 2026-06-08: an interrupted council 17h old still
# read "running" because its pid had been reassigned → the launchpad's Launch button
# stayed disabled forever). So a council is "alive" only if its pid is alive AND its
# status is fresh.
_STALE_RUNNING_SECONDS = 30 * 60


def _running_status_is_stale(status_token: str, payload: dict[str, Any]) -> bool:
    from datetime import datetime, timezone

    epoch: float | None = None
    ts = payload.get("updated_at")
    if isinstance(ts, str) and ts:
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            epoch = when.timestamp()
        except ValueError:
            epoch = None
    if epoch is None:
        try:
            epoch = council_status_json_path(status_token).stat().st_mtime
        except OSError:
            return False  # can't tell when it last updated → fail open (keep running)
    return (time.time() - epoch) > _STALE_RUNNING_SECONDS


def _coerce_stale_running_status(status_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "running":
        return payload
    # Alive ONLY if the runner process is alive AND the status is fresh. Staleness
    # catches the pid-reuse case the liveness check alone misses.
    if _runner_is_alive(payload) and not _running_status_is_stale(status_token, payload):
        return payload

    members = dict(payload.get("members") or {})
    for provider, member_state in list(members.items()):
        if (member_state or {}).get("status") == "running":
            members[provider] = {
                **member_state,
                "status": "failed",
                "completed_at": now_iso(),
                "reasoning_summary": "Council runner exited before completion.",
            }
    payload = {
        **payload,
        "status": "failed",
        "members": members,
        "active_provider": None,
        "active_providers": [],
        "error": payload.get("error") or "Council runner exited before completion.",
        "completed_at": now_iso(),
    }
    return _write_status(status_token, payload)


def _extract_reasoning_summary(text: str, max_length: int = 120) -> str:
    if not text:
        return ""
    text = text.strip()
    for delimiter in (".", "\n", "?"):
        idx = text.find(delimiter)
        if idx > 0:
            summary = text[:idx].strip()
            if summary:
                return summary[:max_length]
    return text[:max_length]


def load_council_status(status_token: str) -> dict[str, Any] | None:
    path = council_status_json_path(status_token)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # guard_shape_not_just_parse: a valid-JSON-but-non-dict status file (partial
    # write, hand-edit) would crash _coerce_stale_running_status's `.get(...)` —
    # on the get_council_status MCP tool + the live-council poll path. Honor the
    # `-> dict | None` contract.
    if not isinstance(payload, dict):
        return None
    return _coerce_stale_running_status(status_token, payload)


def _write_status(status_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload["status_token"] = status_token
    payload["updated_at"] = now_iso()
    council_status_json_path(status_token).write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    js = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(status_token)}] = {json.dumps(payload, separators=(',', ':'), ensure_ascii=True)};\n"
    )
    council_status_js_path(status_token).write_text(js, encoding="utf-8")
    return payload


def write_council_status(
    status_token: str,
    *,
    status: str,
    task_text: str | None = None,
    bundle_id: str | None = None,
    council_id: str | None = None,
    review_path: str | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    members: dict[str, dict[str, Any]] | None = None,
    synthesis: dict[str, Any] | None = None,
    active_provider: str | None = None,
    active_providers: list[str] | None = None,
) -> dict[str, Any]:
    with _STATUS_LOCK:
        payload = load_council_status(status_token) or {}
        payload["status"] = status
        if task_text is not None:
            payload["task_text"] = task_text
        if bundle_id is not None:
            payload["bundle_id"] = bundle_id
        if council_id is not None:
            payload["council_id"] = council_id
        if review_path is not None:
            payload["review_path"] = review_path
        if error is not None:
            payload["error"] = error
        elif "error" in payload and status in {"running", "completed"}:
            payload["error"] = None
        if metadata:
            existing = dict(payload.get("metadata") or {})
            existing.update(metadata)
            payload["metadata"] = existing
        if members is not None:
            payload["members"] = members
        if synthesis is not None:
            payload["synthesis"] = synthesis
        if active_provider is not None or "active_provider" in payload:
            payload["active_provider"] = active_provider
        if active_providers is not None:
            payload["active_providers"] = active_providers
        return _write_status(status_token, payload)


def init_council_run_state(
    status_token: str,
    *,
    task_text: str,
    bundle_id: str,
    members: list[str],
    metadata: dict[str, Any] | None = None,
    council_id: str | None = None,
    runner_pid: int | None = None,
    runner_pgid: int | None = None,
    member_models: dict[str, str] | None = None,
) -> dict[str, Any]:
    member_models = member_models or {}
    payload = {
        "status": "running",
        "task_text": task_text,
        "bundle_id": bundle_id,
        "council_id": council_id or bundle_id,
        "started_at": now_iso(),
        "members": {
            provider: {"status": "pending", "model": member_models.get(provider)}
            for provider in members
        },
        "active_provider": None,
        "active_providers": [],
        "synthesis": {"status": "pending"},
        "review_path": None,
        "error": None,
        "runner_pid": runner_pid,
        "runner_pgid": runner_pgid,
        "metadata": metadata or {},
    }
    # Opportunistic GC on every council launch — keeps the status dir
    # from accumulating without bound. No-op when the dir is small.
    _prune_old_status_files()
    with _STATUS_LOCK:
        return _write_status(status_token, payload)


def _existing_member_model(payload: dict[str, Any], provider: str) -> str | None:
    members = payload.get("members") or {}
    existing = members.get(provider) if isinstance(members, dict) else None
    if isinstance(existing, dict):
        return existing.get("model")
    return None


def start_member_progress(status_token: str, provider: str, *, model: str | None = None) -> None:
    with _STATUS_LOCK:
        payload = load_council_status(status_token)
        if not payload:
            return
        members = dict(payload.get("members") or {})
        active = list(payload.get("active_providers") or [])
        if provider not in active:
            active.append(provider)
        members[provider] = {
            "status": "running",
            "started_at": now_iso(),
            "model": model or _existing_member_model(payload, provider),
        }
        payload["members"] = members
        payload["active_provider"] = provider
        payload["active_providers"] = active
        _write_status(status_token, payload)


def update_member_progress(
    status_token: str, provider: str, response_text: str, *, model: str | None = None,
) -> None:
    with _STATUS_LOCK:
        payload = load_council_status(status_token)
        if not payload:
            return
        members = dict(payload.get("members") or {})
        members[provider] = {
            "status": "done",
            "completed_at": now_iso(),
            "model": model or _existing_member_model(payload, provider),
            "reasoning_summary": _extract_reasoning_summary(response_text),
            "response_text": response_text,
            "response_html": render_markdown(response_text),
        }
        active = [item for item in (payload.get("active_providers") or []) if item != provider]
        payload["members"] = members
        payload["active_providers"] = active
        payload["active_provider"] = active[0] if active else None
        _write_status(status_token, payload)


def update_member_failure(
    status_token: str, provider: str, error_text: str, *, model: str | None = None,
) -> None:
    # The member-failure detail is painted VERBATIM as the failed row's
    # reasoning_summary on the live council page / launchpad running card. Several
    # callers pass a raw `str(exc)` (council_runner member/chain exception paths)
    # or `result.stderr`, which can carry an absolute filesystem path / [Errno N]
    # / a Python type name. Sanitize at this SINK — the one choke point every
    # caller flows through — so no leak reaches the painted member row (#43102d25).
    safe_text = safe_error_message(error_text, fallback="Provider failed.")
    with _STATUS_LOCK:
        payload = load_council_status(status_token)
        if not payload:
            return
        members = dict(payload.get("members") or {})
        members[provider] = {
            "status": "failed",
            "completed_at": now_iso(),
            "model": model or _existing_member_model(payload, provider),
            "reasoning_summary": _extract_reasoning_summary(safe_text),
        }
        active = [item for item in (payload.get("active_providers") or []) if item != provider]
        payload["members"] = members
        payload["active_providers"] = active
        payload["active_provider"] = active[0] if active else None
        _write_status(status_token, payload)


def update_synthesis_progress(
    status_token: str,
    status: str,
    *,
    output_text: str | None = None,
    routing_label: dict[str, Any] | None = None,
) -> None:
    with _STATUS_LOCK:
        payload = load_council_status(status_token)
        if not payload:
            return
        synthesis: dict[str, Any] = {
            "status": status,
            "updated_at": now_iso(),
        }
        if output_text:
            cleaned = _strip_routing_fence(output_text)
            synthesis["response_text"] = cleaned
            synthesis["response_html"] = render_markdown(cleaned)
        if routing_label is not None:
            synthesis["routing_label"] = routing_label
        payload["synthesis"] = synthesis
        if status == "running":
            payload["active_provider"] = None
            payload["active_providers"] = []
        _write_status(status_token, payload)


def _strip_routing_fence(text: str) -> str:
    """Hide the trailing ```routing-json fenced block from the rendered
    chairman response — it's structured data displayed elsewhere."""
    import re

    return re.sub(
        r"```routing-json\s*\n.*?\n```\s*$",
        "",
        text,
        flags=re.DOTALL,
    ).rstrip()


def finalize_council_run_state(
    status_token: str,
    *,
    status: str,
    review_path: str | None = None,
    error: str | None = None,
    council_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with _STATUS_LOCK:
        payload = load_council_status(status_token)
        if not payload:
            return None
        payload["status"] = status
        payload["completed_at"] = now_iso()
        if review_path is not None:
            payload["review_path"] = review_path
        if error is not None:
            payload["error"] = error
        if council_id is not None:
            payload["council_id"] = council_id
        if metadata:
            existing = dict(payload.get("metadata") or {})
            existing.update(metadata)
            payload["metadata"] = existing
        return _write_status(status_token, payload)
