"""Browser guard: the LIVE council page must NOT overflow horizontally at the
320px extreme breakpoint when corpus text (the question / synthesis) carries a long
UNBREAKABLE token (a URL / file path / hash / dev identifier).

The Iter-38 mobile-grid sweep only covered the LAUNCHPAD; the live council page was
never driven at 320px with a long-token question. Driving a 3-member COMPLETED
council (the widest ``answers-grid-three``) at 320px (found 2026-06-18) surfaced a
real page-level overflow:

* The council QUESTION ``<h1>`` (``threadTaskTextDisplay``) had only
  ``text-wrap: balance`` (SHARED_CSS heading rule). ``text-wrap: balance`` only
  redistributes the break points a line ALREADY has — it does NOT break a long
  unbreakable token. A question carrying a URL / path / hash stretched the
  ``<h1>`` to ~1491px inside a 254px box and blew the whole page to
  ``documentElement.scrollWidth == 1524`` on a 320px phone (vs the 320px viewport).
* The synthesis section (a plain ``.markdown-body``, NOT a
  ``.provider-status-response``) had no base ``.markdown-body`` wrap on the live
  page, so chairman prose with a long token overflowed too (~618px).

This is the same long-token-overflow CLASS as the 2026-06-07 routing-label-grid fix
(8190f702) — that fix only covered ``.routing-label-grid``; the question heading and
the synthesis prose were unfixed siblings. The class-level fix adds
``overflow-wrap: break-word`` to the SHARED_CSS ``h1, h2, h3`` rule (covers every
heading on every surface) + a base ``.markdown-body`` wrap on the live council page
(mirroring the static review page).

Loads the real live page over HTTP with ``?council_id=`` (file:// can't carry the
query + the outcome-script fetch), seeds a 3-member completed council with a long
unbreakable token in the question + agreed/disagreed claims + synthesis, sets a 320px
viewport, and asserts ``documentElement.scrollWidth <= clientWidth`` (no page-level
horizontal overflow) while the widest grid actually rendered (3 rows — the false-pass
guard). Synthetic data only; no PII. Slow + browser marked.
"""
from __future__ import annotations

import functools
import http.server
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_CID = "council_320px_token_browsertest"
# A long UNBREAKABLE token — no spaces / break opportunities. This is exactly the
# kind of thing a real council question carries (a repo URL, an error path, a
# commit hash, a dev identifier). text-wrap:balance can't break it.
_LONG_TOKEN = (
    "supercalifragilisticexpialidociousREALLYlongUNBREAKABLEtoken"
    "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZnoSpacesNoBreakpoints"
)


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_token_council() -> None:
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_page import write_portal_html

    members = [
        CouncilMemberResult(
            provider="claude",
            model="claude-opus-4-8",
            output_text="Claude reframes and proposes a cache key " + _LONG_TOKEN,
        ),
        CouncilMemberResult(
            provider="codex", model="gpt-5.5", output_text="Codex enumerates."
        ),
        # A long MODEL name too — the member header label is another at-risk row.
        CouncilMemberResult(
            provider="antigravity",
            model="gemini-3.1-pro-preview-experimental-extended-context-1m-build",
            output_text="Gemini cross-checks and flags a tenancy concern.",
        ),
    ]
    routing_label = CouncilRoutingLabel(
        winner="antigravity",
        runner_up="claude",
        confidence="high",
        task_type="design",
        agreed_claims=["Namespace the cache key per " + _LONG_TOKEN],
        disagreed_claims=[
            {
                "claim": "Per-call vs in-process caching " + _LONG_TOKEN,
                "providers_for": ["claude"],
                "providers_against": ["codex", "antigravity"],
                "why_matters": "A shared in-process cache leaks when " + _LONG_TOKEN,
            }
        ],
        routing_lesson="prefer_per_call_for_multi_tenant_isolation",
    )
    save_council_outcome(
        CouncilOutcome(
            council_run_id=_CID,
            bundle_id=_CID,
            task_cluster_id="cluster_320px_token",
            primary_provider="claude",
            winner_provider="antigravity",
            # The QUESTION carries the long token — this is what stretched the <h1>.
            metadata={"task_text": "Fix the caching crash at " + _LONG_TOKEN},
            member_results=members,
            synthesis_prompt="Review.",
            synthesis_output="# Synthesis\n\nNamespace the key. " + _LONG_TOKEN,
            routing_label=routing_label,
            created_at="2026-06-07T00:00:00+00:00",
        )
    )
    write_portal_html()  # vendor assets the page references
    write_live_council_page()


@pytest.mark.parametrize("bp", [320, 375])
def test_live_council_no_horizontal_overflow_at_320px(tmp_path, monkeypatch, bp):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_token_council()

    httpd, port = _serve(tmp_path)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.set_viewport_size({"width": bp, "height": 720})
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?council_id={_CID}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2200)  # outcome-script hydration + petite-vue mount
                geom = page.evaluate(
                    """() => {
                        const de = document.documentElement;
                        return {
                            scrollW: de.scrollWidth,
                            clientW: de.clientWidth,
                            rows: document.querySelectorAll('.provider-status-row').length,
                            braceLeak: /\\{\\{|\\}\\}/.test(document.body.innerText),
                            // the worst content-overflow offender (an element whose
                            // CONTENT overflows its in-viewport box) — names the culprit
                            // when this reds, so a regression points at the element.
                            worst: (() => {
                                const vw = window.innerWidth;
                                let w = {scrollW: 0, sel: ''};
                                for (const el of document.querySelectorAll('body *')) {
                                    const cs = getComputedStyle(el);
                                    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                                    if (el.scrollWidth > vw + 1 && el.clientWidth <= vw + 1
                                        && el.scrollWidth > w.scrollW) {
                                        w = {scrollW: el.scrollWidth,
                                             sel: el.tagName.toLowerCase() + '.' +
                                                  String(el.className || '').slice(0, 30)};
                                    }
                                }
                                return w;
                            })(),
                        };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council threw at {bp}px: {errors}"
    # False-pass guard: the widest 3-member grid must actually render, else the
    # overflow assertion would pass on an empty page.
    assert geom["rows"] == 3, (
        f"live council didn't render the 3-member grid at {bp}px (rows={geom['rows']}) "
        "— the overflow assertion would be a false pass"
    )
    assert not geom["braceLeak"], f"raw {{{{ }}}} leaked at {bp}px: petite-vue didn't mount"
    # THE class guard: a long unbreakable token in the question / synthesis / claims
    # must wrap, never stretch the page. Pre-fix, the <h1> question blew a 320px phone
    # to documentElement.scrollWidth == 1524.
    assert geom["scrollW"] <= geom["clientW"] + 1, (
        f"live council OVERFLOWS horizontally at {bp}px: "
        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
        f"(worst content offender: {geom['worst']['sel']} @ {geom['worst']['scrollW']}px) "
        "— a council question/synthesis with a long unbreakable token (URL/path/hash) "
        "stretches the phone viewport; headings + .markdown-body need overflow-wrap:break-word"
    )


# A separator-free runner error — the exact shape status.error (= str(exc) /
# why[:200] in council_runner.py) can carry: a quota URL, an exception path, a
# base64/hash with no break opportunity. text-wrap/normal wrap can't break it.
_LONG_ERR = "ConnectionError: dispatch failed for " + ("x" * 180)


def _seed_failed_status(token: str) -> None:
    """A FAILED council status the live page polls via ?status_token= → seg.errorText."""
    from trinity_local.council_review import write_live_council_page
    from trinity_local.council_status import write_council_status
    from trinity_local.launchpad_page import write_portal_html

    write_portal_html()  # vendor assets the page references
    write_live_council_page()
    write_council_status(
        token,
        status="failed",
        task_text="Cache in-process or per-call?",
        error=_LONG_ERR,
    )


@pytest.mark.parametrize("bp", [320, 375])
def test_live_council_failed_long_error_does_not_overflow_at_320px(tmp_path, monkeypatch, bp):
    """The FAILED-council ``seg.errorText`` line (``<p class="status-error">``) renders the
    raw runner error (status.error). A separator-free token in it must WRAP inside the
    launch-status card, never force the card/document wider than the phone viewport.

    Asymmetric sibling of the COMPLETED-council test above: that drives the ``?council_id=``
    path (question heading + synthesis); this drives the ``?status_token=`` FAILED-poll path
    where ``.status-error`` had only ``color`` — no ``overflow-wrap``. Found Iter 205: a
    180-char unbreakable error streamed off-screen right and horizontal-scrolled the 320px
    live companion to documentElement.scrollWidth ~1535px (the popup/launchpad twin class).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    token = "chain_failtest_browser205"
    _seed_failed_status(token)

    httpd, port = _serve(tmp_path)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.set_viewport_size({"width": bp, "height": 720})
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?status_token={token}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(2200)  # status-script poll + petite-vue mount
                geom = page.evaluate(
                    """() => {
                        const de = document.documentElement;
                        const errP = document.querySelector('p.status-error');
                        return {
                            scrollW: de.scrollWidth,
                            clientW: de.clientWidth,
                            // bite-not-vacuous precondition: the long error actually
                            // reached the .status-error paragraph the page failed on.
                            errText: errP ? errP.textContent : null,
                            errScrollW: errP ? errP.scrollWidth : null,
                            errClientW: errP ? errP.clientWidth : null,
                            worst: (() => {
                                const vw = window.innerWidth;
                                let w = {scrollW: 0, sel: ''};
                                for (const el of document.querySelectorAll('body *')) {
                                    const cs = getComputedStyle(el);
                                    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                                    if (el.scrollWidth > vw + 1 && el.clientWidth <= vw + 1
                                        && el.scrollWidth > w.scrollW) {
                                        w = {scrollW: el.scrollWidth,
                                             sel: el.tagName.toLowerCase() + '.' +
                                                  String(el.className || '').slice(0, 30)};
                                    }
                                }
                                return w;
                            })(),
                        };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council threw at {bp}px (failed state): {errors}"
    # Bite-not-vacuous: the long runner error must have rendered into .status-error,
    # else the overflow assertion would pass on a page that never showed the error.
    assert geom["errText"] is not None and _LONG_ERR in geom["errText"], (
        f"the long runner error never reached the .status-error paragraph at {bp}px "
        f"(errText={geom['errText']!r}) — the FAILED poll path didn't render; "
        "the overflow assertion would be a false pass"
    )
    # THE class guard: the raw runner error must wrap, never stretch the page. Pre-fix,
    # the unbreakable token blew the 320px live companion to documentElement.scrollWidth
    # ~1535px while the launch-status card streamed the error off-screen right.
    assert geom["scrollW"] <= geom["clientW"] + 1, (
        f"live council FAILED-state OVERFLOWS horizontally at {bp}px: "
        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
        f"(worst content offender: {geom['worst']['sel']} @ {geom['worst']['scrollW']}px) "
        "— the 'Council failed' error line (seg.errorText = raw status.error) carries a "
        "long unbreakable token (URL/path/hash); .status-error needs overflow-wrap:anywhere"
    )
    # The error paragraph itself must wrap WITHIN its box, not grow to fit the token.
    assert geom["errScrollW"] <= geom["errClientW"] + 1, (
        f".status-error paragraph grew to fit the unbreakable token at {bp}px "
        f"(scrollWidth={geom['errScrollW']} > clientWidth={geom['errClientW']}) — it must wrap"
    )


# A task LONGER than 240 chars carrying a long unbreakable token. A real user
# pastes a prompt like this — a repo URL / error path / regex with no spaces. The
# >240 length forces the collapsible <details> task header instead of the <h1>;
# the <=600 length keeps it :open so the expanded pre-wrap <p> renders the token
# live. (The <summary> is now a fixed "Your question" label — it no longer slices
# the task, so the token reaches only the BODY <p>, which is where it must wrap.)
_LONG_TASK_TOKEN = (
    "https://github.com/keepwhatworks/trinity/blob/main/src/trinity_local/"
    "council_review.py#L1466-" + ("a" * 110)
)
_LONG_TASK = (
    "Please review this file and tell me " + _LONG_TASK_TOKEN + " and also explain "
    "whether the long-token break rule reaches the collapsible task header on the "
    "live council page when a user pastes a giant URL with no spaces in it at all."
)


@pytest.mark.parametrize("bp", [320, 375])
def test_live_council_long_pasted_task_details_no_overflow_at_320px(tmp_path, monkeypatch, bp):
    """A pasted task >240 chars renders the COLLAPSIBLE ``.task-collapsible``
    ``<details>`` header (``threadTaskTextDisplay.length > 240``), not the ``<h1>``.

    Unlike the ``<h1>`` (which inherits the design system's
    ``h1{overflow-wrap:break-word}``), the ``<summary>`` slice and the expanded
    ``<p style="white-space: pre-wrap">`` body had NO break rule — so a long
    unbreakable token in a pasted prompt blew the whole page ~490px wider than a
    375px viewport (the long-token horizontal-overflow class; the
    ``.task-collapsible`` disclosure widget was the one the iter-287/288 prose-box
    sweep missed). Drives the ``?task=`` URL-param path (the real Launch flow puts
    the user's pasted prompt in ``task=``); asserts the details rendered, the token
    reached the summary, and no page-level overflow.
    """
    pytest.importorskip("playwright.sync_api")
    from urllib.parse import quote

    from playwright.sync_api import sync_playwright

    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    write_portal_html()  # vendor assets the page references
    write_live_council_page()

    httpd, port = _serve(tmp_path)
    errors: list[str] = []
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                # Stub the FULL dispatcher interface (dispatch/probe/onStateChange —
                # the page calls probe(true)+onStateChange on init) so no real
                # council/host is ever reached and the page mounts clean.
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    " dispatch: function(o){ if(o&&o.onResult) o.onResult({ok:true}); },"
                    " probe: function(){ return Promise.resolve('ready'); },"
                    " onStateChange: function(){ return function(){}; } };"
                )
                page.set_viewport_size({"width": bp, "height": 800})
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?task={quote(_LONG_TASK)}",
                    wait_until="load",
                    timeout=15000,
                )
                page.wait_for_timeout(1200)  # petite-vue mount
                geom = page.evaluate(
                    """() => {
                        const de = document.documentElement;
                        const det = document.querySelector('.task-collapsible');
                        const summary = det ? det.querySelector('summary') : null;
                        const body = det ? det.querySelector('p') : null;
                        return {
                            scrollW: de.scrollWidth,
                            clientW: de.clientWidth,
                            hasDetails: !!det,
                            summaryText: summary ? summary.textContent : null,
                            summaryScrollW: summary ? summary.scrollWidth : null,
                            summaryClientW: summary ? summary.clientWidth : null,
                            bodyText: body ? body.textContent : null,
                            bodyScrollW: body ? body.scrollWidth : null,
                            bodyClientW: body ? body.clientWidth : null,
                            braceLeak: /\\{\\{|\\}\\}/.test(document.body.innerText),
                            worst: (() => {
                                const vw = window.innerWidth;
                                let w = {scrollW: 0, sel: ''};
                                for (const el of document.querySelectorAll('body *')) {
                                    const cs = getComputedStyle(el);
                                    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') continue;
                                    if (el.scrollWidth > vw + 1 && el.clientWidth <= vw + 1
                                        && el.scrollWidth > w.scrollW) {
                                        w = {scrollW: el.scrollWidth,
                                             sel: el.tagName.toLowerCase() + '.' +
                                                  String(el.className || '').slice(0, 30)};
                                    }
                                }
                                return w;
                            })(),
                        };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert not errors, f"live council threw at {bp}px (long pasted task): {errors}"
    assert not geom["braceLeak"], f"raw {{{{ }}}} leaked at {bp}px: petite-vue didn't mount"
    # Bite precondition #1: the COLLAPSIBLE details must actually render — else this
    # drives the <h1> path (already covered) and the overflow assertion is a false pass.
    assert geom["hasDetails"], (
        f"the .task-collapsible <details> didn't render at {bp}px (task <=240 chars?) "
        "— this test must drive the collapsible header, not the <h1>"
    )
    # Bite precondition #2: the long unbreakable token must have reached the expanded
    # <p> BODY — else the overflow assertion would pass on a token-free header. (The
    # summary is now the fixed "Your question" label; the token lives in the body.)
    assert geom["bodyText"] and "aaaaaaaaaa" in geom["bodyText"], (
        f"the long unbreakable token never reached the .task-collapsible body <p> at {bp}px "
        f"(bodyText={geom['bodyText']!r}) — the overflow assertion would be a false pass"
    )
    # Bite precondition #3: the summary must be the fixed disclosure LABEL, not a slice
    # of the task — its slice form printed the user's question twice (the duplication bug
    # this fix removed). If a regression re-slices it, this assertion names the symptom.
    assert geom["summaryText"] and geom["summaryText"].strip() == "Your question", (
        f"the .task-collapsible <summary> at {bp}px is {geom['summaryText']!r}, not the "
        "fixed 'Your question' label — re-slicing it to threadTaskTextDisplay.slice(0,200) "
        "reprints the first ~200 chars of the question TWICE (summary teaser + full body)"
    )
    # THE class guard: a long unbreakable token in a pasted task must WRAP inside the
    # collapsible body, never stretch the page. Pre-fix, the pre-wrap <p> body blew a
    # 375px phone to documentElement.scrollWidth ~864px.
    assert geom["scrollW"] <= geom["clientW"] + 1, (
        f"live council OVERFLOWS horizontally at {bp}px on a long pasted task: "
        f"documentElement.scrollWidth={geom['scrollW']} > clientWidth={geom['clientW']} "
        f"(worst content offender: {geom['worst']['sel']} @ {geom['worst']['scrollW']}px) "
        "— a pasted prompt with a long unbreakable token (URL/path/regex) in the "
        ".task-collapsible pre-wrap body stretches the phone viewport; it needs "
        "overflow-wrap:anywhere"
    )
    # The expanded body must wrap WITHIN its box, not grow to fit the token.
    assert geom["bodyScrollW"] <= geom["bodyClientW"] + 1, (
        f".task-collapsible pre-wrap body grew to fit the unbreakable token at {bp}px "
        f"(scrollWidth={geom['bodyScrollW']} > clientWidth={geom['bodyClientW']}) — it must wrap"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
