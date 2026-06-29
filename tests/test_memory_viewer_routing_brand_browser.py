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
to match the launchpad's formatProviderLabel). The slug stays raw only in the
picks↔topology IDENTITY bridge logic, never on a user-facing label.

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


# --- picks Reader: cross-language brand parity on legacy-alias winners --------
# The picks Reader paints "Use <providerBrand(winner)>" per card. providerBrand
# folds the winner via canonProviderSlug, which CLAIMED to mirror the Python
# _LEGACY_PROVIDER_ALIASES but carried only {chatgpt,gpt,claude_ai,gemini}. So a
# picks.json winner of "google" / "bard" / "anthropic" / "claude.ai" painted
# "Use Google" / "Use Bard" / "Use Anthropic" here while Python
# provider_model_brand (and the launchpad cheat-sheet) both branded them
# "Gemini" / "Gemini" / "Claude" / "Claude". Same picks.json winner, two readers,
# two answers. This drives the REAL picks Reader and asserts each "Use <brand>"
# equals "Use <provider_model_brand(winner)>". Mutation-provable: shrink
# canonProviderSlug back to the 4-key subset → "Use Google" reds here while
# Python still says Gemini.

# Margins all >= the 0.15 routing floor → the card says "Use" (not "Lean"), so
# the painted prefix is stable.
_PICKS_LEGACY_ALIASES = {
    "b00": {"winner": "gpt", "count": 9, "margin": 0.42, "n_episodes": 9, "evidence": []},
    "b01": {"winner": "google", "count": 6, "margin": 0.31, "n_episodes": 6, "evidence": []},
    "b02": {"winner": "bard", "count": 5, "margin": 0.28, "n_episodes": 5, "evidence": []},
    "b03": {"winner": "anthropic", "count": 4, "margin": 0.22, "n_episodes": 4, "evidence": []},
}


def _render_portal_with_picks(home: Path, picks: dict) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps(picks), encoding="utf-8")
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


def test_picks_reader_brands_legacy_alias_winner_like_python():
    """The picks Reader's providerBrand must brand a legacy-alias winner
    identically to Python provider_model_brand — no cross-language divergence
    (the founder's same-value-two-readers shape)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.council_schema import provider_model_brand

    # Python truth, in the render order (picks render in their dict/key order; we
    # match by reading the basin id off each card so order can't desync the
    # assertion).
    py_brand = {
        bid: provider_model_brand(p["winner"]) for bid, p in _PICKS_LEGACY_ALIASES.items()
    }
    # Source sanity: these slugs genuinely fold to the brand trio in Python.
    assert set(py_brand.values()) == {"GPT", "Gemini", "Claude"}, (
        f"Python brand baseline shifted: {py_brand}"
    )

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal_with_picks(home, _PICKS_LEGACY_ALIASES)

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
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_selector(".pick-card .pick-primary", timeout=10000)
            page.wait_for_timeout(400)

            # Read {basin_id: painted "Use <brand>"} so the assertion is order-free.
            painted = page.evaluate(
                """() => {
                    const out = {};
                    for (const card of document.querySelectorAll('.pick-card')) {
                        const bid = card.dataset.task;
                        const prim = card.querySelector('.pick-primary');
                        if (bid && prim) out[bid] = (prim.innerText || '').trim();
                    }
                    return out;
                }"""
            )

            # Precondition: every seeded pick card mounted (bite = the brand,
            # not a vacuous empty map).
            if set(painted.keys()) != set(py_brand.keys()):
                failures.append(
                    f"not all pick cards mounted: painted basins={sorted(painted.keys())} "
                    f"expected {sorted(py_brand.keys())}"
                )
            for bid, brand in py_brand.items():
                want = f"Use {brand}"
                got = painted.get(bid, "")
                if got != want:
                    failures.append(
                        f"basin {bid} winner {_PICKS_LEGACY_ALIASES[bid]['winner']!r}: "
                        f"picks Reader painted {got!r} but Python provider_model_brand "
                        f"says {want!r} — canonProviderSlug dropped a legacy alias the "
                        "Python map (and the launchpad cheat-sheet) fold."
                    )
            if errs:
                failures.append(f"JS errors rendering the picks reader: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "CROSS-LANGUAGE BRAND DIVERGENCE on the picks Reader — a legacy-alias "
        "winner branded differently than Python provider_model_brand:\n  "
        + "\n  ".join(failures)
    )


def test_routing_subtitle_names_a_tool_the_user_can_invoke_not_retired_route():
    """The routing.json header subtitle must name a LIVE reader, not the retired
    standalone `route` tool.

    Found 2026-06-19 by DRIVING the real memory viewer routing.json view: its
    subtitle read "...read by route + launchpad" — but the standalone `route`
    MCP tool was REMOVED 2026-06-08 (loop-primitive surface cut; see
    retired_names.py `mcp_tool:route`). There is no `route` MCP tool AND no
    `route` CLI verb, so the copy named a reader the user cannot find anywhere —
    the routing track record is actually read by `ask` (which aggregates
    compute_personal_routing_table) + the launchpad, exactly as the SIBLING
    picks.json subtitle correctly says "read by ask + chairman picker".

    This asserts the RENDERED subtitle text (not a source string): it must NOT
    say "read by route" and MUST name `ask`. Mutation-proven: revert the tagline
    to "...read by route + launchpad" and this reds.
    """
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
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_timeout(900)

            # The subtitle is the .meta line under the routing.json <h2> header
            # ("scoreboard (operational) · <tagline>"). Read the rendered text.
            subtitle = page.evaluate(
                """() => {
                    const metas = [...document.querySelectorAll('#content .content-header .meta, #content .meta')];
                    const hit = metas.find(e => /track record|read by/.test(e.innerText || ''));
                    return hit ? (hit.innerText || '').trim() : '';
                }"""
            )

            if not subtitle:
                failures.append(
                    "could not find the routing.json header subtitle — the .meta "
                    "tagline line did not render (precondition for this guard)"
                )
            else:
                low = subtitle.lower()
                # THE BITE: must not name the retired standalone `route` tool.
                if "read by route" in low or "route + launchpad" in low:
                    failures.append(
                        "routing.json subtitle names the RETIRED `route` tool as a "
                        "reader (removed 2026-06-08; no `route` MCP tool or CLI verb "
                        "exists) — the user is pointed at a reader they cannot find. "
                        f"Subtitle: {subtitle!r}"
                    )
                # And it must name a reader the user CAN invoke (ask), matching the
                # picks.json sibling's "read by ask".
                if "ask" not in low:
                    failures.append(
                        "routing.json subtitle does not name `ask` as its reader — "
                        "the routing track record is read by ask (+ launchpad), like "
                        f"the picks.json sibling. Subtitle: {subtitle!r}"
                    )
        finally:
            browser.close()

    assert not failures, (
        "routing.json header subtitle copy regressed — it named a retired/unfindable "
        "reader:\n  " + "\n  ".join(failures)
    )
