#!/usr/bin/env python3
"""Seed a synthetic, schema-correct ~/.trinity so the browser_smoke gate can run
PII-FREE — no founder corpus required.

Why this exists. `scripts/browser_smoke.py` drives the launchpad through ~36
surfaces, but it historically only ran against the founder's REAL ~/.trinity
(processing + screenshotting real prompts — a privacy hazard, and unrunnable in CI
or by a contributor). v1.7.372 made browser_smoke honor $TRINITY_HOME; this script
is the other half: it populates a $TRINITY_HOME with FAKE-but-correctly-shaped data
that lights the data-driven surfaces (ELO chart, routing cheat-sheet, recent
councils, the live-council painkiller with disagreed_claims, topology basins +
pick→topology cross-links, memory tabs).

Usage (PII-free full gate):
    export TRINITY_HOME=$(mktemp -d)/trinity
    python scripts/seed_synthetic_home.py
    python scripts/browser_smoke.py          # serves + reads the synthetic home

What each piece feeds (verified 2026-06-09): councils with `provider_scores` →
ELO (S1) + routing table (S3, n>=2 task_types); a council with `disagreed_claims`
→ painkiller (S35); topics.json basins whose ids match picks.json → topology +
pick→topology (S19/21/22/26); core.md >50 chars → memory viewer (S14b);
me/lenses.json → taste card + its "Copy as text" button (S4). NOT covered (needs a
graph the small synthetic can't produce, by design): node-dimming (S23/S34 — the
highlight dims non-neighbor nodes, but the orthogonal synthetic basins carry no
co-occurrence edges, so there's no neighborhood to contrast). The integrated
`--seed-synthetic` browser_smoke FLAG is a separate, founder-owned workflow
decision — this is just the standalone seeder.
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# Schema-correct seeding goes through the real dataclasses + writers so the shapes
# can't drift from production (a hand-rolled JSON blob would rot the moment a field
# is added). Imports are deferred to here so `--help`-style introspection is cheap.
os.environ.setdefault("TRINITY_AUTOSCAN_DISABLED", "1")

# Councils: repeat task_types so >=2 clear the routing cheat-sheet's n>=2 floor;
# known models (not "?") + non-web-era slugs so the ELO chart plots; one carries a
# real disagreement so the live-council painkiller renders its differentiator.
_COUNCILS = [
    ("design", "claude", "codex", "claude-opus-4-8", "gpt-5.5", True),
    ("design", "claude", "antigravity", "claude-opus-4-8", "gemini-3.1-pro", False),
    ("debug", "codex", "claude", "gpt-5.5", "claude-opus-4-8", False),
    ("debug", "codex", "antigravity", "gpt-5.5", "gemini-3.1-pro", False),
    ("refactor", "claude", "codex", "claude-opus-4-8", "gpt-5.5", False),
]

_BASINS_SPEC = [
    ("Design", ["design", "arch"]),
    ("Debug", ["debug", "fix"]),
    ("Refactor", ["refactor", "clean"]),
    ("Testing", ["test", "guard"]),
]


def _unit(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def seed(home: Path) -> dict[str, int]:
    """Populate `home` with synthetic state and render the portal/live pages.
    Returns a small count summary. `home` is taken as-is (caller sets
    $TRINITY_HOME); we don't mkdir the root here — the writers do."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.personal_routing import freeze_routing_to_disk

    for i, (tt, win, ru, wm, rm, disagree) in enumerate(_COUNCILS):
        members = [
            CouncilMemberResult(provider=win, model=wm, output_text=f"Synthetic {win} answer #{i}. " * 15),
            CouncilMemberResult(provider=ru, model=rm, output_text=f"Synthetic {ru} answer #{i}. " * 15),
        ]
        disagreed = (
            [{"claim": f"tradeoff {i}", "providers_for": [win],
              "providers_against": [ru], "why_matters": "tenancy isolation"}]
            if disagree else []
        )
        label = CouncilRoutingLabel(
            winner=win, runner_up=ru, confidence="high", task_type=tt,
            # provider_scores is what populates by_task_type (the routing cheat-sheet)
            # AND the ELO head-to-head — without it those surfaces stay empty.
            provider_scores={win: {"overall": 0.82}, ru: {"overall": 0.61}},
            agreed_claims=[f"agreed point {i}"], disagreed_claims=disagreed,
        )
        save_council_outcome(CouncilOutcome(
            council_run_id=f"council_syn{i:02d}", bundle_id=f"council_syn{i:02d}",
            task_cluster_id=f"cluster_{tt}", primary_provider=win, primary_model=wm,
            winner_provider=win, winner_model=wm, agreement_score=0.7,
            metadata={"task_text": f"Synthetic question {i} about {tt}?"},
            member_results=members, synthesis_prompt="Review the answers.",
            synthesis_output=f"Synthesis {i}: {win} wins for {tt}.",
            routing_label=label, created_at=f"2026-06-0{(i % 8) + 1}T00:00:00+00:00",
        ))

    freeze_routing_to_disk()  # scoreboard/routing.json from the councils

    basins = []
    for j, (label_text, terms) in enumerate(_BASINS_SPEC):
        centroid = [0.0, 0.0, 0.0, 0.0]
        centroid[j] = 1.0
        basins.append({
            "id": f"b0{j}", "centroid": _unit(centroid), "size": 20 - j * 3,
            "label": label_text, "top_terms": terms,
            "representatives": [{"id": f"r{j}", "snippet": f"a {label_text.lower()} prompt"}],
        })
    memories = home / "memories"
    memories.mkdir(parents=True, exist_ok=True)
    (memories / "topics.json").write_text(json.dumps({"basins": basins}), encoding="utf-8")

    scoreboard = home / "scoreboard"
    scoreboard.mkdir(parents=True, exist_ok=True)
    # picks.json basin ids match topics.json → pick→topology identity cross-links.
    (scoreboard / "picks.json").write_text(json.dumps({
        "b00": {"winner": "claude", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": ["council_syn00"]},
        "b01": {"winner": "codex", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": ["council_syn02"]},
    }), encoding="utf-8")

    (memories / "lens.md").write_text(
        "# Lens\n\n## Tensions\n\n- **concrete vs abstract**: leans concrete\n"
        "- **action vs description**: leads with the action\n", encoding="utf-8")

    # me/lenses.json backs the launchpad TASTE card ("YOUR TASTE, DISTILLED") +
    # its "Copy as text" share button (browser_smoke Surface 4). Without it,
    # _load_taste_lenses() returns None, the card shows the "Run lens" empty CTA,
    # the copy button never renders, and S4 hard-FAILS on the synthetic home —
    # the value-prop card stays dark in the PII-free gate. Rows use the real
    # LensPair schema (pole_a/pole_b/failure_a/failure_b/...); the poles mirror the
    # lens.md tensions above, basins_spanned references the seeded basin ids.
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "lenses.json").write_text(json.dumps({"lenses": [
        {"pole_a": "concrete", "pole_b": "abstract", "failure_a": "vague",
         "failure_b": "brittle", "tension_decisions": [], "dual_evidence": {},
         "basins_spanned": ["b00", "b01"], "verdict": "accepted", "horizon": "tactical"},
        {"pole_a": "action", "pole_b": "description", "failure_a": "reckless",
         "failure_b": "inert", "tension_decisions": [], "dual_evidence": {},
         "basins_spanned": ["b02", "b03"], "verdict": "accepted", "horizon": "strategic"},
    ]}), encoding="utf-8")
    (me / "orderings.json").write_text(json.dumps({"orderings": []}), encoding="utf-8")
    (memories / "vocabulary.md").write_text(
        "# Vocabulary\n\n## Anchors\n- ship\n- guard\n- basin\n", encoding="utf-8")
    # core.md must exceed the viewer's >50-char "real body" check (S14b).
    (home / "core.md").write_text(
        "# Core\n\nYou prefer concrete, action-first answers and lead with the "
        "decision before the rationale. You distrust abstraction without a worked "
        "example.\n", encoding="utf-8")

    write_portal_html()
    write_live_council_page()

    return {"councils": len(_COUNCILS), "basins": len(basins), "picks": 2}


def main() -> int:
    env = os.environ.get("TRINITY_HOME")
    if not env:
        print("error: set $TRINITY_HOME to a throwaway dir first, e.g.:")
        print("    export TRINITY_HOME=$(mktemp -d)/trinity")
        print("    python scripts/seed_synthetic_home.py")
        return 2
    home = Path(env).expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    counts = seed(home)
    print(f"seeded synthetic home at {home}: "
          f"{counts['councils']} councils, {counts['basins']} basins, {counts['picks']} picks")
    print("now run:  TRINITY_HOME=%s python scripts/browser_smoke.py" % home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
