"""The config surface: a pip user must be able to change providers.

`project_root()` is `Path(__file__).parents[2]`, which for a wheel install is
the Python lib directory. Before this surface existed, config.json resolved
somewhere unwritable and `default_primary_provider` — the whole "send every
council to one subscription" story — was unreachable for anyone who installed
from PyPI rather than cloning.
"""
from __future__ import annotations

import argparse
import json

import pytest

from trinity_local.commands import config_cmd
from trinity_local.config import config_path, load_config


def _args(**kw):
    ns = argparse.Namespace(config=None, init=False, path=False,
                            assignments=[], as_json=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _init(home):
    assert config_cmd.handle_config(_args(init=True)) == 0
    return home / "config.json"


class TestResolution:
    def test_state_dir_config_wins(self, home):
        cfg = home / "config.json"
        cfg.write_text(json.dumps({"providers": {}}))
        assert config_path(None) == cfg

    def test_state_dir_beats_repo_root_without_trinity_home_set(self, tmp_path, monkeypatch):
        """The pip-install case: no TRINITY_HOME, a repo checkout present.

        Without the state-dir preference the user's own ~/.trinity/config.json
        is ignored in favour of a checkout they may not even own, which is the
        unreachable-config bug this surface exists to fix.
        """
        from trinity_local import config as config_mod
        monkeypatch.delenv("TRINITY_HOME", raising=False)
        state, repo = tmp_path / "state", tmp_path / "repo"
        state.mkdir(); repo.mkdir()
        (state / "config.json").write_text(json.dumps({"providers": {}}))
        (repo / "config.json").write_text(json.dumps({"providers": {}}))
        monkeypatch.setattr(config_mod, "trinity_home", lambda: state)
        monkeypatch.setattr(config_mod, "project_root", lambda: repo)
        assert config_path(None) == state / "config.json"

    def test_explicit_home_never_reaches_repo_root(self, home):
        """An explicit TRINITY_HOME is an isolation contract.

        Regression: `config --set` wrote the developer's checked-out
        config.json while running under an isolated home.
        """
        from trinity_local import config as config_mod
        repo = home / "fake_repo"
        repo.mkdir()
        (repo / "config.json").write_text(json.dumps({"providers": {}}))
        monkey = repo
        orig = config_mod.project_root
        config_mod.project_root = lambda: monkey
        try:
            resolved = config_path(None)
        finally:
            config_mod.project_root = orig
        assert resolved == home / "config.json"
        assert resolved != repo / "config.json"

    def test_init_creates_in_state_dir_and_is_idempotent(self, home, capsys):
        cfg = _init(home)
        assert cfg.exists()
        first = cfg.read_text()
        assert config_cmd.handle_config(_args(init=True)) == 0
        assert "already exists" in capsys.readouterr().out
        assert cfg.read_text() == first


class TestSet:
    def test_model_set_strips_conflicting_inline_flag(self, home):
        """`_reconcile_model_arg` makes an inline `--model X` authoritative.

        Editing only the `model` field would dispatch one model and record
        another, which is exactly the poisoning that function detects.
        """
        cfg = _init(home)
        before = json.loads(cfg.read_text())["providers"]["codex"]["args"]
        assert "--model" in before, "fixture must carry the inline flag to be meaningful"

        assert config_cmd.handle_config(
            _args(assignments=["providers.codex.model=gpt-6-astra"])) == 0

        codex = json.loads(cfg.read_text())["providers"]["codex"]
        assert codex["model"] == "gpt-6-astra"
        assert "--model" not in codex["args"]
        assert not any(str(a).startswith("--model=") for a in codex["args"])
        assert "--sandbox" in codex["args"], "unrelated args must survive"
        assert load_config(None).providers["codex"].model == "gpt-6-astra"

    def test_chair_round_trips_through_load_config(self, home):
        _init(home)
        assert config_cmd.handle_config(
            _args(assignments=["default_primary_provider=codex"])) == 0
        assert load_config(None).default_primary_provider == "codex"

    def test_disabling_a_provider_round_trips(self, home):
        _init(home)
        assert config_cmd.handle_config(
            _args(assignments=["providers.claude.enabled=false"])) == 0
        assert load_config(None).providers["claude"].enabled is False

    def test_backup_written_before_overwrite(self, home):
        cfg = _init(home)
        original = cfg.read_text()
        config_cmd.handle_config(_args(assignments=["default_primary_provider=codex"]))
        assert (home / "config.json.bak").read_text() == original


class TestRefusals:
    """A typo'd key is silently ignored by load_config, so an unvalidated
    --set would report success for a setting that never applies."""

    @pytest.mark.parametrize("assignment,fragment", [
        ("providers.nope.model=x", "no provider"),
        ("typo=1", "unknown setting"),
        ("providers.codex.command=sh", "cannot set"),
        ("providers.codex.args=--x", "cannot set"),
        ("noequalssign", "KEY=VALUE"),
        ("providers.codex.enabled=maybe", "true/false"),
    ])
    def test_refused_with_nonzero_exit(self, home, assignment, fragment, capsys):
        cfg = _init(home)
        before = cfg.read_text()
        rc = config_cmd.handle_config(_args(assignments=[assignment]))
        assert rc == 1, f"{assignment!r} must be refused"
        assert fragment in capsys.readouterr().out
        assert cfg.read_text() == before, "a refused set must not write"

    def test_set_without_config_refuses(self, home, capsys):
        rc = config_cmd.handle_config(_args(assignments=["default_primary_provider=codex"]))
        assert rc == 1
        assert "--init" in capsys.readouterr().out
