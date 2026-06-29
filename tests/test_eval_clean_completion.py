"""#270 — eval dispatch must be a CLEAN COMPLETION, not a full agent.

Without the clean-completion flags, `claude -p <eval_item>` inherits the user's
~/.claude.json (all MCP servers + tools), so an agentic eval prompt makes the
model try to use the browser and hang. These guards pin that eval dispatch
strips MCP + tools.
"""
from __future__ import annotations

from pathlib import Path

from trinity_local.config import ProviderConfig
from trinity_local.providers import CLIProvider


def _claude_config():
    return ProviderConfig(
        name="claude", type="cli", enabled=True, label="Claude",
        command=["claude", "-p"], args=[], task_types=set(), model="claude-opus-4-8",
    )


class TestCleanCompletionFlags:
    def _captured_command(self, monkeypatch, clean):
        captured = {}

        def _fake_run(self, command, cwd):
            captured["command"] = command
            from trinity_local.providers import ProviderResult
            return ProviderResult(provider="claude", stdout="ok", stderr="", returncode=0)

        monkeypatch.setattr(CLIProvider, "_run_command", _fake_run)
        # No active sampling session in a plain test → subprocess path.
        p = CLIProvider(_claude_config())
        p.clean_completion = clean
        p.run("look at the live app and trace the wall panel generation", Path("."))
        return captured["command"]

    def test_clean_completion_strips_mcp_and_tools(self, monkeypatch):
        cmd = self._captured_command(monkeypatch, clean=True)
        assert "--strict-mcp-config" in cmd
        assert '{"mcpServers":{}}' in cmd
        assert "--disallowedTools" in cmd
        # The prompt still lands after -p.
        assert cmd[-1].startswith("look at the live app")

    def test_default_run_has_no_clean_flags(self, monkeypatch):
        cmd = self._captured_command(monkeypatch, clean=False)
        assert "--strict-mcp-config" not in cmd

    def test_flags_land_before_the_prompt_tail(self, monkeypatch):
        cmd = self._captured_command(monkeypatch, clean=True)
        # -p must come after the clean flags and immediately before the prompt.
        assert cmd.index("-p") < cmd.index(cmd[-1])
        assert cmd.index("--strict-mcp-config") < cmd.index("-p")


class TestRunnerEnablesCleanCompletion:
    def test_runner_sets_clean_completion(self):
        import inspect

        from trinity_local.evals import runner

        src = inspect.getsource(runner)
        assert "clean_completion = True" in src
