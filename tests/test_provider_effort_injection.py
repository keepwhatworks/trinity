"""Provider-side injection of --effort (claude) and -c model_reasoning_effort (codex).

Trinity adds an `effort` field to ProviderConfig (low / medium / high
standardized vocabulary; each CLI accepts its own dialect on top).
providers.py translates this per-provider:

  - claude:       appends `--effort <level>` to the command
  - codex:        appends `-c model_reasoning_effort=<level>` to the args
  - antigravity:  noop — agy CLI has no flag (user sets it via the
                  `/model` slash command which persists to
                  ~/.gemini/antigravity-cli/settings.json)

These tests pin the injection so a refactor can't silently drop the
effort flag — without it the leaderboard claims drift (a council that
configured "claude on high reasoning" but actually ran with claude's
default reasoning would inflate or deflate the scores by a real amount).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trinity_local.config import ProviderConfig


def _make_provider_config(name: str, *, model: str | None = None,
                          effort: str | None = None,
                          command: list[str] | None = None,
                          args: list[str] | None = None,
                          provider_type: str = "cli") -> ProviderConfig:
    return ProviderConfig(
        name=name, type=provider_type, enabled=True, label=name.title(),
        command=command if command is not None else [name, "-p"],
        args=args if args is not None else [],
        task_types=set(), model=model, effort=effort,
    )


class TestClaudeEffortInjection:
    """Claude CLI accepts --effort <low|medium|high|xhigh|max>."""

    def test_effort_appended_when_set(self, monkeypatch):
        """Configured effort lands as `--effort high` in the spawn command,
        positioned BEFORE the prompt-consuming -p flag so the prompt
        still sits last."""
        from trinity_local.providers import CLIProvider

        config = _make_provider_config(
            "claude", model="claude-opus-4-7", effort="high",
            command=["claude", "-p"],
        )
        provider = CLIProvider(config)
        captured = {}

        def fake_run(self, command, cwd):
            captured["command"] = command
            return SimpleNamespace(stdout="ok", stderr="", returncode=0, elapsed_seconds=0.0, provider="claude")

        monkeypatch.setattr(CLIProvider, "_run_command", fake_run)
        provider.run("test prompt", Path("."))
        cmd = captured["command"]
        # --effort must appear with the configured level
        assert "--effort" in cmd, f"--effort missing from claude command: {cmd}"
        idx = cmd.index("--effort")
        assert cmd[idx + 1] == "high", f"--effort value drifted: {cmd[idx+1]}"
        # The -p tail must come AFTER both --model and --effort, with
        # the prompt sitting immediately after -p. Putting --effort
        # between -p and the prompt would break claude's parser.
        assert cmd.index("-p") > idx, "--effort must come before the -p tail flag"
        assert cmd[cmd.index("-p") + 1] == "test prompt", "prompt must follow -p immediately"

    def test_no_effort_means_no_flag(self, monkeypatch):
        """When effort is None, no --effort flag is injected. This keeps
        users who set only `model` from getting a stale default effort
        layered in by Trinity."""
        from trinity_local.providers import CLIProvider

        config = _make_provider_config(
            "claude", model="claude-opus-4-7", effort=None,
            command=["claude", "-p"],
        )
        provider = CLIProvider(config)
        captured = {}
        monkeypatch.setattr(
            CLIProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="claude"))[1],
        )
        provider.run("test", Path("."))
        assert "--effort" not in captured["command"], "no --effort should be injected when effort=None"

    def test_user_provided_effort_in_args_not_double_injected(self, monkeypatch):
        """If the user already pinned `--effort` in config args, Trinity
        must not append a second one — that would cause claude to error
        on the duplicate flag."""
        from trinity_local.providers import CLIProvider

        config = _make_provider_config(
            "claude", model="claude-opus-4-7", effort="medium",
            command=["claude", "-p"],
            args=["--effort", "max"],  # user manually pinned
        )
        provider = CLIProvider(config)
        captured = {}
        monkeypatch.setattr(
            CLIProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="claude"))[1],
        )
        provider.run("test", Path("."))
        # Count occurrences of `--effort` — must be exactly one
        cmd = captured["command"]
        assert cmd.count("--effort") == 1, (
            f"Expected exactly one --effort in command (user pre-pinned); "
            f"got: {cmd}"
        )


class TestCodexEffortInjection:
    """Codex CLI accepts `-c model_reasoning_effort=<minimal|low|medium|high>`."""

    def test_effort_appended_as_toml_override(self, monkeypatch):
        """Configured effort lands as `-c model_reasoning_effort=high`
        — the codex `-c` flag is a generic TOML key=value override path."""
        from trinity_local.providers import CodexProvider

        config = _make_provider_config(
            "codex", model="gpt-5.5", effort="high",
            command=["codex", "exec"],
        )
        provider = CodexProvider(config)
        captured = {}
        monkeypatch.setattr(
            CodexProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="codex"))[1],
        )
        provider.run("test prompt", Path("."))
        cmd = captured["command"]
        # The pair `-c model_reasoning_effort=high` must appear consecutively.
        # Walk for a `-c` whose neighbor carries the right override key
        # (other `-c` flags may be present too — codex supports many).
        assert "-c" in cmd, f"-c flag missing from codex command: {cmd}"
        effort_found = any(
            tok == "-c" and i + 1 < len(cmd)
            and "model_reasoning_effort=high" in cmd[i + 1]
            for i, tok in enumerate(cmd)
        )
        assert effort_found, f"-c model_reasoning_effort=high not found in: {cmd}"

    def test_no_effort_means_no_override(self, monkeypatch):
        """When effort is None, the `-c model_reasoning_effort=` pair is
        not appended. The user's ~/.codex/config.toml setting becomes
        the effective floor — Trinity doesn't override."""
        from trinity_local.providers import CodexProvider

        config = _make_provider_config(
            "codex", model="gpt-5.5", effort=None,
            command=["codex", "exec"],
        )
        provider = CodexProvider(config)
        captured = {}
        monkeypatch.setattr(
            CodexProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="codex"))[1],
        )
        provider.run("test", Path("."))
        cmd = captured["command"]
        # No `-c model_reasoning_effort=...` should appear
        has_effort_override = any(
            tok == "-c" and i + 1 < len(cmd) and "model_reasoning_effort" in cmd[i + 1]
            for i, tok in enumerate(cmd)
        )
        assert not has_effort_override, (
            f"No -c model_reasoning_effort override expected when effort=None; "
            f"got: {cmd}"
        )

    def test_user_provided_override_not_double_injected(self, monkeypatch):
        """If the user already passed `-c model_reasoning_effort=...` in
        args, Trinity must not append a second override — codex would
        process both with last-wins semantics, masking the user's intent."""
        from trinity_local.providers import CodexProvider

        config = _make_provider_config(
            "codex", model="gpt-5.5", effort="medium",
            command=["codex", "exec"],
            args=["-c", "model_reasoning_effort=high"],  # user pre-pinned
        )
        provider = CodexProvider(config)
        captured = {}
        monkeypatch.setattr(
            CodexProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="codex"))[1],
        )
        provider.run("test", Path("."))
        cmd = captured["command"]
        override_count = sum(
            1 for i, tok in enumerate(cmd)
            if tok == "-c" and i + 1 < len(cmd) and "model_reasoning_effort" in cmd[i + 1]
        )
        assert override_count == 1, (
            f"Expected exactly one model_reasoning_effort override (user pre-pinned); "
            f"got {override_count} in: {cmd}"
        )


class TestAntigravityNoEffortFlag:
    """agy CLI has no --effort or --model flag. ProviderConfig.effort
    is still accepted on the antigravity entry (for round-tripping +
    the launchpad chip), but providers.py must NOT try to inject any
    CLI flag for it — that would crash agy with "unknown flag"."""

    def test_no_effort_flag_injected_for_antigravity(self, monkeypatch):
        """Even when effort is set on an antigravity ProviderConfig,
        the spawn command must not include `--effort` (or any other
        flag derived from it). agy users set the model+effort via
        the `/model` slash command inside agy."""
        from trinity_local.providers import CLIProvider

        config = _make_provider_config(
            "antigravity",
            model="Gemini 3.1 Pro",
            effort="high",
            command=["agy", "-p"],
        )
        provider = CLIProvider(config)
        captured = {}
        monkeypatch.setattr(
            CLIProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="antigravity"))[1],
        )
        provider.run("test", Path("."))
        cmd = captured["command"]
        # NO --effort allowed for antigravity (agy CLI has no such flag)
        assert "--effort" not in cmd, (
            f"--effort must NOT be injected for antigravity (agy CLI rejects it); "
            f"got: {cmd}"
        )

    def test_no_model_flag_injected_for_antigravity(self, monkeypatch):
        """Regression: agy CLI has no --model flag (model is its `/model` slash
        command); a truthy config.model used to leak `--model <sku>` and agy
        exits 2 ("flags provided but not defined: -model"), failing the member."""
        from trinity_local.providers import CLIProvider

        config = _make_provider_config(
            "antigravity",
            model="Gemini 3.1 Pro (high)",
            effort="high",
            command=["agy", "-p"],
        )
        provider = CLIProvider(config)
        captured = {}
        monkeypatch.setattr(
            CLIProvider, "_run_command",
            lambda self, command, cwd: (captured.__setitem__("command", command),
                                         SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                         elapsed_seconds=0.0, provider="antigravity"))[1],
        )
        provider.run("test", Path("."))
        cmd = captured["command"]
        assert "--model" not in cmd, (
            f"--model must NOT be injected for antigravity (agy CLI exits 2 on it); "
            f"got: {cmd}"
        )
        # The agy command should be exactly its base + the prompt — no flags.
        assert cmd == ["agy", "-p", "test"], f"unexpected agy command shape: {cmd}"

    def test_claude_and_codex_still_get_model(self, monkeypatch):
        """The antigravity exclusion must not regress --model for providers that
        accept it: claude (via CLIProvider) and codex (via CodexProvider)."""
        from trinity_local.providers import CLIProvider, CodexProvider

        for cls, name, command in (
            (CLIProvider, "claude", ["claude", "-p"]),
            (CodexProvider, "codex", ["codex", "exec"]),
        ):
            config = _make_provider_config(name, model="some-model-v9", command=command)
            provider = cls(config)
            captured = {}
            monkeypatch.setattr(
                cls, "_run_command",
                lambda self, command, cwd: (captured.__setitem__("command", command),
                                             SimpleNamespace(stdout="ok", stderr="", returncode=0,
                                                             elapsed_seconds=0.0, provider=name))[1],
            )
            provider.run("test", Path("."))
            assert "--model" in captured["command"], (
                f"{name} must still get --model injected; got: {captured['command']}"
            )


class TestDispatchedModelReadsAgySettings:
    """recorded == dispatched: antigravity's RECORDED model (eval/council
    target_model) must come from agy's settings.json — the flagless agy CLI
    ignores config.model, so recording config.model mislabels the model that
    actually ran (e.g. eval card says 'Gemini 3.1 Pro' while agy ran 3.5 Flash)."""

    def test_antigravity_reads_agy_settings(self, tmp_path, monkeypatch):
        import json
        from trinity_local.providers import dispatched_model
        gem = tmp_path / ".gemini" / "antigravity-cli"
        gem.mkdir(parents=True)
        (gem / "settings.json").write_text(json.dumps({"model": "Gemini 3.5 Flash (High)"}))
        monkeypatch.setenv("HOME", str(tmp_path))
        config = _make_provider_config("antigravity", model="Gemini 3.1 Pro (high)",
                                       command=["agy", "-p"])
        # The agy-side selection wins over the stale config value.
        assert dispatched_model(config) == "Gemini 3.5 Flash (High)"

    def test_antigravity_falls_back_to_config_when_no_settings(self, tmp_path, monkeypatch):
        from trinity_local.providers import dispatched_model
        monkeypatch.setenv("HOME", str(tmp_path))  # no agy settings file present
        config = _make_provider_config("antigravity", model="Gemini 3.1 Pro (high)",
                                       command=["agy", "-p"])
        assert dispatched_model(config) == "Gemini 3.1 Pro (high)"

    def test_non_antigravity_uses_config_model(self, tmp_path, monkeypatch):
        from trinity_local.providers import dispatched_model
        monkeypatch.setenv("HOME", str(tmp_path))
        config = _make_provider_config("claude", model="claude-opus-4-8", command=["claude", "-p"])
        assert dispatched_model(config) == "claude-opus-4-8"


class TestProviderConfigEffortField:
    """ProviderConfig accepts an `effort` field. None when missing from
    config.json — matches model's optional shape."""

    def test_effort_defaults_to_none(self):
        from trinity_local.config import ProviderConfig
        cfg = ProviderConfig(
            name="x", type="cli", enabled=True, label="X",
            command=["x"], args=[], task_types=set(),
        )
        assert cfg.effort is None

    def test_effort_round_trips_through_load_config(self, tmp_path, monkeypatch):
        """config.json with `"effort": "high"` must surface on the
        loaded ProviderConfig — without it the value silently gets
        dropped at load time and the injection never fires."""
        import json
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "max_turns": 1,
            "notifications": False,
            "providers": {
                "claude": {
                    "type": "cli", "enabled": True, "label": "Claude",
                    "command": ["claude", "-p"], "args": [],
                    "task_types": ["general"],
                    "model": "claude-opus-4-7",
                    "effort": "high",
                },
            },
            "task_preferences": {},
        }))
        from trinity_local.config import load_config
        cfg = load_config(str(config_path))
        assert cfg.providers["claude"].model == "claude-opus-4-7"
        assert cfg.providers["claude"].effort == "high"


class TestModelArgReconcile:
    """Review HIGH#2: the recorded model must equal the dispatched model.
    `--model X` baked into args/command is what the CLI actually runs, so it
    must win and become the authoritative config.model (else councils record
    one model while dispatching another, poisoning routing + benchmark claims).
    """

    def test_args_model_wins_and_is_stripped(self):
        from trinity_local.config import _reconcile_model_arg
        model, command, args = _reconcile_model_arg(
            ["codex", "exec"],
            ["--sandbox", "workspace-write", "--model", "gpt-5.3-codex"],
            "gpt-5.5",
        )
        assert model == "gpt-5.3-codex"  # dispatched value wins
        assert "--model" not in args and "gpt-5.3-codex" not in args
        assert args == ["--sandbox", "workspace-write"]

    def test_equals_form_extracted(self):
        from trinity_local.config import _reconcile_model_arg
        model, _command, args = _reconcile_model_arg(
            ["claude", "-p"], ["--model=claude-opus-4-8"], None
        )
        assert model == "claude-opus-4-8"
        assert args == []

    def test_no_inline_model_keeps_json_field(self):
        from trinity_local.config import _reconcile_model_arg
        model, command, args = _reconcile_model_arg(["claude", "-p"], [], "claude-opus-4-8")
        assert model == "claude-opus-4-8"
        assert command == ["claude", "-p"] and args == []

    def test_load_config_records_dispatched_model(self, tmp_path, monkeypatch):
        import json
        from trinity_local.config import load_config
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "providers": {
                "codex": {
                    "type": "codex", "command": ["codex", "exec"],
                    "args": ["--model", "gpt-5.3-codex"],
                    "model": "gpt-5.5",
                },
            },
        }))
        cfg = load_config(str(config_path))
        # The recorded model now equals what dispatches (args value), and the
        # inline --model is gone so providers.py injects exactly one.
        assert cfg.providers["codex"].model == "gpt-5.3-codex"
        assert "--model" not in cfg.providers["codex"].args
