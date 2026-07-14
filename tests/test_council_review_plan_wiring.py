"""The council-review-plan reflex stays wired (founder: "ensure it's used").
Three wires, each a ratchet: the skill exists with its load-bearing sections,
CLAUDE.md's agent rule points at it, and the amendment ledger parses with its
seed receipts intact. Mutation: delete any wire → RED."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestCouncilReviewPlanWiring:
    def test_skill_exists_with_loadbearing_sections(self):
        p = REPO / ".claude/skills/council-review-plan/SKILL.md"
        assert p.exists(), "the premise-review skill is gone"
        s = p.read_text(encoding="utf-8")
        for section in ("MEASURED FACTS", "pre-registered falsifier",
                        "amendment-ledger.jsonl", "Append-only"):
            assert section in s, f"skill lost its {section!r} clause"

    def test_claude_md_names_the_skill(self):
        s = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        assert "council-review-plan" in s, (
            "CLAUDE.md no longer routes design decisions to the premise-review "
            "skill — the reflex is unwired"
        )
        assert "amendment-ledger.jsonl" in s

    def test_amendment_ledger_parses_and_keeps_seeds(self):
        p = REPO / "internal/amendment-ledger.jsonl"
        assert p.exists(), "amendment ledger missing"
        rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(rows) >= 4, "seed receipts were dropped — the ledger is append-only"
        for r in rows:
            for field in ("id", "council_id", "amendment", "adopted",
                          "prediction", "resolution"):
                assert field in r, f"ledger row {r.get('id')} missing {field!r}"
        assert all(r["resolution"] in ("pending", "held", "failed") for r in rows)

    def test_trinity_discipline_points_here(self):
        s = (REPO / ".claude/skills/trinity-discipline/SKILL.md").read_text(encoding="utf-8")
        assert "council-review-plan" in s
