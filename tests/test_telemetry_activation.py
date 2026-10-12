"""Channel attribution (founder decision 2026-10-11, council_242fa5046e726b94).

The install command carries TRINITY_CHANNEL; `install` stores it and sends nothing; the first
real use (a council or a verify) sends one `activation` event with that channel, once. These
tests read what would go on the wire.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trinity_local import telemetry as T


@pytest.fixture(autouse=True)
def _own_home(patch_trinity_home):
    """Each test gets its own settings file: the channel and the sent mark are per-install state."""
    return patch_trinity_home


@pytest.fixture
def wire(monkeypatch):
    """Capture every POST the telemetry module would make, with credentials present."""
    sent = []
    monkeypatch.setattr(T, "_ga4_credentials", lambda: ("G-TEST", "secret"))

    def fake_urlopen(req, timeout=5):
        import json
        sent.append(json.loads(req.data.decode()))

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b""
        return R()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    real = T._send_event_to_ga4
    monkeypatch.setattr(T, "_send_event_to_ga4", lambda n, p, settings=None, blocking=False:
                        real(n, p, settings=settings, blocking=True))
    return sent


def _events(sent, name):
    return [e for body in sent for e in body["events"] if e["name"] == name]


def test_install_stores_the_channel_and_sends_nothing(wire, monkeypatch):
    from trinity_local.commands import install as I
    from trinity_local.commands import install_umbrella as U
    from trinity_local import setup_check
    monkeypatch.setattr(I, "handle_install_mcp", lambda a: 0)
    monkeypatch.setattr(setup_check, "ensure_new_user_config", lambda: "")
    monkeypatch.setattr(setup_check, "run_check", lambda: {})
    monkeypatch.setattr(setup_check, "format_check", lambda r: "")
    monkeypatch.setenv("TRINITY_CHANNEL", "HN")
    assert U.handle_install_umbrella(SimpleNamespace(check=False)) == 0
    assert T.load_telemetry_settings().activation_source == "hn"
    assert wire == [], "nothing may leave the machine at install time"


def test_first_use_sends_one_activation_with_the_channel(wire):
    T.remember_channel("reddit")
    assert T.record_activation("council") is True
    assert T.record_activation("verify") is False
    acts = _events(wire, "activation")
    assert [a["params"] for a in acts] == [{"activation_source": "reddit", "surface": "council"}]
    assert T.load_telemetry_settings().activation_sent_at


def test_no_channel_reads_none_and_free_text_reads_other(wire):
    assert T.channel_from(None) == "" and T.channel_from("my secret project") == "other"
    T.record_activation("verify")
    assert _events(wire, "activation")[0]["params"]["activation_source"] == "none"
    # The wire chokepoint maps anything off the allowlist to "other", whatever a caller passes.
    body = T.build_outbound_event_payload("activation", {"activation_source": "prompt text", "surface": "x"})
    assert body["events"][0]["params"] == {"activation_source": "other"}


def test_a_suppressed_send_is_not_marked_so_it_can_go_later(monkeypatch):
    monkeypatch.setattr(T, "_ga4_credentials", lambda: None)
    assert T.record_activation("council") is False
    assert T.load_telemetry_settings().activation_sent_at is None


def test_sharing_off_sends_nothing(wire):
    T.disable_telemetry()
    assert T.record_activation("council") is False
    assert wire == []


def test_the_first_stored_channel_wins():
    T.remember_channel("x")
    T.remember_channel("hn")
    assert T.load_telemetry_settings().activation_source == "x"


def test_a_completed_council_fires_the_activation(wire):
    from trinity_local import council_runner as CR
    outcome = SimpleNamespace(routing_label=SimpleNamespace(task_type="design", winner="claude"),
                              member_results=[1, 2, 3], mode="parallel")
    CR._emit_council_telemetry(outcome)
    assert [e["name"] for body in wire for e in body["events"]] == ["council_complete", "activation"]


def test_the_suite_never_holds_real_ga4_credentials():
    import os
    assert not os.environ.get("TRINITY_GA4_MEASUREMENT_ID") and not os.environ.get("TRINITY_GA4_API_SECRET")


def test_a_verify_run_fires_the_activation(wire, tmp_path):
    from trinity_local import verify as V
    V.verify([], "", "", tmp_path, run_panel=False, run_tests=False)
    assert [e["params"]["surface"] for e in _events(wire, "activation")] == ["verify"]
