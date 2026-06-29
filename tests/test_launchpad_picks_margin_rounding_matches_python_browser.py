"""Real-browser guard: the picks.json *margin* renders IDENTICALLY across the
launchpad routing card, the memory-viewer picks Reader, and the CLI `consolidate`
line — never with JavaScript's different rounding.

The cross-language bug this guard bites (the "Python computes, the JS re-formats,
they disagree" vein — the sibling of the eval-score rounding fix): the routing
`margin` is a raw float in picks.json (`lens_routing.py` writes `round(x, 3)`).
THREE surfaces render it:
  * the CLI `consolidate` operator line (`commands/cortex.py` `f"{m:.2f}"`),
  * the launchpad routing card (the JS template), and
  * the memory-viewer picks Reader + topology basin detail (client-side JS).
The two JS surfaces formatted with `Number.toFixed(2)` (round-HALF-UP) while the
CLI uses Python's `format` (round-half-to-EVEN / banker's). They DIVERGE on an
exact dyadic tie:

  * margin exactly 0.625 (== 5/8):
      Python `f"{0.625:.2f}"`    -> "0.62"   (banker's)
      JS     `(0.625).toFixed(2)` -> "0.63"   (half-up)
  * margin exactly 0.125 (== 1/8):
      Python `f"{0.125:.2f}"`    -> "0.12"
      JS     `(0.125).toFixed(2)` -> "0.13"

So the SAME picks.json basin read "margin 0.63" on the launchpad + memory viewer
but "margin 0.62" in the CLI — one ledger, two numbers across surfaces.

Root-cause fix (format ONCE in Python, JS renders verbatim — the _fmt_score
precedent): launchpad_data._load_cortex_rules now adds a pre-formatted
`margin_str` (Python 2dp) the routing-card template renders instead of
`r.margin.toFixed(2)`; the memory viewer injects a server-built
`MARGIN_FMT` map (raw-value repr -> Python 2dp) the JS renders via `fmtMargin`
instead of `mval.toFixed(2)`.

This guard seeds two basins whose margins land EXACTLY on those ties, drives BOTH
the real launchpad routing card AND the memory-viewer picks Reader, reads the
PAINTED "margin X.XX" text, and asserts each equals the Python `:.2f` rendering
(== the CLI value). Source-sanity precondition (the BITE): a naive JS toFixed
WOULD differ, so the fixture genuinely diverges. Mutation-proven to fail on the
old `.toFixed()` renders.

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

# The discriminating boundary margins: exact dyadic ties where Python's
# round-half-to-even and JS's round-half-up DISAGREE.
_M_HI = 5 / 8   # == 0.625 exactly. py :.2f -> "0.62", js toFixed(2) -> "0.63".
_M_LO = 1 / 8   # == 0.125 exactly. py :.2f -> "0.12", js toFixed(2) -> "0.13".


def _render(home: Path) -> tuple[Path, Path]:
    sb = home / "scoreboard"
    sb.mkdir(parents=True, exist_ok=True)
    (sb / "picks.json").write_text(
        json.dumps({
            "b00": {"winner": "claude", "count": 8, "margin": _M_HI,
                    "n_episodes": 8, "evidence": ["council_a"]},
            "b01": {"winner": "codex", "count": 6, "margin": _M_LO,
                    "n_episodes": 6, "evidence": ["council_b"]},
        }),
        encoding="utf-8",
    )
    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    # topics.json so the picks basins resolve names (and the routing card has its
    # topology bridge); the size/thread fields keep the topology detail honest.
    (mem / "topics.json").write_text(
        json.dumps({"basins": [
            {"id": "b00", "label": "data pipelines", "size": 8, "thread_count": 3,
             "representatives": [{"text": "rep one"}], "top_terms": ["etl", "load"]},
            {"id": "b01", "label": "ui layout", "size": 6, "thread_count": 2,
             "representatives": [{"text": "rep two"}], "top_terms": ["flex", "grid"]},
        ]}),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    stats = home / "portal_pages" / "stats.html"
    memory = home / "portal_pages" / "memory.html"
    assert stats.exists() and memory.exists()
    return stats, memory


def test_picks_margin_rounds_like_python_not_javascript():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # The Python (CLI consolidate / launchpad-data) renderings — the values that
    # MUST win on every surface. Computed exactly as commands/cortex.py does.
    py_hi = f"{_M_HI:.2f}"   # "0.62"
    py_lo = f"{_M_LO:.2f}"   # "0.12"
    # Source-sanity (the BITE precondition): a naive JS re-round WOULD differ —
    # otherwise this fixture proves nothing. Confirm the tie really diverges.
    assert py_hi == "0.62", py_hi
    assert py_lo == "0.12", py_lo
    assert f"{_M_HI:.2f}" != "0.63" and f"{_M_LO:.2f}" != "0.13"

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    stats_path, memory_path = _render(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 3200})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            # --- launchpad routing card (the /stats surface) ---
            page.goto(f"file://{stats_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering /stats: {errs[:3]}"
            lp = page.evaluate(
                r"""() => {
                  const body = document.body.innerText || '';
                  const visible = Array.from(body.matchAll(/margin\s+(\d\.\d\d)/g))
                                       .map(m => m[1]);
                  const titles = Array.from(document.querySelectorAll('[title]'))
                    .map(e => e.getAttribute('title') || '')
                    .flatMap(t => Array.from(t.matchAll(/[Mm]argin\s+(\d\.\d\d)/g))
                                       .map(m => m[1]));
                  return {visible, titles};
                }"""
            )

            # --- memory viewer picks Reader ---
            page.goto(f"file://{memory_path}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering picks Reader: {errs[:3]}"
            mv = page.evaluate(
                r"""() => {
                  const body = document.body.innerText || '';
                  return Array.from(body.matchAll(/margin\s+(\d\.\d\d)/g)).map(m => m[1]);
                }"""
            )
        finally:
            browser.close()

    failures: list[str] = []

    lp_visible = set(lp.get("visible") or [])
    lp_titles = set(lp.get("titles") or [])
    mv_visible = set(mv or [])

    # The founder symptom: an exact-tie margin painting "0.63"/"0.13" (JS half-up)
    # on the launchpad while the CLI prints "0.62"/"0.12" (Python banker's).
    if "0.63" in lp_visible or "0.13" in lp_visible:
        failures.append(
            f"LAUNCHPAD routing card painted a JS half-up margin {sorted(lp_visible)} "
            f"(expected Python banker's '{py_hi}'/'{py_lo}') — same picks.json the CLI "
            f"`consolidate` prints '0.62'/'0.12', two numbers across surfaces"
        )
    if py_hi not in lp_visible or py_lo not in lp_visible:
        failures.append(
            f"LAUNCHPAD routing card did not paint the Python margins "
            f"'{py_hi}'/'{py_lo}' — got visible {sorted(lp_visible)}"
        )
    # The title tooltips render the margin too — they must not leak the half-up form.
    if "0.63" in lp_titles or "0.13" in lp_titles:
        failures.append(
            f"LAUNCHPAD routing card TITLE tooltip painted a JS half-up margin "
            f"{sorted(lp_titles)} (expected '{py_hi}'/'{py_lo}')"
        )

    if "0.63" in mv_visible or "0.13" in mv_visible:
        failures.append(
            f"MEMORY VIEWER picks Reader painted a JS half-up margin {sorted(mv_visible)} "
            f"(expected Python banker's '{py_hi}'/'{py_lo}') — same divergence as the "
            f"launchpad pre-fix, against the CLI's '0.62'/'0.12'"
        )
    if py_hi not in mv_visible or py_lo not in mv_visible:
        failures.append(
            f"MEMORY VIEWER picks Reader did not paint the Python margins "
            f"'{py_hi}'/'{py_lo}' — got {sorted(mv_visible)}"
        )

    assert not failures, (
        "picks.json margin diverges across surfaces (JS round-half-up vs Python "
        "banker's):\n  " + "\n  ".join(failures)
        + f"\n  (launchpad visible={sorted(lp_visible)} titles={sorted(lp_titles)}; "
        + f"viewer visible={sorted(mv_visible)})"
    )
