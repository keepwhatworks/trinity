from __future__ import annotations

import json
from pathlib import Path

from .council_schema import CouncilOutcome, PromptBundle
from .state_paths import state_dir, tasks_dir, task_sync_dir
from .task_schema import TaskRecord, TaskRecommendation, TaskRunRef, TaskSyncRecord
from .utils import now_iso, stable_id


def task_index_path() -> Path:
    return state_dir() / "tasks_index.jsonl"


def create_task_record(
    *,
    bundle: PromptBundle,
    title: str | None = None,
    status: str = "suggested",
    recommendation: TaskRecommendation | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> TaskRecord:
    task_id = stable_id(
        "task",
        bundle.task_cluster_id,
        bundle.origin_provider or "",
        bundle.origin_session_id or "",
        bundle.task_text[:240],
    )
    inferred_title = title or (bundle.task_text.strip().splitlines()[0][:120] or "Untitled task")
    return TaskRecord(
        task_id=task_id,
        task_cluster_id=bundle.task_cluster_id,
        title=inferred_title,
        status=status,
        source_provider=bundle.origin_provider,
        source_session_id=bundle.origin_session_id,
        created_at=now_iso(),
        updated_at=now_iso(),
        task_text=bundle.task_text,
        goal=bundle.goal or None,
        comparison_instructions=bundle.comparison_instructions or None,
        recommendation=recommendation,
        current_provider=bundle.origin_provider,
        current_mode="chat" if bundle.origin_provider else None,
        tags=tags or [],
        metadata=metadata or {},
    )


def save_task_record(task: TaskRecord) -> Path:
    task.updated_at = now_iso()
    path = tasks_dir() / f"{task.task_id}.json"
    path.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
    with task_index_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"task_id": task.task_id, "updated_at": task.updated_at, "status": task.status}) + "\n")
    return path


def load_task_record(task_id_or_path: str) -> TaskRecord:
    path = Path(task_id_or_path)
    if not path.exists():
        path = tasks_dir() / f"{task_id_or_path}.json"
    if not path.exists():
        # Clean, catchable error (no leaked absolute path) — glob-scanning callers
        # already catch this; CLI handlers route through `load_task_record_or_exit`.
        raise FileNotFoundError(
            f"No task found for id {str(task_id_or_path)!r}."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"task record at {path} is not a JSON object")
    recommendation = raw.get("recommendation")
    if recommendation:
        raw["recommendation"] = TaskRecommendation(**recommendation)
    raw["runs"] = [TaskRunRef(**run) for run in raw.get("runs", [])]
    return TaskRecord(**raw)


def load_task_record_or_exit(task_id: str) -> TaskRecord:
    """CLI helper: load a task or exit cleanly (one-line error, no traceback) when
    the id is unknown — a typo'd / deleted task id passed to a documented command
    (review --task, open-review --task) shouldn't dump a stack trace. Sibling of
    council_runtime.load_council_outcome_or_exit."""
    try:
        return load_task_record(task_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")


def find_task_by_cluster_id(task_cluster_id: str) -> TaskRecord | None:
    for path in tasks_dir().glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
        except (json.JSONDecodeError, OSError):
            continue
        if raw.get("task_cluster_id") == task_cluster_id:
            return load_task_record(str(path))
    return None


def ensure_task_record(
    *,
    bundle: PromptBundle,
    title: str | None = None,
    status: str = "suggested",
    recommendation: TaskRecommendation | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> TaskRecord:
    existing = find_task_by_cluster_id(bundle.task_cluster_id)
    if existing is not None:
        if recommendation is not None:
            existing.recommendation = recommendation
        if title:
            existing.title = title
        if tags:
            existing.tags = sorted(set([*existing.tags, *tags]))
        if metadata:
            existing.metadata.update(metadata)
        existing.status = status or existing.status
        existing.updated_at = now_iso()
        return existing
    return create_task_record(
        bundle=bundle,
        title=title,
        status=status,
        recommendation=recommendation,
        tags=tags,
        metadata=metadata,
    )


def task_from_council(
    *,
    bundle: PromptBundle,
    outcome: CouncilOutcome,
    review_page_path: str,
    launch_ids: list[str],
) -> TaskRecord:
    task = ensure_task_record(
        bundle=bundle,
        status="ready" if outcome.synthesis_output else "running",
        metadata={"council": True},
        tags=["council"],
    )
    task.current_provider = outcome.primary_provider
    task.current_mode = "council"
    task.status = "ready" if outcome.synthesis_output else "running"
    task.winner_provider = outcome.winner_provider
    task.agreement_score = outcome.agreement_score
    task.needs_followup = outcome.needs_followup
    task.review_page_path = review_page_path
    task.council_run_id = outcome.council_run_id
    task.launch_ids = sorted(set([*task.launch_ids, *launch_ids]))
    task.runs = [
        TaskRunRef(
            kind="member",
            provider=member.provider,
            run_id=member.session_id or member.provider,
            status="completed",
            mode="council_member",
            metadata={"model": member.model},
        )
        for member in outcome.member_results
    ]
    task.runs.append(
        TaskRunRef(
            kind="synthesis",
            provider=outcome.primary_provider,
            run_id=outcome.primary_session_id or outcome.council_run_id,
            status="completed" if outcome.synthesis_output else "running",
            mode="council_primary",
            local_artifact_path=review_page_path,
            metadata={
                "model": outcome.primary_model,
            },
        )
    )
    task.metadata.update(
        {
            "member_count": len(outcome.member_results),
            "difference_count": len(outcome.differences),
        }
    )
    task.updated_at = now_iso()
    return task


def make_sync_record(task: TaskRecord) -> TaskSyncRecord:
    sync_runs = [
        TaskRunRef(
            kind=run.kind,
            provider=run.provider,
            run_id=run.run_id,
            launched_at=run.launched_at,
            status=run.status,
            mode=run.mode,
            metadata={k: v for k, v in run.metadata.items() if k in {"model"}},
        )
        for run in task.runs
    ]
    sync = TaskSyncRecord(
        task_id=task.task_id,
        task_cluster_id=task.task_cluster_id,
        title=task.title,
        status=task.status,
        source_provider=task.source_provider,
        current_provider=task.current_provider,
        current_mode=task.current_mode,
        winner_provider=task.winner_provider,
        agreement_score=task.agreement_score,
        needs_followup=task.needs_followup,
        created_at=task.created_at,
        updated_at=task.updated_at,
        council_run_id=task.council_run_id,
        review_ready=bool(task.review_page_path),
        recommendation=task.recommendation,
        runs=sync_runs,
        metadata={
            "local_only": True,
            "launch_count": len(task.launch_ids),
            "tag_count": len(task.tags),
        },
    )
    return sync


def save_sync_record(task: TaskRecord) -> Path:
    sync = make_sync_record(task)
    path = task_sync_dir() / f"{task.task_id}.json"
    path.write_text(json.dumps(sync.to_dict(), indent=2), encoding="utf-8")
    return path
