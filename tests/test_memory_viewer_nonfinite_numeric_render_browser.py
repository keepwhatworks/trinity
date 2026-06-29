"""Real-browser guard: a CORRUPT non-finite numeric in picks.json / routing.json must
NOT paint "Infinity" / "-Infinity" / "NaN" into the memory viewer's Reader cells.

The numeric-corruption analog of #304 (utils.finite_float_or_none) carried into the
viewer's CLIENT-SIDE render. Unlike the launchpad — whose page_data is inlined through
design_system.page_data_script_json (_finite_json_safe + allow_nan=False, so a
non-finite float becomes null) — the memory viewer inlines picks.json AND routing.json
as RAW TEXT into window.__TRINITY_MEMORIES__ and parses them client-side. A poisoned /
hand-edited `"margin": 1e400` (the literal a non-Python writer or a clamp overflow can
produce) is valid-enough text that JS `JSON.parse` yields `Infinity` — and the picks
Reader's `typeof p.margin === "number"` ACCEPTED it, so `fmtMargin(Infinity)` fell
through to `value.toFixed(2)` and the pick badge painted:

    b00 · Use Claude · margin Infinity · n=Infinity

(driven 2026-06-21, Iter 256). Same shape on the routing.json Reader: an `overall`
of 1e400 painted "Infinity (n=Infinity)" in the per-provider cell.

Fix (memory_viewer.py): a shared `isFiniteNumber(x)` = `typeof x === "number" &&
isFinite(x)` — the JS analog of finite_float_or_none — routed through every picks /
routing numeric-paint site (fmtMargin, renderPicksReader mval/n, the routing cell
overall/n, the cross-memory bridge margin, trustBadgeClass/marginGloss). A non-finite
value is now treated as ABSENT: the margin badge drops, the cell shows "—".

Seeds a picks.json with margin/count = ±1e400 and a routing.json with overall/n = 1e400
(render-INDEPENDENT precondition: the stored fields parse to genuinely non-finite floats
in Python — the seed is corrupt), renders the real portal, and asserts no
Infinity/-Infinity/NaN/undefined token reaches either Reader's DOM AND the surface still
PAINTS (the clean rows render — not a blank-page false pass).

Mutation-proven: revert any of the isFiniteNumber guards in memory_viewer.py (restore
the bare `typeof === "number"`) → re-render → the non-finite assertion reds with the
founder symptom "margin Infinity · n=Infinity". Verified by hand 2026-06-21.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
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

# A non-Python writer / a clamp overflow / a hand-edit can land 1e400 in the JSON.
# Python json.loads("1e400") -> float('inf'); JS JSON.parse(...) -> Infinity.
_PICKS_RAW = """{
  "b00": {"winner": "claude", "count": 1e400, "margin": 1e400, "n_episodes": 5, "evidence": ["c1"]},
  "b01": {"winner": "codex", "count": 4, "margin": -1e400, "n_episodes": 4, "evidence": ["c2"]},
  "b02": {"winner": "antigravity", "count": 3, "margin": 0.42, "n_episodes": 3, "evidence": ["c3"]}
}"""

_ROUTING_RAW = """{
  "by_task_type": {
    "code_generation": {"claude": {"overall": 1e400, "n": 1e400}, "codex": {"overall": 0.8, "n": 5}},
    "debugging": {"claude": {"overall": 0.7, "n": 4}, "codex": {"overall": 0.6, "n": 4}}
  },
  "best_per_task_type": {"code_generation": "codex", "debugging": "claude"},
  "pick_is_tie": {},
  "computed_at": "2026-06-21T00:00:00"
}"""

_BAD_TOKENS = ("Infinity", "-Infinity", "NaN", "undefined", "[object Object]")


def _render_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(_PICKS_RAW, encoding="utf-8")
    (home / "scoreboard" / "routing.json").write_text(_ROUTING_RAW, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def _assert_precondition_corrupt():
    """Render-INDEPENDENT: the seeded fields are genuinely non-finite in Python, so
    the fixture is corrupt BEFORE any render (a clean fixture would make the no-token
    assertion pass vacuously)."""
    import math

    picks = json.loads(_PICKS_RAW)
    assert math.isinf(picks["b00"]["margin"]), "fixture not corrupt: b00.margin must be non-finite"
    assert math.isinf(picks["b00"]["count"]), "fixture not corrupt: b00.count must be non-finite"
    assert math.isinf(picks["b01"]["margin"]), "fixture not corrupt: b01.margin must be non-finite"
    routing = json.loads(_ROUTING_RAW)
    cg = routing["by_task_type"]["code_generation"]["claude"]
    assert math.isinf(cg["overall"]), "fixture not corrupt: routing overall must be non-finite"
    assert math.isinf(cg["n"]), "fixture not corrupt: routing n must be non-finite"


def test_nonfinite_picks_and_routing_render_no_infinity_token():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _assert_precondition_corrupt()

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            # ---- picks Reader ----
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            # (A) the surface PAINTS — the clean basin b02 (margin 0.42) renders, so a
            # blank page can't pass the no-token assertion vacuously.
            cards = page.eval_on_selector_all(
                ".pick-card",
                "els => els.map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())",
            )
            if not any("margin 0.42" in c for c in cards):
                failures.append(
                    "picks Reader did not paint the clean basin (margin 0.42) — "
                    f"can't trust the no-Infinity assertion. cards={cards!r}"
                )
            picks_text = page.evaluate("() => document.body.innerText")
            for tok in _BAD_TOKENS:
                if tok in picks_text:
                    failures.append(
                        f"picks Reader leaked the non-finite token {tok!r} from a "
                        f"corrupt picks.json (1e400 margin/count -> 'margin Infinity · "
                        f"n=Infinity'). cards={cards!r}"
                    )

            # ---- routing.json Reader ----
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_timeout(900)
            rows = page.eval_on_selector_all(
                "table tr",
                "els => els.map(r => (r.innerText || '').replace(/\\s+/g, ' ').trim())",
            )
            # (A) paints — the clean debugging row (0.7 / 0.6) renders.
            if not any("0.7" in r and "0.6" in r for r in rows):
                failures.append(
                    f"routing Reader did not paint the clean row (0.7 / 0.6). rows={rows!r}"
                )
            routing_text = page.evaluate("() => document.body.innerText")
            for tok in _BAD_TOKENS:
                if tok in routing_text:
                    failures.append(
                        f"routing Reader leaked the non-finite token {tok!r} from a "
                        f"corrupt routing.json (1e400 overall/n -> 'Infinity (n=Infinity)'). "
                        f"rows={rows!r}"
                    )

            if errs:
                failures.append(f"JS errors during viewer render: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "memory viewer painted a non-finite numeric from a corrupt scoreboard file:\n  "
        + "\n  ".join(failures)
    )
