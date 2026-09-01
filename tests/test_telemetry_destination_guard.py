"""A green telemetry state must attest a DESTINATION, not just a setting.

`_send_event_to_ga4` returns False when `_ga4_credentials()` is None — the
documented behaviour for a contributor with no GA4 env vars. On a shipped
install nobody has those vars, so the default `sharing_enabled=True` produced a
system that reported healthy and transmitted nothing, with no surface saying
why. Counting activations would then report zero users whether or not anyone
installed, which is indistinguishable from "nobody came".

That is this repo's signature failure (a green over degenerate data) sitting on
the instrument the distribution plan's gating step depends on. These tests pin
the refusal, and pin that every route the `fix` recommends can actually clear
it — advice-closure applied to a non-staleness check.
"""
from __future__ import annotations

import pytest

from trinity_local import health_checks, telemetry


@pytest.fixture
def sharing_on(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_TELEMETRY_ENDPOINT", raising=False)
    s = telemetry.load_telemetry_settings()
    s.sharing_enabled = True
    telemetry.save_telemetry_settings(s)
    return s


class TestTelemetryDestinationGuard:
    def test_refuses_green_when_sharing_is_on_with_no_destination(self, sharing_on, monkeypatch):
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: None)
        r = health_checks._check_telemetry_destination()
        assert r.ok is False, (
            "sharing is ON and no destination exists, so every event is discarded. "
            "This check must REFUSE the green — reporting ok here is the exact "
            "false-green this guard was written for."
        )
        assert "silently discarded" in r.detail
        assert r.fix, "a failing check must tell the operator how to clear it"

    def test_sharing_off_is_honest_not_broken(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.delenv("TRINITY_TELEMETRY_ENDPOINT", raising=False)
        s = telemetry.load_telemetry_settings()
        s.sharing_enabled = False
        telemetry.save_telemetry_settings(s)
        assert health_checks._check_telemetry_destination().ok is True, (
            "sharing off and nothing sent is a CONSISTENT state, not a defect"
        )

    def test_fix_route_custom_endpoint_clears_it(self, sharing_on, monkeypatch):
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: None)
        assert health_checks._check_telemetry_destination().ok is False
        monkeypatch.setenv("TRINITY_TELEMETRY_ENDPOINT", "https://collector.example.com/e")
        assert health_checks._check_telemetry_destination().ok is True, (
            "the fix names TRINITY_TELEMETRY_ENDPOINT — setting it must clear the warning"
        )

    def test_fix_route_ga4_credentials_clears_it(self, sharing_on, monkeypatch):
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: None)
        assert health_checks._check_telemetry_destination().ok is False
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: ("G-XXXX", "secret"))
        assert health_checks._check_telemetry_destination().ok is True, (
            "the fix names GA4 credentials — providing them must clear the warning"
        )

    def test_check_is_wired_into_the_report(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        report = health_checks.run_doctor()
        names = {c.name for c in report.checks}
        assert "telemetry_destination" in names, (
            "the check exists but nothing calls it — a guard nobody runs is decoration"
        )


class TestBundledCredentials:
    """A shipped wheel must be able to report; a git checkout must not leak.

    `pip install trinity-local` sets no env vars, so env-only lookup shipped an
    install that discarded every event. The pair now travels in the wheel and is
    gitignored, because this repo mirrors publicly and a committed api_secret is
    a published one.
    """

    def test_absent_bundle_leaves_the_env_path_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.delenv("TRINITY_GA4_MEASUREMENT_ID", raising=False)
        monkeypatch.delenv("TRINITY_GA4_API_SECRET", raising=False)
        monkeypatch.setattr(telemetry, "_bundled_ga4_credentials", lambda: None)
        assert telemetry._ga4_credentials() is None, (
            "with no env and no bundle the documented no-op must still hold"
        )

    def test_bundle_is_used_when_env_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.delenv("TRINITY_GA4_MEASUREMENT_ID", raising=False)
        monkeypatch.delenv("TRINITY_GA4_API_SECRET", raising=False)
        monkeypatch.setattr(telemetry, "_bundled_ga4_credentials",
                            lambda: ("G-BUNDLED01", "bundled"))
        assert telemetry._ga4_credentials() == ("G-BUNDLED01", "bundled"), (
            "a shipped install has only the bundle — if it is ignored the wheel "
            "is back to silently discarding every event"
        )

    def test_env_wins_over_bundle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_GA4_MEASUREMENT_ID", "G-ENVWINS01")
        monkeypatch.setenv("TRINITY_GA4_API_SECRET", "envsecret")
        monkeypatch.setattr(telemetry, "_bundled_ga4_credentials",
                            lambda: ("G-BUNDLED01", "bundled"))
        assert telemetry._ga4_credentials() == ("G-ENVWINS01", "envsecret"), (
            "a developer must be able to point a checkout at their own property "
            "without rebuilding the package"
        )

    def test_the_generated_module_is_gitignored(self):
        import subprocess
        r = subprocess.run(
            ["git", "check-ignore", "src/trinity_local/_ga4_bundled.py"],
            capture_output=True, text=True)
        assert r.returncode == 0, (
            "src/trinity_local/_ga4_bundled.py is NOT gitignored. This repo "
            "mirrors to a public repository, so committing it publishes the "
            "ingestion secret."
        )
