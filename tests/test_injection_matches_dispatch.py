"""`injects_model_flag` must agree with what the dispatch actually does.

The provenance ladder answers "how trustworthy is the model string this row
records": `pinned` means argv enforces it, `assumed` means nobody checked. It
reads `injects_model_flag`, which is a hand-maintained list of providers whose
dispatch appends `--model`.

A hand-maintained list next to the code it describes drifts. It did, twice:
claude was missing when the ladder was written, and CODEX was missing until
2026-09-11 even though `CodexProvider.run` had always appended the flag — so
every codex row stamped `assumed` while argv enforced the model the whole time.
Both times the symptom was a row that looked less trustworthy than it was,
which is the benign direction; the same drift in reverse would stamp `pinned`
on a model nobody enforced, and the trust ledger keys on model identity.

This test does not maintain the list. It builds the real command each provider
would run and asserts the predicate matches the argv.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trinity_local.config import ProviderConfig
from trinity_local.providers import _has_model_flag, injects_model_flag

CLI_SEATS = ("claude", "codex", "antigravity")


# Each seat's REAL provider type. An earlier version of this file hardcoded
# "cli" for all three, so `make_provider` handed codex a CLIProvider and the
# captured argv was missing every codex-specific flag — the test reported a
# predicate/dispatch mismatch that did not exist. A fixture that builds the
# wrong object tests the wrong object.
_TYPE = {"claude": "cli", "codex": "codex", "antigravity": "cli"}


def _cfg(name: str) -> ProviderConfig:
    return ProviderConfig(
        name=name, type=_TYPE[name], enabled=True, label=name,
        command={"claude": ["claude", "-p"], "codex": ["codex", "exec"],
                 "antigravity": ["agy", "-p"]}[name],
        args=[], model=f"test-model-for-{name}", effort="high",
        task_types={"general"})


def _built_argv(cfg, monkeypatch) -> list[str]:
    """The argv this provider WOULD run, captured without running it.

    The point of this file is to compare the predicate against the real
    invocation, so the comparison has to see the real invocation. An earlier
    version reached for a `_last_command` attribute that does not exist, every
    assertion skipped, and the file asserted nothing while looking green — the
    same shape as the drift it was written to catch.
    """
    from trinity_local import providers as P
    prov = P.make_provider(cfg)
    seen: list[list[str]] = []

    def _capture(self, command, cwd, **kw):
        seen.append(list(command))
        raise RuntimeError("captured")

    monkeypatch.setattr(type(prov), "_run_command", _capture, raising=True)
    try:
        prov.run("hello", Path(tempfile.mkdtemp()))
    except Exception:
        pass
    assert seen, "the provider never reached _run_command; the seam moved"
    return seen[0]


class TestThePredicateMatchesTheInvocation:
    """The assertion is against the ARGV the provider builds, not against a list
    someone remembered to update."""

    @pytest.mark.parametrize("name", CLI_SEATS)
    def test_injection_claim_matches_the_real_command(self, name, monkeypatch):
        cfg = _cfg(name)
        argv = _built_argv(cfg, monkeypatch)
        assert injects_model_flag(cfg) == _has_model_flag(argv), (
            f"{name}: predicate says injects={injects_model_flag(cfg)} but the built "
            f"command is {argv}")

    @pytest.mark.parametrize("name", CLI_SEATS)
    def test_the_model_on_the_command_line_is_the_configured_one(self, name, monkeypatch):
        """Pinned must mean pinned TO THE RIGHT THING. A --model flag carrying a
        stale value would satisfy the predicate and still mislabel the row."""
        cfg = _cfg(name)
        argv = _built_argv(cfg, monkeypatch)
        assert cfg.model in argv, f"{name}: {cfg.model!r} not in {argv}"


class TestEverySeatIsAccountedFor:
    """The failure mode is a seat nobody listed, so enumerate them."""

    @pytest.mark.parametrize("name", CLI_SEATS)
    def test_each_cli_seat_has_a_deliberate_answer(self, name):
        assert isinstance(injects_model_flag(_cfg(name)), bool)

    def test_all_three_cli_seats_inject(self, name=None):
        """As of 2026-09-11 all three append --model at dispatch. If a seat
        stops, this test should fail and be updated WITH the dispatch change,
        not silently drift the way the list did twice."""
        for seat in CLI_SEATS:
            assert injects_model_flag(_cfg(seat)) is True, (
                f"{seat} no longer reports injection — if its dispatch changed, "
                f"update both together; if it did not, the predicate drifted again")

    def test_a_non_cli_provider_does_not_claim_injection(self):
        assert injects_model_flag(_cfg("claude").__class__(
            name="mlx", type="local", enabled=True, label="mlx",
            command=["mlx_lm.generate"], args=[], model="m", effort=None,
            task_types={"general"})) is False

    def test_none_is_handled(self):
        assert injects_model_flag(None) is False


class TestACouncilMemberCannotAct:
    """agy must be sandboxed on the COUNCIL path, not only the verify path.

    agy has no tool-deny flag, so `_CLEAN_COMPLETION_FLAGS` has no entry for it
    and verify's reader path appends `--sandbox` by hand. The council path never
    got that, and the failure is silent in the worst way: on a short prompt
    Gemini answers normally, but on a long one it reaches for a tool, headless
    mode denies it, and the member returns a permission error with EMPTY output.

    Measured 2026-09-11 on council_2e54c56dacdcae08 — agy scored 0 on every
    axis and the council degraded to one member plus a chairman from the same
    seat, which is not a council. A member that can be lost by prompt length is
    worse than a member that is absent, because the degradation is invisible in
    the verdict.
    """

    def test_agy_is_sandboxed_on_the_council_path(self, monkeypatch):
        argv = _built_argv(_cfg("antigravity"), monkeypatch)
        assert "--sandbox" in argv, (
            f"agy dispatches unsandboxed and will lose the member on any prompt "
            f"that makes it reach for a tool: {argv}")

    def test_it_is_a_restriction_never_a_grant(self, monkeypatch):
        """The prompt a member reads is untrusted content. Same reasoning that
        put verify's readers under --sandbox rather than a permission bypass."""
        argv = [str(a) for a in _built_argv(_cfg("antigravity"), monkeypatch)]
        for grant in ("--dangerously-skip-permissions", "--yolo", "--allow-all"):
            assert grant not in argv, f"a member was granted {grant}"

    def test_the_flag_is_not_added_twice(self, monkeypatch):
        """verify's path appends --sandbox itself; the provider must not stack
        a second one when config.args already carries it."""
        cfg = _cfg("antigravity")
        cfg = type(cfg)(**{**cfg.__dict__, "args": ["--sandbox"]})
        argv = [str(a) for a in _built_argv(cfg, monkeypatch)]
        assert argv.count("--sandbox") == 1, f"stacked flags: {argv}"
