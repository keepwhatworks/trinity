"""A member that silently becomes a different model is dropped, not counted.

On 2026-09-11 Trinity pinned `claude-fable-5-1`, the CLI was out of usage
credits for it, and the row came back echoing `claude-haiku-4-5`. The stamp was
correct — echo outranks the config label — but the COUNCIL still counted a
member. That is the dangerous shape: three answers, one of them from a model
nobody chose, a council that looks complete, and a disagreement-ledger cell
keyed to whatever happened to answer.

A degraded council that says so is strictly better than a complete-looking one
that lies about its membership.

The guard must not fire on ALIASING. `gpt-6-astra` legitimately echoes as
`GPT-6`; `gemini-3.8-flash-high` as `Gemini 3.8 Flash`; `claude-opus-5` as a
dated build. A guard that tripped on those would fail every healthy council,
which is a worse failure than the one it prevents.
"""
from __future__ import annotations

import pytest

from trinity_local.providers import _normalize_model, is_model_substitution


class TestARealSwapIsCaught:
    @pytest.mark.parametrize("requested,answered", [
        ("claude-fable-5-1", "claude-haiku-4-5"),     # the incident
        ("claude-opus-5", "claude-fable-5-1"),
        ("gpt-6-astra", "gpt-5.6-sol"),
        ("gemini-3.8-flash-high", "gemini-3.1-pro"),
    ])
    def test_a_different_model_is_a_substitution(self, requested, answered):
        assert is_model_substitution(requested, answered) is True


class TestAliasingIsNotASwap:
    @pytest.mark.parametrize("requested,answered", [
        ("gpt-6-astra", "GPT-6"),                       # coarser family name
        ("claude-opus-5", "claude-opus-5-20260101"),    # dated build
        ("gemini-3.8-flash-high", "Gemini 3.8 Flash"),  # spacing and case
        ("claude-opus-5", "claude-opus-5"),             # identical
        ("CLAUDE-OPUS-5", "claude-opus-5"),             # case only
    ])
    def test_the_same_model_in_another_string_is_not(self, requested, answered):
        assert is_model_substitution(requested, answered) is False, (
            "a guard that fires on aliasing fails every healthy council")


class TestNothingToCompareIsNotEvidence:
    @pytest.mark.parametrize("requested,answered", [
        ("claude-fable-5-1", None), (None, "claude-haiku-4-5"),
        ("claude-fable-5-1", ""), ("", "claude-haiku-4-5"), (None, None),
    ])
    def test_a_missing_side_never_claims_substitution(self, requested, answered):
        """No echo is a provider that did not report, not a provider that
        swapped. Treating silence as a swap would drop healthy members."""
        assert is_model_substitution(requested, answered) is False

    def test_normalization_keeps_only_alphanumerics(self):
        assert _normalize_model("Gemini 3.8 Flash (High)") == "gemini38flashhigh"
        assert _normalize_model(None) == ""


class TestTheCouncilDropsTheMember:
    def test_the_runner_guards_on_a_pinned_model(self):
        """Wiring guard: the check must sit in the member path and must be
        conditioned on the model being PINNED — an unpinned provider's own
        settings legitimately decide what runs."""
        import inspect
        from trinity_local import council_runner as CR
        src = inspect.getsource(CR)
        assert "is_model_substitution" in src, "the council path does not check for substitution"
        assert "injects_model_flag(provider_config)" in src, (
            "the check must only fire when argv pinned the model")
        assert '"reason": "model_substitution"' in src, (
            "a dropped member must say WHY, or a degraded council is indistinguishable "
            "from a provider that simply failed")

    def test_the_dropped_member_carries_both_models(self):
        import inspect
        from trinity_local import council_runner as CR
        src = inspect.getsource(CR)
        assert '"requested"' in src and '"answered"' in src, (
            "the payload must record what was asked for AND what answered; "
            "without both, the incident cannot be reconstructed")
