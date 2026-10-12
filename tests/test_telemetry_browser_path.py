"""The browser never talks to GA4, and never carries the local install id.

Found 2026-10-12 while shipping the GA4 key in release wheels: with credentials present, the
launchpad page was handed the GA4 Measurement Protocol URL (api_secret included) and POSTed its
view and win-rate events verbatim. Those dicts carried the raw local share_install_id, which the
privacy page promises never leaves, and were not the envelope GA4 records anyway.
"""
from __future__ import annotations

import json

import pytest

from trinity_local import telemetry as T

SECRET = "sekrit-api-value"


@pytest.fixture(autouse=True)
def _home(patch_trinity_home, monkeypatch):
    monkeypatch.delenv("TRINITY_TELEMETRY_ENDPOINT", raising=False)
    return patch_trinity_home


def test_with_ga4_credentials_the_page_gets_no_endpoint_and_no_secret(monkeypatch):
    monkeypatch.setattr(T, "_ga4_credentials", lambda: ("G-TEST000", SECRET))
    state = T.launchpad_telemetry_state()
    assert "endpoint" not in state["settings"], "the browser must never be handed the GA4 URL"
    assert SECRET not in json.dumps(state)
    assert state["settings"]["destination"].startswith("Google Analytics")


def test_browser_events_carry_the_rotating_id_never_the_local_one(monkeypatch):
    monkeypatch.setattr(T, "_ga4_credentials", lambda: ("G-TEST000", SECRET))
    state = T.launchpad_telemetry_state()
    local = state["settings"]["share_install_id"]
    assert local
    for ev in (state["view_event"], state["elo_event"]):
        assert local not in json.dumps(ev), f"{ev['event']} carries the local install id"
        assert ev["client_id"] == T.outbound_client_id(local)


def test_a_custom_collector_is_the_only_browser_endpoint(monkeypatch):
    monkeypatch.setenv("TRINITY_TELEMETRY_ENDPOINT", "https://collector.example.org/e")
    monkeypatch.setattr(T, "_ga4_credentials", lambda: ("G-TEST000", SECRET))
    state = T.launchpad_telemetry_state()
    assert state["settings"]["endpoint"] == "https://collector.example.org/e"
    assert SECRET not in json.dumps(state)


def test_without_a_destination_the_page_says_so(monkeypatch):
    monkeypatch.setattr(T, "_ga4_credentials", lambda: None)
    state = T.launchpad_telemetry_state()
    assert "endpoint" not in state["settings"] and "destination" not in state["settings"]
