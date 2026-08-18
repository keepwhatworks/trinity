"""The recorded model is not always an observation, and rows must say which.

Trinity recorded `providers.<name>.model` from config as "the model that
answered". For a provider invoked WITHOUT `--model` that is a static label, not
an observation — nothing verified it and nothing warned. Measured 2026-08-17 on
this machine:

  codex        `codex exec` prints `model: gpt-5.5` on stderr — and Trinity was
               discarding it in favour of the label.
  claude       `claude -p`, no --model, stderr EMPTY. A settings alias pointing
               at Sonnet still records `claude-opus-5`.
  antigravity  `agy -p`, no --model, stderr EMPTY. Observed live: the founder
               switched to Flash 3.7 and Trinity kept writing "Gemini 3.1 Pro
               (high)" until it was hand-edited. Every council in that window
               filed Flash results under 3.1 Pro.

That matters because the disagreement ledger keys on model x version, so a
`routing_lesson` like "for security_fix, prefer claude" can attach to a version
string that never ran.

Mutation-proven 2026-08-17: making `model_provenance` always return "echoed"
REDs the assumed/pinned cases; deleting the label-vs-echo comparison in
`_warn_model_drift` REDs the drift test.
"""
from __future__ import annotations

import dataclasses

import pytest

from trinity_local.council_runner import (_model_provenance, _warn_model_drift,
                                          stamp_member_model)
from trinity_local.providers import model_provenance, parse_model_echo


@pytest.fixture(autouse=True)
def _no_real_settings_files(monkeypatch):
    """Neutralise EVERY provider settings reader for this whole module.

    Provenance now consults each CLI's own config file, so any test here that does
    not say otherwise would silently depend on whether the machine running the suite
    has claude or agy configured -- a test coupled to a developer's home directory.

    This is the SECOND time that bug appeared today. It was fixed for agy by
    patching the one call site, and reappeared for claude hours later when a second
    reader was added. Patching call sites scales with the number of readers; killing
    them all by default does not, so tests that care opt IN by re-patching.
    """
    import trinity_local.providers as _P
    monkeypatch.setattr(_P, "read_agy_active_model_raw", lambda: None)
    monkeypatch.setattr(_P, "read_claude_settings_model", lambda: None)


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


class TestEchoParsing:
    def test_reads_the_codex_line(self):
        assert parse_model_echo("workdir: /x\nmodel: gpt-5.5\nprovider: openai") == "gpt-5.5"

    def test_silence_is_not_a_model(self):
        """The whole defect is treating absence of evidence as evidence."""
        assert parse_model_echo("") is None
        assert parse_model_echo(None) is None
        assert parse_model_echo("thinking about the model to use") is None


class TestProvenance:
    def test_an_echo_outranks_everything(self):
        assert model_provenance(_cfg("codex"), "gpt-5.5") == "echoed"

    def test_argv_pinning_is_a_guarantee_about_the_request(self):
        c = dataclasses.replace(_cfg("claude"), args=["--model", "claude-sonnet-4-6"])
        assert model_provenance(c) == "pinned"

    def test_no_flag_no_echo_and_no_settings_is_only_assumed(self, monkeypatch):
        """claude and agy are invoked bare, so their label is unverified.

        SUPERSEDED IN PART 2026-08-18, and deliberately rather than to go green: agy
        now reads its OWN settings.json, so it earns `configured` when that file is
        present (see test_dispatched_model_recorded.py). `assumed` remains correct for
        agy only when the file is missing, which is what this pins.

        The monkeypatch is not decoration either. The original version called
        model_provenance against the real config AND the real
        ~/.gemini/antigravity-cli/settings.json, so its verdict depended on whether the
        machine running the suite happened to have agy configured. That is a test
        coupled to a developer's home directory, which this repo forbids everywhere
        else.
        """
        import trinity_local.providers as P
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: None)
        # claude is NOT in this list any more, and its removal is the correction:
        # CLIProvider injects --model for claude, so it is `pinned`, not `assumed`.
        # It was under-reported here from the day provenance shipped.
        assert model_provenance(_cfg("antigravity")) == "assumed"
        assert model_provenance(_cfg("claude")) == "pinned"

    def test_agy_with_settings_present_is_configured_not_assumed(self, monkeypatch):
        """The other half of the pair, so the boundary is pinned from both sides."""
        import trinity_local.providers as P
        monkeypatch.setattr(P, "read_agy_active_model_raw", lambda: "Gemini 3.7 Flash (Low)")
        assert model_provenance(_cfg("antigravity")) == "configured"
        assert model_provenance(_cfg("claude")) == "pinned", "claude's --model is injected"

    def test_an_explicit_override_counts_as_pinned(self):
        assert _model_provenance(_cfg("claude"), "claude-opus-4-8") == "pinned"


class TestDriftWarning:
    def test_warns_when_the_cli_contradicts_the_label(self, capsys):
        class R:
            model_echo = "gpt-5.6-sol"

        _warn_model_drift("codex", _cfg("codex"), R())
        err = capsys.readouterr().err
        assert "MODEL DRIFT" in err and "gpt-5.6-sol" in err

    def test_silent_when_they_agree(self, capsys):
        class R:
            model_echo = _cfg("codex").model

        _warn_model_drift("codex", _cfg("codex"), R())
        assert "MODEL DRIFT" not in capsys.readouterr().err

    def test_silent_when_the_cli_says_nothing(self, capsys):
        """No echo must not be reported as agreement — there is nothing to agree
        with, which is precisely the assumed case."""
        class R:
            model_echo = None

        _warn_model_drift("claude", _cfg("claude"), R())
        assert capsys.readouterr().err == ""


class TestProvenanceReachesTheLedgersRecord:
    """res_045's fix went into RUN STATE first, which the ledger never reads.

    Caught on an autonomous tick by opening a real outcome file: it carries
    `member_results[*].model` and `.metadata`, NOT the run-state metadata the
    first version wrote to. A fix the consumer cannot see is the res_042 shape.

    These call `stamp_member_model` — the REAL function the council loop uses.
    The first version of this class reimplemented the logic instead, so a
    mutation reverting `model=(_echo or _label)` to `model=_label` left every
    test green. That is decoration, and it is exactly what this repo's
    mutation-proof rule exists to catch.
    """

    def test_an_echo_becomes_the_recorded_model(self):
        model, source, label = stamp_member_model(_cfg("codex"), None, "gpt-5.6-sol")
        assert model == "gpt-5.6-sol", "the CLI's own statement must win over the label"
        assert source == "echoed"
        assert label == _cfg("codex").model, "the label is kept so drift stays visible"

    def test_no_echo_falls_back_to_the_label_and_says_pinned(self):
        """claude -p states nothing — but Trinity does not dispatch it bare.

        CORRECTED 2026-08-18. This asserted `assumed`, on the belief that claude runs
        without --model. CLIProvider INJECTS it, so the label is enforced on the command
        line and the honest tier is `pinned`. Nothing is OBSERVED here (there is no
        echo), but something is GUARANTEED, and the whole point of the ladder is to keep
        those apart.
        """
        model, source, label = stamp_member_model(_cfg("claude"), None, None)
        assert model == label == _cfg("claude").model
        assert source == "pinned"

    def test_an_override_is_pinned_and_wins(self):
        model, source, _ = stamp_member_model(_cfg("claude"), "claude-sonnet-4-6", None)
        assert model == "claude-sonnet-4-6" and source == "pinned"

    def test_a_missing_config_does_not_crash_the_stamp(self):
        model, source, _ = stamp_member_model(None, None, "gpt-5.5")
        assert model == "gpt-5.5" and source == "unknown"
