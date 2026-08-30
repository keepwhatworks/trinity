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
