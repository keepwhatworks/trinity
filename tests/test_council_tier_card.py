"""Tests for the launchpad council-tier card.

Pillar of the "works with 1, sells the other two" pitch: shows the
user which canonical providers they have installed and what the next
free-tier add unlocks. Pairs with the PATH-filter change to
`default_council_members` — same `shutil.which` source of truth.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def fake_which():
    """Yield a controllable shutil.which patch. Test writes the set of
    "installed" binaries, fake_which returns a path for those and None
    for everything else."""
    installed: set[str] = set()

    def _which(name: str, **_kw):
        return f"/usr/local/bin/{name}" if name in installed else None

    # The tier card + lineup + dispatch gate all resolve via
    # runtime_env.which_on_runtime_path (the enriched-PATH resolver), imported
    # locally inside each function — patch it at the source module.
    with patch(
        "trinity_local.runtime_env.which_on_runtime_path", side_effect=_which
    ) as p:
        yield installed
        del p


# ─── Tier resolution ────────────────────────────────────────────────

class TestCouncilTierStatus:
    def test_all_three_installed_tier_3_card_hidden(self, monkeypatch):
        """Happy path: all three canonical providers on PATH → tier 3,
        show=False, the card stays hidden on the launchpad.

        Note: Antigravity's binary is `agy` (not `antigravity`) — the
        slug→binary mapping lives in `_TIER_PROVIDER_BINARY`."""
        from trinity_local.launchpad_data import _council_tier_status
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda n: f"/usr/local/bin/{n}" if n in {"claude", "codex", "agy"} else None,
        )
        status = _council_tier_status()
        assert status["tier"] == 3
        assert set(status["installed"]) == {"claude", "codex", "antigravity"}
        assert status["missing"] == []
        assert status["nextStep"] is None
        assert status["show"] is False

    def test_one_installed_pitches_second(self, monkeypatch):
        """User has only Claude → tier 1, card pitches Codex as the
        next step (adversarial 2nd voice)."""
        from trinity_local.launchpad_data import _council_tier_status
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda n: f"/usr/local/bin/{n}" if n == "claude" else None,
        )
        status = _council_tier_status()
        assert status["tier"] == 1
        assert status["installed"] == ["claude"]
        assert status["show"] is True
        assert status["nextStep"] is not None
        assert status["nextStep"]["provider"] == "codex"
        # Install command must be runnable verbatim — no placeholders.
        assert "npm install" in status["nextStep"]["installCommand"]
        # Headline name-checks the installed provider so the user sees
        # what they have, not just what they're missing.
        assert "Claude Code" in status["headline"]

    def test_two_installed_pitches_third(self, monkeypatch):
        """User has Claude + Codex (no Antigravity) → tier 2, card pitches
        Antigravity to complete the canonical council."""
        from trinity_local.launchpad_data import _council_tier_status
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda n: f"/usr/local/bin/{n}" if n in {"claude", "codex"} else None,
        )
        status = _council_tier_status()
        assert status["tier"] == 2
        assert status["installed"] == ["claude", "codex"]
        assert status["nextStep"]["provider"] == "antigravity"
        assert "Complete" in status["headline"] or "council" in status["headline"].lower()
        assert status["show"] is True

    def test_only_codex_installed_pitches_claude_first(self, monkeypatch):
        """User installed Codex but not Claude → next pitch must be
        Claude (the canonical anchor voice), not Antigravity. Order
        preference: claude > codex > antigravity per chairman convention."""
        from trinity_local.launchpad_data import _council_tier_status
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda n: f"/usr/local/bin/{n}" if n == "codex" else None,
        )
        status = _council_tier_status()
        assert status["nextStep"]["provider"] == "claude"

    def test_none_installed_tier_0_still_shows_card(self, monkeypatch):
        """Cold install with nothing on PATH → tier 0. Show the card so
        the user has a clear next step; first pitch is Claude (anchor
        voice)."""
        from trinity_local.launchpad_data import _council_tier_status
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda n: None)
        status = _council_tier_status()
        assert status["tier"] == 0
        assert status["installed"] == []
        assert status["show"] is True
        assert status["nextStep"]["provider"] == "claude"

    def test_install_commands_are_known_free_tier_paths(self, monkeypatch):
        """All three pitched commands must be the free-tier install
        paths — no API keys, no paid tiers in the install help. This
        is the marketing claim ('every provider has a free tier');
        if a command says 'sign up at <url>' or asks for an API key,
        the claim is broken."""
        from trinity_local.launchpad_data import _TIER_INSTALL_HELP
        for provider, (label, command, value) in _TIER_INSTALL_HELP.items():
            # No "buy", "subscribe", "API key" — those would imply paid.
            lowered = command.lower()
            for forbidden in ("api key", "subscribe", "purchase", "buy "):
                assert forbidden not in lowered, (
                    f"{provider}: install command must not require a paid tier "
                    f"to claim free-tier coverage — got {command!r}"
                )
            # Value proposition must actually exist — empty strings
            # break the card UI.
            assert value, f"{provider}: missing value proposition"
            assert label, f"{provider}: missing display label"


# ─── Page data integration ─────────────────────────────────────────

class TestCouncilTierInPageData:
    def test_page_data_includes_council_tier(self, monkeypatch, tmp_path):
        """The page-data builder must expose `councilTier` so the
        Vue scope can render the card. Drift here = card silently
        absent from the launchpad."""
        from trinity_local import launchpad_data

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda n: None)
        # Stub out the heavier dependencies — we're testing data shape,
        # not the live council outcome loaders.
        monkeypatch.setattr(launchpad_data, "build_elo_snapshot", lambda: {})
        monkeypatch.setattr(launchpad_data, "_elo_chart_data", lambda s: {})
        monkeypatch.setattr(launchpad_data, "get_global_benchmarks", lambda: {})
        monkeypatch.setattr(launchpad_data, "_provider_health_data", lambda: {
            "providers": [], "missingCount": 0, "hasMissing": False, "footerNote": ""
        })
        monkeypatch.setattr(launchpad_data, "_active_launchpad_operation", lambda: None)
        monkeypatch.setattr(launchpad_data, "_load_personal_routing_table", lambda: {})

        data = launchpad_data.build_page_data(
            live_review_path=tmp_path / "live.html",
            recent_councils=[],
        )
        assert "councilTier" in data, (
            "build_page_data must include councilTier — the launchpad "
            "template gates the tier card on this field."
        )
        tier = data["councilTier"]
        assert "tier" in tier
        assert "installed" in tier
        assert "missing" in tier
        assert "headline" in tier
        assert "nextStep" in tier
        assert "show" in tier

    def test_page_data_default_members_reflects_filtered_providers(
        self, monkeypatch, tmp_path
    ):
        """The launchpad 'Run a Council' card dispatches `defaultMembers`. It
        MUST be the install-filtered lineup (config.default_council_members),
        NOT the full canonical list — otherwise a tier-1 user (only Claude on
        PATH) is promised a 3-member council that then fails 2 of 3. No test
        covered this integration (grep defaultMembers tests/ → nothing); a
        refactor of the assignment could silently re-introduce the misleading
        promise. Verified live 2026-05-31: tier-1 build_page_data →
        defaultMembers == ['claude']."""
        from trinity_local import launchpad_data

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(launchpad_data, "build_elo_snapshot", lambda: {})
        monkeypatch.setattr(launchpad_data, "_elo_chart_data", lambda s: {})
        monkeypatch.setattr(launchpad_data, "get_global_benchmarks", lambda: {})
        monkeypatch.setattr(launchpad_data, "_provider_health_data", lambda: {
            "providers": [], "missingCount": 0, "hasMissing": False, "footerNote": ""
        })
        monkeypatch.setattr(launchpad_data, "_active_launchpad_operation", lambda: None)
        monkeypatch.setattr(launchpad_data, "_load_personal_routing_table", lambda: {})

        # Tier 1: only the `claude` binary on PATH.
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path", lambda n: "/x/" + n if n == "claude" else None
        )
        data = launchpad_data.build_page_data(
            live_review_path=tmp_path / "live.html", recent_councils=[],
        )
        members = data["defaultMembers"]
        assert members == ["claude"], (
            "launchpad defaultMembers must be the install-filtered lineup, not "
            f"the full 3-provider canonical list; got {members!r}"
        )
        assert "codex" not in members and "antigravity" not in members, (
            "a 1-provider user must not be promised a council with uninstalled "
            "providers (it would fail 2 of 3 on dispatch)"
        )
