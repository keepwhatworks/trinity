"""Tests for install-mcp targeting all three harnesses (Claude / Gemini / Codex)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(patch_trinity_home: Path) -> Path:
    return patch_trinity_home


def _run_install(monkeypatch, home_dir: Path, scope: str = "user"):
    from types import SimpleNamespace

    import sys

    from trinity_local.commands import install as _install
    from trinity_local.commands.install import handle_install_mcp

    monkeypatch.setattr(Path, "home", lambda: home_dir)
    # Force the interpreter-fallback path (no trinity-local wrapper on PATH) so
    # these tests pin the pip/venv shape deterministically; the wrapper path is
    # covered by test_resolve_mcp_command_prefers_wrapper.
    monkeypatch.setattr(
        _install, "_resolve_mcp_command",
        lambda: (str(sys.executable), ["-m", "trinity_local.main", "--mcp"]),
    )
    handle_install_mcp(SimpleNamespace(scope=scope))


def test_resolve_mcp_command_prefers_wrapper(monkeypatch, tmp_path):
    # The launch-blocker fix (v1.7.78): a harness must spawn the ABSOLUTE
    # trinity-local wrapper (carries PYTHONPATH), not a bare interpreter that
    # can't import trinity_local on a curl|sh install.
    import shutil

    from trinity_local.commands import install as _install

    # 1. Wrapper ON PATH → use it.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/trinity-local" if name == "trinity-local" else None)
    cmd, mcp_args = _install._resolve_mcp_command()
    assert cmd == "/usr/local/bin/trinity-local"
    assert mcp_args == ["--mcp"]

    # 2. THE CURL|SH BUG (v1.7.315): wrapper is NOT on PATH (~/.local/bin not added
    #    yet — install.sh only warns) but the wrapper FILE exists there. shutil.which
    #    returns None; we must STILL point at the wrapper, not the bare interpreter
    #    that ModuleNotFounds on spawn. Pre-fix this silently returned the broken
    #    bare form — found by running install.sh end-to-end in an isolated HOME.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("TRINITY_BIN_DIR", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / ".local" / "bin").mkdir(parents=True)
    wrapper = fake_home / ".local" / "bin" / "trinity-local"
    wrapper.write_text("#!/bin/sh\nexit 0\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    cmd2, args2 = _install._resolve_mcp_command()
    assert cmd2 == str(wrapper), (
        f"curl|sh install must point the MCP config at the ~/.local/bin wrapper "
        f"(it self-resolves PYTHONPATH), not a bare interpreter; got {cmd2!r}"
    )
    assert args2 == ["--mcp"]

    # 2b. A custom $TRINITY_BIN_DIR is honored ahead of ~/.local/bin.
    custom = tmp_path / "custombin"
    custom.mkdir()
    cwrap = custom / "trinity-local"
    cwrap.write_text("#!/bin/sh\nexit 0\n")
    cwrap.chmod(0o755)
    monkeypatch.setenv("TRINITY_BIN_DIR", str(custom))
    cmd2b, _ = _install._resolve_mcp_command()
    assert cmd2b == str(cwrap)

    # 3. No wrapper ANYWHERE (pip/venv install) → interpreter + module fallback.
    monkeypatch.delenv("TRINITY_BIN_DIR", raising=False)
    empty_home = tmp_path / "emptyhome"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: empty_home)
    _cmd3, args3 = _install._resolve_mcp_command()
    assert args3 == ["-m", "trinity_local.main", "--mcp"]


class TestInstallMcp:
    def test_writes_claude_and_gemini_json_configs(self, home: Path, monkeypatch, capsys):
        _run_install(monkeypatch, home)

        claude_path = home / ".claude.json"
        gemini_path = home / ".gemini" / "settings.json"
        assert claude_path.exists()
        assert gemini_path.exists()

        claude_cfg = json.loads(claude_path.read_text())
        assert "trinity-local" in claude_cfg["mcpServers"]
        assert claude_cfg["mcpServers"]["trinity-local"]["args"] == [
            "-m", "trinity_local.main", "--mcp",
        ]
        gemini_cfg = json.loads(gemini_path.read_text())
        assert "trinity-local" in gemini_cfg["mcpServers"]

    def test_gemini_install_preserves_existing_mcpservers_and_auth(
        self, home: Path, monkeypatch, capsys,
    ):
        """A real user's ~/.gemini/settings.json carries auth, model, and
        OTHER mcpServers. install-mcp must deep-merge — never trample."""
        gemini_path = home / ".gemini" / "settings.json"
        gemini_path.parent.mkdir(parents=True, exist_ok=True)
        gemini_path.write_text(json.dumps({
            "auth": {"method": "api-key", "apiKey": "REDACTED"},
            "model": {"name": "gemini-2.5-flash"},
            "mcpServers": {
                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
                "github": {"command": "github-mcp"},
            },
        }))

        _run_install(monkeypatch, home)

        merged = json.loads(gemini_path.read_text())
        assert merged["auth"]["apiKey"] == "REDACTED", "auth must survive"
        assert merged["model"]["name"] == "gemini-2.5-flash", "model setting must survive"
        assert "playwright" in merged["mcpServers"], "other mcpServers must survive"
        assert "github" in merged["mcpServers"]
        assert "trinity-local" in merged["mcpServers"], "trinity-local was added"

    def test_malformed_existing_config_is_not_clobbered(self, home: Path):
        from trinity_local.commands.install import _write_json_mcp_config
        t = home / ".claude.json"
        bad = "not valid json at all"
        t.write_text(bad, encoding="utf-8")
        assert _write_json_mcp_config(t, {"command": "x"}) is False
        assert t.read_text(encoding="utf-8") == bad
        assert len(list(home.glob("*.bak"))) == 1

    def test_skipped_harness_is_named_in_summary(self, home: Path, monkeypatch, capsys):
        """A corrupt ~/.claude.json is backed-up-and-skipped (not clobbered) —
        but the success line lists only what worked, so without an explicit
        "NOT registered" block a user could miss that the PRIMARY harness
        (Claude Code) got nothing. The summary must name the skipped harness +
        the remediation. Found 2026-05-31 testing the install path."""
        (home / ".claude.json").write_text("{ corrupt json,,,", encoding="utf-8")

        _run_install(monkeypatch, home)

        out = capsys.readouterr().out
        assert "NOT registered" in out, "skipped harness must be surfaced, not silent"
        assert "Claude Code" in out, "the skipped harness must be NAMED"
        assert "re-run" in out, "remediation must be offered"
        # The other harnesses still installed — partial success, not total failure.
        assert (home / ".gemini" / "settings.json").exists()
        assert (home / ".cursor" / "mcp.json").exists()

    def test_no_skipped_block_when_all_succeed(self, home: Path, monkeypatch, capsys):
        """The "NOT registered" block must NOT appear on a clean install —
        it's an exception path, not noise on every run."""
        _run_install(monkeypatch, home)
        out = capsys.readouterr().out
        assert "NOT registered" not in out
        assert "✓ Installed" in out

    def test_writes_cursor_mcp_config(self, home: Path, monkeypatch, capsys):
        """100-persona audit P16/P92 fix: install-mcp must drop a config
        into ~/.cursor/mcp.json too. Cursor uses the same `mcpServers`
        JSON shape as Claude Code; before this fix Cursor users had to
        hand-wire the config, which silently churned them."""
        _run_install(monkeypatch, home)

        cursor_path = home / ".cursor" / "mcp.json"
        assert cursor_path.exists(), (
            "install-mcp must write Cursor config too — Cursor is a first-class harness"
        )
        cursor_cfg = json.loads(cursor_path.read_text())
        assert "trinity-local" in cursor_cfg["mcpServers"]
        assert cursor_cfg["mcpServers"]["trinity-local"]["args"] == [
            "-m", "trinity_local.main", "--mcp",
        ]

    def test_project_scope_writes_both_mcp_json_and_cursor(self, home: Path, monkeypatch, tmp_path, capsys):
        """Project-scoped install (.mcp.json) must ALSO drop the
        Cursor-shaped .cursor/mcp.json so a project-scoped Cursor user
        gets the server in the project's MCP surface."""
        # Project scope uses cwd-relative paths — switch into tmp_path
        # so the test's writes don't leak into the repo.
        monkeypatch.chdir(tmp_path)
        _run_install(monkeypatch, home, scope="project")

        assert (tmp_path / ".mcp.json").exists()
        assert (tmp_path / ".cursor" / "mcp.json").exists(), (
            "project-scoped install-mcp must write .cursor/mcp.json too"
        )


class TestUninstall:
    """100-persona audit P30/P57/P85: removing Trinity required hand-
    editing 4 MCP configs + Chrome ext manifest + skill + ~/.trinity/.
    The 'own your data' wedge demands a clean uninstall path."""

    def _run_uninstall(self, monkeypatch, home_dir: Path, **flags):
        from types import SimpleNamespace
        from trinity_local.commands.install import handle_uninstall

        monkeypatch.setattr(Path, "home", lambda: home_dir)
        defaults = {"yes": False, "include_data": False, "include_hf_cache": False}
        defaults.update(flags)
        args = SimpleNamespace(**defaults)
        return handle_uninstall(args)

    def test_dry_run_lists_targets_without_deleting(self, home: Path, monkeypatch, capsys):
        # First install so the configs exist.
        _run_install(monkeypatch, home)
        claude_path = home / ".claude.json"
        cursor_path = home / ".cursor" / "mcp.json"
        codex_path = home / ".codex" / "config.toml"
        assert claude_path.exists() and cursor_path.exists() and codex_path.exists()

        self._run_uninstall(monkeypatch, home)
        out = capsys.readouterr().out
        assert "Would remove" in out
        assert "dry-run" in out
        # Files still present (dry-run).
        assert claude_path.exists()
        assert cursor_path.exists()
        # Codex TOML config not deleted, just block-stripped (would be in --yes pass).
        assert codex_path.exists()

    def test_yes_actually_removes_mcp_entries(self, home: Path, monkeypatch, capsys):
        _run_install(monkeypatch, home)
        claude_path = home / ".claude.json"
        cursor_path = home / ".cursor" / "mcp.json"
        codex_path = home / ".codex" / "config.toml"

        self._run_uninstall(monkeypatch, home, yes=True)
        out = capsys.readouterr().out
        assert "Removed:" in out

        # Files exist but Trinity entries gone.
        claude_cfg = json.loads(claude_path.read_text())
        assert "trinity-local" not in claude_cfg.get("mcpServers", {})
        cursor_cfg = json.loads(cursor_path.read_text())
        assert "trinity-local" not in cursor_cfg.get("mcpServers", {})
        codex_content = codex_path.read_text()
        assert "[mcp_servers.trinity-local]" not in codex_content

    def test_idempotent_when_nothing_installed(self, home: Path, monkeypatch, capsys):
        """Fresh tmp home: no install ever ran. Uninstall must not crash."""
        self._run_uninstall(monkeypatch, home, yes=True)
        out = capsys.readouterr().out
        assert "Nothing to remove" in out

    def test_include_data_flag_removes_trinity_home(self, home: Path, monkeypatch, capsys, tmp_path):
        """--include-data removes ~/.trinity/ (the corpus)."""
        trinity_dir = tmp_path / "trinity_home_uninstall"
        trinity_dir.mkdir()
        (trinity_dir / "marker.txt").write_text("data here")
        monkeypatch.setenv("TRINITY_HOME", str(trinity_dir))
        _run_install(monkeypatch, home)

        self._run_uninstall(monkeypatch, home, yes=True, include_data=True)
        assert not trinity_dir.exists(), "--include-data must remove ~/.trinity/"

    def test_include_data_omitted_preserves_trinity_home(self, home: Path, monkeypatch, capsys, tmp_path):
        """Default (no --include-data): ~/.trinity/ MUST be preserved.
        The 'own your data' wedge means uninstall doesn't touch user data
        unless they explicitly opted in."""
        trinity_dir = tmp_path / "trinity_home_preserved"
        trinity_dir.mkdir()
        (trinity_dir / "marker.txt").write_text("preserve me")
        monkeypatch.setenv("TRINITY_HOME", str(trinity_dir))
        _run_install(monkeypatch, home)

        self._run_uninstall(monkeypatch, home, yes=True)  # NO include_data
        assert trinity_dir.exists(), "uninstall without --include-data must preserve ~/.trinity/"
        assert (trinity_dir / "marker.txt").read_text() == "preserve me"

    def test_writes_codex_toml_config(self, home: Path, monkeypatch, capsys):
        # Codex CLI uses ~/.codex/config.toml with [mcp_servers.<name>] sections.
        _run_install(monkeypatch, home)

        codex_path = home / ".codex" / "config.toml"
        assert codex_path.exists(), "install-mcp must write Codex CLI config too"
        content = codex_path.read_text()
        assert "[mcp_servers.trinity-local]" in content
        assert 'args = ["-m", "trinity_local.main", "--mcp"]' in content

    def test_codex_toml_windows_path_round_trips_through_tomllib(self, home: Path):
        """A Windows interpreter path (backslashes) must produce VALID TOML.
        A basic (double-quoted) string would mangle `\\P` / `\\x` as escape
        sequences; the writer uses a literal (single-quoted) string, so tomllib
        parses it and the command round-trips byte-for-byte."""
        import pytest as _pytest

        tomllib = _pytest.importorskip("tomllib")  # 3.11+; CI runs 3.12
        from trinity_local.commands.install import _write_codex_toml_mcp_config

        target = home / ".codex" / "config.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        win_cmd = r"C:\Python312\python.exe"
        assert (
            _write_codex_toml_mcp_config(
                target, win_cmd, ["-m", "trinity_local.main", "--mcp"]
            )
            is True
        )
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))
        srv = parsed["mcp_servers"]["trinity-local"]
        assert srv["command"] == win_cmd  # backslashes preserved exactly
        assert srv["args"] == ["-m", "trinity_local.main", "--mcp"]

    def test_codex_toml_preserves_existing_config(self, home: Path, monkeypatch, capsys):
        codex_path = home / ".codex" / "config.toml"
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(
            'model = "gpt-5.5"\n'
            'model_reasoning_effort = "xhigh"\n'
            '\n'
            '[projects."/Users/me/work"]\n'
            'trust_level = "trusted"\n'
        )

        _run_install(monkeypatch, home)

        content = codex_path.read_text()
        # User config preserved
        assert 'model = "gpt-5.5"' in content
        assert "[projects.\"/Users/me/work\"]" in content
        # Trinity MCP block appended
        assert "[mcp_servers.trinity-local]" in content

    def test_codex_toml_idempotent_no_duplicate_blocks(self, home: Path, monkeypatch, capsys):
        # Re-running install-mcp must replace the prior trinity block, not stack.
        _run_install(monkeypatch, home)
        _run_install(monkeypatch, home)

        codex_path = home / ".codex" / "config.toml"
        content = codex_path.read_text()
        assert content.count("[mcp_servers.trinity-local]") == 1

    def test_codex_toml_strips_quoted_and_nested_prior_blocks(self, home: Path, monkeypatch, capsys):
        # User config might already have [mcp_servers."trinity-local"] (some
        # toolchains generate the quoted form) or nested .env subtables.
        # Re-running install-mcp must clean those out, not pile on top.
        codex_path = home / ".codex" / "config.toml"
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(
            'model = "gpt-5.5"\n'
            '\n'
            '[mcp_servers."trinity-local"]\n'
            'command = "/old/python"\n'
            'args = ["-m", "trinity_local.main", "--mcp"]\n'
            '\n'
            '[mcp_servers."trinity-local".env]\n'
            'TRINITY_HOME = "/tmp/old"\n'
            '\n'
            '[mcp_servers.trinity-local.env]\n'
            'STALE = "true"\n'
        )
        _run_install(monkeypatch, home)

        content = codex_path.read_text()
        # Only the canonical (unquoted) section remains; old blocks gone.
        assert content.count("[mcp_servers.") == 1
        assert "/old/python" not in content
        assert "TRINITY_HOME" not in content
        assert "STALE" not in content
        assert 'model = "gpt-5.5"' in content  # unrelated config preserved

    def test_codex_toml_strip_preserves_other_mcp_servers(self, home: Path, monkeypatch, capsys):
        """The strip-old-trinity-blocks rewrite must NOT take an adjacent
        non-trinity [mcp_servers.X] block with it. The sibling strip tests
        assert `count("[mcp_servers.") == 1`, which structurally can't catch
        this — they only seed trinity blocks. A real Codex user has OTHER MCP
        servers; an over-broad strip (e.g. deleting from `[mcp_servers.` to the
        next top-level section) would clobber them. Gemini's JSON path guards
        the same thing (playwright/github survive); this pins the harder TOML
        rewrite path. Manually confirmed against the live install 2026-05-31."""
        import pytest as _pytest

        tomllib = _pytest.importorskip("tomllib")  # 3.11+; CI runs 3.12
        codex_path = home / ".codex" / "config.toml"
        codex_path.parent.mkdir(parents=True, exist_ok=True)
        codex_path.write_text(
            'model = "gpt-5.5"\n'
            '\n'
            '[mcp_servers.playwright]\n'
            'command = "npx"\n'
            'args = ["@playwright/mcp@latest"]\n'
            '\n'
            '[mcp_servers.playwright.env]\n'
            'HEADLESS = "1"\n'
            '\n'
            '[mcp_servers."trinity-local"]\n'   # an OLD trinity block to strip
            'command = "/old/python"\n'
            'args = ["-m", "trinity_local.main", "--mcp"]\n',
            encoding="utf-8",
        )

        _run_install(monkeypatch, home)

        parsed = tomllib.loads(codex_path.read_text(encoding="utf-8"))
        servers = parsed.get("mcp_servers", {})
        # The user's OTHER server (and its nested env subtable) must survive.
        assert "playwright" in servers, "non-trinity mcp_servers block was clobbered by the strip"
        assert servers["playwright"]["command"] == "npx"
        assert servers["playwright"]["env"]["HEADLESS"] == "1"
        # Trinity re-added fresh; the stale /old/python block is gone.
        assert "trinity-local" in servers
        assert servers["trinity-local"]["command"] != "/old/python"
        # Unrelated top-level config preserved.
        assert parsed["model"] == "gpt-5.5"

    def test_install_mcp_announces_all_three_targets(self, home: Path, monkeypatch, capsys):
        _run_install(monkeypatch, home)
        out = capsys.readouterr().out
        assert ".claude.json" in out
        assert ".gemini/settings.json" in out
        assert "config.toml" in out


class TestInstallTrinitySkill:
    """install-mcp must drop ~/.claude/skills/trinity/SKILL.md so /trinity works
    without curl. Council council_d55953003bb29f9d (Claude won, high) named
    skill-not-installed-by-pip as the #1 launch risk and ratified this gate."""

    def test_drops_skill_into_user_skills_dir(self, home: Path, monkeypatch, capsys):
        skill_path = home / ".claude" / "skills" / "trinity" / "SKILL.md"
        assert not skill_path.exists()

        _run_install(monkeypatch, home)

        assert skill_path.exists(), "install-mcp must drop SKILL.md into ~/.claude/skills/trinity/"
        content = skill_path.read_text()
        assert "name: trinity" in content
        assert "trinity-local install-mcp" in content

    def test_skill_install_idempotent_when_unmodified(self, home: Path, monkeypatch, capsys):
        # First install drops the file; second install is a no-op.
        _run_install(monkeypatch, home)
        skill_path = home / ".claude" / "skills" / "trinity" / "SKILL.md"
        first = skill_path.read_text()

        _run_install(monkeypatch, home)
        assert skill_path.read_text() == first
        # No-op: install is announced but file isn't rewritten when content matches.
        # (We don't assert mtime equality — file may not be touched at all, OS-dependent.)

    def test_skill_install_does_not_clobber_user_edits(self, home: Path, monkeypatch, capsys):
        # User has customized the skill — install-mcp must not silently overwrite.
        skill_path = home / ".claude" / "skills" / "trinity" / "SKILL.md"
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        custom = "---\nname: trinity\n---\n# my custom version\n"
        skill_path.write_text(custom)

        _run_install(monkeypatch, home)

        assert skill_path.read_text() == custom, "user-edited SKILL.md must not be overwritten"
        out = capsys.readouterr().out
        assert "skipping" in out.lower() or "local edits" in out.lower()


def test_local_repo_skill_matches_packaged_skill():
    """Repo .claude/skills/trinity/SKILL.md and src/trinity_local/data/skills/trinity/SKILL.md
    must stay in sync — both copies serve the same skill, drift is silent failure.

    .claude/ is gitignored (dev-convenience copy). On CI checkouts it
    doesn't exist; skip there. On a developer's machine the file is
    present and the parity check fires."""
    repo_root = Path(__file__).resolve().parent.parent
    repo_copy = repo_root / ".claude" / "skills" / "trinity" / "SKILL.md"
    pkg_copy = repo_root / "src" / "trinity_local" / "data" / "skills" / "trinity" / "SKILL.md"
    if not repo_copy.exists():
        import pytest
        pytest.skip(".claude/skills/trinity/SKILL.md absent — likely CI checkout or fresh clone; canonical copies are skills/ + src/.../data/skills/ (both gitignored .claude/ is dev-only)")
    assert pkg_copy.exists()
    assert repo_copy.read_text() == pkg_copy.read_text(), (
        "skill drift: .claude/skills/trinity/SKILL.md and src/trinity_local/data/skills/trinity/SKILL.md "
        "must be byte-identical (sync the .claude/ copy from the package data after edits)"
    )
