"""Regression: the memory viewer's routing.json reader must hide n=1 noise rows.

Found 2026-06-01 by EYEBALLING the real routing.json memory view: it rendered a
**22,413px-tall** table — EVERY one of 424 task_types, of which 349 (82%) were
n=1-only (a single council each, no track record). Same unbounded-table class as
the launchpad routing cheat-sheet (#290, which filtered to n>=2) but in a
DIFFERENT surface (renderRoutingReader) that #290 never touched.

Fix (v1.7.226): a task_type earns a row only if it carries real signal — some
provider with n>=2, OR it's the deep-link `?task=` target, OR it bridges to a
topology basin (a consolidated pick). Rows sort most-evidenced first; the hidden
n=1 count is noted; Raw JSON still shows everything. On the real corpus this took
the view 22,413px -> 4,330px (424 -> 75 rows).

This pins the behaviour with a synthetic corpus (no picks.json, so no topology
bridge): only the n>=2 rows render, sorted by descending max-n, with an accurate
"N single-sample task types hidden" note — and a `?task=` deep-link to an n=1
singleton KEEPS that row visible (without it, a cross-link from picks.json would
404 into a "not yet" banner).

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# 5 task_types with a real track record (max-n in parens) + 60 n=1 singletons.
_MEANINGFUL = [("t_big", 10), ("t_mid", 5), ("t_three", 3), ("t_two_a", 2), ("t_two_b", 2)]
_N_SINGLETON = 60


def _synthetic_routing() -> dict:
    by_task_type: dict = {}
    best: dict = {}
    for name, n in _MEANINGFUL:
        # claude has the track record (n>=2) and the best score.
        by_task_type[name] = {
            "claude": {"n": n, "overall": 8.0, "wins": n},
            "codex": {"n": 1, "overall": 5.0, "wins": 0},
        }
        best[name] = "claude"
    for i in range(_N_SINGLETON):
        by_task_type[f"single_{i:02d}"] = {
            "codex": {"n": 1, "overall": float(i % 10), "wins": 0},
        }
    return {
        "by_task_type": by_task_type,
        "best_per_task_type": best,
        "computed_at": "2026-06-01T00:00:00",
    }


def _render_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "routing.json").write_text(
        json.dumps(_synthetic_routing()), encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the routing-filter test: {exc}")
    return sp, browser


def test_routing_reader_hides_n1_rows_and_sorts_by_evidence():
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
        page.wait_for_timeout(1200)
        info = page.evaluate(
            """() => {
              const rows = Array.from(document.querySelectorAll('table.routing-table tbody tr'));
              const firstTask = rows.length ? rows[0].dataset.task : null;
              const metas = Array.from(document.querySelectorAll('p.meta')).map(e => e.textContent);
              const hidden = metas.find(m => m && m.includes('hidden')) || null;
              return {rowCount: rows.length, firstTask, hidden,
                      tasks: rows.map(r => r.dataset.task)};
            }"""
        )
    finally:
        browser.close(); sp.stop()

    # Only the 5 n>=2 rows render — the 60 singletons are filtered out.
    assert info["rowCount"] == len(_MEANINGFUL), (
        f"expected {len(_MEANINGFUL)} n>=2 rows, got {info['rowCount']} "
        f"(n=1 noise not filtered): {info['tasks'][:8]}"
    )
    assert all(not t.startswith("single_") for t in info["tasks"]), (
        f"a single-sample (n=1) row leaked into the table: {info['tasks']}"
    )
    # Sorted most-evidenced first.
    assert info["firstTask"] == "t_big", f"rows not sorted by evidence: {info['tasks']}"
    # The hidden-count note is present and accurate.
    assert info["hidden"] and str(_N_SINGLETON) in info["hidden"], (
        f"hidden-count note missing or wrong: {info['hidden']!r}"
    )


def test_routing_reader_deeplink_keeps_singleton_visible():
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        # Deep-link to an n=1 singleton — it must stay visible (no false "not yet").
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json&task=single_07")
        page.wait_for_timeout(1000)
        present = page.evaluate(
            """() => {
              const focused = document.querySelector('tr.routing-row-focused');
              const banner = document.querySelector('.viewer-health-banner');
              const rows = document.querySelectorAll('table.routing-table tbody tr').length;
              return {focusedTask: focused ? focused.dataset.task : null,
                      banner: !!banner, rows};
            }"""
        )
        # And a genuinely-absent task DOES show the "not yet" banner.
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json&task=zzz_absent")
        page.wait_for_timeout(800)
        absent = page.evaluate(
            "() => !!document.querySelector('.viewer-health-banner')"
        )
    finally:
        browser.close(); sp.stop()

    assert present["focusedTask"] == "single_07", (
        f"deep-linked singleton not kept visible: {present}"
    )
    assert not present["banner"], "a present (singleton) task wrongly showed 'not yet'"
    # 5 meaningful + the 1 kept singleton focus = 6.
    assert present["rows"] == len(_MEANINGFUL) + 1, present
    assert absent, "a genuinely-absent task should show the 'not yet' banner"


def test_reader_raw_json_toggle_swaps_views():
    """Behavioral guard for the Reader | Raw JSON view-toggle (the #293 pattern:
    verified-correct-but-unguarded). The string tests in test_memory_viewer.py
    check the toggle BUTTONS exist; nothing clicks them. The toggle is a petite-vue
    @click that flips a reactive `view` var + re-renders — a binding/reactivity
    regression would silently strand the user in one view (e.g. can't reach the raw
    JSON of their own memory files), and no test would catch it. Browser-found this
    cycle (2026-06-02) by gesture-testing the readers — works, but unguarded. Drive
    the real toggle Reader (table) -> Raw JSON (highlighted <pre>) -> Reader (table
    back), asserting the visible view actually swaps each click AND no console error
    fires (the interaction-throws class, cf. the topology-zoom interrupt bug)."""
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: errors.append(m.text) if m.type == "error" else None,
        )
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
        page.wait_for_timeout(1000)

        def _state():
            return page.evaluate(
                """() => {
                  const btns = Array.from(document.querySelectorAll('.view-toggle button'));
                  const active = (name) => { const b = btns.find(x => x.textContent.trim() === name);
                    return b ? b.className.includes('active') : null; };
                  const tbl = document.querySelector('table.routing-table');
                  const pre = document.querySelector('pre');
                  return {
                    readerActive: active('Reader'),
                    rawActive: active('Raw JSON'),
                    tableVisible: !!(tbl && tbl.offsetParent !== null),
                    rawVisible: !!(pre && pre.offsetParent !== null),
                  };
                }"""
            )

        def _click(name):
            page.evaluate(
                "(name) => { const b = [...document.querySelectorAll('.view-toggle button')]"
                ".find(x => x.textContent.trim() === name); if (b) b.click(); }",
                name,
            )
            page.wait_for_timeout(350)

        s_default = _state()
        _click("Raw JSON")
        s_raw = _state()
        _click("Reader")
        s_back = _state()
    finally:
        browser.close()
        sp.stop()

    # Default: Reader active, table shown, no raw <pre>.
    assert s_default["readerActive"] and s_default["tableVisible"] and not s_default["rawVisible"], (
        f"Reader is not the default visible view: {s_default}"
    )
    # Raw JSON: the highlighted <pre> shows and the table is gone.
    assert s_raw["rawActive"] and s_raw["rawVisible"] and not s_raw["tableVisible"], (
        f"clicking 'Raw JSON' didn't swap to the raw <pre> view: {s_raw}"
    )
    # Back to Reader: the table view returns.
    assert s_back["readerActive"] and s_back["tableVisible"] and not s_back["rawVisible"], (
        f"clicking 'Reader' didn't restore the table view: {s_back}"
    )
    toggle_errs = [e for e in errors if "favicon" not in e]
    assert not toggle_errs, f"the Reader/Raw view-toggle threw on interaction: {toggle_errs}"


def _all_n1_routing() -> dict:
    """Routing data that EXISTS but where EVERY task_type is a single council
    (n=1) — the founder's real shape (349 of 424 task_types n=1-only)."""
    by_task_type: dict = {}
    for i in range(9):
        by_task_type[f"tt_{i:02d}"] = {
            "claude": {"n": 1, "overall": 7.0 + i * 0.1, "wins": 1},
        }
    return {
        "by_task_type": by_task_type,
        "best_per_task_type": {},  # MIN_BEST_SAMPLES=3 → no best at n=1
        "computed_at": "2026-06-18T00:00:00",
    }


def test_routing_reader_all_n1_shows_empty_state_not_ghost_table():
    """When routing.json EXISTS but EVERY task_type is a single council (n=1), the
    reader must show an honest empty-state — NOT a header-only ghost table. The
    early-return at the top of renderRoutingReader only catches a TRULY empty file;
    pre-fix the all-n=1 case fell through it and rendered a <table> with a header
    row ('Task type | Best') and ZERO body rows (a header-only ghost that reads as
    broken/loading), with the real 'all single-council' explanation buried below.

    Rendered-DOM (table presence + body-row count + the empty-state text), NOT a
    source-string check. Mutation-proven: remove the `if (taskTypes.length === 0)`
    empty-state branch → the ghost table (header, 0 body rows) reappears and this
    reds. The sibling tests (5-meaningful-row + deep-link) stay green — the fix
    only affects the all-filtered-out case."""
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "routing.json").write_text(
        json.dumps(_all_n1_routing()), encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
        page.wait_for_timeout(1000)
        info = page.evaluate(
            """() => {
              const tbl = document.querySelector('table.routing-table');
              const bodyRows = tbl ? tbl.querySelectorAll('tbody tr').length : -1;
              const headerCells = tbl ? tbl.querySelectorAll('thead th').length : 0;
              const text = (document.getElementById('content') || document.body).innerText;
              return {
                tablePresent: !!tbl,
                bodyRows,
                headerCells,
                emptyState: /No routing pattern yet/i.test(text),
              };
            }"""
        )
    finally:
        browser.close(); sp.stop()

    # The ghost table: a <table> with a header row and NO body rows.
    assert not (info["tablePresent"] and info["headerCells"] >= 2 and info["bodyRows"] == 0), (
        "the routing.json reader rendered a HEADER-ONLY GHOST TABLE at all-n=1 "
        f"({info['headerCells']} header cells, {info['bodyRows']} body rows) — a "
        "column header with no data reads as broken; render the empty-state instead"
    )
    assert info["emptyState"], (
        "the all-single-council routing.json reader must show an honest 'No routing "
        f"pattern yet' empty-state, not a ghost table: {info}"
    )


def _humanizable_routing() -> dict:
    """Realistic multi-word snake_case task_types (the chairman-emitted +
    heuristic enum: code_generation / data_analysis / cowork_general). All
    n>=2 so they survive the noise filter and render a row each."""
    by_task_type = {
        "code_generation": {"claude": {"n": 8, "overall": 8.2, "wins": 6},
                            "codex": {"n": 4, "overall": 7.1, "wins": 2}},
        "data_analysis": {"codex": {"n": 5, "overall": 7.9, "wins": 4},
                         "claude": {"n": 3, "overall": 6.8, "wins": 1}},
        "cowork_general": {"antigravity": {"n": 4, "overall": 7.4, "wins": 3},
                          "claude": {"n": 2, "overall": 6.0, "wins": 1}},
    }
    return {
        "by_task_type": by_task_type,
        "best_per_task_type": {"code_generation": "claude",
                               "data_analysis": "codex",
                               "cowork_general": "antigravity"},
        "computed_at": "2026-06-21T00:00:00",
    }


def test_routing_reader_humanizes_snake_case_task_names():
    """The routing.json reader must render the task_type as a HUMAN title
    ("Code Generation"), not the raw snake_case enum ("code_generation").

    The launchpad routing cheat-sheet — the deep-link SOURCE that links INTO
    this table via `?file=routing.json&task=<task_type>` — already title-cases
    the SAME value (launchpad_template.py: `taskType.replace(/_/g,' ')…`). The
    memory-viewer table was the un-humanized sibling (the #165 picks.json +
    #191 unified-review-page snake_case-enum-in-prose class): a user who clicks
    "Data Analysis" on the launchpad landed on a row labelled `data_analysis`.

    Rendered-DOM, NOT a source-string check. Asserts BOTH halves of the
    invariant: the displayed CELL text is humanized, AND `tr.dataset.task`
    stays the RAW snake_case (the deep-link key + `?task=` focus matcher must
    keep matching the launchpad's encodeURIComponent(taskType) — humanizing the
    key would 404 every cross-link into a 'not yet' banner).

    Mutation-proven: revert the `humanizeTaskType(t)` table-cell draw back to the
    raw `document.createTextNode(t)` → the cell reads 'code_generation' and this
    reds with the founder symptom. Reverting only the cell (not data-task) also
    reds — the two asserts are independent.
    """
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "routing.json").write_text(
        json.dumps(_humanizable_routing()), encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json")
        page.wait_for_timeout(1200)
        rows = page.evaluate(
            """() => Array.from(document.querySelectorAll('table.routing-table tbody tr'))
                .map(r => ({dataTask: r.dataset.task,
                            cell: (r.querySelector('td') || {}).textContent}))"""
        )
        # And the launchpad's deep-link (raw key) still focuses the right row.
        page.goto(f"file://{pages / 'memory.html'}?file=routing.json&task=data_analysis")
        page.wait_for_timeout(900)
        deeplink = page.evaluate(
            """() => {
              const f = document.querySelector('tr.routing-row-focused');
              return {dataTask: f ? f.dataset.task : null,
                      cell: f ? f.querySelector('td').textContent : null,
                      banner: !!document.querySelector('.viewer-health-banner')};
            }"""
        )
    finally:
        browser.close(); sp.stop()

    by_data = {r["dataTask"]: r["cell"] for r in rows}
    # PRECONDITION: all three multi-word rows rendered (the noise filter kept
    # them) — so the bite below is the humanization, not a missing row.
    assert set(by_data) == {"code_generation", "data_analysis", "cowork_general"}, (
        f"expected the 3 n>=2 snake_case rows, got {sorted(by_data)}"
    )
    # BITE: the DISPLAYED cell is title-cased, NOT the raw snake_case enum.
    assert by_data["code_generation"] == "Code Generation", (
        f"routing table leaked the raw snake_case task_type "
        f"(got {by_data['code_generation']!r}, want 'Code Generation') — the "
        "launchpad routing card title-cases the same value; this is the "
        "un-humanized sibling"
    )
    assert by_data["data_analysis"] == "Data Analysis", by_data
    assert by_data["cowork_general"] == "Cowork General", by_data
    # The RAW snake_case must survive as the dataset key (deep-link contract).
    assert set(by_data) == {"code_generation", "data_analysis", "cowork_general"}, (
        "dataset.task must stay raw snake_case — it's the launchpad deep-link key"
    )
    # The launchpad cross-link (raw key) still resolves to the right, now-humanized row.
    assert deeplink["dataTask"] == "data_analysis" and deeplink["cell"] == "Data Analysis", (
        f"the launchpad ?task=data_analysis deep-link no longer focuses its row "
        f"after humanization (key match broke): {deeplink}"
    )
    assert not deeplink["banner"], (
        "a present task wrongly showed the 'not yet' banner — humanizing the "
        "data-task key would have broken the cross-link match"
    )


def _render_portal_files(home: Path, files: dict) -> Path:
    """Render the portal with an arbitrary {relpath: content} memory-file set."""
    for rel, content in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    return home / "portal_pages"


def test_wrong_type_json_falls_to_raw_not_fake_cards():
    """guard_shape_not_just_parse — the CLIENT instance. renderJson inlines the
    raw memory-file string and parses it in the browser; the Readers
    (renderPicksReader/Routing/Topics) then assume a specific object shape. The
    server-side wrong-type guards (test_corrupted_state_resilience.py) never reach
    this client path. A valid-JSON-but-WRONG-TYPE picks.json (a clobbered/corrupt
    ARRAY) made the picks reader iterate array indices as fake basins — "0"/"1"/"2"
    cards with dead Mark-wrong/View-routing actions (browser-found 2026-06-02
    driving a corrupt home). The readerShapeOk guard now falls such a file to the
    Raw view. Mutation: drop readerShapeOk (readerSupported stays true) → the fake
    basin cards (pick-veto buttons) reappear and this reds."""
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_files(home, {
        # A valid lens proves one corrupt file doesn't break the whole viewer.
        "memories/lens.md": "# Lens\n\n- infrastructure over features\n",
        # WRONG TYPE: a JSON array where the picks reader expects a basin->pattern map.
        "scoreboard/picks.json": '[1, 2, 3, "not an object"]',
    })
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: errors.append(m.text) if m.type == "error" else None,
        )
        page.goto(f"file://{pages / 'memory.html'}?file=picks.json")
        page.wait_for_timeout(1200)
        info = page.evaluate(
            """() => {
              const t = (document.getElementById('content') || document.body).innerText;
              return {
                stuckLoading: /^Loading/.test(t.trim()),
                fakeBasinCards: document.querySelectorAll('button.pick-veto').length,
                hasReaderButton: [...document.querySelectorAll('.view-toggle button')]
                  .some(b => b.textContent.trim() === 'Reader'),
                rawPre: !!document.querySelector('pre'),
              };
            }"""
        )
    finally:
        browser.close()
        sp.stop()

    assert not info["stuckLoading"], "the corrupt picks.json view hung on 'Loading…'"
    assert info["fakeBasinCards"] == 0, (
        f"the wrong-type (array) picks.json rendered {info['fakeBasinCards']} fake "
        "basin cards (array indices as basins) — readerShapeOk didn't fall it to Raw"
    )
    assert not info["hasReaderButton"], (
        "the Reader toggle must be hidden for a wrong-shape file (it can't render it)"
    )
    assert info["rawPre"], "the wrong-type file must show its raw JSON in a <pre>"
    fatal = [e for e in errors if "favicon" not in e]
    assert not fatal, f"the corrupt picks.json view threw a JS error: {fatal}"
