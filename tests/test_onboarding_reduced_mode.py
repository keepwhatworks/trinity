"""Onboarding guide (#25): when <2 providers are authenticated, a council is a
single voice + chairman, NOT the cross-provider deliberation Trinity sells. The
guide must surface that at the moment of use (the MCP `run_council` response),
reading from the SAME detector the `status` row and launchpad card use so the
three surfaces can't disagree.

Every test here is mutation-proven: it reds when the guard it defends is
deleted. See the assertions' inline notes for which mutation each catches.
"""
from __future__ import annotations

import trinity_local.health_checks as hc
import trinity_local.mcp_server as mcp


def _fake_authed(authed: set[str]):
    """A `_check_provider` stand-in that reports exactly `authed` as ready.
    Both `authed_providers` and (through it) the guidance detector route
    through `_check_provider`, so this is the single seam to control."""
    def _check(provider: str, cli_name: str) -> hc.CheckResult:
        return hc.CheckResult(name=f"provider:{provider}", ok=provider in authed)
    return _check


class TestAuthedProviders:
    def test_counts_only_the_authed(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude", "codex"}))
        assert hc.authed_providers() == ["claude", "codex"]

    def test_empty_when_none_authed(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed(set()))
        assert hc.authed_providers() == []


class TestReducedModeGuidance:
    def test_two_authed_returns_none(self, monkeypatch):
        # Mutation guard: deleting the `len(ready) >= 2 -> None` branch makes
        # this return a block instead of None → this test reds.
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude", "antigravity"}))
        assert hc.council_reduced_mode_guidance() is None

    def test_three_authed_returns_none(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude", "codex", "antigravity"}))
        assert hc.council_reduced_mode_guidance() is None

    def test_one_authed_names_reduced_mode_and_the_lone_provider(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude"}))
        g = hc.council_reduced_mode_guidance()
        assert g is not None
        assert g["authed"] == ["claude"]
        # The message must NAME the reduced state and the lone provider — a
        # generic "add more providers" nag would not teach what's missing.
        assert "REDUCED" in g["detail"]
        assert "claude" in g["detail"]
        assert g["fix"]  # a non-empty next action

    def test_zero_authed_is_distinct_from_one(self, monkeypatch):
        # 0-authed must not claim a council "runs in reduced mode" — it can't
        # run at all. Distinct detail; empty authed list.
        monkeypatch.setattr(hc, "_check_provider", _fake_authed(set()))
        g = hc.council_reduced_mode_guidance()
        assert g is not None
        assert g["authed"] == []
        assert "cannot run" in g["detail"]
        assert "REDUCED" not in g["detail"]


class TestCheckCouncilBreadthDelegates:
    """The `status` row must read from the same detector — one edit to the copy
    updates both surfaces."""

    def test_ok_when_two_authed(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude", "codex"}))
        r = hc._check_council_breadth()
        assert r.ok is True
        assert "2 providers authed" in r.detail

    def test_soft_gap_detail_matches_guidance(self, monkeypatch):
        monkeypatch.setattr(hc, "_check_provider", _fake_authed({"claude"}))
        r = hc._check_council_breadth()
        g = hc.council_reduced_mode_guidance()
        assert g is not None
        assert r.ok is False
        # Mutation guard: if _check_council_breadth stops delegating and hand-
        # rolls its own string, this equality reds — the surfaces would drift.
        assert r.detail == g["detail"]
        assert r.fix == g["fix"]


class TestAttachCouncilGuidance:
    """The MCP wiring: a launched-council response gains `reduced_mode` iff the
    detector says <2 authed. Pure helper → testable without a real council."""

    def test_attaches_block_when_guidance_present(self, monkeypatch):
        sentinel = {"authed": ["claude"], "detail": "REDUCED …", "fix": "auth a 2nd"}
        monkeypatch.setattr(hc, "council_reduced_mode_guidance", lambda: sentinel)
        out = mcp._attach_council_guidance({"ok": True, "council_run_id": "c1"})
        # Mutation guard: delete the `response["reduced_mode"] = guidance` line
        # and this reds.
        assert out["reduced_mode"] == sentinel

    def test_no_key_when_two_plus_authed(self, monkeypatch):
        monkeypatch.setattr(hc, "council_reduced_mode_guidance", lambda: None)
        out = mcp._attach_council_guidance({"ok": True, "council_run_id": "c1"})
        # Mutation guard: change the injection to unconditional and this reds —
        # a 2-provider user would get a spurious reduced-mode nag on every
        # council.
        assert "reduced_mode" not in out

    def test_detection_failure_never_breaks_the_response(self, monkeypatch):
        def _boom():
            raise RuntimeError("detector exploded")
        monkeypatch.setattr(hc, "council_reduced_mode_guidance", _boom)
        out = mcp._attach_council_guidance({"ok": True, "council_run_id": "c1"})
        # A launched council must still return cleanly — guidance is best-effort.
        assert out["ok"] is True
        assert "reduced_mode" not in out
