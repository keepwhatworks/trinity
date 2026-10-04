"""The echoed model must be the one that ANSWERED, not whichever is listed first.

`claude -p --output-format json` reports `modelUsage`, a dict keyed by every
model that ran during the turn — and Claude Code runs a small model for
background work (titles, summaries) alongside the one that answered the prompt.

The derivation took `next(iter(modelUsage))`, whichever landed first. On
2026-09-16 that returned `claude-haiku-4-5` while the answer itself said
"Claude Fable 5.1". Because the substitution guard compares the requested model
against this echo, a HEALTHY Fable member was dropped from every council as an
impostor — eight consecutive councils ran two-lab without anyone noticing, and
the product's whole premise is three labs that cannot see each other.

The guard was right to act on what it was told. It was told wrong. A bad
observation is more dangerous than no observation, because everything
downstream trusts it by design.

The answering model is the one that produced the output tokens; helpers produce
few or none.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.providers import parse_claude_json


def _payload(model_usage: dict, result: str = "ok") -> str:
    return json.dumps({"result": result, "usage": {}, "modelUsage": model_usage})


def _mu(name: str, out_tokens: int, canonical: str | None = None) -> dict:
    d = {"outputTokens": out_tokens}
    if canonical is not None:
        d["canonicalModel"] = canonical
    return {name: d}


class TestTheAnsweringModelWins:
    def test_a_single_model_is_reported(self):
        got = parse_claude_json(_payload(_mu("claude-fable-5-1", 4, "claude-fable-5-1")))
        assert got["model"] == "claude-fable-5-1"

    def test_a_background_helper_listed_FIRST_does_not_win(self):
        """The exact 2026-09-16 shape: haiku first with NO output, fable second
        with the answer. One producer, so it is an observation rather than a
        choice between candidates."""
        mu = {**_mu("claude-haiku-4-5", 0, "claude-haiku-4-5"),
              **_mu("claude-fable-5-1", 120, "claude-fable-5-1")}
        assert parse_claude_json(_payload(mu))["model"] == "claude-fable-5-1"

    def test_two_models_that_BOTH_produced_output_are_ambiguous(self):
        """SUPERSEDED 2026-09-16, hours after it was written. This asserted that
        the biggest producer wins — fable at 120 tokens beating a helper at 3.
        An Astra audit pointed out the mirror case: a terse answer at 20 tokens
        beside a chatty helper at 100 records the HELPER. "Most tokens" was a
        different guess, not a verification.

        Two producers is ambiguity, and the honest report is None. A missing
        echo means "nothing to compare", so the substitution guard leaves the
        member alone — uncertainty costs a stamp, not a healthy council member."""
        mu = {**_mu("claude-fable-5-1", 120, "claude-fable-5-1"),
              **_mu("claude-haiku-4-5", 3, "claude-haiku-4-5")}
        assert parse_claude_json(_payload(mu))["model"] is None

    def test_three_producers_are_ambiguous_however_lopsided(self):
        """Also superseded. 900 tokens against 40 and 2 LOOKS decisive, and that
        intuition is exactly what made "most tokens" feel safe. It is still a
        guess about which model the user's prompt was answered by, and a wrong
        stamp here drops a member or mislabels a ledger cell."""
        mu = {**_mu("claude-haiku-4-5", 2, "claude-haiku-4-5"),
              **_mu("claude-opus-5", 900, "claude-opus-5"),
              **_mu("claude-sonnet-4-6", 40, "claude-sonnet-4-6")}
        assert parse_claude_json(_payload(mu))["model"] is None


class TestItDegradesWithoutInventing:
    def test_missing_token_counts_still_yield_a_model(self):
        assert parse_claude_json(_payload({"claude-opus-5": {"canonicalModel": "claude-opus-5"}}))["model"] \
            == "claude-opus-5"

    def test_the_key_is_used_when_canonicalModel_is_absent(self):
        assert parse_claude_json(_payload({"claude-opus-5": {"outputTokens": 5}}))["model"] == "claude-opus-5"

    def test_no_modelUsage_reports_no_model_rather_than_a_guess(self):
        assert parse_claude_json(json.dumps({"result": "ok", "usage": {}}))["model"] is None

    @pytest.mark.parametrize("bad", ["", "not json", "[]", '{"no_result": 1}'])
    def test_a_non_claude_payload_returns_none(self, bad):
        assert parse_claude_json(bad) is None


class TestTheGuardIsNotTrippedByAHelper:
    """The consequence that made this urgent: the substitution guard drops a
    member whose echo disagrees with the pinned model."""

    def test_a_healthy_member_is_not_called_a_substitution(self):
        from trinity_local.providers import is_model_substitution
        mu = {**_mu("claude-haiku-4-5", 0, "claude-haiku-4-5"),
              **_mu("claude-fable-5-1", 120, "claude-fable-5-1")}
        echo = parse_claude_json(_payload(mu))["model"]
        assert is_model_substitution("claude-fable-5-1", echo) is False, (
            "a background helper must never make a healthy member look like an impostor")

    def test_a_REAL_substitution_is_still_caught(self):
        """The guard must keep working: if the answering model really differs,
        that is still a drop."""
        from trinity_local.providers import is_model_substitution
        echo = parse_claude_json(_payload(_mu("claude-haiku-4-5", 120, "claude-haiku-4-5")))["model"]
        assert is_model_substitution("claude-fable-5-1", echo) is True
