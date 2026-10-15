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
