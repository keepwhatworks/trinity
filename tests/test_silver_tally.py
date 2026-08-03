"""Guards for the SILVER tier (council_a5ba36c437d492f9) — chairman-adjudicated
claim-side win rates from the user's OWN live labels, opt-in only.

The council's decisive catch shaped these tests: the calibration text must NAME
ITS SOURCE (the 63% transfer was measured on the re-chaired research corpus,
not on the live labels the cells come from), the floor is SEPARATE from gold's
MIN_TALLY_N (an ~11%-nondeterministic adjudicator at n≈10 fails the floor's
purpose), and crediting is claim-SIDE, never member-vs-winner (that rule was
falsified by an 89%-vs-23% opponent-pool artifact within one family).

Mutation targets, each reds a named test: set SILVER_MIN_N=1 →
test_below_floor_cells_are_withheld; drop the family fold →
test_family_labels_fold_to_dispatch_slugs; edit the calibration wording →
test_calibration_names_its_source.
"""
from __future__ import annotations

import json
from pathlib import Path

from trinity_local.disagreement_ledger import SILVER_MIN_N, silver_tally


def _council(home: Path, cid: str, *, resolution, fors, against,
             members, n_claims: int = 1) -> None:
    d = {
        "council_run_id": cid,
        "created_at": "2026-08-01T00:00:00+00:00",
        "member_results": [
            {"provider": prov, "model": model,
             "metadata": ({"effort": eff} if eff else {})}
            for prov, model, eff in members
        ],
        "routing_label": {
            "winner": fors[0],
            "disagreed_claims": [
                {"claim": f"claim {i}", "resolution": resolution,
                 "providers_for": list(fors), "providers_against": list(against)}
                for i in range(n_claims)
            ],
        },
    }
    out = home / "council_outcomes"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{cid}.json").write_text(json.dumps(d), encoding="utf-8")


MEMBERS = [("claude", "claude-opus-5", "high"),
           ("antigravity", "gemini-3.1-pro", None)]


def test_claim_side_credit_and_floor_clearing(tmp_path):
    """SILVER_MIN_N credited claims -> the winning side's cell shows W=all,
    the losing side's shows L=all. Claim-side, not member-vs-winner."""
    _council(tmp_path, "council_a", resolution="claude",
             fors=["claude"], against=["gemini"],
             members=MEMBERS, n_claims=SILVER_MIN_N)
    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == SILVER_MIN_N
    assert sv["cells"]["claude · opus · 5"]["w"] == SILVER_MIN_N
    assert sv["cells"]["claude · opus · 5"]["l"] == 0
    assert sv["cells"]["google · pro · 3.1"]["l"] == SILVER_MIN_N


def test_below_floor_cells_are_withheld(tmp_path):
    """n = SILVER_MIN_N - 1 -> NO cell rendered; counted as withheld. The
    disqualifier lives IN the gate, not in a sibling field."""
    _council(tmp_path, "council_a", resolution="claude",
             fors=["claude"], against=["gemini"],
             members=MEMBERS, n_claims=SILVER_MIN_N - 1)
    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == SILVER_MIN_N - 1
    assert sv["cells"] == {}
    assert sv["withheld_cells"] == 2


def test_unparseable_resolutions_are_skipped_not_guessed(tmp_path):
    for i, res in enumerate(["unresolved", "both sides have merit", "", None]):
        _council(tmp_path, f"council_{i}", resolution=res,
                 fors=["claude"], against=["gemini"], members=MEMBERS)
    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == 0
    assert sv["claims_skipped"] == 4
    assert sv["cells"] == {}


def test_family_labels_fold_to_dispatch_slugs(tmp_path):
    """The chairman writes 'gemini'; the member row says 'antigravity'. Without
    the fold every web-style label reads as absent and nothing credits."""
    _council(tmp_path, "council_a", resolution="gemini",
             fors=["gemini"], against=["claude"],
             members=MEMBERS, n_claims=SILVER_MIN_N)
    sv = silver_tally(str(tmp_path))
    assert sv["cells"]["google · pro · 3.1"]["w"] == SILVER_MIN_N
    assert sv["cells"]["claude · opus · 5"]["l"] == SILVER_MIN_N


def test_identity_unknown_members_carry_no_cell(tmp_path):
    """model=None must not mint a '? · ? · ?' junk cell."""
    _council(tmp_path, "council_a", resolution="claude",
             fors=["claude"], against=["gemini"],
             members=[("claude", None, None), ("antigravity", None, None)],
             n_claims=SILVER_MIN_N)
    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == SILVER_MIN_N
    assert sv["cells"] == {}


def test_empty_home_abstains(tmp_path):
    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == 0 and sv["cells"] == {}


def test_calibration_names_its_source(tmp_path):
    """The council's decisive catch: 63% was measured on the RE-CHAIRED corpus,
    not the live labels. The stamp must say so, and must say the live labels are
    not yet calibrated — an unqualified transfer number would launder a
    research-harness figure into a product claim."""
    sv = silver_tally(str(tmp_path))
    cal = sv["calibration"]
    assert "re-chair" in cal
    assert "NOT been separately calibrated" in cal
    assert "opinion, not behaviour" in cal
    assert "gold" in cal.lower()


def test_separate_floor_from_gold(tmp_path):
    """SILVER_MIN_N is its own constant, strictly above gold's MIN_TALLY_N —
    reusing one constant for two evidence units was rejected by the council."""
    from trinity_local.disagreement_ledger import MIN_TALLY_N

    assert SILVER_MIN_N > MIN_TALLY_N
