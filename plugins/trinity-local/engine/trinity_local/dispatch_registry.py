from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any


DISPATCH_ACTIONS = {
    "run_command",
    "launch_council",
    "stop_council",
    "open_review",
    "start_council",
    "open_path",
    "open_url",
    "run_applescript",
}


@dataclass
class DispatchAction:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"name": self.name}
        if self.args:
            payload["args"] = self.args
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


def make_dispatch_action(
    name: str,
    *,
    args: dict[str, Any] | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> DispatchAction:
    if name not in DISPATCH_ACTIONS:
        raise ValueError(f"Unsupported dispatch action: {name}")
    return DispatchAction(
        name=name,
        args=args or {},
        task_id=task_id,
        metadata=metadata or {},
    )


def command_for_dispatch(action: DispatchAction) -> str | None:
    args = action.args
    if action.name == "run_command":
        return str(args.get("command") or "").strip() or None
    if action.name == "launch_council":
        task = args.get("task")
        if not task:
            return None
        goal = args.get("goal") or "Find the strongest answer."
        cwd = args.get("cwd") or "."
        # Single-provider users (persona P89) get the enabled subset, not
        # the full hardcoded 3-lineup that fails 2/3.
        from .config import default_council_members
        members = args.get("members") or default_council_members()
        primary_provider = args.get("primary_provider")
        member_args = " ".join(shlex.quote(str(member)) for member in members)
        parts = [
            "trinity-local council-launch",
            f"--task {shlex.quote(str(task))}",
            f"--goal {shlex.quote(str(goal))}",
            f"--members {member_args}",
            f"--cwd {shlex.quote(str(cwd))}",
        ]
        # Only pass --primary-provider when explicitly set; otherwise the
        # CLI auto-selects the strongest predicted chairman for the task.
        if primary_provider:
            parts.insert(4, f"--primary-provider {shlex.quote(str(primary_provider))}")
        status_token = args.get("status_token")
        if status_token:
            parts.append(f"--status-token {shlex.quote(str(status_token))}")
        if args.get("open_browser", True):
            parts.append("--open-browser")
        return " ".join(parts)
    if action.name == "stop_council":
        status_token = args.get("status_token")
        if not status_token:
            return None
        return f"trinity-local council-stop --status-token {shlex.quote(str(status_token))}"
    if action.name == "open_review":
        task_id = args.get("task_id") or action.task_id
        if task_id:
            return f"trinity-local open-review --task {shlex.quote(str(task_id))}"
        outcome_id = args.get("outcome_id")
        if outcome_id:
            return f"trinity-local open-review --outcome {shlex.quote(str(outcome_id))}"
        review_path = args.get("path")
        if review_path:
            return f"trinity-local open-review --path {shlex.quote(str(review_path))}"
        return None
    if action.name == "start_council":
        bundle_id = args.get("bundle_id")
        members = args.get("members") or []
        primary_provider = args.get("primary_provider")
        cwd = args.get("cwd") or "."
        if bundle_id and members and primary_provider:
            member_args = " ".join(shlex.quote(str(member)) for member in members)
            return (
                f"trinity-local council-start --bundle {shlex.quote(str(bundle_id))} "
                f"--members {member_args} --primary-provider {shlex.quote(str(primary_provider))} --cwd {shlex.quote(str(cwd))}"
            )
        return None
    if action.name == "open_path":
        path = args.get("path")
        if path:
            return f"open {shlex.quote(str(path))}"
        return None
    if action.name == "open_url":
        url = args.get("url")
        if url:
            return f"open {shlex.quote(str(url))}"
        return None
    if action.name == "run_applescript":
        script = args.get("script")
        if script:
            escaped = str(script).replace("'", "'\"'\"'")
            return f"osascript -e '{escaped}'"
        return None
    return None
