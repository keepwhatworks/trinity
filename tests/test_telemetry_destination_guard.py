"""Where telemetry goes, stated truthfully; the "can a shipped build send" refusal
lives at release time.

History: this check refused green whenever sharing was on and the build had no
key, because a shipped wheel without the key reported healthy and sent nothing.
Since v1.7.402 the release wheel carries the key, so a keyless build is a
deliberate one (a source checkout, or the keyless fleet install enterprise.md
recommends), and its users saw a red check asking for a secret they cannot have
(coherence review 2026-10-15). The status line now states the truth in every
state, and `internal/sync_public.sh` refuses to announce a release whose wheel
lacks the key (tests/test_public_installer_source.py pins that).
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
    def test_a_keyless_build_is_told_nothing_is_sent_not_failed(self, sharing_on, monkeypatch):
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: None)
        r = health_checks._check_telemetry_destination()
        assert r.ok is True and "no telemetry key" in r.detail and not r.fix

    def test_a_keyed_build_says_where_events_go(self, sharing_on, monkeypatch):
        monkeypatch.setattr(telemetry, "_ga4_credentials", lambda: ("G-X", "s"))
        r = health_checks._check_telemetry_destination()
        assert r.ok is True and "Google Analytics" in r.detail

    def test_sharing_off_says_nothing_is_sent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        s = telemetry.load_telemetry_settings()
        s.sharing_enabled = False
        telemetry.save_telemetry_settings(s)
        r = health_checks._check_telemetry_destination()
        assert r.ok is True and "off" in r.detail

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
