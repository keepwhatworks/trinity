from __future__ import annotations

import json
import re
from pathlib import Path

from .action_schema import PendingAction
from .dispatch_registry import command_for_dispatch, make_dispatch_action
from .state_paths import actions_dir
from .task_schema import TaskRecord
from .utils import now_iso, stable_id


# `status` is one of the first 4-5 fields in the JSON, but
# task_cluster_id sometimes holds a long absolute file path (~250
# bytes alone), so a 256-byte head window misses ~0.1% of files.
# A 2KB window covers every action file observed on real installs
# (largest seen ~2.7KB) without paying for a full json.loads on
# each of the (potentially tens of thousands of) entries.
_STATUS_RE = re.compile(rb'"status"\s*:\s*"([a-z_]+)"')
_STATUS_READ_WINDOW = 2048


def create_recommendation_action(
    *,
    task: TaskRecord,
    bundle_id: str | None = None,
    command_hint: str | None = None,
) -> PendingAction:
    recommendation = task.recommendation
    provider = recommendation.recommended_provider if recommendation else None
    mode = recommendation.recommended_mode if recommendation else None
    reason = recommendation.reason if recommendation and recommendation.reason else "A better tool may be available."
    title = "Trinity suggestion"
    message = reason
    if provider:
        message = f"{provider.capitalize()} might be better. {reason}"
    if mode == "council":
        message = f"{message} Start council to compare answers."
    action_id = stable_id(
        "action",
        task.task_id,
        task.status,
        provider or "",
        mode or "",
        task.updated_at or now_iso(),
    )
    dispatch = None
    if command_hint:
        dispatch = make_dispatch_action(
            "run_command",
            args={"command": command_hint},
            task_id=task.task_id,
            metadata={"kind": "recommendation"},
        )
    return PendingAction(
        action_id=action_id,
        task_id=task.task_id,
        task_cluster_id=task.task_cluster_id,
        kind="recommendation",
        status="pending",
        title=title,
        message=message,
        bundle_id=bundle_id,
        created_at=now_iso(),
        updated_at=now_iso(),
        recommended_provider=provider,
        recommended_mode=mode,
        command_hint=command_hint or (command_for_dispatch(dispatch) if dispatch else None),
        dispatch_action=dispatch.to_dict() if dispatch else {},
        metadata={"source_provider": task.source_provider, "confidence": recommendation.confidence if recommendation else None},
    )


def create_council_start_action(
    *,
    task: TaskRecord,
    bundle_id: str,
    members: list[str],
    primary_provider: str,
    cwd: str = ".",
) -> PendingAction:
    action_id = stable_id(
        "action",
        task.task_id,
        bundle_id,
        "start_council",
    )
    dispatch = make_dispatch_action(
        "start_council",
        args={
            "bundle_id": bundle_id,
            "members": members,
            "primary_provider": primary_provider,
            "cwd": cwd,
        },
        task_id=task.task_id,
        metadata={"kind": "start_council"},
    )
    command_hint = command_for_dispatch(dispatch)
    provider_copy = ", ".join(members)
    return PendingAction(
        action_id=action_id,
        task_id=task.task_id,
        task_cluster_id=task.task_cluster_id,
        kind="start_council",
        status="pending",
        title="Start Trinity council",
        message=f"Compare {provider_copy} and synthesize with {primary_provider} for: {task.title}",
        bundle_id=bundle_id,
        created_at=now_iso(),
        updated_at=now_iso(),
        recommended_provider=primary_provider,
        recommended_mode="council",
        command_hint=command_hint,
        dispatch_action=dispatch.to_dict(),
        metadata={"members": members, "primary_provider": primary_provider, "cwd": cwd},
    )


def create_review_ready_action(
    *,
    task: TaskRecord,
    command_hint: str | None = None,
) -> PendingAction:
    action_id = stable_id(
        "action",
        task.task_id,
        task.council_run_id or "",
        "review",
    )
    dispatch = None
    if task.review_page_path or command_hint:
        dispatch = make_dispatch_action(
            "open_review",
            args={"task_id": task.task_id, "path": task.review_page_path} if task.review_page_path else {"task_id": task.task_id},
            task_id=task.task_id,
            metadata={"kind": "review_ready"},
        )
    return PendingAction(
        action_id=action_id,
        task_id=task.task_id,
        task_cluster_id=task.task_cluster_id,
        kind="review_ready",
        status="pending",
        title="Trinity council ready",
        message=f"Council finished for: {task.title}",
        created_at=now_iso(),
        updated_at=now_iso(),
        review_page_path=task.review_page_path,
        command_hint=command_hint or (command_for_dispatch(dispatch) if dispatch else None),
        dispatch_action=dispatch.to_dict() if dispatch else {},
        metadata={"winner_provider": task.winner_provider, "needs_followup": task.needs_followup},
    )


def save_action(action: PendingAction) -> Path:
    from .utils import atomic_write_text
    action.updated_at = now_iso()
    path = actions_dir() / f"{action.action_id}.json"
    atomic_write_text(path, json.dumps(action.to_dict(), indent=2))
    return path


def load_action(action_id_or_path: str) -> PendingAction:
    path = Path(action_id_or_path)
    if not path.exists():
        path = actions_dir() / f"{action_id_or_path}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PendingAction(**raw)


def list_actions(*, status: str | None = None) -> list[PendingAction]:
    items: list[PendingAction] = []
    for path in sorted(actions_dir().glob("*.json")):
        action = load_action(str(path))
        if status and action.status != status:
            continue
        items.append(action)
    return items


def count_actions_by_status() -> dict[str, int]:
    """Count actions per status WITHOUT loading the full PendingAction.

    Status callers (`trinity-local status`, launchpad) only need `len()`
    of pending + completed. The previous path went through
    list_actions(status=X) twice, calling load_action() once per file —
    10× slower on real installs than necessary. Skim the first 256 bytes
    instead, regex-extract `status`, count. Files with malformed prefix
    silently fall through (counted under no status — same shape as the
    full-load path's exception handling).
    """
    counts: dict[str, int] = {}
    try:
        for path in actions_dir().glob("*.json"):
            try:
                with path.open("rb") as fh:
                    head = fh.read(_STATUS_READ_WINDOW)
            except OSError:
                continue
            m = _STATUS_RE.search(head)
            if not m:
                continue
            key = m.group(1).decode("ascii", errors="replace")
            counts[key] = counts.get(key, 0) + 1
    except OSError:
        pass
    return counts


def find_action(*, task_id: str, kind: str, status: str | None = "pending") -> PendingAction | None:
    for action in list_actions(status=status):
        if action.task_id == task_id and action.kind == kind:
            return action
    return None


def mark_action_status(action: PendingAction, status: str) -> PendingAction:
    action.status = status
    action.updated_at = now_iso()
    return action
