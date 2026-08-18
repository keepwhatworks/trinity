"""The council must record the model it DISPATCHED, not Trinity's config label.

`providers.dispatched_model()` already existed, and its docstring already stated the rule:
"what eval/council must RECORD (the recorded == dispatched invariant)". ask.py called it.
evals/runner.py called it twice. `council_runner._provider_model` -- the one that feeds the
disagreement ledger the whole which-model-to-trust claim rests on -- was
`return override or config.model`.

That matters most for antigravity, whose CLI has NO --model flag: `config.model` is a value
agy ignores entirely, and the user's `/model` selection in
~/.gemini/antigravity-cli/settings.json is what actually runs. Measured 2026-08-18 across
the 400 most recent councils: 68 antigravity rows recorded 'Gemini 3.1 Pro (high)' and 3
recorded 'Gemini 3.7 Flash', while agy's live setting was 'Gemini 3.7 Flash (Low)'. Two
symptoms from one cause -- the window of Flash results filed under 3.1 Pro, and the missing
effort leg, because for agy the effort lives INSIDE the model string and config.model drops
it.

These call the REAL functions the council loop uses. The res_045 tests reimplemented the
logic instead, so a mutation reverting the behaviour left every one of them green.

Mutation-proven 2026-08-18:
  _provider_model -> `return config.model`        REDs test_records_the_dispatched_model
  label = recorded in stamp_member_model          REDs test_label_stays_static_so_drift_shows
  drop the `configured` branch in model_provenance REDs test_settings_read_is_configured
"""
from __future__ import annotations

import dataclasses

from trinity_local import providers as P
from trinity_local.council_runner import _warn_model_drift, stamp_member_model


# Provider configs are built HERE, not read from the founder's config.json.
# These tests asserted literals like "claude-opus-5" and "gpt-5.5" against
# load_config(), so pinning the council models to fable-5 / gpt-5.6-sol broke
# four of them. A test that fails when the USER changes a model is coupled to a
# machine, which is the same hermeticity bug found twice already today (a
# settings.json reader, then a second one). Fixed at the root: synthesise the
# config instead of inheriting it.
_FAKE = {
    "claude": dict(name="claude", model="claude-opus-5", args=[], command=["claude", "-p"]),
    "codex": dict(name="codex", model="gpt-5.5", args=["--sandbox", "workspace-write"],
                  command=["codex", "exec"]),
    # agy label deliberately DIFFERS from what the drift tests patch into
    # settings, or agreement fires and the drift case becomes untestable.
    "antigravity": dict(name="antigravity", model="Gemini 3.7 Flash", args=[],
                        command=["agy", "-p"]),
}


def _cfg(slug: str):
    import dataclasses as _dc
    from trinity_local.config import load_config as _lc
    base = _lc().providers[slug]
    return _dc.replace(base, **_FAKE[slug])


class TestProvenanceTier:
    def test_settings_read_is_configured_not_assumed(self, monkeypatch):
        """Reading agy's own settings is a real source. Calling it `assumed` would
        under-report evidence we actually have."""
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        assert P.model_provenance(_cfg("antigravity")) == "configured"

    def test_absent_settings_falls_back_to_assumed(self, monkeypatch):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: None)
        assert P.model_provenance(_cfg("antigravity")) == "assumed"

    def test_an_echo_still_outranks_configured(self, monkeypatch):
        """An observation of the run beats a reading of the config."""
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        assert P.model_provenance(_cfg("antigravity"), "gemini-x") == "echoed"

    def test_a_pin_still_outranks_configured(self, monkeypatch):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        c = dataclasses.replace(_cfg("antigravity"), args=["--model", "gemini-3.1-pro-high"])
        assert P.model_provenance(c) == "pinned"

    def test_which_providers_publish_a_settings_model(self, monkeypatch):
        """agy and claude do; codex is deliberately excluded.

        SUPERSEDES an earlier version of this test that asserted antigravity ONLY.
        claude was added the same day, so the assertion changed with the behaviour --
        recorded rather than quietly rewritten.

        codex stays out on purpose: it ECHOES its model on stderr, and an observation
        outranks a config reading. Wiring ~/.codex/config.toml would only ever matter
        when that echo is missing, which has not been observed. Adding it for symmetry
        would be adding a weaker source that can only disagree with a stronger one.
        """
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        monkeypatch.setattr(P, "read_claude_settings_model", lambda: "opus")
        assert P.settings_model(_cfg("antigravity")) == "Gemini 3.7 Flash (Low)"
        # claude was briefly on this tier and should never have been: Trinity injects
        # --model for it, so its settings cannot decide what runs.
        assert P.settings_model(_cfg("claude")) is None
        assert P.settings_model(_cfg("codex")) is None


class TestWhatTheCouncilActuallyRecords:
    def test_records_the_dispatched_model_not_the_config_label(self, monkeypatch):
        """THE BITE. config.model is a string agy ignores; settings.json is what runs."""
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        recorded, source, label = stamp_member_model(_cfg("antigravity"), None, None)
        assert recorded == "Gemini 3.7 Flash (Low)", (
            "the council recorded the config label instead of the dispatched model")
        assert source == "configured"

    def test_label_stays_static_so_drift_shows(self, monkeypatch):
        """Setting label = recorded makes drift invisible BY DEFINITION. Carrying both is
        the only reason a reader can see Trinity's label disagree with what ran."""
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        recorded, _, label = stamp_member_model(_cfg("antigravity"), None, None)
        assert label == _cfg("antigravity").model
        assert recorded != label, "this fixture is only meaningful when they differ"

    def test_the_effort_tier_survives(self, monkeypatch):
        """model_identity keys on model x size x EFFORT, and for agy the effort lives
        inside the model string. Dropping it silently merges two cells."""
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        recorded, _, _ = stamp_member_model(_cfg("antigravity"), None, None)
        assert "(Low)" in recorded

    def test_other_providers_are_untouched(self, monkeypatch):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        for slug in ("claude", "codex"):
            recorded, _, label = stamp_member_model(_cfg(slug), None, None)
            assert recorded == label == _cfg(slug).model, slug

    def test_an_explicit_override_still_wins_over_settings(self, monkeypatch):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        recorded, source, _ = stamp_member_model(_cfg("antigravity"), "gemini-3.1-pro-high", None)
        assert recorded == "gemini-3.1-pro-high" and source == "pinned"


class TestClaudeIsPinnedByInjection:
    """CORRECTS an earlier version of this file that had claude backwards.

    That version asserted claude follows ~/.claude/settings.json, on the belief that
    `claude -p` is dispatched bare. It is not: CLIProvider INJECTS
    `--model <config.model>` (and `--effort`) for claude at dispatch, so the real argv
    is `claude --model claude-opus-5 --effort high -p <prompt>`. config.model is the
    flag the CLI receives, and it OVERRIDES settings.json.

    So the founder's question — "if the claude default is changed, would it be recorded
    correctly?" — has the opposite answer to the one first given: changing it does not
    affect Trinity at all, because Trinity pins over it. The record was already right,
    and the "fix" would have recorded a model Trinity never dispatched.

    Mutation-proven 2026-08-18: deleting `injects_model_flag` from the provenance ladder
    REDs test_claude_is_pinned_by_injection; making dispatched_model consult
    read_claude_settings_model again REDs test_settings_cannot_override_a_pinned_model.
    """

    def test_claude_is_pinned_by_injection_not_assumed(self):
        """The flag is on the command line even though config.args is empty."""
        assert P.injects_model_flag(_cfg("claude")) is True
        assert P.model_provenance(_cfg("claude")) == "pinned"

    def test_settings_cannot_override_a_pinned_model(self, monkeypatch):
        """THE BITE, and the inverse of what the first version asserted."""
        monkeypatch.setattr(P, "read_claude_settings_model", lambda: "sonnet")
        recorded, source, _ = stamp_member_model(_cfg("claude"), None, None)
        assert recorded == "claude-opus-5", (
            "settings must not override a model Trinity pins on the command line")
        assert source == "pinned"

    def test_a_config_that_already_carries_the_flag_is_not_double_injected(self):
        c = dataclasses.replace(_cfg("claude"), args=["--model", "claude-sonnet-4-6"])
        assert P.injects_model_flag(c) is False
        assert P.model_provenance(c) == "pinned", "still pinned, just via args"

    def test_agy_does_not_get_the_injection(self):
        """agy has no --model flag at all — passing one makes it exit 2 — which is
        exactly why it needs the settings tier and claude does not."""
        assert P.injects_model_flag(_cfg("antigravity")) is False
        assert P.settings_model(_cfg("claude")) is None, "claude is off the settings tier"


class TestDriftWarnsWithoutAnEcho:
    """The warning used to fire for codex ONLY — the one provider that was never the
    problem.

    `_warn_model_drift` began with `if not echo: return`, so the two providers the whole
    provenance arc exists for — agy, which has no --model flag at all, and claude, where
    a changed default lands in settings.json — could drift in total silence. The RECORD
    was fixed first and the warning was left half-wired, which is the same
    producer-fixed/consumer-missed shape as res_045 and res_051.

    Mutation-proven 2026-08-18: restoring `if not echo: return` REDs both no-echo tests;
    dropping the echo branch REDs test_an_echo_still_warns_and_wins.
    """

    class _R:
        model_echo = None

    def test_agy_settings_drift_warns_even_though_nothing_echoes(self, monkeypatch, capsys):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.1 Pro (High)")
        _warn_model_drift("antigravity", _cfg("antigravity"), self._R())
        err = capsys.readouterr().err
        assert "MODEL DRIFT" in err and "Gemini 3.1 Pro (High)" in err
        assert "SETTINGS decide" in err, "must name the settings as the authority, not the echo"

    def test_claude_settings_disagreement_is_an_OVERRIDE_notice(self, monkeypatch, capsys):
        """Different fact, so a different message. Trinity pins claude, so a differing
        settings value is not a mislabelled row -- it is Trinity overriding a default
        the user chose. Worth saying; not the same warning as agy's."""
        monkeypatch.setattr(P, "read_claude_settings_model", lambda: "sonnet")
        _warn_model_drift("claude", _cfg("claude"), self._R())
        err = capsys.readouterr().err
        assert "OVERRIDING" in err and "sonnet" in err
        assert "MODEL DRIFT" not in err, "a pinned model is not drift"

    def test_agreement_is_silent(self, monkeypatch, capsys):
        """An alias that agrees is not worth a line. Warning on it would train the
        reader to ignore the warning, which is worse than having none."""
        monkeypatch.setattr(P, "read_claude_settings_model", lambda: "opus")
        _warn_model_drift("claude", _cfg("claude"), self._R())
        assert capsys.readouterr().err == ""

    def test_no_settings_and_no_echo_is_silent(self, monkeypatch, capsys):
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: None)
        monkeypatch.setattr(P, "read_claude_settings_model", lambda: None)
        for slug in ("claude", "antigravity"):
            _warn_model_drift(slug, _cfg(slug), self._R())
        assert capsys.readouterr().err == ""

    def test_an_echo_still_warns_and_wins(self, monkeypatch, capsys):
        """An observation of the run outranks a config reading — the echo branch must
        still fire, and must not be shadowed by the settings branch."""
        class R:
            model_echo = "gpt-5.6-sol"
        _warn_model_drift("codex", _cfg("codex"), R())
        err = capsys.readouterr().err
        assert "MODEL DRIFT" in err and "ECHO is ground truth" in err
