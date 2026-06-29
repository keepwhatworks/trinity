"""Browser guard: the eval card's empty-state CTA must follow the real
prerequisite chain — `lens` → `eval-build` → `eval-run` — so a brand-new user is
never handed a command that hard-fails.

Found 2026-06-07 dogfooding a FRESH home (no `lens` run): the empty state led with
`trinity-local eval-build`, but `build_eval_set()` reads `preference_acts.jsonl`
and raises FileNotFoundError without it ("Run `trinity-local lens` to mine
rejections first"). So step 1 was a dead-end for exactly the user the card is for.
The 2026-06-07 persona audit flagged this as the consumer-CLI-dev first-run break.

The fix added a `rejections_available` signal to evalSummary and split the empty
state into three v-if branches:
  - no rejections in the ledger        → lead with `trinity-local lens` (MINES them)
  - rejections exist, no eval set yet   → `trinity-local eval-build` (now works)
  - eval set built                      → `trinity-local eval-run --target X`

This pins each branch in a real browser (petite-vue evaluates the v-if client-side,
so a static string check can't see it). Mutation-provable: revert the State-A lead
back to `eval-build` and the first assertion reds.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _eval_summary(*, rejections: bool, eval_set: bool) -> dict:
    return {
        "evalSummary": {
            "has_results": False,
            "target": None, "target_display": None, "model": None,
            "aggregate_score": None, "axes": [], "total_runs": 0,
            "items_completed": 0, "items_total": 0, "eval_id": None,
            "ran_at": None, "result_path": None,
            "rejections_available": rejections,
            "eval_set_available": eval_set,
        }
    }


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _write_prod_layout(html: str, serve_root: Path) -> str:
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return "portal_pages/launchpad.html"


# Read the eval-empty-state card's visible text + the chip labels.
_PROBE = """() => {
  const c = document.querySelector('.eval-empty-state-card');
  if (!c) return {found: false};
  const chips = [...c.querySelectorAll('button')].map(b => (b.innerText||'').trim().toLowerCase());
  return { found: true, chips, text: (c.innerText || '').toLowerCase() };
}"""


def _probe(tmp_path, page_data):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data=page_data, view="stats")
    rel = _write_prod_layout(html, tmp_path)
    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:160]))
                page.goto(f"http://127.0.0.1:{port}/{rel}", wait_until="networkidle", timeout=20000)
                page.wait_for_selector(".eval-empty-state-card", timeout=10000)
                result = page.evaluate(_PROBE)
                assert not errs, f"JS errors rendering the eval empty state: {errs[:3]}"
                return result
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_state_a_no_rejections_leads_with_lens_not_eval_build(tmp_path):
    """A FRESH home (no rejections in the ledger) must lead with `lens` — the
    command that MINES the rejections — and must NOT show `eval-build`, which would
    FileNotFoundError. This is the first-run dead-end the fix removed."""
    r = _probe(tmp_path, _eval_summary(rejections=False, eval_set=False))
    assert r["found"], "eval empty-state card did not render"
    chips = " | ".join(r["chips"])
    assert "trinity-local lens" in chips, (
        f"State A must lead with `trinity-local lens` (it mines the rejections "
        f"eval-build needs). Chips were: {r['chips']}"
    )
    assert "eval-build" not in chips, (
        f"State A must NOT offer `eval-build` — with no ledger it hard-fails "
        f"(FileNotFoundError → 'Run trinity-local lens'). Chips were: {r['chips']}"
    )


def test_state_b_rejections_present_offers_eval_build(tmp_path):
    """Once rejections exist (lens ran / imports landed) but no eval set is built,
    `eval-build` is the correct next step and now works."""
    r = _probe(tmp_path, _eval_summary(rejections=True, eval_set=False))
    chips = " | ".join(r["chips"])
    assert "trinity-local eval-build" in chips, (
        f"State B (rejections present, no eval set) must offer `eval-build`. "
        f"Chips were: {r['chips']}"
    )


def test_state_c_eval_set_built_offers_eval_run(tmp_path):
    """With an eval set on disk, the card drives `eval-run --target X`."""
    r = _probe(tmp_path, _eval_summary(rejections=True, eval_set=True))
    chips = " | ".join(r["chips"])
    assert "--target claude" in chips, (
        f"State C (eval set built) must offer `eval-run --target …`. Chips were: {r['chips']}"
    )
