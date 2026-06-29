"""k-NN advisory analytics — production observability for the advisory layer.

Tracks every k-NN advisory call and provides aggregated reporting for:
  1. Evidence spam: how many evidence lines are being appended?
  2. Threshold brittleness: does council_confidence behave consistently
     across task_types and provider pairs?
  3. Product metrics: "acted on suggestion" and "later switched anyway"

Log is append-only JSONL at ~/.trinity/analytics/knn_advisory.jsonl
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .state_paths import analytics_dir


def _advisory_log_path() -> Path:
    return analytics_dir() / "knn_advisory.jsonl"


# ---------------------------------------------------------------------------
# Advisory event logging
# ---------------------------------------------------------------------------

@dataclass
class AdvisoryEvent:
    """One k-NN advisory call record."""

    timestamp: str
    session_id: str
    provider: str
    task_type: str
    prompt_len: int

    # k-NN outputs
    knn_available: bool
    neighbor_count: int = 0
    council_confidence: float = 0.0
    should_council: bool = False
    reroute_provider: str | None = None
    reroute_similarity: float = 0.0
    top2_providers: list[str] = field(default_factory=list)
    evidence_count: int = 0

    # Decision tracking
    heuristic_mode: str = ""           # original mode before k-NN
    final_mode: str = ""               # mode after k-NN upgrade
    was_upgraded: bool = False          # did k-NN change the mode?
    recommended_provider: str = ""

    # Product outcome (filled later by outcome tracker)
    suggestion_acted_on: bool | None = None
    later_switched: bool | None = None
    switch_target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def log_advisory_event(event: AdvisoryEvent) -> None:
    """Append an advisory event to the log."""
    with _advisory_log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict()) + "\n")


def load_advisory_log() -> list[AdvisoryEvent]:
    """Load all advisory events from disk."""
    path = _advisory_log_path()
    if not path.exists():
        return []
    events: list[AdvisoryEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):  # guard_shape_not_just_parse: .get below raises AttributeError (not caught by the except)
                continue
            events.append(AdvisoryEvent(
                timestamp=raw.get("timestamp", ""),
                session_id=raw.get("session_id", ""),
                provider=raw.get("provider", ""),
                task_type=raw.get("task_type", ""),
                prompt_len=raw.get("prompt_len", 0),
                knn_available=raw.get("knn_available", False),
                neighbor_count=raw.get("neighbor_count", 0),
                council_confidence=raw.get("council_confidence", 0.0),
                should_council=raw.get("should_council", False),
                reroute_provider=raw.get("reroute_provider"),
                reroute_similarity=raw.get("reroute_similarity", 0.0),
                top2_providers=raw.get("top2_providers", []),
                evidence_count=raw.get("evidence_count", 0),
                heuristic_mode=raw.get("heuristic_mode", ""),
                final_mode=raw.get("final_mode", ""),
                was_upgraded=raw.get("was_upgraded", False),
                recommended_provider=raw.get("recommended_provider", ""),
                suggestion_acted_on=raw.get("suggestion_acted_on"),
                later_switched=raw.get("later_switched"),
                switch_target=raw.get("switch_target"),
            ))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return events


def mark_suggestion_outcome(
    session_id: str,
    *,
    acted_on: bool,
    later_switched: bool = False,
    switch_target: str | None = None,
) -> bool:
    """Update an advisory event with its product outcome.

    Rewrites the log entry in-place for the given session_id.
    Returns True if the event was found and updated.
    """
    path = _advisory_log_path()
    if not path.exists():
        return False

    lines = path.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if not line.strip():
            new_lines.append(line)
            continue
        try:
            raw = json.loads(line)
            # guard_shape_not_just_parse: a non-dict line falls to `else` and is
            # preserved verbatim (a read-modify-write must NOT drop or crash on it).
            if isinstance(raw, dict) and raw.get("session_id") == session_id and not updated:
                raw["suggestion_acted_on"] = acted_on
                raw["later_switched"] = later_switched
                if switch_target:
                    raw["switch_target"] = switch_target
                updated = True
                new_lines.append(json.dumps(raw))
            else:
                new_lines.append(line)
        except json.JSONDecodeError:
            new_lines.append(line)

    if updated:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return updated


# ---------------------------------------------------------------------------
# Analytics report
# ---------------------------------------------------------------------------

@dataclass
class AdvisoryReport:
    """Aggregated analytics for the k-NN advisory layer."""

    total_events: int = 0
    knn_active_count: int = 0
    knn_inactive_count: int = 0

    # Evidence spam
    evidence_count_avg: float = 0.0
    evidence_count_max: int = 0
    evidence_count_p95: int = 0

    # Upgrade rate
    upgrades_total: int = 0
    upgrade_rate: float = 0.0

    # Council trigger breakdown
    council_triggered: int = 0
    council_by_heuristic: int = 0
    council_by_knn: int = 0

    # Threshold analysis by task_type
    # (Field name was `confidence_by_task_kind` pre-Tier-1-#3 rename;
    # renamed iter #67 to align with task #92's `task_kind` → `task_type`
    # sweep, which had missed this surface. Old AdvisoryReport.to_dict()
    # JSON consumers reading the old key need to migrate at the same
    # time — knn_advisory_report.json on existing user installs will
    # show the new key on next generate_report() call.)
    confidence_by_task_type: dict[str, dict[str, float]] = field(default_factory=dict)

    # Threshold analysis by provider pair
    confidence_by_provider_pair: dict[str, dict[str, float]] = field(default_factory=dict)

    # Product metrics
    suggestions_total: int = 0
    suggestions_acted_on: int = 0
    act_rate: float | None = None
    later_switched_total: int = 0
    later_switch_rate: float | None = None
    # The key product metric: of acted-on suggestions, how many switched anyway?
    # If this drops over time, the product is getting smarter.
    switch_after_acted_total: int = 0
    switch_after_acted_rate: float | None = None
    switch_targets: dict[str, int] = field(default_factory=dict)

    # Alerts
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in d:
            if isinstance(d[key], float) and d[key] is not None:
                d[key] = round(d[key], 4)
        return d


def generate_report() -> AdvisoryReport:
    """Analyze the advisory log and produce a report."""
    events = load_advisory_log()
    report = AdvisoryReport(total_events=len(events))

    if not events:
        return report

    # Basic counts
    active = [e for e in events if e.knn_available]
    report.knn_active_count = len(active)
    report.knn_inactive_count = len(events) - len(active)

    # Evidence spam
    evidence_counts = [e.evidence_count for e in events]
    report.evidence_count_avg = sum(evidence_counts) / len(evidence_counts)
    report.evidence_count_max = max(evidence_counts)
    sorted_counts = sorted(evidence_counts)
    p95_idx = int(len(sorted_counts) * 0.95)
    report.evidence_count_p95 = sorted_counts[min(p95_idx, len(sorted_counts) - 1)]

    if report.evidence_count_max > 8:
        report.alerts.append(
            f"EVIDENCE SPAM: max evidence lines = {report.evidence_count_max} "
            f"(p95 = {report.evidence_count_p95}). Consider capping at 6."
        )

    # Upgrade tracking
    upgrades = [e for e in events if e.was_upgraded]
    report.upgrades_total = len(upgrades)
    report.upgrade_rate = len(upgrades) / len(events)

    # Council triggers
    council_events = [e for e in events if e.final_mode == "council"]
    report.council_triggered = len(council_events)
    report.council_by_heuristic = sum(
        1 for e in council_events if e.heuristic_mode == "council"
    )
    report.council_by_knn = sum(
        1 for e in council_events if e.was_upgraded and e.heuristic_mode != "council"
    )

    # Threshold analysis by task_type
    by_task_type: dict[str, list[float]] = defaultdict(list)
    for e in active:
        by_task_type[e.task_type].append(e.council_confidence)
    for task_type, confs in by_task_type.items():
        report.confidence_by_task_type[task_type] = {
            "mean": sum(confs) / len(confs),
            "min": min(confs),
            "max": max(confs),
            "count": len(confs),
        }

    # Check for threshold brittleness
    if len(by_task_type) >= 2:
        means = [sum(c) / len(c) for c in by_task_type.values()]
        spread = max(means) - min(means)
        if spread > 0.3:
            # task_type name as a stable tie-break so the named high/low type in
            # the alert is deterministic on a mean tie (else the dict order
            # picks). `min` over (-mean, name) = highest mean / smallest name;
            # `min` over (mean, name) = lowest mean / smallest name.
            _mean = lambda k: sum(by_task_type[k]) / len(by_task_type[k])
            high_type = min(by_task_type, key=lambda k: (-_mean(k), k))
            low_type = min(by_task_type, key=lambda k: (_mean(k), k))
            report.alerts.append(
                f"THRESHOLD BRITTLENESS: council_confidence varies by {spread:.0%} "
                f"across task_types ({high_type}={sum(by_task_type[high_type])/len(by_task_type[high_type]):.0%} "
                f"vs {low_type}={sum(by_task_type[low_type])/len(by_task_type[low_type]):.0%}). "
                f"Consider per-task_type thresholds."
            )

    # Threshold analysis by provider pair
    by_pair: dict[str, list[float]] = defaultdict(list)
    for e in active:
        if e.reroute_provider:
            pair = f"{e.provider}->{e.reroute_provider}"
            by_pair[pair].append(e.reroute_similarity)
    for pair, sims in by_pair.items():
        report.confidence_by_provider_pair[pair] = {
            "mean": sum(sims) / len(sims),
            "min": min(sims),
            "max": max(sims),
            "count": len(sims),
        }

    # Product metrics
    with_outcome = [e for e in events if e.suggestion_acted_on is not None]
    report.suggestions_total = len(with_outcome)
    report.suggestions_acted_on = sum(1 for e in with_outcome if e.suggestion_acted_on)
    if with_outcome:
        report.act_rate = report.suggestions_acted_on / len(with_outcome)

    switched = [e for e in events if e.later_switched]
    report.later_switched_total = len(switched)
    if with_outcome:
        report.later_switch_rate = len(switched) / len(with_outcome)

    # The key product metric: of ACTED-ON suggestions, how many switched anyway?
    acted_on = [e for e in with_outcome if e.suggestion_acted_on]
    acted_then_switched = [e for e in acted_on if e.later_switched]
    report.switch_after_acted_total = len(acted_then_switched)
    if acted_on:
        report.switch_after_acted_rate = len(acted_then_switched) / len(acted_on)

    target_counts: Counter[str] = Counter()
    for e in switched:
        if e.switch_target:
            target_counts[e.switch_target] += 1
    report.switch_targets = dict(target_counts)

    if report.act_rate is not None and report.act_rate < 0.1 and report.suggestions_total > 20:
        report.alerts.append(
            f"LOW ACT RATE: only {report.act_rate:.0%} of suggestions were acted on "
            f"({report.suggestions_acted_on}/{report.suggestions_total}). "
            f"Consider raising council_threshold or reducing noise."
        )

    if report.switch_after_acted_rate is not None and report.switch_after_acted_rate > 0.3:
        report.alerts.append(
            f"HIGH SWITCH-AFTER-ACTED RATE: {report.switch_after_acted_rate:.0%} of "
            f"acted-on suggestions resulted in a later provider switch "
            f"({report.switch_after_acted_total}/{len(acted_on)}). "
            f"The advisory may be recommending the wrong provider."
        )

    return report


def save_report(report: AdvisoryReport) -> Path:
    """Save report to disk."""
    path = analytics_dir() / "knn_advisory_report.json"
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path
