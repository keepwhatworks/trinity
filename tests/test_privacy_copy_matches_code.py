"""What the product says about telemetry matches what it sends (coherence review 2026-10-15).

Since v1.7.402 the release wheel carries the send key and installs send an activation event,
while the README still said "Trinity uploads nothing", `telemetry-show --wire` said sharing
was ON after `telemetry-disable`, `telemetry-endpoint` claimed to redirect events it never
touched, and install never named the off switch. Each test reads the shipped surface.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from trinity_local import telemetry as T

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _home(patch_trinity_home):
    return patch_trinity_home


def test_readme_names_every_field_that_ships_and_no_false_absence():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Privacy by default"):readme.index("## Objections")]
    for field in sorted(T.DISCLOSED_EVENT_PARAMS):
        assert f"`{field}`" in section, f"{field} ships but the README privacy section omits it"
    for false_claim in ("uploads nothing", "silently no-op", "does **not** ship"):
        assert false_claim not in readme, false_claim


def test_wire_view_says_off_after_disable(capsys):
    from trinity_local.commands import telemetry as C
    T.disable_telemetry()
    C._print_wire(T.load_telemetry_settings())
    out = capsys.readouterr().out
    assert "Sharing is currently: OFF" in out
    assert "%Y" not in out, "a strftime format string is not a word users can read"


def test_a_collector_does_not_hide_that_cli_events_go_to_google(monkeypatch):
    monkeypatch.setattr(T, "_ga4_credentials", lambda: ("G-X", "s"))
    monkeypatch.setenv("TRINITY_TELEMETRY_ENDPOINT", "https://collector.example/t")
    dest = T.telemetry_destination()
    assert "Google Analytics" in dest and "collector.example" in dest


def test_telemetry_endpoint_says_it_redirects_nothing(capsys):
    from trinity_local.commands import telemetry as C
    C.handle_telemetry_endpoint(SimpleNamespace(url="https://collector.example/t", clear=False))
    assert "does not redirect" in capsys.readouterr().err


def test_install_names_the_off_switch(monkeypatch, capsys):
    from trinity_local import setup_check
    from trinity_local.commands import install as I
    from trinity_local.commands import install_umbrella as U
    monkeypatch.setattr(I, "handle_install_mcp", lambda a: 0)
    monkeypatch.setattr(setup_check, "ensure_new_user_config", lambda: "")
    monkeypatch.setattr(setup_check, "run_check", lambda: {})
    monkeypatch.setattr(setup_check, "format_check", lambda r: "")
    U.handle_install_umbrella(SimpleNamespace(check=False))
    out = capsys.readouterr().out
    assert re.search(r"telemetry-disable", out) and "telemetry-show --wire" in out
