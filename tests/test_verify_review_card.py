"""What READ shows a human must be worth the step it adds.

`verify`'s most common answer is READ — "a human must look at this" — which
ADDS a review step rather than removing one. Until 2026-09-13 the command
printed the whole result object and the reviewers' reasoning was not in it at
all: the `why` every reviewer sends was parsed and discarded, surviving only as
a truncated `raw_tail`. So the product told a user to go read something and
handed them nothing to read.

Council 595c6e34, agreed by all three members: "Generic READ output does not
justify the review burden" and "READ should surface concrete concerns tied to
code."

The load-bearing negative case is the last class here. When nobody raised a
concern, the card must SAY SO rather than manufacture something to justify the
step — an empty card is a real answer and the honest one.
"""
from __future__ import annotations

from trinity_local.commands.verify import render_card


def _out(triage="READ", reason="r", reads=None, criteria=None, kernel=None):
    return {
        "triage": triage, "reason": reason,
        "kernel": kernel or {"ran": False},
        "panel": {"ran": bool(reads), "reads": reads or []},
        "criteria": criteria or [{"id": "c1", "statement": "The fix is complete."}],
    }


def _read(provider, lab, votes, why="", error=None, raw_tail=""):
    return {"provider": provider, "lab": lab, "votes": votes, "passed": all(votes.values()),
            "why": why, "error": error, "raw_tail": raw_tail}


class TestConcernsReachTheHuman:
    def test_each_doubters_own_words_are_shown(self):
        card = render_card(_out(reads=[
            _read("claude", "anthropic", {"c1": False}, why="b is left unguarded on the second path."),
            _read("codex", "openai", {"c1": False}, why="the b-is-None case still raises."),
            _read("antigravity", "google", {"c1": True}),
        ]))
        assert "b is left unguarded on the second path." in card
        assert "the b-is-None case still raises." in card
        assert "anthropic" in card and "openai" in card

    def test_the_concern_is_tied_to_its_criterion(self):
        card = render_card(_out(
            reads=[_read("claude", "anthropic", {"c1": False}, why="incomplete.")],
            criteria=[{"id": "c1", "statement": "Every affected site is addressed."}]))
        assert "[c1]" in card
        assert "Every affected site is addressed." in card, (
            "a concern without its requirement is not actionable")

    def test_a_reviewer_that_passed_is_not_listed_as_a_doubter(self):
        card = render_card(_out(reads=[
            _read("claude", "anthropic", {"c1": True}, why="looks right"),
            _read("codex", "openai", {"c1": False}, why="the real concern"),
        ]))
        assert "the real concern" in card
        assert "looks right" not in card


class TestNoManufacturedConcerns:
    """The honest empty case. If the card invents a concern to justify the step,
    the step becomes noise and the product trains users to ignore it."""

    def test_unanimous_pass_says_nothing_was_flagged(self):
        card = render_card(_out(reads=[
            _read("claude", "anthropic", {"c1": True}),
            _read("codex", "openai", {"c1": True}),
            _read("antigravity", "google", {"c1": True}),
        ]))
        assert "No reviewer raised a concern" in card
        assert "criterion/criteria drew a concern" not in card

    def test_it_still_does_not_call_that_a_pass(self):
        """No concern is not approval — the card must not imply the change is fine."""
        card = render_card(_out(reads=[_read("claude", "anthropic", {"c1": True})]))
        # assert the MEANING, not a phrase I guessed at. The card says
        # "Nothing here is a substitute for reading the change" — an earlier
        # version of this assertion looked for wording the card never used and
        # failed for that reason rather than for the behaviour it guards.
        assert "substitute for reading" in card
        assert "nothing was flagged" in card


class TestDegradationIsVisible:
    def test_a_lost_reviewer_is_named_not_hidden(self):
        card = render_card(_out(reads=[
            _read("claude", "anthropic", {"c1": False}, why="x"),
            _read("codex", "openai", {}, error="unparseable vote"),
        ]))
        assert "1 of 2 reviewers voted" in card
        assert "unparseable vote" in card, (
            "a council that silently lost a member reads as a complete one")

    def test_a_red_test_leads_the_card(self):
        card = render_card(_out(triage="STOP", reason="a relevant test is red",
                                kernel={"ran": True, "relevant": True, "green": False}))
        assert card.splitlines()[0].startswith("STOP")
        assert "tests: red" in card

    def test_an_irrelevant_test_says_so(self):
        card = render_card(_out(kernel={"ran": True, "relevant": False, "green": True}))
        assert "does NOT exercise the changed files" in card

    def test_a_doubter_with_no_why_is_marked_not_dropped(self):
        card = render_card(_out(reads=[
            _read("claude", "anthropic", {"c1": False}, why="", raw_tail="...some raw output")]))
        assert "no reason given" in card


class TestEvidenceFirst:
    """VeriHarness-style evidence record: what was run leads; what nothing ran is listed."""

    def test_each_test_run_is_listed_with_its_result(self):
        card = render_card(_out(kernel={"ran": True, "relevant": True, "green": False, "runs": [
            {"id": "T1", "command": "pytest tests/test_a.py", "green": True, "seconds": 1.2},
            {"id": "T2", "command": "pytest tests/test_b.py", "green": False, "seconds": 3.4}]}))
        assert "ok  [T1] pytest tests/test_a.py  (1.2s)" in card
        assert "RED [T2] pytest tests/test_b.py  (3.4s)" in card

    def test_criteria_no_test_runs_are_listed_as_unverified(self):
        card = render_card(_out(criteria=[{"id": "T1", "kind": "test", "statement": "suite"},
                                          {"id": "J1", "kind": "judgment", "statement": "No duplicated spec."}]))
        assert "unverified: 1 criteria nothing tested against this change" in card and "[J1] No duplicated spec." in card
        assert "[T1]" not in card.split("unverified:")[1]

    def test_verify_returns_the_unverified_ids(self, tmp_path):
        from trinity_local import verify as v
        out = v.verify([{"id": "T1", "kind": "test", "statement": "s", "command": "true"},
                        {"id": "J1", "kind": "judgment", "statement": "j"}], "", "", tmp_path, run_panel=False)
        assert out["unverified"] == ["J1"]
