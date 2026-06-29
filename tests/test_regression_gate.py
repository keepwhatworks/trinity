"""The lens constitution's VALIDATOR — the held-out preference-regression gate.

`flip_check` refuses a candidate tension whose optimized-for pole (`pole_a`) is contradicted
by a statistically significant majority of held-out corrections; it ABSTAINS on a balanced
tension, thin evidence, or the TF-IDF fallback (never falsely blocks). `commit_through_gate`
is the single clobber-safe write path wrapping both `reconcile` sites: default OFF →
byte-identical to `reconcile(accepted)`; armed → drops only the rejected candidates
(shrink-only), never `allow_shrink=True`.
"""
from __future__ import annotations

from trinity_local.me.pair_mining import LensPair
from trinity_local.me.preference_acts import MODEL_MISS, PreferenceAct
from trinity_local.me.regression_gate import (
    MIN_HELDOUT_ACTS,
    commit_through_gate,
    flip_check,
    regression_gate_enabled,
)

# A deterministic 1-axis embedder: dim0 = terse(+)/verbose(−). Enough to drive flip_check.
_TERSE = ("short", "one line", "just the answer", "keep it")
_VERBOSE = ("detailed", "thorough", "explanation", "caveat", "full context")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        tl = t.lower()
        v = sum(1.0 for k in _TERSE if k in tl) - sum(1.0 for k in _VERBOSE if k in tl)
        # 2-D so unit() is well-defined even when the axis signal is 0.
        out.append([v, 0.0])
    return out


def _act(id_, privileged, sacrificed):
    return PreferenceAct(id=id_, trigger=MODEL_MISS, privileged=privileged,
                         sacrificed=sacrificed, kind="REFRAME", why="w")


_PAIR_TERSE = LensPair(pole_a="just the answer keep it short",
                       pole_b="a detailed thorough explanation", failure_a="", failure_b="",
                       verdict="accepted")
_PAIR_VERBOSE = LensPair(pole_a="a detailed thorough explanation",
                         pole_b="just the answer keep it short", failure_a="", failure_b="",
                         verdict="accepted")


def _toward_verbose(n):
    # corrections where the user privileged the VERBOSE form over the terse one.
    return [_act(f"v{i}", "a detailed thorough explanation with full context",
                 "just the answer keep it short") for i in range(n)]


def _toward_terse(n):
    return [_act(f"t{i}", "just the answer keep it short",
                 "a detailed thorough explanation with full context") for i in range(n)]


class TestFlipCheck:
    def test_rejects_a_candidate_contradicted_by_heldout(self):
        # Candidate optimizes for TERSE (pole_a), but every held-out correction steers toward
        # VERBOSE — a flip against a still-holding revealed preference.
        v = flip_check(_PAIR_TERSE, _toward_verbose(10), embed_fn=_fake_embed)
        assert v.verdict == "reject" and v.discordant == 10 and v.concordant == 0
        assert v.p is not None and v.p < 0.05

    def test_abstains_on_a_balanced_tension(self):
        # Corrections go BOTH ways → a genuine bidirectional tension → never blocked.
        acts = _toward_terse(6) + _toward_verbose(6)
        v = flip_check(_PAIR_TERSE, acts, embed_fn=_fake_embed)
        assert v.verdict in ("pass", "abstain") and v.verdict != "reject"

    def test_supportive_evidence_passes(self):
        v = flip_check(_PAIR_VERBOSE, _toward_verbose(10), embed_fn=_fake_embed)
        assert v.verdict == "pass" and v.concordant == 10

    def test_abstains_when_heldout_too_thin(self):
        v = flip_check(_PAIR_TERSE, _toward_verbose(MIN_HELDOUT_ACTS - 1), embed_fn=_fake_embed)
        assert v.verdict == "abstain"

    def test_abstains_without_real_embeddings(self):
        v = flip_check(_PAIR_TERSE, _toward_verbose(10), embed_fn=None)
        if v.verdict == "abstain":  # the expected path on a TF-IDF / no-embedder box
            assert "embedding" in v.reason.lower()


class TestCommitThroughGate:
    """A fake reconcile records exactly which candidates reach the write — and must never be
    handed allow_shrink (the gate only ever shrinks the candidate list)."""

    def _fake_reconcile(self, captured):
        def _rec(pairs, **kwargs):
            captured["pairs"] = list(pairs)
            captured["kwargs"] = kwargs
            return pairs
        return _rec

    def test_off_by_default_is_byte_identical(self, monkeypatch):
        monkeypatch.delenv("TRINITY_REGRESSION_GATE", raising=False)
        assert not regression_gate_enabled()
        captured: dict = {}
        accepted = [_PAIR_VERBOSE, _PAIR_TERSE]  # _PAIR_TERSE WOULD be a flip if armed
        commit_through_gate(accepted, acts=_toward_verbose(10), embed_fn=_fake_embed,
                            reconcile_fn=self._fake_reconcile(captured))
        # OFF → every candidate reaches reconcile untouched; flip_check never runs.
        assert captured["pairs"] == accepted
        assert captured["kwargs"] == {}  # never allow_shrink

    def test_armed_drops_only_the_flip(self, monkeypatch):
        monkeypatch.setenv("TRINITY_REGRESSION_GATE", "1")
        assert regression_gate_enabled()
        captured: dict = {}
        accepted = [_PAIR_VERBOSE, _PAIR_TERSE]
        commit_through_gate(accepted, acts=_toward_verbose(10), embed_fn=_fake_embed,
                            reconcile_fn=self._fake_reconcile(captured))
        # _PAIR_TERSE is contradicted → dropped; _PAIR_VERBOSE is supported → kept.
        assert captured["pairs"] == [_PAIR_VERBOSE]
        assert captured["kwargs"] == {}  # shrink-only, never allow_shrink=True

    def test_armed_keeps_everything_when_nothing_flips(self, monkeypatch):
        monkeypatch.setenv("TRINITY_REGRESSION_GATE", "1")
        captured: dict = {}
        accepted = [_PAIR_VERBOSE]  # supported by the evidence
        commit_through_gate(accepted, acts=_toward_verbose(10), embed_fn=_fake_embed,
                            reconcile_fn=self._fake_reconcile(captured))
        assert captured["pairs"] == [_PAIR_VERBOSE]
