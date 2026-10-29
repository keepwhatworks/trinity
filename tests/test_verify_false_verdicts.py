"""verify must not report a verdict nothing earned (coherence review 2026-10-15).

verify gates every later change, so a gate that can falsely approve makes each
of those passes unreliable (council_34633698184fa40b). Each test drives the
shipped entry point (verify(), read_panel, or the MCP handler) and fails on the
code before the fix:

- a block with only a non-blocking test read "tests: green" above "RED [t]";
- a reader counted as passing with no blocking judgment to pass;
- `effort` could LOWER a reader below its configured level;
- `gemini` (or a comma string over MCP) silently lost a reader;
- tests that never ran, or ran red without blocking, were missing from
  `unverified`, the list hosts are told to name;
- a host that forgot `exclude_lab` had its own lab read its own diff.
"""
from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest

from trinity_local import verify as V
from trinity_local.verify import Criterion


def _verify(tmp_path, criteria, **kw):
    kw.setdefault("run_panel", False)
    kw.setdefault("differential", False)
    kw.setdefault("quality", False)
    return V.verify(criteria, "diff --git a/x b/x\n", "ctx", tmp_path, **kw)


class TestNoBlockingTestIsNoGreen:
    def test_a_lone_non_blocking_red_test_is_not_green(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s",
                                  "command": "false", "blocking": False}])
        assert out["kernel"]["green"] is None and out["kernel"]["relevant"] is False
        assert out["triage"] == "READ"
        assert "non-blocking" in out["reason"]

    def test_a_blocking_green_test_still_reads_green(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "true"}])
        assert out["kernel"]["green"] is True and out["kernel"]["relevant"] is True

    def test_a_blocking_red_test_still_stops(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "false"}])
        assert out["triage"] == "STOP"

    @pytest.mark.parametrize("flag, value", [("advisory", "false"), ("blocking", "true"),
                                             ("guard", "false"), ("advisory", 0), ("blocking", 1)])
    def test_a_flag_that_is_not_a_bool_is_refused(self, tmp_path, flag, value):
        # bool("false") is True: "advisory": "false" made this red test advisory, so it read READ.
        with pytest.raises(ValueError, match=f"{flag} must be true or false"):
            _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "false",
                                flag: value}])

    def test_pytest_that_ran_nothing_is_no_result_not_red(self, tmp_path):
        # A mistyped test path (pytest exit 4) or a file with no tests (exit 5) ran nothing,
        # and read "a relevant test is red" -> STOP (twice on 2026-10-26).
        import sys
        empty = tmp_path / "test_empty.py"
        empty.write_text("x = 1\n", encoding="utf-8")
        base = f"{sys.executable} -m pytest -q -p no:cacheprovider "
        for cmd in (base + str(tmp_path / "test_missing.py"), base + str(empty)):
            out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": cmd}])
            assert out["triage"] == "READ", (cmd, out["reason"])
            assert out["kernel"]["runs"][0]["could_not_run"] is True, out["kernel"]

    def test_a_real_pytest_failure_and_another_programs_exit_4_still_stop(self, tmp_path):
        import sys
        red = tmp_path / "test_red.py"
        red.write_text("def test_x():\n    assert False\n", encoding="utf-8")
        # Programs that print pytest's own words and exit 4, invoked with no shell operator,
        # so only the which-program check can tell them from a real pytest run.
        fake_sh = tmp_path / "fake.sh"
        fake_sh.write_text("echo no tests ran\nexit 4\n", encoding="utf-8")
        fake_py = tmp_path / "fake.py"
        fake_py.write_text("print('no tests ran')\nraise SystemExit(4)\n", encoding="utf-8")
        for cmd in (f"{sys.executable} -m pytest -q -p no:cacheprovider {red}", "sh -c 'exit 4'",
                    f"sh {fake_sh} pytest", f"{sys.executable} {fake_py} pytest",
                    # Codex's verify read: a pytest WORD is not a pytest run.
                    "sh -c 'echo no tests ran; exit 4' pytest", "echo pytest no tests ran; exit 4",
                    f"{sys.executable} -m pytest -q -p no:cacheprovider {tmp_path / 'gone.py'} && sh -c 'exit 4'"):
            out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": cmd}])
            assert out["triage"] == "STOP", (cmd, out["reason"])

    def test_a_guard_whose_test_path_is_gone_still_stops(self, tmp_path):
        # A renamed or deleted guard test must not quietly stop guarding.
        import sys
        cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider {tmp_path / 'test_gone.py'}"
        out = _verify(tmp_path, [{"id": "g", "kind": "test", "statement": "s", "command": cmd, "guard": True},
                                 {"id": "t", "kind": "test", "statement": "s", "command": "true"}])
        assert out["triage"] == "STOP", out["reason"]

    def test_a_null_flag_is_unset_and_a_red_test_still_stops(self, tmp_path):
        # Hosts send null for optional fields they did not set; bool(None) made this non-blocking.
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "false",
                                  "blocking": None, "advisory": None, "guard": None}])
        assert out["triage"] == "STOP"


def _fake_reader(votes_for):
    def fake(name, prompt, cwd, config, effort):
        body = ", ".join(f'"{cid}": "PASS"' for cid in votes_for)
        return SimpleNamespace(stdout='{"votes": {%s}, "why": ""}' % body, stderr="",
                               returncode=0, usage={}), config.providers[name]
    return fake


class TestVacuousPassIsNotAPass:
    def test_readers_with_no_blocking_judgment_do_not_pass(self, monkeypatch, tmp_path):
        crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s",
                                     "blocking": False})]
        monkeypatch.setattr(V, "_dispatch", _fake_reader(["j"]))
        panel, reads = V.read_panel(crit, "diff", "ctx", tmp_path)
        assert reads and all(r.error is None for r in reads)
        assert not panel.consensus_pass() and not any(panel.votes.values())

    def test_a_blocking_judgment_still_passes(self, monkeypatch, tmp_path):
        crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s"})]
        monkeypatch.setattr(V, "_dispatch", _fake_reader(["j"]))
        panel, _ = V.read_panel(crit, "diff", "ctx", tmp_path)
        assert panel.votes and all(panel.votes.values())

    def test_it_is_not_reported_as_an_objection(self, monkeypatch, tmp_path):
        """2026-10-26: two readers voted PASS on every item, none of them blocking, and the
        verdict read "all 2 reviewing labs objected". Not passing is not objecting."""
        from trinity_local.verify_rule import Kernel, triage
        crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s",
                                     "blocking": False})]
        monkeypatch.setattr(V, "_dispatch", _fake_reader(["j"]))
        panel, _ = V.read_panel(crit, "diff", "ctx", tmp_path)
        assert panel.ran and panel.gated is False
        for kernel in (Kernel(ran=True, relevant=True, green=True), Kernel.not_run()):
            reason = triage(kernel, panel).reason
            assert "objected" not in reason and "agreed" not in reason, reason
            assert "no judgment item is blocking" in reason, reason


class TestEffortOnlyRaises:
    def _dispatched_effort(self, monkeypatch, configured, asked):
        from trinity_local import providers as P
        from trinity_local.config import load_config

        config = load_config()
        config.providers["codex"] = dataclasses.replace(config.providers["codex"], effort=configured)
        seen = {}

        class Fake:
            def __init__(self, cfg):
                seen["effort"] = cfg.effort
            clean_completion = False

            def run(self, prompt, cwd):
                return SimpleNamespace(stdout="", stderr="", returncode=0, usage={})
        monkeypatch.setattr(P, "CodexProvider", Fake)
        V._dispatch("codex", "p", __import__("pathlib").Path("."), config, asked)
        return seen["effort"]

    def test_a_host_cannot_lower_the_catching_reader(self, monkeypatch, patch_trinity_home):
        assert self._dispatched_effort(monkeypatch, "xhigh", "low") == "xhigh"

    def test_it_can_still_raise_one(self, monkeypatch, patch_trinity_home):
        assert self._dispatched_effort(monkeypatch, "medium", "xhigh") == "xhigh"

    def test_an_unknown_level_keeps_the_configured_one(self, monkeypatch, patch_trinity_home):
        assert self._dispatched_effort(monkeypatch, "xhigh", "turbo") == "xhigh"


class TestMembersAreCanonicalAndLossesAreShown:
    @pytest.mark.parametrize("given,want", [
        (["claude", "codex", "gemini"], ("claude", "codex", "antigravity")),
        ("claude,gemini", ("claude", "antigravity")),
        (("chatgpt", "codex"), ("codex",)),
        (None, V.DEFAULT_PANEL),
    ])
    def test_brand_names_and_strings_fold(self, given, want):
        assert V.panel_members(given) == want

    def test_gemini_is_read_not_dropped(self, monkeypatch, tmp_path, patch_trinity_home):
        crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s"})]
        monkeypatch.setattr(V, "_dispatch", _fake_reader(["j"]))
        panel, reads = V.read_panel(crit, "diff", "ctx", tmp_path, providers=["claude", "gemini"])
        assert {r.provider for r in reads} == {"claude", "antigravity"}

    def test_an_unknown_member_is_a_lost_read(self, monkeypatch, tmp_path, patch_trinity_home):
        crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s"})]
        monkeypatch.setattr(V, "_dispatch", _fake_reader(["j"]))
        panel, reads = V.read_panel(crit, "diff", "ctx", tmp_path, providers=["claude", "grok"])
        lost = [r for r in reads if r.provider == "grok"]
        assert lost and lost[0].error and "grok" not in panel.votes


class TestUnverifiedNamesEveryTestThatDidNotVerify:
    def test_tests_that_never_ran_are_unverified(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "true"}],
                      run_tests=False)
        assert out["unverified"] == ["t"]

    def test_a_non_blocking_red_test_is_unverified(self, tmp_path):
        out = _verify(tmp_path, [
            {"id": "t", "kind": "test", "statement": "s", "command": "true"},
            {"id": "u", "kind": "test", "statement": "s", "command": "false", "blocking": False}])
        assert out["unverified"] == ["u"]

    def test_a_green_test_is_not_listed(self, tmp_path):
        out = _verify(tmp_path, [{"id": "t", "kind": "test", "statement": "s", "command": "true"}])
        assert out["unverified"] == []


class TestTheHostsOwnLabIsExcludedByDefault:
    @pytest.mark.parametrize("client,lab", [
        ("claude-code", "anthropic"), ("codex-mcp-client", "openai"),
        ("gemini-cli-mcp-client", "google"), ("antigravity", "google"),
        ("cursor-vscode", None), (None, None),
    ])
    def test_host_lab(self, client, lab):
        from trinity_local.mcp_server import host_lab
        assert host_lab(client) == lab

    def _call(self, monkeypatch, client, args):
        from trinity_local import mcp_server
        seen = {}

        def fake_run(*a, **kw):
            seen.update(kw)
            return {"triage": "READ"}
        monkeypatch.setattr(mcp_server, "_client_name", lambda: client)
        monkeypatch.setattr(V, "verify", fake_run)
        res = asyncio.run(mcp_server._verify({"criteria": [{"id": "j", "kind": "judgment",
                                                            "statement": "s"}],
                                              "diff": "d", **args}))
        return seen, res

    def test_a_claude_host_that_forgets_is_still_excluded(self, monkeypatch):
        seen, _ = self._call(monkeypatch, "claude-code", {})
        assert seen["exclude_lab"] == "anthropic"

    def test_an_explicit_argument_wins(self, monkeypatch):
        seen, _ = self._call(monkeypatch, "claude-code", {"exclude_lab": "openai"})
        assert seen["exclude_lab"] == "openai"

    def test_an_unknown_host_excludes_nothing_and_says_so(self, monkeypatch):
        import json
        seen, res = self._call(monkeypatch, "cursor-vscode", {})
        assert seen["exclude_lab"] is None
        body = res[0]["text"] if isinstance(res[0], dict) else res[0].text
        assert json.loads(body)["excluded_lab"]["lab"] is None
