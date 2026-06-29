"""Corpus milestones — fire-once celebrations as the lens deepens.

Trinity counts everything (councils, prompts, topic basins) but never
celebrates a *threshold crossing*. This surfaces a one-time "you crossed N"
event, marked in ``~/.trinity/milestones.json`` so it fires once and then goes
quiet — the same self-hiding discipline as the cold-open and the 🎉 new-model
nudge. The accumulation is invisible today; a milestone turns it into a felt
arc as the corpus grows.

Design notes:
- ``pending_milestone()`` is READ-ONLY (safe to call from any surface).
- ``surface_milestone()`` returns the pending milestone AND marks it celebrated
  (fire-once) — the caller is asserting the user actually saw it.
- Per the "analytics never crash" discipline, every stat read is defensive:
  a corrupt/absent state file yields 0, never an exception.
- One milestone per call (councils > prompts > basins priority) so a big jump
  celebrates the biggest crossing once, without spamming the user.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .state_paths import (
    council_outcomes_dir,
    prompts_dir,
    state_dir,
)
from .utils import atomic_write_text

# (metric, ascending thresholds). Sparse on purpose — a milestone should feel
# earned, not fire every few councils.
_THRESHOLDS: dict[str, list[int]] = {
    "councils": [1, 10, 25, 50, 100, 250, 500, 1000],
    "prompts": [100, 500, 1000, 5000, 10000, 25000, 50000, 100000],
    "basins": [5, 10, 20, 30, 48],
}
# Display priority when several metrics cross at once.
_PRIORITY = ("councils", "prompts", "basins")


@dataclass(frozen=True)
class Milestone:
    metric: str
    threshold: int
    message: str


def milestones_marker_path() -> Path:
    return state_dir() / "milestones.json"


def _load_marker() -> dict[str, int]:
    p = milestones_marker_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, int)}


def _count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _count_basins() -> int:
    from .lens_routing import load_topics_basins

    return len(load_topics_basins())


def compute_corpus_stats() -> dict[str, int]:
    """Current corpus size per tracked metric. Never raises."""
    try:
        council_dir = council_outcomes_dir()
        councils = sum(1 for _ in council_dir.glob("*.json")) if council_dir.exists() else 0
    except OSError:
        councils = 0
    prompts = _count_lines(prompts_dir() / "prompt_nodes.jsonl")
    return {"councils": councils, "prompts": prompts, "basins": _count_basins()}


def _message(metric: str, n: int) -> str:
    if metric == "councils":
        ordinal = "1st" if n == 1 else f"{n}th"
        return f"🏛  Your {ordinal} council — Trinity's routing is learning your picks."
    if metric == "prompts":
        return f"📚  {n:,} prompts indexed — your lens has that much more to read."
    if metric == "basins":
        return f"🗺  Your lens now spans {n} topic basins."
    return f"{n} {metric}"


def pending_milestone(stats: dict[str, int] | None = None) -> Milestone | None:
    """The single highest newly-crossed milestone (celebrated < threshold <=
    current), or None. READ-ONLY — does not touch the marker."""
    stats = stats if stats is not None else compute_corpus_stats()
    marker = _load_marker()
    for metric in _PRIORITY:
        current = stats.get(metric, 0)
        celebrated = marker.get(metric, 0)
        crossed = [t for t in _THRESHOLDS[metric] if celebrated < t <= current]
        if crossed:
            t = max(crossed)
            return Milestone(metric, t, _message(metric, t))
    return None


def surface_milestone() -> Milestone | None:
    """Return the pending milestone AND mark it celebrated (fire-once). Call
    this only from a surface that actually shows the user the message — the
    first caller wins, and the milestone never re-shows."""
    stats = compute_corpus_stats()
    m = pending_milestone(stats)
    if m is None:
        return None
    marker = _load_marker()
    # Mark this metric's crossed threshold so it (and every lower one) goes
    # quiet. Other metrics' pending milestones surface on later calls.
    marker[m.metric] = max(marker.get(m.metric, 0), m.threshold)
    try:
        atomic_write_text(milestones_marker_path(), json.dumps(marker, indent=2))
    except OSError:
        # Couldn't persist the mark — better to risk re-showing once than to
        # crash the surface. Honest degradation.
        pass
    return m
