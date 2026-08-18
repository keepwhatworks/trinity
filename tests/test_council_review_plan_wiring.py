"""The council-review-plan reflex stays wired (founder: "ensure it's used").
Three wires, each a ratchet: the skill exists with its load-bearing sections,
CLAUDE.md's agent rule points at it, and the amendment ledger parses with its
seed receipts intact. Mutation: delete any wire → RED."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class TestCouncilReviewPlanWiring:
    def test_both_mined_lists_exist_and_CLAUDE_md_points_at_them(self):
        """A list nothing reads is the producer-asserted / consumer-unverified
        shape this repo keeps shipping — so the CONSUMER side is ratcheted, not
        just the file's existence.

        Two lists, two jobs, both mined: the failure-modes list is checked
        against a design before building, and the working-with-founder list is
        read before substantive work. CLAUDE.md is where an agent in this repo
        looks for behavioural rules, so the pointer lives there and this asserts
        it survives.
        """
        skill = (REPO / ".claude" / "skills" / "council-review-plan" / "SKILL.md").read_text()
        for heading in ("## The known-failure-modes list",
                        "## Working with this founder"):
            assert heading in skill, f"mined list gone from the skill: {heading}"
        # Each list must carry provenance, or "mined" is an unbacked adjective.
        assert "[incidents" in skill and "catches" in skill, (
            "the failure-modes list lost its [incidents N / catches M] provenance fields"
        )
        claude = (REPO / "CLAUDE.md").read_text()
        assert "known-failure-modes list" in claude and "Working with this founder" in claude, (
            "CLAUDE.md no longer points at the mined lists — an agent reading only "
            "CLAUDE.md would never learn they exist, which is the whole failure mode"
        )

    def test_skill_file_is_TRACKED_not_merely_present(self):
        """Existence is not the contract; being IN THE REPO is.

        Found 2026-08-12: `.gitignore` ignores `.claude/`, and two skill files
        stayed tracked only because an ignore does not apply to an already-
        tracked file. council-review-plan was created later and was never
        added, so the known-failure-modes list lived untracked for its whole
        life -- absent from a fresh clone, never syncable -- while every test
        here passed by reading the local copy. A ratchet that reads the working
        tree cannot tell a committed file from an ignored one, which is this
        repo's producer-asserted / consumer-unverified shape applied to its own
        method contract.
        """
        import subprocess
        out = subprocess.run(
            ["git", "ls-files", "--error-unmatch",
             ".claude/skills/council-review-plan/SKILL.md"],
            cwd=str(REPO), capture_output=True, text=True)
        assert out.returncode == 0, (
            "the premise-review skill is NOT tracked by git — it exists on this "
            "machine only. `git add -f` it; `.gitignore` negates `.claude/` for "
            "skill files precisely so this cannot recur silently."
        )

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
        assert all(r["resolution"] in ("pending", "held", "failed", "dropped") for r in rows)
        # Schema drift the guard used to tolerate silently: the test accepted
        # "dropped" while the skill documented only three states. Now both list
        # four, and this asserts they agree.
        skill = (REPO / ".claude/skills/council-review-plan/SKILL.md").read_text(encoding="utf-8")
        for state in ("pending", "held", "failed"):
            assert f'"{state}"' in skill, f"skill no longer documents the {state!r} resolution"

    def test_ledger_schema_supports_declined_proposals(self):
        """The denominator guard (2026-08-06, council_a3196cfdb40680a5).

        For 80 rows this ledger recorded ONLY adoptions, so "N of M held" measured
        our own adoption filter rather than the councils — per-model hold rates read
        96/92/100% and discriminated nothing. The fix is structural: declined
        proposals must be recordable, with a reason, carrying the same `prediction`
        field (a declined proposal that would have HELD is the most expensive lesson
        this file can hold).

        This pins the CONTRACT, not a population: it must be legal to write
        adopted:false with declined_because, and the skill must instruct it. It does
        NOT require declined rows to exist yet — retroactive reconstruction was left
        unresolved by the council, so back-filling is not assumed."""
        skill = (REPO / ".claude/skills/council-review-plan/SKILL.md").read_text(encoding="utf-8")
        assert "declined_because" in skill, (
            "the ledger template no longer carries declined_because — declined "
            "proposals are unrecordable again and the denominator is lost"
        )
        assert "DECLINE" in skill.upper(), "the skill no longer instructs recording declined proposals"

        p = REPO / "internal/amendment-ledger.jsonl"
        rows = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        declined = [r for r in rows if r.get("adopted") is False]
        for r in declined:
            assert r.get("declined_because"), (
                f"declined row {r.get('id')} has no declined_because — a refusal "
                "without its reason teaches nothing"
            )
            assert "prediction" in r, (
                f"declined row {r.get('id')} has no prediction — then it cannot ever "
                "be scored, which is the entire point of recording it"
            )

    def test_trinity_discipline_points_here(self):
        s = (REPO / ".claude/skills/trinity-discipline/SKILL.md").read_text(encoding="utf-8")
        assert "council-review-plan" in s


class TestIdentityTripleStamping:
    """The behavioral stream stamps the #239 identity triple forward
    (2026-07-14): council members carry effort in metadata; the disagreement
    ledger joins member models per claim. Source-level pins (the scorer-guard
    pattern): deleting either stamp reds."""

    def test_council_runner_stamps_member_effort(self):
        src = (REPO / "src/trinity_local/council_runner.py").read_text(encoding="utf-8")
        idx = src.find('"stdout": execution.stdout,')
        assert idx != -1
        window = src[idx:idx + 300]
        assert '"effort"' in window and "_effective_effort" in window, (
            "council members no longer stamp effort — behavioral data loses "
            "the third identity leg from here on"
        )

    def test_ledger_joins_member_models(self):
        src = (REPO / "scripts/disagreement_ledger.py").read_text(encoding="utf-8")
        assert "member_models" in src, "the ledger dropped the model join"
