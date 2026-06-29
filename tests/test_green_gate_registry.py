"""Ratchet: every decision-directive green must be registered in the green-gate checklist.

Decision-directive greens — booleans/metrics that tell a consumer to take an
ACTION based on data (`*_recommended`, `should_auto_*`) — are the highest-risk
green-while-degenerate shape. The holdout `flip_recommended` shipped
coverage-ungated (2026-06-02) because new code didn't inherit the launchpad-card
discipline (gate + pre-registered floor + degenerate-data test). This ratchet
forces a NEW decision-directive green to be classified in
`docs/green-gate-checklist.md` before it can ship — and classifying it makes the
author state its gate: a data-directive needs a pre-registered floor; a
heuristic-hint gates on task/route shape and needs none, but must say so.

Same ratchet shape as `scripts/known_orphans.txt` — a finite registry that CI
keeps honest. See principle #35 (+ corollary) and the green-gate checklist.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src" / "trinity_local"
_CHECKLIST = _ROOT / "docs" / "green-gate-checklist.md"

# The decision-directive green name shapes (a consumer acts on these).
_PATTERNS = (
    re.compile(r"\b([a-z_]+_recommended)\b"),
    re.compile(r"\b(should_auto_\w+)\b"),
)


def _decision_greens_in_src() -> set[str]:
    names: set[str] = set()
    for py in _SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for pat in _PATTERNS:
            names.update(pat.findall(text))
    return names


def test_every_decision_directive_green_is_registered():
    registry = _CHECKLIST.read_text(encoding="utf-8")
    found = _decision_greens_in_src()
    # sanity: the patterns still match the known set (catch a regex that rots to
    # matching nothing, which would make the ratchet silently pass).
    assert "flip_recommended" in found, (
        "the scan no longer finds flip_recommended — the patterns have drifted"
    )
    missing = sorted(n for n in found if n not in registry)
    assert not missing, (
        "These decision-directive greens are not registered in "
        "docs/green-gate-checklist.md. A green that tells a consumer to act on "
        "data must be classified there before it ships — data-directive (needs a "
        "pre-registered floor, gate it like flip_recommended) vs heuristic-hint "
        "(task/route shape, no data floor). Missing:\n"
        + "\n".join(f"  - {n}" for n in missing)
    )


def test_registered_data_directive_floors_exist():
    """The pre-registered floors the registry names for the one data-directive
    (flip_recommended) must actually exist and be sane — a registry that points at
    a deleted floor is itself a green-over-degenerate."""
    from trinity_local.me.holdout_scorer import (
        COVERAGE_FLOOR,
        MIN_DISCORDANT_PAIRS,
        N_C_FLOOR,
    )

    assert 0 < COVERAGE_FLOOR <= 1
    assert N_C_FLOOR >= 1
    assert MIN_DISCORDANT_PAIRS >= 1
    # and they're named in the checklist so the registry stays truthful
    registry = _CHECKLIST.read_text(encoding="utf-8")
    assert "COVERAGE_FLOOR" in registry and "N_C_FLOOR" in registry
