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

from tests.conftest import private_only
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


def test_nothing_reads_a_self_configured_collector_any_more():
    """The launchpad's collector path (TRINITY_TELEMETRY_ENDPOINT) and the telemetry-endpoint
    verb were removed 2026-10-15 (founder-approved). No shipped code may read the variable,
    register the verb, or give the page a sender."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
    readers = [p.name for p in src.rglob("*.py")
               if "TRINITY_TELEMETRY_ENDPOINT" in p.read_text(encoding="utf-8")
               and p.name != "retired_names.py"]
    assert not readers, f"code reads TRINITY_TELEMETRY_ENDPOINT again: {readers}"
    from trinity_local.main import build_parser
    import argparse
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert "telemetry-endpoint" not in sub.choices
    assert "maybeSendTelemetry" not in (src / "launchpad_template.py").read_text(encoding="utf-8")
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


def _assert_lists_exactly_what_ships(text: str, where: str) -> None:
    """Every field that ships is named, and every snake_case name in backticks is one that
    ships. provider_scores and routing_lesson are council-outcome fields that stay local;
    the lock and the spec named them as telemetry until 2026-10-28."""
    named = set(re.findall(r"`([a-z_]+)`", text))
    missing = T.DISCLOSED_EVENT_PARAMS - named
    extra = {n for n in named if "_" in n or n in T.DISCLOSED_EVENT_PARAMS} - T.DISCLOSED_EVENT_PARAMS
    assert not missing, f"{where} omits fields that ship: {sorted(missing)}"
    assert not extra, f"{where} names fields that never ship: {sorted(extra)}"


def test_product_spec_names_exactly_the_fields_that_ship():
    spec = (REPO / "docs" / "product-spec.md").read_text(encoding="utf-8")
    lock = next(line for line in spec.splitlines() if line.startswith("2. **Prompt content never uploads.**"))
    section = spec[spec.index("### Telemetry model"):]
    section = section[: section.index("\n---")]
    _assert_lists_exactly_what_ships(lock, "product-spec's 'Prompt content never uploads' commitment")
    _assert_lists_exactly_what_ships(section, "product-spec's Telemetry model section")


@private_only
def test_agents_md_lock_names_exactly_the_fields_that_ship():
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    lock = next(line for line in agents.splitlines() if line.startswith("2. **Prompt content never uploads.**"))
    _assert_lists_exactly_what_ships(lock, "AGENTS.md's 'Prompt content never uploads' commitment")
