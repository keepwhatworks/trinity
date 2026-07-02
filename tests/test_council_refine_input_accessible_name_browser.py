"""UX sweep Iter 97 — the chain "refine" ``<textarea>`` must carry an ACCESSIBLE
NAME on BOTH council surfaces (the LIVE poll page and the STATIC review page).

THE DEFECT (found 2026-06-18 driving both pages with the W3C accessible-name probe
in the a11y sweep): the ``.chain-refine-input`` textarea — the PRIMARY "add a new
directive / refine the council" control on both council pages — had NO ``aria-label``
and NO ``<label for>``; it carried ONLY a ``placeholder``. Under the W3C accessible
name computation a ``placeholder`` is NOT a name source (ARIA explicitly excludes it,
and WCAG 4.1.2 Name/Role/Value is not satisfied by placeholder alone), so a screen
reader announced the control as just "edit text, blank" — the user had no way to know
what the field does. The launchpad's task textarea is correctly named via
``<label for="council-prompt">Task</label>``; the same discipline was simply never
applied to the refine input.

THE FIX: both textareas (``render_live_council_page`` + ``render_unified_council_page``)
gained ``aria-label="Refine directive for the next council round"``.

This guard drives the REAL rendered pages in a browser and computes the accessible
name of ``.chain-refine-input`` using the W3C precedence (aria-label →
aria-labelledby → text → title → ``<label for>``; placeholder DELIBERATELY excluded,
matching real AT behavior) and asserts it is NON-EMPTY on BOTH surfaces. Mutation-
proven: stripping the ``aria-label`` turns the computed name empty and the guard red.

Slow/browser-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


# W3C-ish accessible-name computation. CRITICAL: placeholder is NOT a name source —
# this is the whole point of the test (a placeholder-only field is unnamed to AT).
_ACC_NAME_JS = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return { found: false };
  function accName(e){
    const al = (e.getAttribute('aria-label')||'').trim();
    if (al) return al;
    const lb = e.getAttribute('aria-labelledby');
    if (lb){
      const parts = lb.split(/\s+/).map(id => {
        const t = document.getElementById(id);
        return t ? (t.textContent||'').trim() : '';
      }).filter(Boolean);
      if (parts.join(' ').trim()) return parts.join(' ').trim();
    }
    const txt = (e.textContent||'').trim();
    if (txt) return txt;
    const ti = (e.getAttribute('title')||'').trim();
    if (ti) return ti;
    if (e.id){
      const lab = document.querySelector('label[for="'+CSS.escape(e.id)+'"]');
      if (lab && (lab.textContent||'').trim()) return (lab.textContent||'').trim();
    }
    // placeholder is intentionally NOT consulted.
    return '';
  }
  return { found: true, name: accName(el) };
}
"""


def _serve(directory):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _live_status(token):
    """A completed single-round council on the poll path — enough to mount the
    page and render the chain-refine row (which shows when !chainBusy)."""
    return {
        "status": "completed",
        "status_token": token,
        "task_text": "Should I ship the refine input?",
        "council_id": "c_refine_name",
        "memberOrder": ["claude", "antigravity"],
        "members": {
            "claude": {"status": "done", "model": "claude-opus-4-8",
                       "response_text": "Claude's answer.",
                       "response_html": "<p>Claude's answer.</p>"},
            "antigravity": {"status": "done", "model": "gemini-3.1-pro",
                            "response_text": "Gemini's answer.",
                            "response_html": "<p>Gemini's answer.</p>"},
        },
        "synthesis": {
            "status": "done", "response_text": "Synthesis.",
            "response_html": "<p>Synthesis.</p>",
            "routing_label": {"winner": "claude", "confidence": "high",
                              "agreed_claims": ["Both agree on X"],
                              "disagreed_claims": []},
        },
        "metadata": {"chairman_provider": "claude", "council_id": "c_refine_name",
                     "members": ["claude", "antigravity"]},
    }


def _seed_live(tmp_path, monkeypatch, token):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())

    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    status = _live_status(token)
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")


def _name_of_refine_input(port, url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(url)
            page.wait_for_timeout(2600)
            res = page.evaluate(_ACC_NAME_JS, ".chain-refine-input")
            return res, errs
        finally:
            browser.close()


def test_live_council_refine_input_has_accessible_name(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    token = "tok_refine_name"
    _seed_live(tmp_path, monkeypatch, token)
    httpd, port = _serve(tmp_path)
    try:
        res, _errs = _name_of_refine_input(
            port,
            f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={token}",
        )
    finally:
        httpd.shutdown()

    assert res.get("found"), "the LIVE council page rendered no .chain-refine-input textarea"
    assert res.get("name", "").strip(), (
        "the LIVE council page's refine <textarea> has an EMPTY accessible name — it "
        "carries only a placeholder (which AT/WCAG 4.1.2 do NOT count as a name), so a "
        "screen reader announces this PRIMARY 'add a new directive' control as just "
        "'edit text, blank'. It needs an aria-label or a <label for>."
    )


# (test_static_review_refine_input_has_accessible_name removed with
# render_unified_council_page, #311/#8 — the live-page test above guards the
# refine textarea's accessible name on the page a teammate actually opens.)
