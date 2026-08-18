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
    # This used to assert the word "gold". The tier was relabelled PROXY on
    # 2026-08-06 (council_a3196cfdb40680a5, unanimous) because its labels are
    # COMPOSED by a model and reproduce at 45%. The guard's intent is unchanged
    # and is what matters: the stamp must still name the tier that outranks
    # silver, so a reader can never take a chairman-opinion cell for the
    # behavioural verdict. Asserting the concept, not the retired vocabulary.
    assert "behavioural ledger" in cal.lower(), (
        "the silver stamp no longer names the behavioural tier as the verdict "
        "tier — a chairman-opinion cell could be read as the verdict"
    )


def test_separate_floor_from_gold(tmp_path):
    """SILVER_MIN_N is its own constant, strictly above gold's MIN_TALLY_N —
    reusing one constant for two evidence units was rejected by the council."""
    from trinity_local.disagreement_ledger import MIN_TALLY_N

    assert SILVER_MIN_N > MIN_TALLY_N


def test_behavioural_tier_caveat_reaches_every_surface(tmp_path, monkeypatch):
    """The PROXY caveat must RENDER, not merely exist as a constant.

    The gold->proxy relabel (council-ratified, unanimous) introduced
    BEHAVIOURAL_TIER_CAVEAT and wired it to nothing: the CLI tally, the MCP trust
    payload and the launchpad card all kept shipping per-model win rates as a
    settled behavioural verdict, while CLAUDE.md said "do not quote these numbers
    as settled without that caveat" and the sibling SILVER tier carried its
    calibration on every surface. Producer-asserted, consumer-unverified — the
    exact class this repo names. The caveat now rides aggregate_tally's dict, so
    every consumer inherits it; this pins all three ends.
    """
    from trinity_local.commands.trust import _tally_lines
    from trinity_local.disagreement_ledger import BEHAVIOURAL_TIER_CAVEAT, aggregate_tally

    agg = aggregate_tally([], {})
    assert agg.get("caveat") == BEHAVIOURAL_TIER_CAVEAT, (
        "aggregate_tally no longer emits the caveat — summary.json, `trust --json` "
        "and the MCP payload all lose it at once"
    )

    # CLI, withheld branch (the common case on a thin ledger)
    assert "PROXY" in _tally_lines(agg)

    # CLI, rendered-tally branch
    rendered = _tally_lines({
        **agg, "tally_trustworthy": True, "k3_in_band": True, "k4_discriminates": True,
        "resolved": 128,
        "records": {"claude · opus · 4.8": {"w": 28, "l": 13, "win_rate": 0.683,
                                            "ci": [0.53, 0.81], "ci_excludes_half": True}},
    })
    assert "PROXY" in rendered, "the rendered tally dropped the caveat"
    assert rendered.index("PROXY") < rendered.index("which "), (
        "the caveat must precede the numbers, not trail them"
    )

    # Launchpad card payload
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    led = tmp_path / "disagreement_ledger"
    led.mkdir(parents=True)
    (led / "summary.json").write_text(json.dumps(agg), encoding="utf-8")
    from trinity_local.launchpad_data import _load_trust_data

    card = _load_trust_data()
    if card is not None:          # None on a corpus with no cross-provider splits
        assert card.get("caveat"), "the launchpad trust card dropped the caveat"

    # And the template actually binds it
    from trinity_local.launchpad_template import render_launchpad_html

    assert "trustData.caveat" in render_launchpad_html(page_data={}), (
        "the trust card template no longer binds trustData.caveat — the payload "
        "carries it but nothing paints it"
    )


def test_silver_excludes_dream_mined_synthesis_councils(tmp_path, monkeypatch):
    """SILVER must not credit mode='synthesis_only' councils.

    The behavioural tier excludes them by name — "authored with hindsight over the
    same transcripts, so crediting it would be circular" (load_disagreements). The
    silver tier stamped itself "computed from your own live council labels
    (prosecutor era)" and had NO such filter: measured 2026-08-07, 6 of 53 credited
    claims came from 4 synthesis_only councils. The escalation is what made it
    urgent: `lens --deep` mines hundreds of virtual councils and now stamps member
    identities on them, so the next deep build would have flooded the silver cells
    with hindsight-authored labels under a stamp promising live ones.
    """
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    out = tmp_path / "council_outcomes"
    out.mkdir(parents=True)

    def _council(cid, mode):
        doc = {
            "council_run_id": cid,
            "created_at": "2026-08-01T00:00:00+00:00",
            "member_results": [
                {"provider": "claude", "model": "claude-opus-5",
                 "metadata": {"effort": "high"}, "output_text": "A"},
                {"provider": "codex", "model": "gpt-5.5",
                 "metadata": {"effort": "xhigh"}, "output_text": "B"},
            ],
            "routing_label": {"winner": "claude", "disagreed_claims": [
                {"claim": "c", "providers_for": ["claude"],
                 "providers_against": ["codex"], "resolution": "claude survives"}
            ]},
        }
        if mode:
            doc["metadata"] = {"mode": mode}
        (out / f"{cid}.json").write_text(json.dumps(doc), encoding="utf-8")

    _council("council_live", None)
    _council("council_dream", "synthesis_only")

    sv = silver_tally(str(tmp_path))
    assert sv["claims_credited"] == 1, (
        f"expected only the LIVE council to be credited, got "
        f"{sv['claims_credited']} — a synthesis_only (dream-mined) council is "
        "feeding the tier that stamps itself 'your own live council labels'"
    )


class TestCaveatFailsClosedOnLegacySummary:
    """A summary.json written BEFORE the caveat rode the aggregate must still
    render the caveat on every surface.

    The original guard was vacuous: it wrote a summary produced by
    aggregate_tally() in the same test — the only case where the "caveat" key is
    present — so it could never fail on a real legacy file. Meanwhile the live
    ledger (built 2026-08-02) had no such key, and all three consumers degraded
    via `or ""`, so `trinity-local trust`, the MCP trust payload and the
    launchpad card each rendered per-model verdicts with NO caveat at all. That
    is precisely the state the fix claimed to have closed, and it is the
    producer-asserted / consumer-unverified shape this repo keeps re-shipping.

    A council unanimously required the caveat on every per-model number, so this
    disclosure must fail CLOSED.
    """

    @staticmethod
    def _legacy_summary() -> dict:
        """What a pre-fix summary.json actually looks like: a trustworthy tally,
        no caveat key."""
        return {
            "built_at": "2026-08-02T00:00:00Z", "resolved": 128, "records": 128,
            "tally_trustworthy": True, "k3_in_band": True, "k4_discriminates": True,
            "k3_chairman_agreement": 0.661,
            "tally": {"claude · opus · 4.8": {"wins": 28, "losses": 13}},
        }

    def test_mcp_payload_carries_caveat_without_the_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local import mcp_server
        from trinity_local.disagreement_ledger import BEHAVIOURAL_TIER_CAVEAT, _ledger_dir

        d = _ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps(self._legacy_summary()),
                                        encoding="utf-8")

        out = mcp_server._load_trust_summary()
        assert out.get("caveat") == BEHAVIOURAL_TIER_CAVEAT, (
            "the MCP trust payload handed an agent per-model rates with no "
            "caveat — the agent cannot see that the disclosure is missing"
        )

    def test_caveat_text_no_longer_claims_the_retracted_attenuation_rescue(self):
        """The retraction swept CLAUDE.md but not the shipped string.

        The attenuation argument ("noise biases toward 50%, so rates are
        understated") requires NON-DIFFERENTIAL noise, which is unmeasured — the
        per-model flip rate runs 0% to 27%. It must not be told to users as fact,
        least of all inside the honesty disclosure itself.
        """
        from trinity_local.disagreement_ledger import BEHAVIOURAL_TIER_CAVEAT

        low = BEHAVIOURAL_TIER_CAVEAT.lower()
        assert "understated than inflated" not in low, (
            "the shipped caveat still asserts the retracted attenuation rescue")
        assert "unmeasured" in low, (
            "the caveat should say the bias DIRECTION is unmeasured")


class TestCrossProviderRequiresAnActualSplit:
    """The cross-provider gate must attest a SPLIT, not co-occurrence.

    It tested `len(providers_for | providers_against) >= 2`, so a claim two labs
    jointly asserted with NOBODY arguing against read as a cross-provider
    disagreement — agreement admitted as disagreement. Measured on the live corpus:
    6 of 357 admitted patterns (1.7%) had an empty `against` side, each crediting a
    win with no opposing side to lose. This is the repo's signature shape: a green
    that gates on a proxy instead of the invariant it attests.
    """

    @staticmethod
    def _pattern(for_, against):
        from trinity_local.disagreement_ledger import DisagreementPattern

        return DisagreementPattern(
            claim_id="c1", council_id="k1", at="2026-08-07T00:00:00Z",
            claim="x", why_matters="", providers_for=for_,
            providers_against=against, chairman_winner="anthropic")

    def test_two_labs_agreeing_is_not_a_disagreement(self):
        p = self._pattern(["google", "openai"], [])
        assert p.is_cross_provider is False, (
            "two labs jointly asserting a claim with nobody against was admitted "
            "as a cross-provider DISAGREEMENT")

    def test_nobody_for_is_not_a_disagreement(self):
        assert self._pattern([], ["google", "openai"]).is_cross_provider is False

    def test_one_lab_arguing_with_itself_is_not_cross_provider(self):
        assert self._pattern(["anthropic"], ["anthropic"]).is_cross_provider is False

    def test_a_real_split_still_admits(self):
        assert self._pattern(["anthropic"], ["google"]).is_cross_provider is True
        assert self._pattern(["anthropic", "google"],
                             ["openai"]).is_cross_provider is True
