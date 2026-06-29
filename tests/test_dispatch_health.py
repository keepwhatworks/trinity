"""Tests for per-provider failure-mode tracking.

Reads `~/.trinity/analytics/dispatch_outcomes.jsonl` (written by ask.run_ask)
and surfaces which providers are currently in failure states the routing
layer should deprioritize.

The decay windows + thresholds are calibrated for the 3 CLI shapes we
ship — these tests lock them in so a tuning change is explicit.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from trinity_local.dispatch_health import (
    clear_health_cache,
    compute_health,
    log_member_failure,
    unhealthy_providers,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_health_cache()
    yield
    clear_health_cache()


def _write_log(tmp_path, entries: list[dict]):
    """Helper — drop a dispatch_outcomes.jsonl into the test TRINITY_HOME."""
    log_dir = tmp_path / "analytics"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "dispatch_outcomes.jsonl"
    with log_path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return log_path


class TestComputeHealth:
    def test_no_log_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        assert compute_health() == {}

    def test_recent_rate_limit_marks_provider_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        _write_log(tmp_path, [
            {
                "ts": (now - timedelta(minutes=2)).isoformat(),
                "primary": "claude",
                "succeeded_on": "codex",
                "retries": 1,
                "rate_limit_save": True,
                "failure_kind": "rate_limited",
            },
        ])
        result = compute_health(now=now)
        assert "claude" in result
        h = result["claude"]
        assert h.is_unhealthy is True
        assert h.recent_failures == 1
        assert h.last_failure_kind == "rate_limited"

    def test_old_rate_limit_decays_out_of_window(self, tmp_path, monkeypatch):
        """Rate-limit at 9am is irrelevant by 11am — decay window is 10 min."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 11, 0, 0, tzinfo=timezone.utc)
        _write_log(tmp_path, [
            {
                "ts": (now - timedelta(minutes=120)).isoformat(),  # 2 hours ago
                "primary": "claude",
                "succeeded_on": "codex",
                "retries": 1,
                "rate_limit_save": True,
                "failure_kind": "rate_limited",
            },
        ])
        result = compute_health(now=now)
        # Claude's rate-limit window expired → not unhealthy anymore.
        assert "claude" not in result

    def test_billing_failure_keeps_provider_unhealthy_for_a_day(self, tmp_path, monkeypatch):
        """Billing exceeded usually needs a manual fix → 24hr decay."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        _write_log(tmp_path, [
            {
                "ts": (now - timedelta(hours=4)).isoformat(),
                "primary": "codex",
                "succeeded_on": "claude",
                "retries": 1,
                "rate_limit_save": True,
                "failure_kind": "billing_exceeded",
            },
        ])
        result = compute_health(now=now)
        # 4 hours after a billing failure — still in the 24h window.
        assert "codex" in result
        assert result["codex"].is_unhealthy is True

    def test_successful_dispatch_doesnt_register_as_failure(self, tmp_path, monkeypatch):
        """Only entries with failure_kind set count — successful first-try
        calls don't drag the provider's health down."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        _write_log(tmp_path, [
            {
                "ts": (now - timedelta(minutes=2)).isoformat(),
                "primary": "claude",
                "succeeded_on": "claude",
                "retries": 0,
                "rate_limit_save": False,
                "failure_kind": None,
            },
        ])
        assert compute_health(now=now) == {}

    def test_threshold_for_unhealthy_is_configurable(self, tmp_path, monkeypatch):
        """min_failures_for_unhealthy lets tighter SLA shops require more
        repeated failures before flagging a provider."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        _write_log(tmp_path, [
            {
                "ts": (now - timedelta(minutes=2)).isoformat(),
                "primary": "claude",
                "succeeded_on": "codex",
                "retries": 1,
                "rate_limit_save": True,
                "failure_kind": "rate_limited",
            },
        ])
        # Default threshold (1): unhealthy.
        result_loose = compute_health(now=now, min_failures_for_unhealthy=1)
        assert result_loose["claude"].is_unhealthy is True
        # Tight threshold (3): still tracked but not flagged unhealthy.
        clear_health_cache()
        result_tight = compute_health(now=now, min_failures_for_unhealthy=3)
        assert result_tight["claude"].is_unhealthy is False
        assert result_tight["claude"].recent_failures == 1


class TestUnhealthyProviders:
    def test_returns_only_currently_unhealthy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        now = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        # claude: rate-limited 2 min ago (in window)
        # gemini: rate-limited 2 hours ago (out of window)
        # codex: billing failure 4 hours ago (in 24h window)
        _write_log(tmp_path, [
            {"ts": (now - timedelta(minutes=2)).isoformat(),
             "primary": "claude", "succeeded_on": "codex", "retries": 1,
             "rate_limit_save": True, "failure_kind": "rate_limited"},
            {"ts": (now - timedelta(hours=2)).isoformat(),
             "primary": "antigravity", "succeeded_on": "codex", "retries": 1,
             "rate_limit_save": True, "failure_kind": "rate_limited"},
            {"ts": (now - timedelta(hours=4)).isoformat(),
             "primary": "codex", "succeeded_on": "claude", "retries": 1,
             "rate_limit_save": True, "failure_kind": "billing_exceeded"},
        ])
        # Use compute_health directly with the test `now` since unhealthy_providers
        # uses wall-clock; cache is cleared between calls per the autouse fixture.
        health = compute_health(now=now)
        assert "claude" in health
        assert "antigravity" not in health  # decayed out
        assert "codex" in health

    def test_no_failures_yields_empty_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        assert unhealthy_providers() == set()


class TestPoolDemotion:
    """`_full_provider_pool` demotes (not excludes) unhealthy providers to
    the end. Routing layer can still fall back to them when nothing else fits.
    """

    def test_unhealthy_providers_move_to_end(self, monkeypatch, tmp_path):
        from trinity_local import mcp_server, local_models, dispatch_health

        # Stub config pool: claude, codex, gemini all enabled.
        fake_cfg = type("C", (), {})()
        fake_cfg.providers = {
            name: type("P", (), {"name": name, "enabled": True})()
            for name in ["claude", "codex", "antigravity"]
        }
        import trinity_local.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: fake_cfg)
        monkeypatch.setattr(local_models, "detect_local_models", lambda: [])
        # claude is unhealthy → should appear at the END of the pool.
        monkeypatch.setattr(dispatch_health, "unhealthy_providers", lambda: {"claude"})

        pool = mcp_server._full_provider_pool()
        # Codex/gemini come first (healthy); claude comes last (sick).
        assert pool == ["codex", "antigravity", "claude"]
        # All three still in the pool — demotion not exclusion.
        assert "claude" in pool

    def test_no_unhealthy_preserves_order(self, monkeypatch):
        from trinity_local import mcp_server, local_models, dispatch_health

        fake_cfg = type("C", (), {})()
        fake_cfg.providers = {
            name: type("P", (), {"name": name, "enabled": True})()
            for name in ["claude", "codex"]
        }
        import trinity_local.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: fake_cfg)
        monkeypatch.setattr(local_models, "detect_local_models", lambda: [])
        monkeypatch.setattr(dispatch_health, "unhealthy_providers", lambda: set())

        pool = mcp_server._full_provider_pool()
        assert pool == ["claude", "codex"]


class TestLogMemberFailure:
    """100-persona audit P46 fix: council member dispatch failures must
    write to dispatch_outcomes.jsonl so compute_health() can demote the
    provider on the next ask. Without this the council was silent —
    rate-limited Codex stayed routable, rate-limit-saves missed council
    saves entirely."""

    def test_writes_to_outcomes_log(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        log_member_failure(
            provider="codex",
            council_run_id="council_test_001",
            failure_kind="rate_limited",
            stderr_excerpt="429 Too Many Requests",
        )
        path = tmp_path / "analytics" / "dispatch_outcomes.jsonl"
        assert path.exists()
        entry = json.loads(path.read_text().strip())
        assert entry["primary"] == "codex"
        assert entry["failure_kind"] == "rate_limited"
        assert entry["source"] == "council_member"
        assert entry["council_run_id"] == "council_test_001"
        assert entry["succeeded_on"] is None

    def test_council_failure_marks_provider_unhealthy(self, tmp_path, monkeypatch):
        """End-to-end: log a council member rate-limit, run compute_health(),
        codex should land in unhealthy_providers() set."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        log_member_failure(
            provider="codex",
            council_run_id="council_e2e",
            failure_kind="rate_limited",
            stderr_excerpt="429",
        )
        clear_health_cache()
        unhealthy = unhealthy_providers()
        assert "codex" in unhealthy, (
            "Council-logged rate limit did not propagate to dispatch_health "
            "— the loop is broken again"
        )

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        """Per contract, observability must NOT crash the dispatch path.
        Force a write failure and confirm no exception escapes."""
        monkeypatch.setenv("TRINITY_HOME", "/this/dir/cannot/possibly/exist/nope")
        # No raise expected
        log_member_failure(
            provider="codex",
            council_run_id="x",
            failure_kind="rate_limited",
        )
