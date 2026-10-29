"""Real-browser guard (#275, Iter 79): the memory viewer's routing.json Reader
must render provider names by MODEL BRAND (Claude / GPT / Gemini), NOT the raw
dispatch slug — in BOTH the column headers AND the per-row "Best" column.

Found 2026-06-18 by DRIVING the real routing.json Reader: its column headers read
'antigravity' / 'codex' and its Best column read 'codex' / 'antigravity' — the raw
dispatch slugs — while the launchpad routing table renders the SAME routing.json
data branded "GPT"/"Gemini" (formatProviderLabel). The #275 raw-slug-vs-brand class
the council surfaces closed (a3f9cfac), with the routing.json Reader as the leftover
un-branded sibling (alongside the picks Reader's "Use <X>" and the topology basin
detail's "Routes to <X>", fixed in the same iteration). The founder brand call
landed 2026-06-06; this surface was simply never flipped.

The fix brands at the two DISPLAY sites via a new `providerBrand()` helper
(canonProviderSlug for web-era folding + the Claude/GPT/Gemini map, single-sourced
to match the launchpad's formatProviderLabel), never a raw slug on a user-facing
label.

The KEY to this guard biting: seed `codex` and `antigravity` — the two slugs that
DIFFER from their brand — as both a column AND the Best winner. `claude` self-brands
("claude"→"Claude") and would HIDE the leak (the exact trap that let the sibling
survive). This drives the rendered DOM headers + Best column and asserts the brand
is present and the raw slug is ABSENT. Mutation-proven: revert either render site to
the raw slug → reds with 'antigravity'/'codex' in the headers or Best column.

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

# Raw dispatch slugs that must NOT appear in the rendered routing table (claude is
# excluded — it is both the slug and lower("Claude"), the one self-branding case).
_LEAK_SLUGS = ("codex", "antigravity")


def _synthetic_routing() -> dict:
    # Two task_types, each with a clear winner that is a NON-self-branding provider:
    #   debug → codex (→ GPT),  design → antigravity (→ Gemini).
    # All providers carry n>=2 so they survive the renderRoutingReader noise floor.
    return {
        "by_task_type": {
            "debug": {
                "claude": {"n": 4, "overall": 0.6, "wins": 1},
                "codex": {"n": 5, "overall": 0.9, "wins": 3},
                "antigravity": {"n": 3, "overall": 0.4, "wins": 0},
            },
            "design": {
                "codex": {"n": 3, "overall": 0.5, "wins": 1},
                "antigravity": {"n": 6, "overall": 0.9, "wins": 4},
            },
        },
        "best_per_task_type": {"debug": "codex", "design": "antigravity"},
        "pick_is_tie": {},
        "computed_at": "2026-06-18T00:00:00Z",
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
    assert (pages / "memory.html").exists()
    return pages


def test_routing_reader_brands_headers_and_best_column():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

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
            page = browser.new_context(viewport={"width": 1400, "height": 1100}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_timeout(1000)

            headers = page.evaluate(
                """() => [...document.querySelectorAll('.routing-table thead th')]
                          .map(e => (e.innerText || '').trim())"""
            )
            best_col = page.evaluate(
                """() => [...document.querySelectorAll('.routing-table tbody tr')]
                          .map(tr => (tr.lastElementChild.innerText || '').trim())"""
            )
            header_blob = " ".join(headers).lower()
            best_blob = " ".join(best_col).lower()

            # The branded model names must be present where the data has codex/antigravity.
            if "gpt" not in header_blob:
                failures.append(f"routing header column did not brand codex→GPT: {headers!r}")
            if "gemini" not in header_blob:
                failures.append(f"routing header column did not brand antigravity→Gemini: {headers!r}")
            if "gpt" not in best_blob:
                failures.append(f"Best column did not brand codex→GPT: {best_col!r}")
            if "gemini" not in best_blob:
                failures.append(f"Best column did not brand antigravity→Gemini: {best_col!r}")

            # And NO raw dispatch slug may leak into either the headers or the Best column.
            for slug in _LEAK_SLUGS:
                if slug in header_blob:
                    failures.append(f"raw dispatch slug {slug!r} leaked into routing headers — expected the brand (#275): {headers!r}")
                if slug in best_blob:
                    failures.append(f"raw dispatch slug {slug!r} leaked into the Best column — expected the brand (#275): {best_col!r}")

            if errs:
                failures.append(f"JS errors rendering the routing reader: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "routing.json Reader provider-brand (#275) regressed — a raw codex/antigravity "
        "slug leaked where the launchpad renders GPT/Gemini:\n  " + "\n  ".join(failures)
    )


# --- cross-language brand parity on legacy-alias providers --------------------
# providerBrand folds a slug through canonProviderSlug, which once claimed to mirror
# Python's _LEGACY_PROVIDER_ALIASES but carried only {chatgpt,gpt,claude_ai,gemini},
# so "google" / "bard" / "anthropic" painted as themselves here while Python
# provider_model_brand (and the launchpad) branded them Gemini / Gemini / Claude.
# This drove the picks Reader until picks.json left the viewer (2026-10-26); the
# routing Reader's Best column runs the same providerBrand, so it pins the parity
# now. Mutation-provable: shrink canonProviderSlug back to the 4-key subset and
# "Google" paints here while Python still says Gemini.
_LEGACY_BEST = {"t_gpt": "gpt", "t_google": "google", "t_bard": "bard", "t_anthropic": "anthropic"}


def _legacy_alias_routing() -> dict:
    return {
        "by_task_type": {task: {slug: {"n": 3, "overall": 0.8, "wins": 2}}
                         for task, slug in _LEGACY_BEST.items()},
        "best_per_task_type": dict(_LEGACY_BEST),
        "pick_is_tie": {},
        "computed_at": "2026-10-26T00:00:00Z",
    }


def test_routing_reader_brands_legacy_alias_best_like_python():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.council_schema import provider_model_brand

    py_brand = {task: provider_model_brand(slug) for task, slug in _LEGACY_BEST.items()}
    assert set(py_brand.values()) == {"GPT", "Gemini", "Claude"}, f"Python brand baseline shifted: {py_brand}"

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "routing.json").write_text(json.dumps(_legacy_alias_routing()), encoding="utf-8")
    env = dict(os.environ, TRINITY_HOME=str(home), TRINITY_AUTOSCAN_DISABLED="1")
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", "portal-html"],
                       env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1100}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_selector(".routing-table tbody tr", timeout=10000)
            painted = page.evaluate(
                """() => Object.fromEntries([...document.querySelectorAll('.routing-table tbody tr')]
                     .map(tr => [tr.dataset.task, (tr.lastElementChild.innerText || '').trim()]))"""
            )
            if set(painted) != set(py_brand):
                failures.append(f"not every row mounted: {sorted(painted)} vs {sorted(py_brand)}")
            for task, brand in py_brand.items():
                got = painted.get(task, "")
                if brand not in got:
                    failures.append(f"{task} ({_LEGACY_BEST[task]!r}): Best painted {got!r}, Python brands it {brand!r}")
            if errs:
                failures.append(f"JS errors rendering the routing reader: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "cross-language brand divergence in the routing Reader — a legacy-alias provider "
        "branded differently than Python provider_model_brand:\n  " + "\n  ".join(failures)
    )


def test_routing_subtitle_names_no_retired_reader():
    """The routing.json header subtitle once named `route` (removed 2026-06-08) and
    then `ask` (removed 2026-08-11) as its reader. An earlier version of this test
    REQUIRED "ask" and passed only because "per-task-type" contains those letters.
    Whole words now: no retired reader, and the subtitle says where the numbers
    come from."""
    pytest.importorskip("playwright.sync_api")
    import re

    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1100}).new_page()
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_timeout(900)
            subtitle = page.evaluate(
                """() => {
                    const metas = [...document.querySelectorAll('#content .content-header .meta, #content .meta')];
                    const hit = metas.find(e => /track record/.test(e.innerText || ''));
                    return hit ? (hit.innerText || '').trim() : '';
                }"""
            )
        finally:
            browser.close()

    assert subtitle, "the routing.json header subtitle did not render"
    low = subtitle.lower()
    retired = [w for w in ("route", "ask", "get_picks") if re.search(rf"\b{w}\b", low)]
    assert not retired, f"routing.json subtitle names a retired reader {retired}: {subtitle!r}"
    assert "council outcomes" in low, f"routing.json subtitle no longer says where its numbers come from: {subtitle!r}"
