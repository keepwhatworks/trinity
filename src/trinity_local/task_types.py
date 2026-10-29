from __future__ import annotations

import re

# The database/systems sense of "write" — NOT a request to author prose.
# Bare-substring "write" used to mislabel all of these as `writing` and
# route them on the prose-writing track record (live 2026-05-31:
# "Postgres or DynamoDB for a write-heavy event log" → writing). Covers
# "write-heavy", "high write throughput", "write IOPS", "write-ahead log",
# "write amplification/lock/path/back", "read/write", "writes per second".
_WRITE_TECH = re.compile(
    r"write[-\s]?(heav|through|iops|ahead|amplif|lock|path|back)"
    r"|read[\s/-]+write"
    r"|writes?\s*/?\s*per\s*sec"
)
_CODING_TERMS = ("code", "refactor", "repo", "function", "script")


def guess_task_type(text: str, provider: str | None = None) -> str:
    """Heuristic classifier — the short COARSE label that flows into routing
    decisions: one of {research, debugging, writing, cowork_general, coding,
    general}.

    NOTE (verified 2026-05-31, #277): this does NOT share a vocabulary with the
    chairman's emitted ``task_type``. The chairman emits fine-grained,
    open-ended labels (``architecture_decision``, ``strategic_foresight``,
    ``concept_explanation``, ``market_analysis``, …) which become the cortex
    basin keys in picks.json. So the exact-match fast path in
    the removed lens-basin router (``patterns.get(guess_task_type(query))``) almost
    never hits a fine-grained basin — basin routing relies on the embedding
    centroid fallback. Don't assume this label can address a cortex basin by
    name; it can't.
    """
    lowered = text.lower()
    if any(term in lowered for term in ("stock", "research", "compare", "market", "investigate")):
        return "research"
    if any(term in lowered for term in ("debug", "bug", "error", "failing", "traceback")):
        return "debugging"
    if any(term in lowered for term in ("write", "draft", "email", "memo")):
        # "write" is overloaded: prose ("write a blog post"), database/
        # systems ("write-heavy", "read/write"), and coding ("write a
        # function"). Only claim `writing` for genuine prose authoring —
        # let the systems case fall to general and the code case to coding.
        if not _WRITE_TECH.search(lowered) and not any(t in lowered for t in _CODING_TERMS):
            return "writing"
    if provider == "cowork":
        return "cowork_general"
    if any(term in lowered for term in _CODING_TERMS):
        return "coding"
    return "general"


# Horizon classification — used by council_runtime to hint chairman which
# lens-card resolution to weight (per #139). Heuristic v1: keyword/regex
# over query text. The plan named "LLM-tagged v2 if v1 is too noisy" as
# the upgrade path; today v1 is cheap and good enough to start surfacing
# the signal.
_PHILOSOPHICAL_PHRASES = (
    "what kind of",
    "what should i build",
    "what do i want",
    "who am i",
    "what's my",
    "what is my",
    "should i become",
    "should i be the kind",
    "what's the point",
    "what matters",
    "is this the right life",
    "is this the right path",
    "what's the meaning",
    "five years from now",
    "ten years from now",
    "10 years from now",
    "long arc",
    "long-arc",
    "identity",
    "value system",
)
_STRATEGIC_PHRASES = (
    "should i",
    "trade-off",
    "tradeoff",
    "bet on",
    "betting on",
    "this quarter",
    "next quarter",
    "this year",
    "next year",
    "roadmap",
    "long-term",
    "longterm",
    "strategy",
    "strategic",
    "pivot",
    "moat",
    "build vs buy",
    "build vs. buy",
    "or should i",
    "or should we",
)


def guess_horizon(text: str) -> str:
    """Classify a query into tactical | strategic | philosophical.

    Used by chairman context to hint which lens-cards to weight (#139).
    Heuristic: most specific (philosophical) → least specific (strategic) →
    default (tactical). "Tactical" is the safe always-applies floor;
    misclassifying a strategic question as tactical means chairman uses
    the local-shape lenses, which still helps; misclassifying tactical
    as philosophical drowns the signal.
    """
    if not text:
        return "tactical"
    lowered = text.lower()
    if any(phrase in lowered for phrase in _PHILOSOPHICAL_PHRASES):
        return "philosophical"
    if any(phrase in lowered for phrase in _STRATEGIC_PHRASES):
        return "strategic"
    return "tactical"
