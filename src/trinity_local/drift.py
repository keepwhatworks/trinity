"""Model drift detection.

Tracks (provider, normalized_model_id, task_type) → rolling outcome scores.
Compares current-window scores against the immediately-preceding baseline
window and emits alerts when a provider's quality drops significantly. With
the defaults the current window is the last 7 days and the baseline is the
single prior week (records aged 7–14 days), so the alert copy says "the prior
week", NOT "2 weeks" — the baseline span is baseline_window_days minus
current_window_days, not the raw baseline_window_days lookback.

Only detects drift when there's enough data — at least 3 sessions in the
current window and 5 in the baseline.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from .state_paths import outcomes_log_path


@dataclass
class DriftAlert:
    """A detected quality change for a provider/model/task combination."""
    provider: str
    model_id: str | None
    task_type: str
    baseline_score: float
    current_score: float
    delta_pct: float
    baseline_sessions: int
    current_sessions: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class OutcomeRecord:
    """One session outcome for drift tracking."""
    provider: str
    model_id: str | None
    task_type: str
    completed: bool
    error_count: int
    session_seconds: float | None
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _outcomes_path() -> Path:
    path = outcomes_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_outcome(record: OutcomeRecord) -> None:
    """Append an outcome record to the drift tracking log."""
    with _outcomes_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict()) + "\n")


def _load_outcomes() -> list[OutcomeRecord]:
    """Load all outcome records."""
    path = _outcomes_path()
    if not path.exists():
        return []
    records: list[OutcomeRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):  # guard_shape_not_just_parse: .get below crashes on a non-dict line
            continue
        records.append(OutcomeRecord(
            provider=raw.get("provider", ""),
            model_id=raw.get("model_id"),
            task_type=raw.get("task_type", "general"),
            completed=raw.get("completed", False),
            # error_count feeds `_score_outcome`'s `> 2`/`> 0` numeric compares.
            # A hand-edited outcomes.jsonl can carry it as a string ("lots"),
            # null, or a float — and `"lots" > 2` raises TypeError that bubbled
            # out of `check_drift` and crashed `trinity-local status` (exit 1,
            # the Iter-257 corrupt-field-crash class on the drift ledger).
            # Coerce to a clean int at the load boundary so a garbled count
            # degrades to 0 (a clean completion) instead of nuking status.
            error_count=_safe_int(raw.get("error_count"), 0),
            session_seconds=raw.get("session_seconds"),
            timestamp=raw.get("timestamp", ""),
        ))
    return records


def _safe_int(value: object, default: int) -> int:
    """Coerce a JSON-loaded value to an int, falling back to `default`.

    Load-boundary shape-guard for the drift ledger (outcomes.jsonl is an
    append-only file the user can hand-edit). A bare `int(...)` / `>` compare on
    a non-numeric string or None raises and crashes the status render; bool is
    an int subclass and passes through as 0/1, which is the intended truthiness.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and value not in (float("inf"), float("-inf")) else default
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, TypeError):
            return default
    return default


def _score_outcome(record: OutcomeRecord) -> float:
    """Score a single outcome: 1.0 for clean completion, 0.0 for failure."""
    if not record.completed:
        return 0.0
    if record.error_count > 2:
        return 0.3
    if record.error_count > 0:
        return 0.7
    return 1.0


def _parse_ts(ts: str) -> float:
    """Parse an ISO timestamp to Unix epoch seconds."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _window_phrase(days: int) -> str:
    """Human-readable span for a drift window — so the alert copy is DERIVED from
    the real day count and can never overstate it (the old hardcoded "prior 2
    weeks" claimed double the baseline span the math actually compared over)."""
    if days % 7 == 0 and days >= 7:
        weeks = days // 7
        return "week" if weeks == 1 else f"{weeks} weeks"
    return "day" if days == 1 else f"{days} days"


def check_drift(
    *,
    current_window_days: int = 7,
    baseline_window_days: int = 14,
    min_current: int = 3,
    min_baseline: int = 5,
    threshold_pct: float = 20.0,
) -> list[DriftAlert]:
    """Check for model drift by comparing recent outcomes to baseline.

    Returns a list of DriftAlert for any (provider, model, task_type) combo
    where current-window quality has dropped by more than threshold_pct.
    """
    now = datetime.now(timezone.utc).timestamp()
    current_cutoff = now - (current_window_days * 86400)
    baseline_cutoff = now - (baseline_window_days * 86400)

    outcomes = _load_outcomes()

    # Group by (provider, model_id, task_type).
    # Keep this as a plain type alias assignment so it works at runtime on 3.10+.
    Key = Tuple[str, Optional[str], str]
    current_scores: dict[Key, list[float]] = {}
    baseline_scores: dict[Key, list[float]] = {}

    for record in outcomes:
        ts = _parse_ts(record.timestamp)
        if ts <= 0:
            continue
        key: Key = (record.provider, record.model_id, record.task_type)
        score = _score_outcome(record)

        if ts >= current_cutoff:
            current_scores.setdefault(key, []).append(score)
        elif ts >= baseline_cutoff:
            baseline_scores.setdefault(key, []).append(score)

    # The baseline bucket holds records OLDER than the current window but newer
    # than the baseline cutoff — i.e. records aged (current_window_days,
    # baseline_window_days]. Its span is the DIFFERENCE, not baseline_window_days
    # (which is the lookback FROM now). With the defaults that's 14−7 = 7 days =
    # one week, NOT two — the message used to claim "prior 2 weeks", overstating
    # the comparison window by 2× ("claude's coding quality dropped 100% this week
    # vs prior 2 weeks" while the baseline was a single prior week). Derive the
    # phrase from the real parameters so the claim can never drift from the math.
    baseline_span_days = max(baseline_window_days - current_window_days, 0)
    baseline_phrase = _window_phrase(baseline_span_days)
    current_phrase = _window_phrase(current_window_days)

    alerts: list[DriftAlert] = []
    for key, current in current_scores.items():
        baseline = baseline_scores.get(key, [])
        if len(current) < min_current or len(baseline) < min_baseline:
            continue

        current_avg = sum(current) / len(current)
        baseline_avg = sum(baseline) / len(baseline)

        if baseline_avg <= 0:
            continue

        delta_pct = ((current_avg - baseline_avg) / baseline_avg) * 100

        if delta_pct < -threshold_pct:
            provider, model_id, task_type = key
            alerts.append(DriftAlert(
                provider=provider,
                model_id=model_id,
                task_type=task_type,
                baseline_score=round(baseline_avg, 3),
                current_score=round(current_avg, 3),
                delta_pct=round(delta_pct, 1),
                baseline_sessions=len(baseline),
                current_sessions=len(current),
                message=(
                    f"{provider}'s {task_type} quality dropped {abs(delta_pct):.0f}% "
                    f"in the last {current_phrase} vs the prior {baseline_phrase} "
                    f"(model: {model_id or 'unknown'}, "
                    f"{len(current)} recent sessions vs {len(baseline)} baseline)"
                ),
            ))

    return alerts
