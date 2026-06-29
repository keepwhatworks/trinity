"""Heuristic ranker: task-kind-based routing with outcome/cost evidence.

Also exposes `prompt_calls_for_council(task_text) -> (escalate, signals)` —
a structural-shape detector used by route() to escalate prompts that ask for
a comparison even when task_type is something pedestrian. Folded in from the
former standalone `prompt_shape.py`; same logic, one fewer top-level module.
"""
from __future__ import annotations

import re

from .base import Ranker
from .types import RoutingContext, RoutingDecision


# Patterns that capture distinct alternative labels — used for ≥2-label gating.
_LABEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # `A)` at start of line:   "A) first option..."
    ("labeled_alternatives_paren_after", re.compile(
        r"(?:^|\n)\s*([A-G])\)\s+\S",
        re.MULTILINE,
    )),
    # `(A)` at start of line:  "(A) first option..."
    ("labeled_alternatives_paren_around", re.compile(
        r"(?:^|\n)\s*\(([A-G])\)\s+\S",
        re.MULTILINE,
    )),
    # `1.` at start of line:   "1. first option..."
    ("numbered_alternatives", re.compile(
        r"(?:^|\n)\s*([1-9])\.\s+\S",
        re.MULTILINE,
    )),
]

# Strong comparative phrases — any single match escalates.
_COMPARATIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "option A" / "approach 1" / "design B" / "candidate C" / "proposal X"
    ("named_candidates", re.compile(
        r"\b(?:option|approach|candidate|alternative|design|proposal)\s+[A-G1-9]\b",
        re.IGNORECASE,
    )),
    # "vs.", "versus"
    ("vs", re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE)),
    # "which X (is|are|wins|works|fits) (best|better|strongest|right)"
    # plus "which (is|one) (best|...)"
    # plus "which X best" (e.g. "which design best fits…")
    ("which_best", re.compile(
        r"\bwhich(?:\s+\w+){0,3}\s+(?:is|are|wins|works|fits|fit|best|better|strongest|stronger|right|wrong)\b",
        re.IGNORECASE,
    )),
    # "tradeoffs", "trade-offs", "pros and cons"
    ("tradeoffs", re.compile(
        r"\b(?:tradeoffs?|trade-offs?|pros and cons)\b",
        re.IGNORECASE,
    )),
    # "compare", "comparison", "compare X to Y"
    ("compare", re.compile(r"\bcompare\b|\bcomparison\b", re.IGNORECASE)),
    # "pick the best/winner", "choose between", "rank these"
    ("pick_among", re.compile(
        r"\b(?:"
        r"pick the (?:best|right|winner|strongest)|"
        r"choose between|"
        r"rank (?:these|the (?:options|candidates|alternatives|designs|approaches))"
        r")\b",
        re.IGNORECASE,
    )),
    # "reasonable people disagree", "two senior engineers"
    ("reasonable_disagreement", re.compile(
        r"\b(?:reasonable(?:ly)? disagree|two senior engineers)\b",
        re.IGNORECASE,
    )),
    # "failure mode of the (loser|losing|other|two|three)" — strong signal
    # that the user wants the chairman to identify why losers lost.
    ("failure_mode", re.compile(
        r"\bfailure mode\b|\bweakness(?:es)? of\b|\bwhy (?:the )?(?:loser|losing)\b",
        re.IGNORECASE,
    )),
    # "best of", "strongest of these/the following/the options/etc."
    ("superlative_among", re.compile(
        r"\bof (?:these|the following|the above|the options?|the candidates?|the alternatives?|the designs?|the approaches?)\b",
        re.IGNORECASE,
    )),
]


def prompt_calls_for_council(task_text: str) -> tuple[bool, list[str]]:
    """Return (should_escalate_to_council, matched_signals).

    Escalates if EITHER:
      - ≥2 distinct labeled alternatives appear (paren-after, paren-around, or numbered)
      - any comparative-phrase pattern fires

    The label pattern requires distinct labels to avoid false-positives on a
    lone "1." in the middle of regular prose.
    """
    if not task_text:
        return False, []

    signals: list[str] = []
    for label, pattern in _LABEL_PATTERNS:
        n = len(set(pattern.findall(task_text)))
        if n >= 2:
            signals.append(f"{label}({n})")

    for label, pattern in _COMPARATIVE_PATTERNS:
        if pattern.search(task_text):
            signals.append(label)

    return (len(signals) > 0, signals)


class HeuristicRanker(Ranker):
    """Routes based on task_type (research/coding/debugging/general).

    Evidence comes from recent session outcomes and cost comparisons.
    Recommendations: research→antigravity, coding→codex, default→claude.
    Confidence is fixed by task_type (0.72/0.68/0.55).
    """

    def advise(self, context: RoutingContext) -> RoutingDecision:
        """Advise based on task_type with outcome/cost evidence."""
        task_type = context.task_type
        evidence = self._gather_evidence(context)

        if task_type in {"research", "cowork_general"}:
            return RoutingDecision(
                recommended_provider="antigravity",
                top_k=["antigravity", "codex"],
                needs_council=True,
                confidence=0.72,
                evidence=evidence + [
                    "Antigravity is likely stronger for broad research and comparison."
                ],
                backend="heuristic",
            )
        if task_type in {"coding", "debugging"}:
            return RoutingDecision(
                recommended_provider="codex",
                top_k=["codex", "claude"],
                needs_council=True,
                confidence=0.68,
                evidence=evidence + [
                    "Codex is likely stronger for execution-heavy coding work."
                ],
                backend="heuristic",
            )
        return RoutingDecision(
            recommended_provider="claude",
            top_k=[],
            needs_council=False,
            confidence=0.55,
            evidence=evidence + [
                "Claude is still the best default for this task shape."
            ],
            backend="heuristic",
        )

    def _gather_evidence(self, context: RoutingContext) -> list[str]:
        """Query outcome logs to build evidence."""
        from ..drift import _load_outcomes

        evidence: list[str] = []

        # Check recent outcomes for this provider + task kind
        try:
            outcomes = _load_outcomes()
            provider_outcomes = [
                o for o in outcomes
                if o.provider == context.current_provider and o.task_type == context.task_type
            ]
            if len(provider_outcomes) >= 3:
                completed = sum(1 for o in provider_outcomes[-10:] if o.completed)
                total = min(len(provider_outcomes), 10)
                rate = completed / total
                evidence.append(
                    f"{context.current_provider} completed {completed}/{total} recent {context.task_type} tasks "
                    f"({rate:.0%} completion rate)."
                )
                errored = sum(1 for o in provider_outcomes[-10:] if o.error_count > 0)
                if errored > 0:
                    evidence.append(
                        f"{errored}/{total} of those sessions had tool errors."
                    )
        except Exception:
            pass  # Gracefully degrade if outcome logs unavailable

        return evidence
