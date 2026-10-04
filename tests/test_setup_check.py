"""`trinity-local install --check`: the read-only three-CLI onboarding check (amd_0280)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from trinity_local import setup_check as sc


@pytest.fixture
def home(patch_trinity_home: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: patch_trinity_home)
    return patch_trinity_home


class TestIsRegistered:
    def test_absent_config_is_not_registered(self, home):
        for provider, _, _ in sc.MEMBERS:
            assert sc.is_registered(provider) is False

    def test_json_harnesses(self, home):
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"trinity-local": {"command": "x"}}}))
        (home / ".gemini").mkdir()
        (home / ".gemini" / "settings.json").write_text(json.dumps({"mcpServers": {"other": {}}}))
        assert sc.is_registered("claude") is True
        assert sc.is_registered("antigravity") is False

    @pytest.mark.parametrize("toml, expected", [
        ('[mcp_servers.trinity-local]\ncommand = "x"\n', True),
        ('[mcp_servers."trinity-local"]\ncommand = "x"\n', True),
        ('[mcp_servers]\ntrinity-local = { command = "x", args = [] }\n', True),
        ('[mcp_servers.other]\ncommand = "x"\n', False),
    ])
    def test_codex_toml_shapes(self, home, toml, expected):
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text(toml)
        assert sc.is_registered("codex") is expected

    def test_unreadable_config_is_none_not_false(self, home):
        """A corrupt config is a different fix from a missing registration."""
        (home / ".claude.json").write_text("{not json")
        assert sc.is_registered("claude") is None

    def test_unreadable_config_fix_is_a_command_that_finds_the_error(self, home):
        import subprocess
        (home / ".claude.json").write_text("{not json")
        cmd = sc.unreadable_config_fix("claude").split("   #")[0]
        r = subprocess.run(cmd.replace("python3", sys.executable, 1), shell=True, capture_output=True, text=True)
        assert r.returncode != 0 and "line 1" in (r.stderr + r.stdout)
        (home / ".claude.json").write_text("{}")
        assert subprocess.run(cmd.replace("python3", sys.executable, 1), shell=True).returncode == 0


class TestAdviceClosure:
    def test_install_mcp_clears_not_registered(self, home, monkeypatch):
        """The fix the check prints for 'NOT registered' must clear it."""
        from trinity_local.commands import install as _install
        assert all(sc.is_registered(p) is False for p, _, _ in sc.MEMBERS)
        monkeypatch.setattr(_install, "_resolve_mcp_command",
                            lambda: (str(sys.executable), ["-m", "trinity_local.main", "--mcp"]))
        _install.handle_install_mcp(SimpleNamespace(scope="user"))
        assert all(sc.is_registered(p) is True for p, _, _ in sc.MEMBERS)


class TestNewerFamilies:
    LISTED = ["gemini-3.8-flash-high", "gemini-3.1-pro-high", "gemini-4-argon-high", "claude-opus-4-6-thinking"]

    def test_flags_a_newer_major_family_only(self):
        assert sc.newer_families("gemini-3.8-flash-high", self.LISTED) == ["gemini-4-argon-high"]

    def test_reads_display_names_too(self):
        assert sc.newer_families("Gemini 3.1 Pro (high)", self.LISTED) == ["gemini-4-argon-high"]

    def test_same_family_is_not_newer(self):
        assert sc.newer_families("gemini-3.1-pro-high", ["gemini-3.8-flash-high"]) == []

    def test_unknown_member_model_flags_nothing(self):
        assert sc.newer_families(None, self.LISTED) == []


def _adapter(installed=True, count=3):
    return SimpleNamespace(installed=installed, version="1.0" if installed else None, transcript_count=count)


def _config(model="gemini-3.8-flash-high", enabled=True):
    return SimpleNamespace(providers={"antigravity": SimpleNamespace(model=model, enabled=enabled)})


class TestCheckMember:
    def test_missing_cli_gets_the_install_command(self, home):
        c = sc.check_member("codex", "codex", "Codex CLI", adapter=_adapter(installed=False), config=None)
        assert not c.ready
        assert any("npm install -g @openai/codex" in f for f in c.fixes)
        assert "trinity-local install-mcp" in c.fixes

    def test_newer_model_is_reported_never_switched(self, home):
        c = sc.check_member("antigravity", "agy", "agy", adapter=_adapter(), config=_config(),
                          listed=["gemini-4-argon-high"])
        assert c.newer_models == ["gemini-4-argon-high"]
        assert c.member_model == "gemini-3.8-flash-high"           # untouched
        assert any("does not switch on its own" in n for n in c.notes)

    def test_disabled_member_is_not_ready(self, home):
        c = sc.check_member("antigravity", "agy", "agy", adapter=_adapter(), config=_config(enabled=False), listed=[])
        assert not c.member_enabled and not c.ready
        assert "trinity-local config --set providers.antigravity.enabled=true" in c.fixes


class TestRunCheck:
    def test_writes_nothing_and_names_its_config_source(self, home, monkeypatch):
        monkeypatch.setattr(sc, "agy_listed_models", lambda: [])
        before = sorted(str(p) for p in home.rglob("*"))
        report = sc.run_check()
        assert sorted(str(p) for p in home.rglob("*")) == before
        assert report["writes_nothing"] is True
        assert report["config_source"].startswith("bundled defaults")   # isolated home has no config.json
        assert len(report["members"]) == 3
        assert any("council" in a for a in report["next"]["asks"]) and any("Verify" in a for a in report["next"]["asks"])

    def test_council_ready_needs_two(self, home, monkeypatch):
        monkeypatch.setattr(sc, "agy_listed_models", lambda: [])
        report = sc.run_check()
        assert report["council_ready"] == (len(report["ready"]) >= 2)

    def test_cli_flag_prints_json(self, home, monkeypatch, capsys):
        from trinity_local.commands.install_umbrella import handle_install_umbrella
        monkeypatch.setattr(sc, "agy_listed_models", lambda: [])
        assert handle_install_umbrella(SimpleNamespace(check=True, as_json=True)) == 0
        assert json.loads(capsys.readouterr().out)["writes_nothing"] is True


def test_enable_fix_clears_a_disabled_member(tmp_path, monkeypatch):
    """Advice closure: the printed `config --set ... enabled=true` re-enables the member."""

    import subprocess
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.config import load_config
    src = Path(sc.__file__).parent / "data" / "config.example.json"
    cfg = json.loads(src.read_text())
    cfg["providers"]["antigravity"]["enabled"] = False
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    assert not load_config().providers["antigravity"].enabled
    c = sc.check_member("antigravity", "agy", "agy", adapter=_adapter(), config=load_config(), listed=[])
    fix = next(f for f in c.fixes if "--set" in f)
    args = fix.split()[1:]                                   # drop the leading `trinity-local`
    env = dict(__import__("os").environ, TRINITY_HOME=str(tmp_path),
               PYTHONPATH=str(Path(sc.__file__).parents[1]))
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", *args], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert load_config().providers["antigravity"].enabled



class TestNewUserGemini:
    def test_gemini_4_pro_wins_when_served(self):
        listed = ["gemini-3.8-flash-high", "gemini-3.1-pro-high", "gemini-4-flash-high",
                  "gemini-4-pro-low", "gemini-4-pro-high", "claude-opus-4-6-thinking"]
        assert sc.best_gemini(listed) == "gemini-4-pro-high"

    def test_today_newest_version_first(self):
        """Without Gemini 4 the newest 3.x wins over an older Pro (founder kept 3.8 Flash)."""
        assert sc.best_gemini(["gemini-3.8-flash-high", "gemini-3.1-pro-high", "gemini-3.8-flash-low"]) == "gemini-3.8-flash-high"

    def test_nothing_listed(self):
        assert sc.best_gemini([]) is None

    def test_new_user_config_is_seeded_once_and_never_rewritten(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        msg = sc.ensure_new_user_config(listed=["gemini-4-pro-high", "gemini-3.8-flash-high"])
        cfg = json.loads((tmp_path / "config.json").read_text())
        assert cfg["providers"]["antigravity"]["model"] == "gemini-4-pro-high" and "gemini-4-pro-high" in msg
        cfg["providers"]["antigravity"]["model"] = "my-choice"
        (tmp_path / "config.json").write_text(json.dumps(cfg))
        assert sc.ensure_new_user_config(listed=["gemini-5-pro-high"]) is None       # existing user: untouched
        assert json.loads((tmp_path / "config.json").read_text())["providers"]["antigravity"]["model"] == "my-choice"


class TestUpdateOnUvInstall:
    """`update` on a one-line (uv tool) install re-runs the installer instead of
    failing with 'not a git checkout'."""

    def test_check_names_the_installer(self, tmp_path, monkeypatch, capsys):
        from trinity_local.commands import update as upd
        from trinity_local.facts import INSTALL_COMMAND
        (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        assert upd.handle_update(SimpleNamespace(deps=False, check=True, json=False, skill_dir=None)) == 0
        assert INSTALL_COMMAND in capsys.readouterr().out

    def test_update_reruns_the_installer(self, tmp_path, monkeypatch):
        import subprocess

        from trinity_local.commands import update as upd
        from trinity_local.facts import INSTALL_COMMAND
        (tmp_path / "uv-receipt.toml").write_text("[tool]\n")
        monkeypatch.setattr(sys, "prefix", str(tmp_path))
        ran = []
        monkeypatch.setattr(subprocess, "run", lambda a, **k: ran.append(a) or SimpleNamespace(returncode=0))
        assert upd.handle_update(SimpleNamespace(deps=False, check=False, json=False, skill_dir=None)) == 0
        assert ran == [["sh", "-c", INSTALL_COMMAND]]
