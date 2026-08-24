"""res_026: the core gate needs an axis its bits ruler cannot see.

`score_bits` is content-blind, and that is measured rather than suspected —
hq_083 fed it a word-shuffled twin of an artifact and the SHUFFLE won, 148-52,
p<1e-4. So a candidate asserting the OPPOSITE of the incumbent in the same
vocabulary prices identically and would be admitted.

hq_104 licensed the fix: on real `core.md` versus a coherent inversion of the
same length and register — written by the judge itself, so the handicap ran
against the real document — a local 27B preferred the real one 54/60 = 90%,
CI [0.82, 0.98], with a 53% position rate against a 70% bias bar.

The property these tests pin is narrow and deliberate: stance can only make the
gate STRICTER, and a missing judge must change nothing.

Mutation-proven 2026-08-17: deleting `ok = ok and stance_ok` REDs
`test_stance_veto_refuses_a_candidate_bits_would_admit`.

COMPLETED 2026-08-24 (res_079). The content-blindness above was diagnosed
correctly and treated with a VETO, which can only reject. It could not stop the
bits ruler from PROMOTING junk, and a missing judge changes nothing by design —
so the promotion path stayed open and admitted an OAuth error into core.md one
day after this fix landed. The gate now fails closed on the promoter itself;
these tests run against the dormant path via the fixture below.
"""
from __future__ import annotations

import pytest

from trinity_local import core_gate


@pytest.fixture(autouse=True)
def _dormant_ruler(monkeypatch):
    """The stance axis runs only when the bits ruler is allowed to admit.

    res_079 measured that ruler as length-confounded and shipped the gate
    fail-closed, so in production stance is never consulted — there is nothing
    to veto when nothing is promoted. These tests keep the mechanism honest for
    the day a ruler passes a length-matched control and the flag clears.
    """
    monkeypatch.setattr(core_gate, "LENGTH_CONFOUNDED_RULER", False)


def test_no_judge_reachable_returns_none_rather_than_a_verdict(monkeypatch):
    """A judge that cannot be reached must not be read as approval OR refusal."""
    monkeypatch.setattr(core_gate, "STANCE_URL", "http://127.0.0.1:1/definitely-not-serving")
    assert core_gate.stance_prefers_candidate("cand", "incumbent", ["a", "b", "c", "d"]) is None


def test_empty_heldout_returns_none(monkeypatch):
    assert core_gate.stance_prefers_candidate("cand", "incumbent", []) is None


def test_stance_veto_refuses_a_candidate_bits_would_admit(monkeypatch, tmp_path):
    """THE POINT OF THE WHOLE FIX. Bits say admit; stance says no; gate refuses."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "core.md").write_text("incumbent core", encoding="utf-8")
    monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
    monkeypatch.setattr(core_gate, "heldout_texts", lambda: [f"t{i}" for i in range(50)])
    monkeypatch.setattr(core_gate, "score_bits", lambda texts, art: (100.0, "stub"))
    v = core_gate.propose_core("a candidate that prices identically",
                               stance_fn=lambda c, i, t: (False, "judge prefers the incumbent 2/9"))
    assert not v.admitted, "bits tied, stance objected — the gate must refuse"
    assert "stance" in v.reason


def test_stance_agreement_leaves_a_bits_admission_intact(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "core.md").write_text("incumbent core", encoding="utf-8")
    monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
    monkeypatch.setattr(core_gate, "heldout_texts", lambda: [f"t{i}" for i in range(50)])
    monkeypatch.setattr(core_gate, "score_bits", lambda texts, art: (100.0, "stub"))
    v = core_gate.propose_core("a candidate",
                               stance_fn=lambda c, i, t: (True, "judge prefers the candidate 8/9"))
    assert v.admitted and "stance" in v.reason


def test_a_missing_judge_changes_nothing(monkeypatch, tmp_path):
    """Graceful degradation, and the direction matters: no judge means the gate
    behaves EXACTLY as it did before this fix — not that everything is admitted."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "core.md").write_text("incumbent core", encoding="utf-8")
    monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
    monkeypatch.setattr(core_gate, "heldout_texts", lambda: [f"t{i}" for i in range(50)])

    monkeypatch.setattr(core_gate, "score_bits", lambda texts, art: (100.0, "stub"))
    assert core_gate.propose_core("cand").admitted, "bits admit, no judge -> admitted"

    calls = {"n": 0}

    def worse(texts, art):
        calls["n"] += 1
        return (100.0 if calls["n"] > 1 else 10_000.0, "stub")

    monkeypatch.setattr(core_gate, "score_bits", worse)
    assert not core_gate.propose_core("cand").admitted, "bits refuse, no judge -> still refused"


def test_stance_is_never_consulted_when_bits_already_refuse(monkeypatch, tmp_path):
    """Cost discipline: the judge is ~17s per prompt, so a candidate already
    refused on bits must not pay for it."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "core.md").write_text("incumbent core", encoding="utf-8")
    monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
    monkeypatch.setattr(core_gate, "heldout_texts", lambda: [f"t{i}" for i in range(50)])
    calls = {"n": 0}

    def worse(texts, art):
        calls["n"] += 1
        return (10_000.0 if calls["n"] == 1 else 100.0, "stub")

    monkeypatch.setattr(core_gate, "score_bits", worse)

    def boom(c, i, t):
        pytest.fail("stance judge consulted on an already-refused candidate")

    assert not core_gate.propose_core("cand", stance_fn=boom).admitted


class TestTheGateFailsClosedWhenItCannotScore:
    """An unreadable corpus is a fact about THIS RUN, not about the incumbent.

    A `lens --deep` run on 2026-08-18 had its OAuth expire mid-flight. The distill
    step produced the 72-byte string "Failed to authenticate: OAuth session expired
    and could not be refreshed", and the SAME auth failure emptied the held-out
    corpus — so `propose_core` took its cold-install branch and admitted that string
    as the founder's core identity. The chairman reads core.md FIRST (res_062).

    The ruler was never the problem: with a real corpus present it rejects that
    string outright (6104 bits against the incumbent's 5087). The hole was a
    fail-OPEN branch in a function whose docstring promises it FAILS CLOSED, resting
    on the claim that "no corpus means the incumbent was never earned either" —
    false whenever an incumbent exists.

    Mutation-proven 2026-08-18: flipping that branch back to CoreVerdict(True, ...)
    REDs test_incumbent_is_kept_when_the_ruler_cannot_score.
    """

    def test_incumbent_is_kept_when_the_ruler_cannot_score(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "core.md").write_text("a real, earned core", encoding="utf-8")
        monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
        monkeypatch.setattr(core_gate, "heldout_texts", lambda: [])
        # A PLAUSIBLE candidate, deliberately. This test names the
        # unreadable-corpus branch, and the OAuth string it used to carry now
        # trips the provider-error tripwire first (res_079) — still refused, but
        # by a different mechanism, which would leave this branch untested under
        # a name that claims otherwise.
        v = core_gate.propose_core(
            "you reason from the constraint that actually binds, and you would "
            "rather hold a runnable artifact than an explanation of one")
        assert not v.admitted, "an unscoreable candidate must not replace an earned core"
        assert "incumbent EXISTS" in v.reason

    def test_a_genuine_first_build_still_writes(self, monkeypatch, tmp_path):
        """The fix must not brick a cold install — with no incumbent there is
        nothing to protect, and refusing would leave the user with no core at all."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(core_gate, "core_path", lambda: tmp_path / "core.md")
        monkeypatch.setattr(core_gate, "heldout_texts", lambda: [])
        v = core_gate.propose_core("a real first core")
        assert v.admitted and "first write" in v.reason
