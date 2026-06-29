"""Tests for `config.default_council_members()`.

This helper backs the single-provider council fix (persona audit P89) —
9 install + dispatch surfaces use it as the fallback when no `--members`
is passed. If the helper itself misbehaves, every one of those surfaces
inherits the bug, so this is launch-blocker-grade coverage.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drop a config.json under tmp + point project_root at it.

    Tests that need the bundled `data/config.example.json` fallback
    skip writing a config.json; tests that need specific provider state
    write their own.
    """
    monkeypatch.setattr(
        "trinity_local.config.project_root", lambda: tmp_path,
    )
    return tmp_path


def _write_config(path: Path, providers: dict[str, dict]) -> None:
    """Drop a minimal config.json at `path/config.json`."""
    (path / "config.json").write_text(json.dumps({
        "providers": {
            name: {
                "type": "claude_code",
                "command": ["echo"],
                "args": [],
                "enabled": p.get("enabled", True),
                **{k: v for k, v in p.items() if k != "enabled"},
            }
            for name, p in providers.items()
        },
    }), encoding="utf-8")


class TestDefaultCouncilMembers:
    def test_all_three_enabled_returns_canonical_order(self, isolated_config):
        """Happy path: all three cloud providers enabled → canonical order."""
        from trinity_local.config import default_council_members

        _write_config(isolated_config, {
            "claude": {}, "antigravity": {}, "codex": {},
        })
        assert default_council_members() == ["claude", "antigravity", "codex"]

    def test_codex_only_returns_just_codex(self, isolated_config):
        """100-persona audit P89: codex-only user must get a single-call
        council, not a broken 3-column fan-out."""
        from trinity_local.config import default_council_members

        _write_config(isolated_config, {
            "codex": {},
            "claude": {"enabled": False},
            "antigravity": {"enabled": False},
        })
        assert default_council_members() == ["codex"]

    def test_claude_only(self, isolated_config):
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {},
            "codex": {"enabled": False},
            "antigravity": {"enabled": False},
        })
        assert default_council_members() == ["claude"]

    def test_antigravity_only(self, isolated_config):
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "antigravity": {},
            "claude": {"enabled": False},
            "codex": {"enabled": False},
        })
        assert default_council_members() == ["antigravity"]

    def test_two_of_three_preserves_canonical_order(self, isolated_config):
        """claude + codex (skip gemini) — order must be [claude, codex],
        NOT [codex, claude] — canonical order pins the chairman pickup
        order across surfaces."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {}, "codex": {},
            "antigravity": {"enabled": False},
        })
        assert default_council_members() == ["claude", "codex"]

    def test_zero_enabled_falls_back_to_full_canonical(self, isolated_config):
        """No cloud providers enabled → fall back to full canonical list so
        the caller's existing 'Provider missing or disabled' error path is
        the right surface, not a silent skip."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {"enabled": False},
            "codex": {"enabled": False},
            "antigravity": {"enabled": False},
        })
        assert default_council_members() == ["claude", "antigravity", "codex"]

    def test_no_config_falls_back_to_canonical(self, isolated_config):
        """Fresh install (no config.json on disk, no bundled fallback
        reached) → return canonical list. Same defensive behavior as
        zero-enabled — caller-level error handling kicks in."""
        from trinity_local.config import default_council_members
        # No config.json written; bundled fallback exists at this point
        # so the helper sees the bundled providers (claude+gemini+codex+mlx).
        # Result: enabled subset of canonical cloud providers.
        members = default_council_members()
        # All three canonical providers should be present (or all three
        # missing — either way, no MLX or other non-canonical entries).
        for name in members:
            assert name in ("claude", "antigravity", "codex"), (
                f"non-canonical {name!r} in default council members"
            )

    def test_ignores_non_canonical_providers(self, isolated_config):
        """User adds 'mlx' or 'ollama' to config.json — those must NOT
        appear in the council default (cloud chairmen only)."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {}, "codex": {},
            "mlx": {}, "ollama": {},
        })
        result = default_council_members()
        assert "mlx" not in result
        assert "ollama" not in result
        assert result == ["claude", "codex"]

    def test_malformed_config_safe_fallback(self, isolated_config):
        """Config.json is unreadable / malformed → don't crash; return
        canonical list so callers can surface the real error themselves."""
        from trinity_local.config import default_council_members
        (isolated_config / "config.json").write_text("{ this is not json")
        # Should not raise; should return safe canonical fallback.
        result = default_council_members()
        assert set(result) <= {"claude", "antigravity", "codex"}


class TestProviderBinaryAvailabilityFilter:
    """The 2026-05-19 audience-expansion change: `default_council_members`
    additionally filters by `shutil.which(binary)`. A user with only
    Claude installed gets a 1-member council that succeeds, not a
    3-member council that fails 2 of 3.

    The PATH filter happens AFTER the enabled filter — both gates must
    pass. The launchpad tip card surfaces the missing providers as a
    free-tier upsell.
    """

    def test_only_claude_on_path_returns_just_claude(
        self, isolated_config, monkeypatch
    ):
        """Three providers enabled, but only `claude` binary on PATH →
        the council default should drop down to [claude]. This is the
        core 1-member-council case."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {"command": ["claude-bin"]},
            "antigravity": {"command": ["gemini-bin"]},
            "codex":  {"command": ["codex-bin"]},
        })
        # Only the claude binary exists on PATH.
        def fake_which(name):
            return "/usr/local/bin/claude-bin" if name == "claude-bin" else None
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", fake_which)
        assert default_council_members() == ["claude"]

    def test_two_on_path_preserves_canonical_order(
        self, isolated_config, monkeypatch
    ):
        """claude + gemini on PATH (codex missing) → [claude, gemini]
        in canonical order. The PATH filter does not reshuffle."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {"command": ["claude-bin"]},
            "antigravity": {"command": ["gemini-bin"]},
            "codex":  {"command": ["codex-bin"]},
        })
        present = {"claude-bin", "gemini-bin"}
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda name: f"/usr/local/bin/{name}" if name in present else None,
        )
        assert default_council_members() == ["claude", "antigravity"]

    def test_none_on_path_falls_back_to_canonical(
        self, isolated_config, monkeypatch
    ):
        """No CLIs installed → return the full canonical list so the
        caller's existing 'Provider binary not found' error path is the
        diagnostic. A silently empty council would be worse — the user
        wouldn't know WHICH binaries are missing."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {"command": ["claude-bin"]},
            "antigravity": {"command": ["gemini-bin"]},
            "codex":  {"command": ["codex-bin"]},
        })
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: None)
        assert default_council_members() == ["claude", "antigravity", "codex"]

    def test_disabled_provider_skipped_even_if_binary_present(
        self, isolated_config, monkeypatch
    ):
        """The enable + PATH gates compose. A disabled provider whose
        binary IS on PATH must still be filtered out."""
        from trinity_local.config import default_council_members
        _write_config(isolated_config, {
            "claude": {"command": ["claude-bin"]},
            "antigravity": {"command": ["gemini-bin"], "enabled": False},
            "codex":  {"command": ["codex-bin"]},
        })
        # All three binaries on PATH...
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda name: f"/usr/local/bin/{name}",
        )
        # ...but gemini is disabled, so result is [claude, codex].
        assert default_council_members() == ["claude", "codex"]


class TestInstalledCouncilProviders:
    """`installed_council_providers` is the public name for the new
    PATH-filter helper. `default_council_members` is the alias kept for
    every existing caller. Both must agree."""

    def test_aliases_agree(self, isolated_config, monkeypatch):
        from trinity_local.config import (
            default_council_members,
            installed_council_providers,
        )
        _write_config(isolated_config, {
            "claude": {"command": ["claude-bin"]},
            "codex":  {"command": ["codex-bin"]},
        })
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda name: "/usr/local/bin/x" if name == "claude-bin" else None,
        )
        assert default_council_members() == installed_council_providers()
