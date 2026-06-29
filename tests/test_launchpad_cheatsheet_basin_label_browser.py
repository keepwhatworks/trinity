"""Browser guard: the /stats "model cheat-sheet" row must NAME the kind of
question (the basin's top terms, e.g. "design · arch") — never the opaque
internal basin id "b00".

The cheat-sheet card headlines "Which model to use for what / one line per KIND
of question". Its first column is the only thing that tells the user WHICH kind of
question each row is about. The pick rows are keyed on the lens-basin id
(``r.basin_id`` -> "b00"/"b01"), and the human label for that basin (its top TF-IDF
terms) is already in the payload as ``topologyBasinLabels`` — the
``_topology_basin_labels`` docstring itself admits "the basin id 'b03' alone is
opaque". For a long time the row rendered the raw id ``{{ r.basin_id }}`` as the
PRIMARY label, parking the real "design · arch" name inside a hover tooltip on the
small "→ topology" chip — invisible on touch, and meaningless on a glance: a card
that promises "one line per kind of question" answered every line with "b00".

The fix renders ``cheatSheetLabel(r)`` (topology label first, basin label next,
cleaned id only as a last resort) as the primary row text. This drives the REAL
petite-vue render of /stats from the seeded synthetic home (b00 -> "design · arch",
b01 -> "debug · fix" via topics.json top_terms) and reads the rendered DOM:

  • the cheat-sheet's first-column primary link text equals the HUMAN term label,
  • it is NOT the bare basin id "b00"/"b01",
  • the basin id still rides the link :title for traceability.

Mutation-proven to bite: revert ``cheatSheetLabel`` back to ``r.basin_id`` and the
visible-text assertion goes red with the founder symptom named.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _seed_home(tmp_path: Path, monkeypatch) -> None:
    """Seed the synthetic home: picks.json {b00,b01} + topics.json basins whose
    top_terms give b00 -> 'design · arch', b01 -> 'debug · fix'. write_portal_html
    renders portal_pages/stats.html off this state."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    spec = importlib.util.spec_from_file_location(
        "seed_synthetic_home",
        str(_repo_root() / "scripts" / "seed_synthetic_home.py"),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.seed(tmp_path)


def _overwrite_picks(tmp_path: Path, picks: dict) -> None:
    """Replace the seeded picks.json with a chosen, discriminating set of basin
    tallies, then RE-RENDER the portal so stats.html reflects them.

    `seed()` already wrote portal_pages/stats.html off the DEFAULT picks
    (b00=0.42, b01=0.31), so the overwrite alone wouldn't repaint the card — we
    re-run write_portal_html() to bake these picks into the served HTML."""
    import json

    (tmp_path / "scoreboard" / "picks.json").write_text(
        json.dumps(picks), encoding="utf-8"
    )
    from trinity_local.launchpad_page import write_portal_html

    write_portal_html()


def test_cheatsheet_paints_rows_in_margin_descending_order(tmp_path, monkeypatch):
    """The picks cheat-sheet must PAINT its rows highest-margin first.

    The card's own copy promises "highest margin first ... what the user wants
    to see at the top", and `_load_cortex_rules` sorts
    `(-margin, basin_id)` then the template iterates `v-for="r in
    cortexRules.rules"` with NO JS re-sort — so the server sort IS the painted
    order. That painted order is the load-bearing value: a first-time user reads
    the TOP row as "the model Trinity is most confident about for that kind of
    question". Nothing asserted the painted row ORDER (the sibling label /
    demotion / dedup guards check the cell contents, never the sequence), so a
    regression that drops the `-` (ascending), keys on basin_id, or adds a JS
    re-sort would paint the LEAST-confident pick at the top while every existing
    cheat-sheet test stays green.

    DISCRIMINATING fixture: picks.json inserts b00 BEFORE b01 but gives b00 the
    SMALLER margin (0.20 < 0.60). A broken/absent sort paints dict order
    (design·arch first); the correct margin-desc sort must REVERSE it to paint
    debug·fix (0.60) first. Mutation-proven: remove the `rules.sort(...)` in
    launchpad_data._load_cortex_rules and this goes red.
    """
    pytest.importorskip("playwright.sync_api")
    _seed_home(tmp_path, monkeypatch)
    # b00 inserted first but margin 0.20 < b01's 0.60 → discriminates a broken
    # sort (dict order) from the correct margin-descending order.
    _overwrite_picks(
        tmp_path,
        {
            "b00": {
                "winner": "claude",
                "count": 3,
                "margin": 0.20,
                "n_episodes": 3,
                "evidence": ["bundle_a"],
            },
            "b01": {
                "winner": "codex",
                "count": 9,
                "margin": 0.60,
                "n_episodes": 9,
                "evidence": ["bundle_b"],
            },
        },
    )
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.goto(
                    f"{base}/portal_pages/stats.html", wait_until="networkidle"
                )
                page.wait_for_timeout(700)
                assert not errs, f"console/page errors on /stats: {errs}"

                rows = page.eval_on_selector_all(
                    "table.cortex-cheat-sheet tbody tr",
                    """trs => trs.map(tr => {
                        const a = tr.querySelector('td a');
                        const meta = tr.querySelector('td .meta');
                        return {
                            label: (a ? a.textContent : '').trim(),
                            margin: (meta ? meta.textContent : '').replace(/\\s+/g, ' ').trim(),
                        };
                    })""",
                )
                # BITE PRECONDITION 1: both seeded basins actually painted.
                assert len(rows) == 2, (
                    "cheat-sheet didn't paint both seeded picks (b00/b01) — "
                    f"got {rows!r}; harness drift, not the sort bug."
                )
                labels = [r["label"] for r in rows]
                margins = [r["margin"] for r in rows]
                # BITE PRECONDITION 2: the fixture IS discriminating — the two
                # painted margins are the seeded 0.60 / 0.20 (so an ordering
                # assertion can't pass vacuously on equal/absent margins).
                assert "0.60" in margins[0] + margins[1] and "0.20" in margins[0] + margins[1], (
                    "discriminating fixture lost its 0.60/0.20 margins on the "
                    f"painted card (got {margins!r}) — the row sort can't be "
                    "proven without distinct margins."
                )

                # THE GUARD: the TOP painted row is the highest-margin pick
                # (debug · fix @ 0.60), NOT the dict-insertion-first pick
                # (design · arch @ 0.20). Names the founder symptom.
                assert labels[0] == "debug · fix", (
                    "Picks cheat-sheet painted its rows in the WRONG order: the "
                    f"top row is {labels[0]!r} (got order {labels!r}). The card "
                    "promises 'highest margin first' but painted the lower-margin "
                    "pick (design · arch @ 0.20) above the higher-margin one "
                    "(debug · fix @ 0.60). _load_cortex_rules must sort "
                    "(-margin, basin_id) and the template must not JS-re-sort — "
                    "a first-time user reads the TOP row as Trinity's most "
                    "confident routing pick."
                )
                assert labels[1] == "design · arch", (
                    f"cheat-sheet bottom row should be the lower-margin pick "
                    f"(design · arch @ 0.20); got order {labels!r}."
                )
                # And the painted margin on the top row is the larger number,
                # so order matches the value the row displays (not just labels).
                assert "0.60" in margins[0], (
                    f"top row margin text should be 0.60 (got {margins!r}) — the "
                    "painted order must agree with the painted margin."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_cheatsheet_row_names_the_kind_not_the_basin_id(tmp_path, monkeypatch):
    """The cheat-sheet's first column must read the human topic label, not 'b00'."""
    pytest.importorskip("playwright.sync_api")
    _seed_home(tmp_path, monkeypatch)
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.goto(
                    f"{base}/portal_pages/stats.html", wait_until="networkidle"
                )
                page.wait_for_timeout(700)
                assert not errs, f"console/page errors on /stats: {errs}"

                # The picks cheat-sheet table (NOT the by-task-type routing-table).
                rows = page.eval_on_selector_all(
                    "table.cortex-cheat-sheet tbody tr",
                    """trs => trs.map(tr => {
                        const a = tr.querySelector('td a');
                        return {
                            label: (a ? a.textContent : '').trim(),
                            title: a ? (a.getAttribute('title') || '') : '',
                        };
                    })""",
                )
                assert rows, (
                    "cheat-sheet (table.cortex-cheat-sheet) rendered no rows — the "
                    "seeded picks (b00/b01) didn't reach the card; harness drift, "
                    "not the label bug."
                )
                labels = [r["label"] for r in rows]

                # The founder symptom: a card that promises "one line per KIND of
                # question" answered every line with the opaque internal id "b00".
                bare_id_rows = [lab for lab in labels if lab in ("b00", "b01")]
                assert not bare_id_rows, (
                    "Cheat-sheet row labelled with the OPAQUE BASIN ID "
                    f"{bare_id_rows!r} instead of the kind of question. The card "
                    "headlines 'one line per KIND of question' but the row reads "
                    "'b00' — the human label (topologyBasinLabels, e.g. "
                    "'design · arch') was parked in a hover tooltip. Render "
                    "cheatSheetLabel(r) as the primary row text."
                )

                # And it must POSITIVELY show the seeded human term labels.
                assert "design · arch" in labels, (
                    "Cheat-sheet first column lost the human topic label "
                    f"'design · arch' (got {labels!r}). The basin's top_terms are "
                    "the 'kind of question' the card is organized by."
                )
                assert "debug · fix" in labels, (
                    f"Cheat-sheet missing the 'debug · fix' label (got {labels!r})."
                )

                # The basin id stays reachable for traceability via the link title.
                titled = [r for r in rows if r["label"] == "design · arch"]
                assert titled and "b00" in titled[0]["title"], (
                    "the basin id must still ride the row link :title so the "
                    f"deep-link target stays traceable (got title {titled and titled[0]['title']!r})."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
