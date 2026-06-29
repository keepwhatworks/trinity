"""Real-browser guard: the picks.json Reader heads each pick card with the
basin's HUMAN-READABLE name, not just the opaque basin id.

picks.json is the routing scoreboard — "which model wins for which kind of
question". Post-#298 each pick is keyed by a lens basin id (b00, b01, …). A pick
card that reads only "b00 · Use Claude · margin 0.42" leaves the user unable to
tell WHAT kind of question that routing rule is for: the basin id is opaque, and
the only thing connecting it to a topic (the topology `label`) lived behind an
off-card "View in topology →" hover. topics.json already carries each basin's
semantic `label` (falling back to top_terms), and the viewer already loads it as
`basinNames` in loadCrossMemoryMaps — the picks Reader just wasn't surfacing it.

This drives the picks.json view over file:// on a PII-free seeded home (basins
labeled "Design"/"Debug") and asserts each pick card renders a `.pick-basin-name`
span carrying the matching topology label ALONGSIDE the basin id. A pick whose
basin no longer exists in topology (orphan) must still render — the id alone, no
empty span, no crash.

Mutation-proven: drop the basin-name span from the pick head (or stop populating
basinNames) → the per-card name assertion reds with the founder symptom that the
routing scoreboard headed every pick with an opaque "b00".

Slow + browser marked; skips without Playwright/chromium; runs in the CI `browser`
job.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_picks_name", _SEEDER)
    assert spec and spec.loader, "could not load scripts/seed_synthetic_home.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_picks_reader_heads_each_card_with_basin_name(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    mod = _load_seeder()
    mod.seed(home)
    # The seeded picks (b00, b01) match topology basins labeled "Design"/"Debug".
    # Add an ORPHAN pick whose basin is NOT in topology — it must still render
    # (id only, no empty name span, no crash).
    import json

    picks_path = home / "scoreboard" / "picks.json"
    picks = json.loads(picks_path.read_text())
    picks["b99"] = {
        "winner": "antigravity", "count": 3, "margin": 0.20,
        "n_episodes": 3, "evidence": ["c_orphan"],
    }
    picks_path.write_text(json.dumps(picks), encoding="utf-8")

    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 768, "height": 1000})
            errs: list[str] = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errs.append("PAGEERROR: " + str(e)))
            page.goto(f"file://{mv}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)

            cards = page.evaluate(
                """() => [...document.querySelectorAll('.pick-card')].map(c => ({
                    basin: (c.querySelector('.pick-basin') || {}).textContent || null,
                    name: (c.querySelector('.pick-basin-name') || {}).textContent || null,
                    primary: (c.querySelector('.pick-primary') || {}).textContent || null,
                }))"""
            )
            page.close()
        finally:
            browser.close()

    assert not errs, f"picks Reader threw console/page errors: {errs[:2]}"

    by_basin = {c["basin"]: c for c in cards}
    # The labeled picks MUST surface their topology name in the card head —
    # the founder symptom was the routing scoreboard headed every pick with an
    # opaque basin id ("b00 · Use Claude") with no kind-of-question label.
    assert "b00" in by_basin, f"b00 pick card missing; cards={cards}"
    assert by_basin["b00"]["name"] == "Design", (
        "picks Reader did not head the b00 pick with its basin NAME "
        f"('Design') — the routing scoreboard reads as an opaque basin id; "
        f"card={by_basin['b00']}"
    )
    assert "b01" in by_basin, f"b01 pick card missing; cards={cards}"
    assert by_basin["b01"]["name"] == "Debug", (
        "picks Reader did not head the b01 pick with its basin NAME ('Debug'); "
        f"card={by_basin['b01']}"
    )
    # The name sits ALONGSIDE the id, not replacing it (the id is still the
    # stable cross-link key) — and the winner brand still renders.
    assert by_basin["b00"]["basin"] == "b00"
    assert by_basin["b00"]["primary"] and "Claude" in by_basin["b00"]["primary"]

    # Orphan pick (basin absent from topology) renders id-only — no empty span,
    # no crash. Its name MUST be falsy (graceful degrade, not a stray label).
    assert "b99" in by_basin, f"orphan pick b99 didn't render; cards={cards}"
    assert not by_basin["b99"]["name"], (
        "orphan pick (no matching topology basin) rendered a basin-name span — "
        f"it should degrade to id-only; card={by_basin['b99']}"
    )
    assert by_basin["b99"]["primary"] and "Gemini" in by_basin["b99"]["primary"]
