"""Trinity capability categories.

Names + ordering aligned with the public LMArena leaderboard so we can
slot in external benchmarks later without re-mapping our UI. For v1 we
display these labels and aggregate the personal routing table into them;
no external data is auto-fetched.
"""
from __future__ import annotations


# (key, display label, trinity task_types that map into this category)
# Order matters — UI renders categories in this order on the launchpad.
CATEGORY_REGISTRY: list[tuple[str, str, tuple[str, ...]]] = [
    ("overall",               "Overall",                ("general", "cowork_general")),
    ("coding",                "Coding",                 ("coding", "debugging")),
    # "Hard prompts" is the umbrella for multi-step reasoning tasks Trinity
    # councils chew on most often — architecture, planning, audits. The
    # hardcoded JS map on the launchpad used to miss these and the
    # capabilities chart rendered empty. Sourcing the map from this list
    # via task_type_to_category() keeps server + UI in sync.
    ("hard_prompts",          "Hard Prompts",           (
        "research",
        "council_synthesis",
        "system_design",
        "architecture_decision",
        "architecture_ratification",
        "launch_readiness_audit",
        "launch_readiness_decision",
        "launch_risk_triage",
        "launch_copy_review",
        "launch_strategy_decision",
    )),
    ("creative_writing",      "Creative Writing",       ("writing",)),
    ("instruction_following", "Instruction Following",  ()),
    ("multiturn",             "Multi-Turn",             ()),
    ("math",                  "Math",                   ()),
]


# Default category for any task_type not explicitly registered above.
# Anything that smells like reasoning lands here instead of disappearing
# from the capabilities chart entirely.
DEFAULT_CATEGORY_FOR_UNKNOWN_TASK_TYPE = "hard_prompts"


def category_keys() -> list[str]:
    return [key for key, _, _ in CATEGORY_REGISTRY]


def category_labels() -> dict[str, str]:
    return {key: label for key, label, _ in CATEGORY_REGISTRY}


def task_type_to_category() -> dict[str, str]:
    """Reverse map trinity task_type → category key."""
    out: dict[str, str] = {}
    for key, _, task_types in CATEGORY_REGISTRY:
        for task_type in task_types:
            out[task_type] = key
    return out
