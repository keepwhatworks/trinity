"""Browser guard: on the LIVE POLL path (``?status_token=``) a still-RUNNING or
FAILED member's ``reasoning_summary`` — painted into ``.provider-status-detail`` —
must WRAP a long unbreakable token, never blow the live council page past a 320px
phone viewport.

THE GAP this closes (found 2026-06-22 driving the genuine in-flight poll state with
a member whose ``reasoning_summary`` is a separator-free path/URL/error token): the
live council page (``render_live_council_page``) is the streaming "watch it"
companion people open on PHONES, and a member's ``reasoning_summary`` — produced by
``council_status._extract_reasoning_summary``, which TRUNCATES to 120 chars but does
NOT space-break — can be a token like
``ECONNREFUSED:/Users/.../python-3.10-aaaa…``. The live page's
``.provider-status-detail`` carried NO break rule (``overflow-wrap: normal;
min-width: auto``), so at 320px the token forced the document ~112px past the
viewport (393 → 39px, 375 → 57px); the whole answers-grid → provider-status-row →
MAIN chain horizontal-scrolled. A SHORT detail at the SAME 3-member grid fits with
ZERO overflow, so the token is the sole cause.

The LAUNCHPAD's twin ``.provider-status-detail`` already breaks this exact class
(``test_sidepanel_running_council_320_overflow_browser`` — "the 320px
running-council horizontal-scroll class") and the launchpad CSS already carries the
``overflow-wrap: anywhere; min-width: 0`` belt. The LIVE council page — the one
people actually watch on a phone — had its OWN copy of ``.provider-status-detail``
and was the asymmetric miss. The running-member binding test
(``test_live_council_running_member_status_browser``) drives the SAME poll path but
seeds members with no ``reasoning_summary`` (short "Working…"/"Queued." text) and
asserts BINDING, never overflow geometry — so a dropped break rule stays green there.

MUTATION-PROVEN to BITE (against ``src/trinity_local/council_review.py``, the SAME
source ``render_live_council_page`` renders from): strip ``overflow-wrap: anywhere``
+ ``min-width: 0`` from the live page's ``.provider-status-detail`` rule → the token
overflows the 320px viewport and this guard reds with the founder symptom, while the
two BITE preconditions (page mounted with 3 rows + the long token actually landed in
a ``.provider-status-detail``) stay GREEN first.

Serves an isolated, PII-free synthetic RUNNING council over http (file:// can't carry
``?status_token=`` reliably) and reads the rendered DOM geometry. Slow + browser
marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_MEMBERS = ["claude", "codex", "antigravity"]

# The DISCRIMINATING shape: a running member AND a failed member each carry a
# reasoning_summary that is a single 120-char unbreakable token (the real shape
# _extract_reasoning_summary emits when the member's text/error has no separator in
# its first 120 chars — a path/URL/stack token). Top-level status 'running'.
_LONG_TOKEN = (
    "ECONNREFUSED:/Users/founder/.trinity/venv/bin/python-3.10-"
    + "a" * 40
    + "-END"
)


def _running_status(token: str) -> dict:
    return {
        "status": "running",
        "status_token": token,
        "task_text": "Compare these approaches",
        "memberOrder": _MEMBERS,
        "members": {
            "claude": {"status": "running", "model": "claude-opus-4-8", "reasoning_summary": _LONG_TOKEN},
            "codex": {"status": "failed", "model": "gpt-5.5", "reasoning_summary": _LONG_TOKEN},
            "antigravity": {"status": "pending", "model": "gemini-3.1-pro"},
        },
        "synthesis": {"status": "pending"},
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed(tmp_path, monkeypatch, status):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())
    # The live page loads ../portal_pages/vendor/petite-vue.iife.js relative to
    # review_pages/, so the IIFE must ALSO sit under portal_pages/vendor/.
    _vendor.publish_vendor_files(portal_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    token = status["status_token"]
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")


def _drive(port, token, viewport_width):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": viewport_width, "height": 1200}
            ).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(
                f"http://127.0.0.1:{port}/review_pages/live_council.html"
                f"?status_token={token}&members=claude,codex,antigravity"
            )
            page.wait_for_timeout(2600)  # let the first poll land + mount
            return page.evaluate(
                r"""(token) => {
                  const de = document.documentElement;
                  const details = Array.from(
                    document.querySelectorAll('.provider-status-detail')
                  ).map(el => {
                    const r = el.getBoundingClientRect();
                    return { text: (el.textContent || '').trim(), right: Math.round(r.right) };
                  });
                  // widest visible element right edge (the offender, if any)
                  let maxRight = 0, worst = null;
                  document.querySelectorAll('*').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.right > maxRight) {
                      maxRight = Math.round(r.right);
                      worst = (el.tagName + '.' + (el.className || '')).slice(0, 60);
                    }
                  });
                  return {
                    clientW: de.clientWidth,
                    scrollW: de.scrollWidth,
                    rowCount: document.querySelectorAll('.provider-status-row').length,
                    braceLeak: document.body.innerText.includes('{{'),
                    details, maxRight, worst,
                  };
                }""",
                token,
            ), errs
        finally:
            browser.close()


@pytest.mark.parametrize("viewport_width", [320, 375, 393])
def test_running_member_long_detail_token_does_not_overflow_live_page(
    tmp_path, monkeypatch, viewport_width
):
    pytest.importorskip("playwright.sync_api")
    token = "tok_detail_overflow"
    status = _running_status(token)

    # BITE precondition B (render-independent): the fixture carries the long
    # unbreakable token on a running AND a failed member, checked on the fixture
    # CONSTANTS so a real CSS regression reds on the geometry assertion below,
    # never on a misleading "seed" message.
    assert status["members"]["claude"]["reasoning_summary"] == _LONG_TOKEN
    assert status["members"]["codex"]["reasoning_summary"] == _LONG_TOKEN
    assert " " not in _LONG_TOKEN and len(_LONG_TOKEN) > 80, (
        "the discriminating token must be a long SPACE-FREE string (the only shape "
        "that needs overflow-wrap to break)"
    )

    _seed(tmp_path, monkeypatch, status)
    httpd, port = _serve(tmp_path)
    try:
        geo, errs = _drive(port, token, viewport_width)
    finally:
        httpd.shutdown()

    # BITE precondition A: the page actually MOUNTED (no JS error, no raw mustache
    # leak, all 3 member rows rendered, and the long token actually LANDED in a
    # .provider-status-detail). Otherwise the overflow assertion is vacuous.
    assert not errs, f"live RUNNING page raised JS pageerrors at {viewport_width}px: {errs[:3]}"
    assert not geo["braceLeak"], (
        f"raw petite-vue '{{{{ }}}}' leaked at {viewport_width}px — it never mounted, "
        "so the detail below is unbound mustache text, not a real binding"
    )
    assert geo["rowCount"] == 3, (
        f"expected 3 member rows at {viewport_width}px, got {geo['rowCount']}"
    )
    landed = [d for d in geo["details"] if _LONG_TOKEN in d["text"]]
    assert len(landed) >= 2, (
        "the long reasoning_summary token did not land in the running/failed "
        f"member .provider-status-detail cells (got {[d['text'][:40] for d in geo['details']]}) "
        "— fix the fixture before trusting the overflow assertion"
    )

    # THE GEOMETRY ASSERTION (founder symptom): a still-running / failed member's
    # 120-char unbreakable reasoning_summary forced the live council page ~112px
    # past a 320px phone viewport (the .provider-status-detail had overflow-wrap:
    # normal; min-width: auto, while the launchpad twin already breaks it).
    assert geo["scrollW"] <= geo["clientW"] + 1, (
        f"the LIVE council page horizontally OVERFLOWS at {viewport_width}px "
        f"(scrollWidth {geo['scrollW']} > clientWidth {geo['clientW']}, widest "
        f"element {geo['worst']!r}) — a running/failed member's unbreakable "
        "reasoning_summary token stretched .provider-status-detail past the viewport. "
        "The detail must overflow-wrap: anywhere + min-width: 0 like the launchpad twin "
        "(the streaming 'watch it' companion is opened on phones)."
    )
    for d in landed:
        assert d["right"] <= geo["clientW"] + 1, (
            f"a running/failed member detail token spills past the {viewport_width}px "
            f"viewport (right {d['right']} > clientWidth {geo['clientW']})."
        )
