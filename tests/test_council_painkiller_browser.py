"""Browser guard for the LIVE council page's PAINKILLER content + no-404 probe.

The product differentiator is the synthesized cross-provider answer WITH the
`disagreed_claims` (where the models diverged) — not just the side-by-side member
layout (that's `test_council_live_layout_browser.py`). Two things had a guard
ONLY in the non-CI `scripts/browser_smoke.py` (Surface 35), never in the
CI-protected `-m browser` suite, so a regression in either passed CI green:

  1. **The claims render.** Surfaces 5/9 rendered the live page but asserted only
     synthesis + quote chips — never the agreed/disagreed claims. A binding break
     on `routingLabelFor(seg).disagreed_claims` would silently drop the
     differentiator.
  2. **No thread-manifest 404 on a standalone council.** `_maybeOfferThreadLink`
     once probed `_thread_council_<id>.js` for EVERY council via a
     `chain_root_id || councilId` fallback; standalone councils (98.8%) have no
     chain_root_id, so that file never exists → a 404 on every single-council
     view ([[live_council_chain_root_stale_js_404]] / Surface-35 finding). The
     fix only probes when chain_root_id is explicit. This pins it: a standalone
     council must load with ZERO `_thread_*` 404s.

Serves an isolated, PII-free synthetic council over http (the page reads
`?council_id=`; file:// can't carry it). Slow + browser marked; skips without
Playwright/chromium — and runs in the CI `browser` job, unlike browser_smoke.py.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_painkiller_browsertest"
_DISAGREED_CLAIM = "Whether to cache per-call or in-process changes the failure mode"
_AGREED_CLAIM = "Both agree the cache key must include the provider slug"

# --- Brand-fold + claim-count discriminating fixture (for the scalar guard) ----
# The LIVE council page hydrates the winner verdict client-side via
# `{{ formatProviderLabel(lensPickProviderFor(seg)) }}` and lists agreed claims
# with a `<li v-for>`. A DISCRIMINATING winner = "codex": the brand-fold MUST
# paint "GPT" — a slug-passthrough regression (dropping formatProviderLabel)
# would paint the raw "codex", which `claude→Claude` (a mere capitalize) cannot
# expose. THREE agreed claims (a count distinct from the TWO disagreed) so a
# `v-for` re-bound to the wrong array, or a slice, paints a wrong <li> count.
_BRAND_CID = "council_brandcount_browsertest"
_BRAND_WINNER_SLUG = "codex"          # render-independent fixture constant
_BRAND_WINNER_LABEL = "GPT"           # what formatProviderLabel(codex) must paint
_BRAND_RUNNER_SLUG = "antigravity"
_BRAND_AGREED = [
    "Validate at the boundary, not in the core.",
    "Cache keyed on the provider slug to avoid cross-tenant leaks.",
    "Prefer an explicit schema over duck-typing the payload.",
]
_BRAND_DISAGREED = [
    {
        "claim": "Whether to memoize per request",
        "providers_for": ["codex"],
        "providers_against": ["antigravity"],
        "why_matters": "memory growth vs latency",
    },
    {
        "claim": "Sync vs async dispatch",
        "providers_for": ["antigravity"],
        "providers_against": ["codex"],
        "why_matters": "back-pressure handling",
    },
]


def _seed_painkiller() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider=p, model="m", output_text=f"Answer from {p}. " * 30)
        for p in ("claude", "codex")
    ]
    routing_label = CouncilRoutingLabel(
        winner="claude",
        runner_up="codex",
        confidence="high",
        task_type="design",
        agreed_claims=[_AGREED_CLAIM],
        disagreed_claims=[
            {
                "claim": _DISAGREED_CLAIM,
                "providers_for": ["claude"],
                "providers_against": ["codex"],
                "why_matters": "per-call caching leaks across tenants",
            }
        ],
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID,  # standalone: NO chain_root_id in metadata
            bundle_id=_CID,
            task_cluster_id="cluster_cache",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Cache in-process or per-call?"},
            member_results=members,
            synthesis_prompt="Review the two answers.",
            synthesis_output="In-process caching wins for this tenancy model.",
            routing_label=routing_label,
            created_at="2026-06-05T00:00:00+00:00",
        )
    )
    write_portal_html()  # writes vendor/ assets the page references
    write_live_council_page()


def _serve(tmp_path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_live_council_renders_painkiller_and_no_thread_404(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_painkiller()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                errs: list[str] = []
                bad_responses: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                # A 404 is a successful HTTP response with status 404 (not a
                # `requestfailed`), so capture by status. Record the URL so the
                # assertion message names the offending probe.
                page.on(
                    "response",
                    lambda r: bad_responses.append(r.url) if r.status >= 400 else None,
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2000)  # outcome-script hydration + petite-vue mount
                state = page.evaluate(
                    """(frag) => {
                      const strongs = Array.from(document.querySelectorAll('strong'))
                        .map(e => (e.textContent || '').trim());
                      const body = document.body.textContent || '';
                      return {
                        disagreed_header: strongs.includes('Disagreed claims'),
                        agreed_header: strongs.includes('Agreed claims'),
                        synth: !!document.querySelector('.synthesis-section .markdown-body'),
                        claim_rendered: body.includes(frag),
                      };
                    }""",
                    _DISAGREED_CLAIM[:40],
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errs, f"JS page errors on the live council page: {errs[:3]}"
    # A standalone council must NOT probe a thread manifest that can't exist.
    thread_404s = [u for u in bad_responses if "_thread" in u]
    assert not thread_404s, (
        "live council page 404'd on a thread-manifest probe for a STANDALONE "
        f"council (the _maybeOfferThreadLink regression): {thread_404s[:2]}"
    )
    # No other broken resource loads either (the page must be self-contained).
    assert not bad_responses, f"broken resource loads on the live council page: {bad_responses[:3]}"
    # The painkiller itself — the cross-provider disagreement — must render.
    assert state["disagreed_header"], "the 'Disagreed claims' section did not render (the differentiator)"
    assert state["claim_rendered"], "the disagreed-claim text did not render into the DOM"
    assert state["agreed_header"], "the 'Agreed claims' section did not render"
    assert state["synth"], "the chairman synthesis body did not render"


def test_live_council_renders_task_title_from_metadata(tmp_path, monkeypatch):
    """The council's QUESTION must render on the live review page — sourced from
    the outcome's metadata.task_text when no prompt_bundle exists. _seed_painkiller
    writes NO bundle (like ~every council whose bundle wasn't written or was
    pruned), so this exercises the metadata path specifically.

    This is the live-page sibling of the recent-councils rail fix
    (launchpad_data._load_recent_councils): the rail degraded to "[Council prompt
    unavailable]" for bundle-less councils until it learned to fall back to
    metadata.task_text. The live page's JS already does
    `taskText: outcome.task_text || metadata.task_text || ''`, but nothing
    asserted the title actually renders into the DOM — a binding break would
    silently drop the question from every council review. Pins it on the lead
    product surface."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_painkiller()  # metadata.task_text = "Cache in-process or per-call?", NO bundle written

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2000)  # outcome-script hydration + petite-vue mount
                body = page.evaluate("() => document.body.textContent || ''")
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert "Cache in-process or per-call?" in body, (
        "the council QUESTION (metadata.task_text) did not render on the live "
        "review page — the bundle-less metadata fallback silently dropped the "
        "title from a council review"
    )


_ACTIVE_TOK = "council_active_browsertest"


def _seed_active_council() -> None:
    """An IN-PROGRESS council: runner alive (this process), one member finished
    with streamed output, one running, one pending, synthesis pending — the live
    state a user watches right after launching."""
    import os

    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_status import (
        init_council_run_state,
        start_member_progress,
        update_member_progress,
    )
    from trinity_local.launchpad_page import write_portal_html

    init_council_run_state(
        _ACTIVE_TOK,
        task_text="Ship the rate limiter as a sidecar or in-process?",
        bundle_id=_ACTIVE_TOK,
        members=["claude", "codex", "antigravity"],
        council_id=_ACTIVE_TOK,
        runner_pid=os.getpid(),  # a LIVE pid → not coerced to 'failed' by the staleness guard
        member_models={
            "claude": "claude-opus-4-8",
            "codex": "gpt-5.5",
            "antigravity": "gemini-3.1-pro-preview",
        },
    )
    start_member_progress(_ACTIVE_TOK, "claude")
    update_member_progress(
        _ACTIVE_TOK, "claude",
        "In-process is simpler to deploy; a sidecar isolates blast radius. " * 4,
    )
    start_member_progress(_ACTIVE_TOK, "codex")  # codex running; antigravity stays pending
    write_portal_html()  # publishes vendor/ + the portal_pages/status dir
    write_live_council_page()


def test_live_council_active_state_streams_in_progress(tmp_path, monkeypatch):
    """The IN-PROGRESS council — the lead product's "watch it happen" moment —
    must render in a real browser.

    Every existing live-council browser test drives `?council_id=` (a COMPLETED
    outcome). The ACTIVE `?status_token=` path — `startPolling()` + the per-member
    running/done/pending UI streaming off `window.__TRINITY_COUNCIL_STATUS__` — had
    ZERO real-browser coverage; the poller tests (test_launchpad_status_poll_timeout)
    are string-presence on the source. A petite-vue binding break on a running
    member, or a council-status shape change, would blank the live view and pass
    CI green. (Found driving the live page 2026-06-06: `?council_id=` on a
    not-yet-completed council correctly errors 'Could not load council outcome' —
    the active view is `?status_token=`, and it was untested.) Pins: 'Council
    running' renders, the finished member's output streams in, a genuinely-running
    council never shows the failed/Could-not-load state, 0 page errors, no broken
    resource loads, no template leak."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_active_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                errs: list[str] = []
                bad_responses: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.on(
                    "response",
                    lambda r: bad_responses.append(r.url) if r.status >= 400 else None,
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={_ACTIVE_TOK}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2000)  # status-script load + petite-vue mount + a poll tick
                state = page.evaluate(
                    """() => {
                      const t = document.body.innerText || '';
                      return {
                        bodyLen: t.trim().length,
                        running: /council running|running|streaming/i.test(t),
                        memberOutput: t.includes('In-process is simpler'),
                        failedState: /could not load|council failed/i.test(t),
                        leak: /\\{\\{|\\}\\}/.test(t),
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errs, f"JS page errors on the active live council: {errs[:3]}"
    assert not bad_responses, f"broken resource loads on the active live council: {bad_responses[:3]}"
    assert state["bodyLen"] > 150, f"the active live council is ~blank (body={state['bodyLen']})"
    assert state["running"], "the in-progress council did not render a 'running' state"
    assert state["memberOutput"], (
        "the finished member's streamed output did not render into the live view "
        "(a binding break on a running-council member)"
    )
    assert not state["failedState"], (
        "a genuinely-running council (live pid) wrongly showed 'Could not load / "
        "council failed' — the active-status path is broken"
    )
    assert not state["leak"], "petite-vue template leak ({{ }}) on the active live council"


_CID_CODE = "council_codemobile_browsertest"
_CODE_ANSWER = (
    "Here's the fix:\n\n```python\n"
    "result = await client.dispatch_council_member_with_a_really_long_method_name("
    "provider=provider, task=task, timeout=600, retries=3)\n"
    "VERY_LONG_CONSTANT = 'https://example.com/some/really/long/path/that/keeps/"
    "going/and/going/and/going/forever/until/it/overflows'\n"
    "```\n\nbare token: "
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
)


def _seed_code_heavy() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider=p, model="m", output_text=_CODE_ANSWER)
        for p in ("claude", "codex")
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID_CODE, bundle_id=_CID_CODE, task_cluster_id="cluster_code",
            primary_provider="claude", winner_provider="claude",
            metadata={"task_text": "dispatch a council member with a timeout?"},
            member_results=members,
            synthesis_prompt="Review the two answers.",
            synthesis_output="In-process caching wins for this tenancy model.",
            routing_label=CouncilRoutingLabel(
                winner="claude", runner_up="codex", confidence="high", task_type="design",
                agreed_claims=["both agree the key must include the provider slug"],
                disagreed_claims=[],
            ),
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def test_live_council_code_heavy_no_horizontal_overflow_at_mobile(tmp_path, monkeypatch):
    """The LIVE council page (render_live_council_page) is the streaming "watch
    it" companion link people open on phones — sibling of the static unified
    review page. A member output with a wide code block / long unbreakable token
    forced horizontal page scroll at 375px: the `.provider-status-row` grid item
    (min-width:auto, like .answer-card) stretched to ~995px → 638px page overflow.

    The fix for the UNIFIED page (fcfbc473) landed in a SEPARATE render fn and did
    NOT reach this one — found 2026-06-07 by grepping the sibling surface. Fix:
    `.provider-status-row { min-width: 0 }` + `.provider-status-response.markdown-body
    pre/table { max-width:100%; overflow-x:auto; overflow-wrap:anywhere }`.

    The existing painkiller mobile coverage used trivial prose ("Answer from p. "
    * 30) so it never exercised this. Mutation: drop the row min-width:0 → the page
    overflows ~638px at 375px → this reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_code_heavy()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID_CODE}",
                    wait_until="load", timeout=15000,
                )
                page.wait_for_timeout(2200)  # outcome-script hydration + petite-vue mount
                geom = page.evaluate(
                    """() => {
                      let worst=null, max=window.innerWidth;
                      for (const el of document.querySelectorAll('*')) {
                        const r = el.getBoundingClientRect();
                        if (r.right > max && r.width > 0) {
                          max = r.right;
                          worst = el.tagName + '.' + String(el.className||'').split(' ')[0]
                                  + ' w=' + Math.round(r.width);
                        }
                      }
                      return {
                        overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                        scrollWidth: document.documentElement.scrollWidth,
                        n_rows: document.querySelectorAll('.provider-status-row').length,
                        worst,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert geom["n_rows"] == 2, (
        f"live council didn't hydrate the 2 member rows (got {geom['n_rows']}) — "
        f"the overflow check would be vacuous"
    )
    assert geom["overflow_x"] <= 4, (
        f"code-heavy live council overflows the 375px phone viewport by "
        f"{geom['overflow_x']}px (scrollWidth={geom['scrollWidth']}). The wide "
        f"member output must scroll within its row, not stretch the page. "
        f"Widest off-viewport element: {geom['worst']}"
    )


_CID_CLAIMS = "council_claimsmobile_browsertest"
_CLAIMS_LONG = "averylongunbreakableidentifier_dispatch_council_member_with_timeout_and_retries_that_never_wraps_on_a_phone"


def _seed_claims_council() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID_CLAIMS, bundle_id=_CID_CLAIMS, task_cluster_id="cluster_claims",
            primary_provider="claude", winner_provider="claude",
            metadata={"task_text": "cache per-call or in-process?"},
            member_results=[
                CouncilMemberResult(provider="claude", model="m", output_text="A"),
                CouncilMemberResult(provider="codex", model="m", output_text="B"),
            ],
            synthesis_prompt="Review.", synthesis_output="In-process wins for this tenancy model.",
            routing_label=CouncilRoutingLabel(
                winner="claude", runner_up="codex", confidence="high", task_type="design",
                agreed_claims=[f"They concur the config flag is {_CLAIMS_LONG}"],
                disagreed_claims=[{
                    "claim": f"Per-call vs in-process when {_CLAIMS_LONG} is set",
                    "providers_for": ["claude"], "providers_against": ["codex"],
                    "why_matters": f"leaks across tenants under load — {_CLAIMS_LONG}",
                }],
            ),
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def test_live_council_claims_long_token_no_horizontal_overflow_at_mobile(tmp_path, monkeypatch):
    """The live council page's structured agreed/disagreed-CLAIMS section (the
    comparative analysis, rendered into `.routing-label-grid`) is the sibling of
    the static review page's claims section. A claim / why_matters carrying a long
    unbreakable token (a dev identifier / URL) overflowed the 375px live companion
    by ~510px — the grid's implicit `auto` column sized to the token. Fixed:
    `.routing-label-grid { grid-template-columns: minmax(0,1fr); overflow-wrap:
    break-word }`. Sibling of the static-page fix 8190f702. Mutation: revert the
    grid column to auto → ~510px overflow → reds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_claims_council()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 375, "height": 1400}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_CID_CLAIMS}",
                    wait_until="load", timeout=15000,
                )
                page.wait_for_timeout(2200)  # outcome-script hydration + petite-vue mount
                geom = page.evaluate(
                    """() => {
                      const txt = document.body.innerText || '';
                      let worst=null, max=window.innerWidth;
                      for (const el of document.querySelectorAll('*')) {
                        const r = el.getBoundingClientRect();
                        if (r.right > max && r.width > 0) { max=r.right;
                          worst = el.tagName+'.'+String(el.className||'').split(' ')[0]+' w='+Math.round(r.width); }
                      }
                      return {
                        claimsShown: txt.includes('config flag is') || /Per-call vs in-process/.test(txt),
                        overflow_x: document.documentElement.scrollWidth - window.innerWidth,
                        scrollWidth: document.documentElement.scrollWidth,
                        worst,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert geom["claimsShown"], f"the claims section didn't render — overflow check would be vacuous: {geom}"
    assert geom["overflow_x"] <= 4, (
        f"live-council claims with a long token overflow the 375px viewport by "
        f"{geom['overflow_x']}px (scrollWidth={geom['scrollWidth']}) — the claim must "
        f"wrap inside .routing-label-grid. Widest element: {geom['worst']}"
    )


def _seed_brand_count() -> None:
    """A completed STANDALONE council whose winner brand-folds non-trivially
    (codex→GPT) and whose agreed-claims count (3) is distinct from disagreed (2)."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(provider=p, model="m", output_text=f"Answer from {p}. " * 30)
        for p in (_BRAND_WINNER_SLUG, _BRAND_RUNNER_SLUG)
    ]
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_BRAND_CID,
            bundle_id=_BRAND_CID,
            task_cluster_id="cluster_brandcount",
            primary_provider=_BRAND_WINNER_SLUG,
            winner_provider=_BRAND_WINNER_SLUG,
            metadata={"task_text": "Where should boundary validation live?"},
            member_results=members,
            synthesis_prompt="Review the two answers.",
            synthesis_output="Boundary validation wins for this layering.",
            routing_label=CouncilRoutingLabel(
                winner=_BRAND_WINNER_SLUG,
                runner_up=_BRAND_RUNNER_SLUG,
                confidence="high",
                task_type="design",
                agreed_claims=list(_BRAND_AGREED),
                disagreed_claims=list(_BRAND_DISAGREED),
            ),
            created_at="2026-06-05T00:00:00+00:00",
        )
    )
    write_portal_html()
    write_live_council_page()


def test_live_council_winner_brand_and_agreed_count_are_correct(tmp_path, monkeypatch):
    """The LIVE council page's chairman-verdict SCALARS — the winner attribution
    (brand-folded) and the agreed-claims COUNT — are hydrated CLIENT-SIDE by
    petite-vue (`{{ formatProviderLabel(lensPickProviderFor(seg)) }}` and a
    `<li v-for="c in ...agreed_claims">`), so an HTML-string test on the template
    cannot see them. Iter 222 surveyed these as "already value-guarded", but the
    only winner-brand assertion on the LIVE page used winner="claude" + a
    lowercased substring (passes for BOTH the brand "Claude" AND the raw slug
    "claude"), and NO browser test asserted the agreed <li> COUNT against the seed.

    FOUNDER SYMPTOM this bites: a regression that drops formatProviderLabel
    (slug-passthrough → the live verdict reads "codex — the answer you'd have
    picked." instead of "GPT"), or re-binds the agreed `v-for` to the wrong array
    / slices it (the verdict lists a wrong number of agreed claims), would paint a
    WRONG scalar while every existing live-council browser test stays green.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # BITE precondition (checked on the FIXTURE CONSTANTS, render-independently):
    # the seed is genuinely discriminating — winner brand-folds non-trivially and
    # the agreed/disagreed counts differ — so neither assertion can pass vacuously.
    assert _BRAND_WINNER_SLUG != _BRAND_WINNER_LABEL.lower(), (
        "fixture not discriminating: winner slug folds to the same token, a "
        "slug-passthrough regression couldn't be detected"
    )
    assert len(_BRAND_AGREED) != len(_BRAND_DISAGREED), (
        "fixture not discriminating: agreed/disagreed counts equal, a wrong-array "
        "v-for rebind couldn't be detected"
    )
    _seed_brand_count()

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?council_id={_BRAND_CID}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2500)  # outcome hydration + petite-vue mount
                res = page.evaluate(
                    """() => {
                      const verdict = document.querySelector('.winner-verdict');
                      const body = document.body.textContent || '';
                      // Locate the Agreed-claims <ul> by its preceding <strong>.
                      let agreed = null;
                      for (const s of document.querySelectorAll('strong')) {
                        if ((s.textContent || '').trim() === 'Agreed claims') {
                          const ul = s.parentElement && s.parentElement.querySelector('ul');
                          agreed = ul ? ul.querySelectorAll('li').length : 0;
                        }
                      }
                      return {
                        verdictText: verdict ? (verdict.textContent || '').trim() : null,
                        agreedCount: agreed,
                        braceLeak: body.includes('{{'),
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errs, f"JS page errors on the live council page: {errs[:3]}"
    # BITE precondition (A): the verdict element actually PAINTED — an un-mounted
    # petite-vue would leave `{{ }}` and a null verdict, making the scalar
    # assertions non-bite.
    assert not res["braceLeak"], "raw {{ }} leaked — petite-vue did not mount; scalar checks would be vacuous"
    assert res["verdictText"], "the .winner-verdict did not render — the scalar assertions would be vacuous"

    # THE BINDING ASSERTIONS (the sole things keyed on the client-hydrated scalars):
    # (1) the winner verdict brand-folds the slug to the MODEL BRAND.
    assert _BRAND_WINNER_LABEL in res["verdictText"], (
        f"the LIVE winner verdict did not brand-fold the winner: painted "
        f"{res['verdictText']!r} (expected it to name {_BRAND_WINNER_LABEL!r}). The "
        f"#275 raw-slug-vs-brand straggler on the client-hydrated path — a dropped "
        f"formatProviderLabel paints the raw slug {_BRAND_WINNER_SLUG!r}."
    )
    assert _BRAND_WINNER_SLUG not in res["verdictText"], (
        f"the LIVE winner verdict LEAKED the raw slug {_BRAND_WINNER_SLUG!r}: "
        f"{res['verdictText']!r} (must read the brand {_BRAND_WINNER_LABEL!r})."
    )
    # (2) the agreed-claims <li> COUNT equals the seeded agreed_claims length —
    # NOT the disagreed count, NOT a slice.
    assert res["agreedCount"] == len(_BRAND_AGREED), (
        f"the LIVE page painted {res['agreedCount']} agreed-claim rows, but the "
        f"council agreed on {len(_BRAND_AGREED)} — the verdict's agreed-claims count "
        f"disagrees with the council outcome (a v-for re-bound to disagreed_claims "
        f"would paint {len(_BRAND_DISAGREED)})."
    )
