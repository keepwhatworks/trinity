from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PendingAction:
    action_id: str
    task_id: str
    task_cluster_id: str
    kind: str
    status: str
    title: str
    message: str
    bundle_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    recommended_provider: str | None = None
    recommended_mode: str | None = None
    review_page_path: str | None = None
    command_hint: str | None = None
    # Retired 2026-05-17 with the macOS Shortcut dispatcher; kept on the
    # dataclass so saved JSON files written before the kill still load
    # (`PendingAction(**raw)` would otherwise raise on unexpected kwargs).
    shortcut_url: str | None = None
    dispatch_action: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}
