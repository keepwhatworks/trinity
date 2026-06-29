"""Browser guard for the LIVE council page's member-response layout.

The side-by-side comparison of member answers IS the council painkiller — seeing
Claude's and Codex's answers next to each other is the whole value. The layout
special-cases ONLY exactly-3 members (`answers-grid-three` → 3 fixed columns); the
DOMINANT case — 2 members (79% of the founder's councils, per
[[live_council_page_verified_2member_dominant]]) — rides the base `.answers-grid`
(`repeat(auto-fit, minmax(380px, 1fr))`). A minmax bump or a wrong class condition
would silently STACK the answers, killing the comparison.

`test_council_review_layout_browser.py` guards this — but on `render_unified_council_page`,
which (verified 2026-06-02) has ZERO production callers: `write_unified_council_page`
only writes a redirect to `live_council.html` (`render_live_council_page`), so the
page users actually hit is the LIVE one, and its 2-member layout had no equivalent
browser guard. (The guard-on-dead-renderer is the orphan-code-path trap from
[[mutation_testing_validates_regression_coverage]].) This closes that gap on the
page production serves.

Serves an isolated, PII-free synthetic council over http (the page reads
`?council_id=`, file:// can't carry it). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _seed(cid: str, n_members: int):
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    providers = ["claude", "codex", "antigravity"][:n_members]
    members = [
        CouncilMemberResult(provider=p, model="m", output_text=f"Answer from {p}. " * 30)
        for p in providers
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid,
            task_cluster_id="cluster_layout",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review.",
            synthesis_output="In-process caching wins.",
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at="2026-06-02T00:00:00+00:00",
        )
    )
    write_portal_html()  # vendor
    write_live_council_page()


_GEOM = """() => {
  const cards = [...document.querySelectorAll('.provider-status-row')];
  const grid = document.querySelector('.answers-grid');
  return {
    n: cards.length,
    gridClass: grid ? grid.className : null,
    rects: cards.map(c => { const r = c.getBoundingClientRect();
      return {x: Math.round(r.left), top: Math.round(r.top), w: Math.round(r.width)}; }),
  };
}"""


def _serve(tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _measure(tmp_path, cid: str, viewport_width: int) -> dict:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": viewport_width, "height": 1000}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}")
                page.wait_for_timeout(1100)
                geom = page.evaluate(_GEOM)
                geom["errs"] = errs
                return geom
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def _side_by_side(rects: list[dict]) -> bool:
    """All cards share a top edge AND occupy distinct x positions (no stacking)."""
    if len(rects) < 2:
        return False
    tops = {r["top"] for r in rects}
    xs = {r["x"] for r in rects}
    return len(tops) == 1 and len(xs) == len(rects)


def test_two_member_live_layout_is_side_by_side(tmp_path, monkeypatch):
    """The dominant 2-member case must render side-by-side on the LIVE page."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed("c_two", 2)

    geom = _measure(tmp_path, "c_two", 1400)
    assert not geom["errs"], f"JS errors: {geom['errs'][:3]}"
    assert geom["n"] == 2, f"expected 2 member cards, got {geom['n']}"
    # 2-member rides the BASE grid — must NOT pick up the 3-col special-case class.
    assert geom["gridClass"] == "answers-grid", (
        f"2-member grid class drifted to {geom['gridClass']!r} (should be base 'answers-grid')"
    )
    assert _side_by_side(geom["rects"]), (
        f"2-member answers STACKED instead of side-by-side — the comparison is broken: {geom['rects']}"
    )


def test_three_member_live_layout_uses_three_column_class(tmp_path, monkeypatch):
    """The 3-member trio must carry the explicit 3-column class AND render side-by-side."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed("c_three", 3)

    geom = _measure(tmp_path, "c_three", 1400)
    assert not geom["errs"], f"JS errors: {geom['errs'][:3]}"
    assert geom["n"] == 3, f"expected 3 member cards, got {geom['n']}"
    # Assert the class ATTRIBUTE, not a bare substring — `.answers-grid-three` is
    # always present as a <style> RULE (the v1.7.228 trap).
    assert geom["gridClass"] == "answers-grid answers-grid-three", (
        f"3-member grid did not get the 3-column class: {geom['gridClass']!r}"
    )
    assert _side_by_side(geom["rects"]), f"3-member answers not side-by-side: {geom['rects']}"


# --- mid-width tablet band: the LIVE page's 3-member grid must reflow 3→2→1 ---
#
# The two tests above drive the LIVE page's 3-member grid ONLY at 1400px (3 cols);
# test_live_council_320px_token_overflow_browser drives it at 320/375 (1 col).
# The MID-WIDTH tablet band — 769–1200px (→ 2 cols) plus the 768px 1-col boundary —
# was NEVER driven on the LIVE page (render_live_council_page). The static page's
# equivalent band IS guarded (test_council_review_midwidth_grid_reflow_browser) but
# that drives render_unified_council_page, which has ZERO production callers (the
# orphan-renderer trap this file's own header calls out). The page production serves
# carries its OWN copy of the answers-grid media queries (council_review.py ~1001 /
# ~1010 / ~1016) and its own `.provider-status-row { min-width: 0 }` containment —
# either can drift from the static page while the 1400 + 320/375 guards stay green,
# stranding a real tablet user on a cramped/overflowing 3-up. This closes that band.

# A WIDE code block + a long UNBREAKABLE token: the two shapes that historically
# stretched a grid track past the viewport (the min-width:0 / pre-overflow class).
_WIDE_CODE = (
    "```python\n"
    "def build_cache_key(tenant_id, namespace, payload_hash, region, version, replica):\n"
    "    return f'{tenant_id}:{namespace}:{payload_hash}:{region}:{version}:{replica}:cache'\n"
    "```\n"
)
_LONG_TOKEN = "supercalifragilisticexpialidocious_" * 4 + "END"


def _seed_three_wide(cid: str):
    """3-member live council whose answers carry a wide code block + a long
    unbreakable token — the worst case for a grid track stretching past the
    viewport in the 2-up / 1-up reflow band."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    providers = ["claude", "codex", "antigravity"]
    members = [
        CouncilMemberResult(
            provider=p,
            model="m",
            output_text=(f"Answer from {p}. " * 20)
            + "\n\n"
            + _WIDE_CODE
            + "\n\nToken: "
            + _LONG_TOKEN,
        )
        for p in providers
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=cid,
            bundle_id=cid,
            task_cluster_id="cluster_midband",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review.",
            synthesis_output="In-process caching wins.",
            routing_label=CouncilRoutingLabel(
                winner="claude", confidence="high", task_type="design"
            ),
            created_at="2026-06-02T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


# Column count = max distinct grid-item left edges sharing a row; plus page-level
# h-overflow (scrollW vs clientW AND no element's right edge past the viewport).
_BAND_GEOM = """(vw) => {
  const cards = [...document.querySelectorAll('.provider-status-row')];
  const grid = document.querySelector('.answers-grid');
  const xsPerTop = {};
  cards.forEach(c => { const r = c.getBoundingClientRect();
    const t = Math.round(r.top); (xsPerTop[t] = xsPerTop[t] || new Set()).add(Math.round(r.left)); });
  const cols = Math.max(0, ...Object.values(xsPerTop).map(v => v.size));
  const de = document.documentElement;
  let maxRight = 0, worst = null;
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > maxRight) { maxRight = Math.round(r.right);
      worst = (el.tagName + '.' + (el.className||'')).slice(0, 60); }
  });
  return {
    n: cards.length,
    gridClass: grid ? grid.className : null,
    cols,
    docOverflow: de.scrollWidth > de.clientWidth + 1,
    maxRight,
    worst,
    braceLeak: document.body.innerText.includes('{{'),
  };
}"""


def _measure_band(tmp_path, cid: str, vw: int) -> dict:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": vw, "height": 1000}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={cid}"
                )
                page.wait_for_timeout(1100)
                geom = page.evaluate(_BAND_GEOM, vw)
                geom["errs"] = errs
                return geom
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# 1280 → 3 cols, the 769–1200 tablet band → 2 cols, the 768 boundary + phone → 1 col.
_BAND_EXPECT = {1280: 3, 1200: 2, 1024: 2, 900: 2, 768: 1, 560: 1, 375: 1}


@pytest.mark.parametrize("vw,want_cols", list(_BAND_EXPECT.items()))
def test_three_member_live_grid_reflows_across_midwidth_band(tmp_path, monkeypatch, vw, want_cols):
    """The LIVE page's 3-member grid must reflow 3→2→1 across the tablet band and
    NEVER overflow horizontally — even with a wide code block + a long unbreakable
    token. Bites a media-query drift or a dropped `.provider-status-row{min-width:0}`
    that strands a tablet user on a cramped/overflowing 3-up (the live page carries
    its OWN copy of the rules the dead static renderer's guard covers)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_three_wide("c_midband")

    geom = _measure_band(tmp_path, "c_midband", vw)
    assert not geom["errs"], f"JS errors at {vw}px: {geom['errs'][:3]}"
    assert not geom["braceLeak"], f"raw {{{{ }}}} leaked at {vw}px: petite-vue didn't mount"
    # False-pass guard: all 3 member rows must actually render, else the column
    # assertion is vacuous.
    assert geom["n"] == 3, f"expected 3 member rows at {vw}px, got {geom['n']}"
    assert geom["gridClass"] == "answers-grid answers-grid-three", (
        f"3-member grid lost the 3-column class at {vw}px: {geom['gridClass']!r}"
    )
    assert geom["cols"] == want_cols, (
        f"LIVE 3-member grid did not reflow to {want_cols} column(s) at {vw}px "
        f"(got {geom['cols']}) — the live page's answers-grid media query drifted, "
        f"stranding a tablet user on a cramped 3-up"
    )
    # The founder symptom: a wide code block / long token stretches a grid track
    # past the viewport (the .provider-status-row{min-width:0} + pre-overflow class).
    assert not geom["docOverflow"] and geom["maxRight"] <= vw + 1, (
        f"LIVE 3-member grid OVERFLOWS horizontally at {vw}px "
        f"(maxRight={geom['maxRight']}, widest={geom['worst']!r}) — a member answer's "
        f"wide code/long token stretched a grid track past the viewport"
    )
