"""The judge-alignment validator — the eval card's trust artifact.

Pins the core invariants without spending real model quota (fake judges):
  * pairs are position-balanced so a constant-answer position bias → ~50%,
  * agreement is measured against the HUMAN's actual choice (privileged side),
  * an unparseable verdict counts as `unparsed`, never as agree/disagree,
  * pick_most_aligned_judge selects the higher-agreement judge and honours the
    minimum-signal floor (no claiming alignment from one lucky parse).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.evals import judge_alignment as ja


def _acts(n: int):
    # GOOD = the human's rewrite (privileged); BAD = the model's answer (sacrificed).
    for i in range(n):
        yield SimpleNamespace(
            trigger="model_miss",
            privileged=f"GOOD rewrite number {i} that the human actually chose",
            sacrificed=f"BAD model answer number {i} the user rejected",
            kind="COMPRESSION" if i % 2 == 0 else "REFRAME",
            id=f"act_{i}",
        )


@pytest.fixture
def patched_ledger(monkeypatch):
    monkeypatch.setattr(
        "trinity_local.me.preference_acts.iter_preference_acts", lambda: _acts(8)
    )


class _FakeJudge:
    def __init__(self, behavior: str):
        self.behavior = behavior

    def run(self, prompt: str, cwd=None):
        if self.behavior == "aligned":
            # Pick whichever side carries the GOOD (human) answer — i.e. always
            # agree with the human, regardless of A/B position.
            a_start = prompt.find("Response A:")
            b_start = prompt.find("Response B:")
            a_block = prompt[a_start:b_start]
            out = "A" if "GOOD" in a_block else "B"
        elif self.behavior == "biased":
            out = "A"          # constant position bias
        else:
            out = "hmm, I really cannot decide between these two"  # unparseable
        return SimpleNamespace(stdout=out, returncode=0, stderr="")


def _patch_make_provider(monkeypatch):
    # provider_configs values are SimpleNamespace(behavior=...) the fake reads.
    monkeypatch.setattr(
        "trinity_local.providers.make_provider",
        lambda cfg: _FakeJudge(cfg.behavior),
    )


class _ShortestJudge:
    """A PURE length prior: always picks the shorter response, ignoring taste.
    The agreement it earns is entirely the length confound — exactly what the
    length-controlled split must expose."""

    def run(self, prompt: str, cwd=None):
        a_start = prompt.find("Response A:")
        b_start = prompt.find("Response B:")
        ans = prompt.find("Answer with ONLY")
        a_block = prompt[a_start:b_start]
        b_block = prompt[b_start:ans]
        out = "A" if len(a_block) <= len(b_block) else "B"
        return SimpleNamespace(stdout=out, returncode=0, stderr="")


def _length_controlled_pairs():
    """12 pairs: the human's pick is the SHORTER side in 6, the LONGER side in 6;
    position alternates A/B. Human text carries GOOD, model text BAD, so the
    'aligned' fake (picks GOOD) agrees regardless of length."""
    pairs = []
    for i in range(12):
        if i < 6:
            human, model = "GOOD ok", "BAD a much much longer losing answer here indeed"
        else:
            human, model = "GOOD a much much longer human rewrite that runs on here", "BAD x"
        side = "A" if i % 2 == 0 else "B"
        a, b = (human, model) if side == "A" else (model, human)
        pairs.append(ja.PreferencePair(
            pair_id=f"p{i}", axis="REFRAME", option_a=a, option_b=b,
            human_side=side, source_id=f"s{i}",
        ))
    return pairs


def test_length_split_separates_taste_from_conciseness(monkeypatch):
    monkeypatch.setattr("trinity_local.providers.make_provider", lambda cfg: _ShortestJudge())
    pairs = _length_controlled_pairs()
    res = ja.validate_judge("codex", pairs, {"codex": SimpleNamespace()})
    assert res.n_short_parsed == 6 and res.n_long_parsed == 6
    assert res.agreement_when_human_shorter == 1.0   # always agrees when human is shorter
    assert res.agreement_when_human_longer == 0.0    # never agrees when human is longer
    assert res.length_gap == 1.0                     # maximal → flagged as a length prior
    # And the raw agreement alone would have read as a benign ~50%.
    assert res.agreement == 0.5


def test_aligned_judge_has_no_length_gap(monkeypatch):
    _patch_make_provider(monkeypatch)  # the 'aligned' fake picks the GOOD (human) side
    pairs = _length_controlled_pairs()
    res = ja.validate_judge("claude", pairs, {"claude": SimpleNamespace(behavior="aligned")})
    assert res.agreement == 1.0
    assert res.agreement_when_human_shorter == 1.0
    assert res.agreement_when_human_longer == 1.0
    assert res.length_gap == 0.0  # alignment holds regardless of length → trustworthy


def test_length_gap_none_when_a_bucket_is_thin():
    # < MIN_LENGTH_BUCKET in the 'longer' bucket → gap is None, not a noisy number.
    r = ja.JudgeAlignmentResult("claude", n_pairs=20, n_parsed=20, n_agreed=18,
                                n_short_parsed=18, n_short_agreed=17,
                                n_long_parsed=2, n_long_agreed=1)
    assert r.length_gap is None


def test_shuffle_null_collapses_to_chance_for_a_real_agreement():
    """The negative control: a judge whose verdicts genuinely track the human side
    (real agreement 100%) must collapse to ~chance once the labels are shuffled — that
    gap is the proof the agreement is EARNED, not an artifact."""
    # 12 position-balanced pairs; the judge mirrors the human side every time.
    verdicts = [("A", "A") if i % 2 == 0 else ("B", "B") for i in range(12)]
    real = sum(1 for h, v in verdicts if h == v) / len(verdicts)
    null = ja.shuffle_null_agreement(verdicts)
    assert real == 1.0
    assert null is not None and 0.35 <= null <= 0.65, (
        f"shuffle-null must collapse to ~chance for an earned agreement, got {null}"
    )
    assert real - null > 0.3, "the real-vs-null gap is what proves the agreement is real"


def test_shuffle_null_is_deterministic_and_floored():
    v = [("A", "A"), ("B", "B"), ("A", "B"), ("B", "A"), ("A", "A"), ("B", "B")]
    assert ja.shuffle_null_agreement(v) == ja.shuffle_null_agreement(v)  # seeded → stable
    assert ja.shuffle_null_agreement([("A", "A"), ("B", "B")]) is None   # < 4 parsed → None


def test_validate_judge_reports_shuffle_null(patched_ledger, monkeypatch):
    """End-to-end: the aligned fake judge scores 100% agreement, and validate_judge
    attaches a shuffle-null near chance — the rigged-vs-earned discriminator ships on
    the result + in to_dict (privacy-safe: a float, no pair contents)."""
    monkeypatch.setattr("trinity_local.me.preference_acts.iter_preference_acts", lambda: _acts(12))
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    res = ja.validate_judge("claude", pairs, {"claude": SimpleNamespace(behavior="aligned")})
    assert res.agreement == 1.0
    assert res.shuffle_null is not None and 0.30 <= res.shuffle_null <= 0.70
    assert "shuffle_null" in res.to_dict()


def test_pairs_are_position_balanced(patched_ledger):
    pairs = ja.build_preference_pairs()
    assert len(pairs) == 8
    a_sides = sum(1 for p in pairs if p.human_side == "A")
    b_sides = sum(1 for p in pairs if p.human_side == "B")
    assert a_sides == b_sides == 4, "human-preferred side must alternate A/B to cancel position bias"


def test_degenerate_pairs_are_skipped(monkeypatch):
    monkeypatch.setattr(
        "trinity_local.me.preference_acts.iter_preference_acts",
        lambda: iter([
            SimpleNamespace(trigger="model_miss", privileged="same", sacrificed="same", kind="X", id="a"),
            SimpleNamespace(trigger="model_miss", privileged="real choice here", sacrificed="", kind="X", id="b"),
            SimpleNamespace(trigger="self_expressed", privileged="p", sacrificed="q", kind="X", id="c"),
            SimpleNamespace(trigger="model_miss", privileged="kept this", sacrificed="dropped that", kind="X", id="d"),
        ]),
    )
    pairs = ja.build_preference_pairs()
    # Only the last act survives: "a" is degenerate (privileged==sacrificed),
    # "b" has an empty side, "c" is self_expressed (not a model_miss).
    assert [p.source_id for p in pairs] == ["d"]


def test_parse_ab_handles_real_and_garbage():
    assert ja._parse_ab("A") == "A"
    assert ja._parse_ab("B") == "B"
    assert ja._parse_ab("```\nA\n```") == "A"
    assert ja._parse_ab('{"answer": "B"}') == "B"
    assert ja._parse_ab("A — because it's more concise") == "A"
    assert ja._parse_ab("Response B is better") == "B"
    assert ja._parse_ab("I cannot decide") is None
    assert ja._parse_ab("") is None


def test_aligned_judge_scores_full_agreement(patched_ledger, monkeypatch):
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    res = ja.validate_judge("claude", pairs, {"claude": SimpleNamespace(behavior="aligned")})
    assert res.n_parsed == 8
    assert res.n_agreed == 8
    assert res.agreement == 1.0
    assert res.unparsed == 0


def test_position_biased_judge_lands_near_half(patched_ledger, monkeypatch):
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    res = ja.validate_judge("codex", pairs, {"codex": SimpleNamespace(behavior="biased")})
    # Always "A": agrees only when the human side happens to be A → exactly half,
    # BECAUSE the pairs are position-balanced. This is the anti-fake-score guard.
    assert res.agreement == 0.5


def test_unparseable_judge_yields_no_agreement(patched_ledger, monkeypatch):
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    res = ja.validate_judge("antigravity", pairs, {"antigravity": SimpleNamespace(behavior="garbage")})
    assert res.unparsed == 8
    assert res.n_parsed == 0
    assert res.agreement is None, "a judge that never answers must not get a fabricated agreement rate"


def test_pick_most_aligned_judge_selects_the_aligned_one(monkeypatch):
    # >= MIN_ALIGNMENT_PAIRS (15) so the signal clears the floor; aligned (100%)
    # leads biased (50%) by 50 pts >> the margin → a real, choosable winner.
    monkeypatch.setattr("trinity_local.me.preference_acts.iter_preference_acts", lambda: _acts(16))
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    configs = {
        "claude": SimpleNamespace(behavior="aligned"),
        "codex": SimpleNamespace(behavior="biased"),
        "antigravity": SimpleNamespace(behavior="garbage"),
    }
    chosen, results = ja.pick_most_aligned_judge(["claude", "codex", "antigravity"], pairs, configs)
    assert chosen == "claude"
    assert results["claude"].agreement == 1.0
    assert results["codex"].agreement == 0.5
    assert results["antigravity"].agreement is None


def test_select_refuses_a_within_noise_lead():
    """The n=12-Gemini-by-2-pairs trap: even above the pair floor, a winner that
    only leads the runner-up by < MIN_ALIGNMENT_MARGIN is NOT chosen — it's tied."""
    from trinity_local.evals.judge_alignment import JudgeAlignmentResult, select_aligned_judge

    # Both clear the 15-pair floor; lead is 5 pts (< 10) → statistically tied.
    results = {
        "antigravity": JudgeAlignmentResult("antigravity", n_pairs=20, n_parsed=20, n_agreed=12),  # 60%
        "claude": JudgeAlignmentResult("claude", n_pairs=20, n_parsed=20, n_agreed=11),             # 55%
    }
    chosen, reason = select_aligned_judge(results)
    assert chosen is None
    assert "tied" in reason.lower()

    # A clear lead (60% vs 45% = 15 pts) DOES choose.
    results["claude"] = JudgeAlignmentResult("claude", n_pairs=20, n_parsed=20, n_agreed=9)  # 45%
    chosen, reason = select_aligned_judge(results)
    assert chosen == "antigravity"


def test_select_refuses_below_the_pair_floor():
    """n below MIN_ALIGNMENT_PAIRS → no judge chosen even at 100%, with an
    actionable reason (the real n=12 situation)."""
    from trinity_local.evals.judge_alignment import JudgeAlignmentResult, select_aligned_judge

    results = {"antigravity": JudgeAlignmentResult("antigravity", n_pairs=12, n_parsed=12, n_agreed=9)}  # 75% n=12
    chosen, reason = select_aligned_judge(results)
    assert chosen is None
    assert "floor" in reason.lower() or "larger" in reason.lower()


def test_floor_rejects_alignment_from_too_little_signal(monkeypatch):
    # Only 2 pairs → below the floor (max(5, n//4)=5) → no judge is "chosen"
    # even at 100% agreement: a trust claim needs real signal.
    monkeypatch.setattr(
        "trinity_local.me.preference_acts.iter_preference_acts", lambda: _acts(2)
    )
    _patch_make_provider(monkeypatch)
    pairs = ja.build_preference_pairs()
    chosen, results = ja.pick_most_aligned_judge(
        ["claude"], pairs, {"claude": SimpleNamespace(behavior="aligned")}
    )
    assert chosen is None
    assert results["claude"].agreement == 1.0  # measured, but not enough to trust
