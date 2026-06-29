"""Cross-surface guard: a recent-council card on the launchpad must CLICK THROUGH
to a live-council page that renders THAT council — the #1 thing a user does.

This is the boundary contract between two surfaces tested only from each side
today. The launchpad EMITS the card href — `../review_pages/live_council.html
?thread_id=<bundle_id>` (a relative path that climbs out of `portal_pages/` and a
`?thread_id=` param). The live council page CONSUMES that param to find the
council. Existing browser tests cover each side in isolation:
  • the live-council tests (`test_council_painkiller_browser`,
    `test_council_chain_action_browser`) navigate DIRECTLY to `?council_id=…` or
    `?thread_id=<root>` — they never start on the launchpad;
  • `test_council_rail_filter_browser` monkeypatches `_load_recent_councils` and
    only exercises the rail's filter UI, not the link target.
So nothing pins the actual contract: that the href the launchpad RENDERS for a real
council resolves — relative path + param name + the page's lookup — to a page
showing that council. A regression on either side (the card's `../review_pages/`
prefix, `thread_id` vs `council_id`, or the page's thread lookup) yields a dead
card or a blank council page, and every per-side test stays green
([[test_the_boundary_and_the_action]], [[live_council_chain_root_stale_js_404]]).

This seeds one real council with a distinctive synthesis marker, renders the real
launchpad + live page, serves them from the home root exactly like `handle_serve`
(so `../review_pages/` resolves), then CLICKS the recent card (not a hand-built
URL) and asserts the resulting page renders that council's synthesis with no 4xx.

Mutation-proven: break the launchpad card's relative-path/param template or the
live page's `?thread_id=` lookup and the click lands on a 404 / blank page → the
marker assertion reds. (Verified by hand during authoring: the real click-through
renders the marker; a broken href does not.)

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_clickthrough_a"
_MARKER = "Distinctive-synthesis-marker-XYZ wins for this tenancy model."


def _seed_one_council() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider=p, model="m", output_text=f"Answer from {p}. " * 20)
        for p in ("claude", "codex")
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=["both agree on the cache key"],
        disagreed_claims=[
            {
                "claim": "per-call vs in-process caching",
                "providers_for": ["claude"],
                "providers_against": ["codex"],
                "why_matters": "tenancy isolation",
            }
        ],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID,
            bundle_id=_CID,
            task_cluster_id="cluster_cache",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review the two answers.",
            synthesis_output=_MARKER,
            routing_label=routing_label,
            created_at="2026-06-05T00:00:00+00:00",
        )
    )
    write_portal_html()  # launchpad + vendor assets
    write_live_council_page()


def _serve(home: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(home)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_recent_council_card_clicks_through_to_its_live_page(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_one_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                bad: list[str] = []
                page_errs: list[str] = []
                page.on(
                    "response",
                    lambda r: bad.append(f"{r.status} {r.url.split('/')[-1]}")
                    if r.status >= 400 and "favicon" not in r.url
                    else None,
                )
                page.on("pageerror", lambda e: page_errs.append(str(e)[:160]))

                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                )
                page.wait_for_timeout(800)

                # The recent-council card link. It must exist (the launchpad found
                # the seeded council) and open in the SAME tab.
                link = page.locator("a[href*='live_council']").first
                href = link.get_attribute("href")
                assert href and "thread_id=" in href, (
                    f"recent-council card href is not the thread_id form: {href!r}"
                )
                assert link.get_attribute("target") in (None, "", "_self"), (
                    "recent card opens in a new tab — the click-through changed shape"
                )

                # CLICK it (not a hand-built URL) — the browser resolves the
                # `../review_pages/` relative path against the launchpad URL.
                link.click()
                page.wait_for_load_state("load")
                page.wait_for_timeout(1500)  # outcome-script + petite-vue hydration

                landed = page.url
                body = page.evaluate("document.body.innerText")
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert "/review_pages/live_council.html" in landed and "thread_id=" in landed, (
        f"the card did not navigate to the live council page (landed at {landed!r}) "
        "— the relative ../review_pages/ path or the param broke"
    )
    assert _MARKER in body, (
        "the live council page did not render THIS council's synthesis after the "
        "click-through — the page's ?thread_id= lookup didn't resolve the council "
        f"(body had {len(body)} chars). Landed at {landed!r}"
    )
    assert not bad, f"4xx responses during the click-through: {bad}"
    assert not page_errs, f"JS errors during the click-through: {page_errs[:4]}"


def _seed_chain_and_single() -> None:
    """A 3-round refine/continue chain (one chain_root_id, 3 outcomes) + a
    one-shot council — so the rail renders both a multi-round and a single row."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    root = "bundle_chain_root"
    rounds = [
        ("council_chain_a", "ORIGINAL chain question about caching", "2026-06-02T00:00:00+00:00", 1),
        ("council_chain_b", "refine round two", "2026-06-02T00:01:00+00:00", 2),
        ("council_chain_c", "refine round three", "2026-06-02T00:02:00+00:00", 3),
    ]
    for cid, task, ts, rnum in rounds:
        save_council_outcome(
            CouncilOutcome(
                council_run_id=cid,
                bundle_id=root,
                task_cluster_id="cluster_chain",
                primary_provider="claude",
                winner_provider="claude",
                metadata={"task_text": task, "chain_root_id": root, "round_number": rnum},
                member_results=[
                    CouncilMemberResult(provider="claude", model="m", output_text="a full answer here. " * 5)
                ],
                synthesis_output="s",
                routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
                created_at=ts,
            )
        )
    save_council_outcome(
        CouncilOutcome(
            council_run_id="council_solo",
            bundle_id="bundle_solo",
            task_cluster_id="cluster_solo",
            primary_provider="codex",
            winner_provider="codex",
            metadata={"task_text": "SOLO one-shot rename question", "chain_root_id": "bundle_solo", "round_number": 1},
            member_results=[
                CouncilMemberResult(provider="codex", model="m", output_text="a full answer here. " * 5)
            ],
            synthesis_output="s",
            routing_label=CouncilRoutingLabel(winner="codex", confidence="high", task_type="refactor"),
            created_at="2026-06-03T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def test_rail_multi_round_chain_shows_round_badge_single_does_not(tmp_path, monkeypatch):
    """USEFULNESS regression (ux sweep): the rendered rail must show a round-count
    badge on a multi-round chain row and NOT on a one-shot — driven in a real
    browser. segment_count is computed + threaded to the render, but the rail
    rendered a 3-round chain identically to a single council, so the chain
    (refine/continue/auto-chain) feature was invisible in the rail."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _seed_chain_and_single()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1100}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                state = page.evaluate(
                    "(vw) => {"
                    " const rows = [...document.querySelectorAll('.rail-council')];"
                    " const chain = rows.find(a => /ORIGINAL chain question/.test(a.innerText));"
                    " const solo = rows.find(a => /SOLO one-shot/.test(a.innerText));"
                    " const pill = chain ? chain.querySelector('.rail-council-rounds') : null;"
                    " const r = pill ? pill.getBoundingClientRect() : null;"
                    " return {"
                    "   chainFound: !!chain, soloFound: !!solo,"
                    "   chainBadgeText: pill ? pill.innerText : null,"
                    "   chainBadgeVisible: pill ? (pill.offsetParent !== null) : false,"
                    "   chainBadgeRight: r ? r.right : null,"
                    "   soloHasBadge: solo ? !!solo.querySelector('.rail-council-rounds') : null,"
                    " }; }",
                    1280,
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert state["chainFound"] and state["soloFound"], (
        f"rail did not render both seeded councils: {state}"
    )
    assert state["chainBadgeText"] and "3 rounds" in state["chainBadgeText"], (
        "the 3-round chain rendered with NO round-count badge in the rail — it "
        "looks IDENTICAL to a one-shot council, so the chain (refine/continue) "
        f"feature is invisible: {state}"
    )
    assert state["chainBadgeVisible"], (
        f"the chain round-count badge is in the DOM but not visible: {state}"
    )
    assert state["chainBadgeRight"] is not None and state["chainBadgeRight"] <= 1280 + 1, (
        f"the chain round-count badge overflows the 1280px viewport: {state}"
    )
    assert state["soloHasBadge"] is False, (
        "a one-shot council wrongly rendered a round-count badge — only "
        f"multi-round chains should: {state}"
    )
    assert not errs, f"JS errors rendering the rail with a chain badge: {errs[:4]}"
