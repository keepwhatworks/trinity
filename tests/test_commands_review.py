from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local.commands import review as review_cmd


def test_reviewer_command_for_uses_defaults_when_config_missing(monkeypatch: pytest.MonkeyPatch):
    def fake_load_config(_path):
        raise FileNotFoundError

    monkeypatch.setattr(review_cmd, "load_config", fake_load_config)

    assert review_cmd._reviewer_command_for(reviewer="antigravity", config_path=None) == ["agy", "-p"]
    assert review_cmd._reviewer_command_for(reviewer="custom", config_path=None) == ["custom"]


def test_reviewer_command_for_prefers_config(monkeypatch: pytest.MonkeyPatch):
    config = SimpleNamespace(
        providers={
            "antigravity": SimpleNamespace(command=["/tmp/gemini", "--model", "pro"]),
        }
    )

    monkeypatch.setattr(review_cmd, "load_config", lambda _path: config)

    assert review_cmd._reviewer_command_for(reviewer="antigravity", config_path=None) == [
        "/tmp/gemini",
        "--model",
        "pro",
    ]
