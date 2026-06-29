"""Tests for provider adapter discovery."""
from __future__ import annotations

from pathlib import Path

from trinity_local.adapters import AdapterStatus, check_adapter, _count_transcripts


class TestAdapterStatus:
    def test_to_dict_strips_none(self):
        status = AdapterStatus(
            provider="claude",
            cli_name="claude",
            installed=True,
            version="1.0.0",
        )
        d = status.to_dict()
        assert d["provider"] == "claude"
        assert d["installed"] is True
        assert "error" not in d  # None values stripped

    def test_to_dict_keeps_false(self):
        status = AdapterStatus(
            provider="codex",
            cli_name="codex",
            installed=False,
            error="not found",
        )
        d = status.to_dict()
        assert d["installed"] is False
        assert d["error"] == "not found"


class TestCountTranscripts:
    def test_empty_dir(self, tmp_path):
        assert _count_transcripts(tmp_path, "*.json") == 0

    def test_nonexistent_dir(self, tmp_path):
        assert _count_transcripts(tmp_path / "nope", "*.json") == 0

    def test_counts_matching(self, tmp_path):
        (tmp_path / "session-001.json").write_text("{}")
        (tmp_path / "session-002.json").write_text("{}")
        (tmp_path / "other.txt").write_text("nope")
        assert _count_transcripts(tmp_path, "session-*.json") == 2

    def test_glob_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "session-deep.json").write_text("{}")
        assert _count_transcripts(tmp_path, "**/session-*.json") == 1


class TestCheckAdapter:
    def test_missing_cli(self):
        spec = {
            "provider": "testprov",
            "cli_name": "nonexistent-cli-12345",
            "version_args": ["nonexistent-cli-12345", "--version"],
            "transcript_root": lambda: Path("/tmp/nonexistent-trinity-test"),
            "glob": "*.json",
        }
        status = check_adapter(spec)
        assert status.provider == "testprov"
        assert status.installed is False
        assert status.error is not None

    def test_desktop_app_missing(self, tmp_path):
        spec = {
            "provider": "cowork",
            "cli_name": "claude-desktop",
            "version_args": None,
            "transcript_root": lambda: tmp_path / "nonexistent",
            "glob": "local_*.json",
        }
        status = check_adapter(spec)
        assert status.installed is False

    def test_desktop_app_present(self, tmp_path):
        transcript_dir = tmp_path / "sessions"
        transcript_dir.mkdir()
        (transcript_dir / "local_001.json").write_text("{}")
        spec = {
            "provider": "cowork",
            "cli_name": "claude-desktop",
            "version_args": None,
            "transcript_root": lambda: transcript_dir,
            "glob": "local_*.json",
        }
        status = check_adapter(spec)
        assert status.installed is True
        assert status.transcript_count == 1
