"""A council member or chairman reads the user's repo; it never writes to it.

Members and the chairman run with cwd in the user's repo, often while the host
agent is editing it. Until 2026-10-15 the shipped config gave codex
`--sandbox workspace-write` there, and `claude -p` inherited the user's
permission mode and every MCP server in ~/.claude.json, Trinity's own included,
so a member could edit the tree or start a nested council. The 2026-07-13
incident (codex refactored 22 files from an eval dispatch) was fixed for evals
only; councils kept the write grant.

These tests drive the real `run_council` with the real providers and replace
only the subprocess call, so every argv checked is the one the shipped path
builds, members and chairman alike.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from trinity_local import provider_quota
from trinity_local.providers import BaseProvider, ProviderResult, _codex_read_only_args

CHAIRMAN = ("Agreed claims\n- one\n\n```json\n"
            '{"winner":"codex","confidence":"medium","task_type":"comparison"}\n```\n')

# Every spelling of a write grant a user's existing config.json could carry.
WRITE_GRANTS = [
    ["--sandbox", "workspace-write"],
    ["-s", "workspace-write"],
    ["--sandbox=danger-full-access"],
    ["-sworkspace-write"],
    ["--full-auto"],
    ["--dangerously-bypass-approvals-and-sandbox"],
]


@pytest.fixture(autouse=True)
def _clean():
    provider_quota.clear()
    yield
    provider_quota.clear()


def _dispatches(patch_trinity_home, monkeypatch, *, primary, codex_grant):
    from trinity_local.config import load_config
    from trinity_local.council_runner import run_council
    from trinity_local.council_runtime import create_prompt_bundle

    seen: list[tuple[str, list[str]]] = []

    def _fake_run_command(self, command, cwd, *, timeout=None):
        seen.append((self.config.name, list(command)))
        if any("synthesizer" in str(a).lower() for a in command):
            return ProviderResult(provider=self.config.name, stdout=CHAIRMAN,
                                  stderr="", returncode=0)
        return ProviderResult(provider=self.config.name, stdout=f"{self.config.name} answer",
                              stderr="", returncode=0)

    monkeypatch.setattr(BaseProvider, "_run_command", _fake_run_command)
    config = load_config()
    codex = config.providers["codex"]
    config.providers["codex"] = replace(codex, args=[*codex_grant, *codex.args])
    run_council(
        config=config,
        bundle=create_prompt_bundle(task_cluster_id="cluster_read_only",
                                    task_text="review src/app.py", goal="pick one"),
        member_providers=["claude", "codex", "antigravity"],
        primary_provider=primary, cwd=Path(patch_trinity_home))
    return seen


def _sandbox_value(argv):
    i = argv.index("--sandbox")
    return argv[i + 1]


@pytest.mark.parametrize("grant", WRITE_GRANTS, ids=lambda g: " ".join(g))
@pytest.mark.parametrize("primary", ["claude", "codex"])
def test_no_member_or_chair_can_write(patch_trinity_home, monkeypatch, grant, primary):
    seen = _dispatches(patch_trinity_home, monkeypatch, primary=primary, codex_grant=grant)
    names = {name for name, _ in seen}
    assert {"claude", "codex", "antigravity"} <= names, f"a member never dispatched: {names}"
    chair_argvs = [a for _, a in seen if any("synthesizer" in str(x).lower() for x in a)]
    assert chair_argvs, "the chairman never dispatched"

    for name, argv in seen:
        if name == "codex":
            joined = " ".join(argv)
            assert "workspace-write" not in joined and "danger-full-access" not in joined, argv
            for flag in ("--full-auto", "--dangerously-bypass-approvals-and-sandbox"):
                assert flag not in argv, argv
            assert _sandbox_value(argv) == "read-only", argv
            assert [a for a in argv if a in ("--sandbox", "-s")] == ["--sandbox"], argv
        elif name == "claude":
            _assert_claude_reads_only(argv)
        elif name == "antigravity":
            assert "--sandbox" in argv, argv


def _assert_claude_reads_only(argv):
    assert "--strict-mcp-config" in argv, "a member would boot every MCP server, Trinity's included"
    assert argv.count("--mcp-config") == 1 and argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    # Plan mode, and no settings source whose allow rules could grant a write:
    # `--permission-mode manual` still let `git tag` through `Bash(git *)` (probed 2026-10-15).
    assert argv.count("--permission-mode") == 1 and argv[argv.index("--permission-mode") + 1] == "plan"
    assert argv.count("--setting-sources") == 1 and argv[argv.index("--setting-sources") + 1] == ""
    for flag in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                 "--allowedTools", "--allowed-tools", "--settings"):
        assert flag not in argv, (flag, argv)
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool in denied, f"{tool} still available to a claude member"
    # Members run `git log`/grep for real; denying Bash removed that (probed 2026-10-15).
    assert "Bash" not in denied and "*" not in denied


CLAUDE_GRANTS = [
    ["--dangerously-skip-permissions"],
    ["--allow-dangerously-skip-permissions"],
    ["--permission-mode", "bypassPermissions"],
    ["--permission-mode=acceptEdits"],
    ["--setting-sources", "user,project,local"],
    ["--settings", '{"permissions":{"allow":["Bash(*)"]}}'],
    ["--mcp-config", "/home/u/.claude.json"],
    ["--allowedTools", "Bash(*)", "Write"],
]


@pytest.mark.parametrize("grant", CLAUDE_GRANTS, ids=lambda g: g[0])
def test_a_claude_config_cannot_grant_back_a_write(monkeypatch, grant):
    from trinity_local.config import ProviderConfig
    from trinity_local.providers import CLIProvider

    captured = {}

    def _fake(self, command, cwd, *, timeout=None):
        captured["argv"] = list(command)
        return ProviderResult(provider="claude", stdout="ok", stderr="", returncode=0)

    monkeypatch.setattr(BaseProvider, "_run_command", _fake)
    cfg = ProviderConfig(name="claude", type="cli", enabled=True, label="Claude",
                         command=["claude", "-p"], args=[*grant, "--model", "claude-opus-5-5"],
                         task_types=set(), model="claude-opus-5-5")
    CLIProvider(cfg).run("review src/app.py", Path("."))
    argv = captured["argv"]
    _assert_claude_reads_only(argv)
    assert argv[-2:] == ["--model", "claude-opus-5-5"], "unrelated config args must survive"


@pytest.mark.parametrize("grant", WRITE_GRANTS, ids=lambda g: " ".join(g))
def test_every_spelling_of_a_write_grant_is_scrubbed(grant):
    out = _codex_read_only_args([*grant, "--model", "gpt-5.6-sol"])
    assert out[:2] == ["--sandbox", "read-only"]
    assert out[2:] == ["--model", "gpt-5.6-sol"]


def test_the_shipped_example_config_grants_no_write():
    import json
    from importlib import resources

    cfg = json.loads(resources.files("trinity_local")
                     .joinpath("data/config.example.json").read_text(encoding="utf-8"))
    for name, prov in cfg["providers"].items():
        args = prov.get("args") or []
        assert "workspace-write" not in args and "danger-full-access" not in args, name
