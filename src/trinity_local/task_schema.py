from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TaskRecommendation:
    recommended_provider: str | None = None
    recommended_mode: str | None = None
    reason: str | None = None
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)
    created_at: str | None = None
    # k-NN advisory metadata (set when embedding neighbors were consulted)
    knn_method: str | None = None
    knn_neighbor_count: int | None = None
    knn_council_confidence: float | None = None
    top2_providers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}
        return d


@dataclass
class TaskRunRef:
    kind: str
    provider: str | None = None
    run_id: str | None = None
    launched_at: str | None = None
    status: str | None = None
    mode: str | None = None
    local_artifact_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"kind": self.kind}
        for key in ("provider", "run_id", "launched_at", "status", "mode", "local_artifact_path"):
            value = getattr(self, key)
            if value not in (None, "", {}, []):
                payload[key] = value
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass
class TaskRecord:
    task_id: str
    task_cluster_id: str
    title: str
    status: str
    source_provider: str | None = None
    source_session_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    task_text: str | None = None
    goal: str | None = None
    comparison_instructions: str | None = None
    context_excerpt_path: str | None = None
    recommendation: TaskRecommendation | None = None
    current_provider: str | None = None
    current_mode: str | None = None
    winner_provider: str | None = None
    agreement_score: float | None = None
    needs_followup: bool | None = None
    review_page_path: str | None = None
    council_run_id: str | None = None
    switched_from_provider: str | None = None
    switched_from_task_id: str | None = None
    launch_ids: list[str] = field(default_factory=list)
    runs: list[TaskRunRef] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "task_cluster_id": self.task_cluster_id,
            "title": self.title,
            "status": self.status,
            "runs": [run.to_dict() for run in self.runs],
        }
        for key in (
            "source_provider",
            "source_session_id",
            "created_at",
            "updated_at",
            "task_text",
            "goal",
            "comparison_instructions",
            "context_excerpt_path",
            "current_provider",
            "current_mode",
            "winner_provider",
            "agreement_score",
            "needs_followup",
            "review_page_path",
            "council_run_id",
            "switched_from_provider",
            "switched_from_task_id",
        ):
            value = getattr(self, key)
            if value not in (None, "", {}, []):
                payload[key] = value
        if self.recommendation is not None:
            payload["recommendation"] = self.recommendation.to_dict()
        if self.launch_ids:
            payload["launch_ids"] = self.launch_ids
        if self.tags:
            payload["tags"] = self.tags
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass
class TaskSyncRecord:
    task_id: str
    task_cluster_id: str
    title: str
    status: str
    source_provider: str | None = None
    current_provider: str | None = None
    current_mode: str | None = None
    winner_provider: str | None = None
    agreement_score: float | None = None
    needs_followup: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None
    council_run_id: str | None = None
    review_ready: bool | None = None
    recommendation: TaskRecommendation | None = None
    runs: list[TaskRunRef] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "task_id": self.task_id,
            "task_cluster_id": self.task_cluster_id,
            "title": self.title,
            "status": self.status,
            "runs": [run.to_dict() for run in self.runs],
        }
        for key in (
            "source_provider",
            "current_provider",
            "current_mode",
            "winner_provider",
            "agreement_score",
            "needs_followup",
            "created_at",
            "updated_at",
            "council_run_id",
            "review_ready",
        ):
            value = getattr(self, key)
            if value not in (None, "", {}, []):
                payload[key] = value
        if self.recommendation is not None:
            payload["recommendation"] = self.recommendation.to_dict()
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload
