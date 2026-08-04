"""Browser guards for the 2026-08-03 launchpad recap pass.

1. The trust card — the product's one behaviourally MEASURED claim ("which
   model you side with when the labs split") — must be VISIBLE on the simple
   HOME view, not only behind the /stats link. Before this pass it was tagged
   `stats-card`, so the home page carried zero trace of the hero claim; the
   card is now untagged (both views), still self-hiding via v-if="trustData"
   on a cold install. CSS visibility only resolves in a real browser, so a
   re-tag regression passes every string-presence unit test — hence browser.

(A second guard for a cross-eyebrow empty-state dedup lived here for an hour
and was removed with the reverted edit it enforced: Iter 188 ratified the
routing empty-state as the cold-state owner, and the two cold nudges describe
different mechanisms. See the template comment at the routing empty-state.)
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _build_page_data(tmp_home: Path, monkeypatch) -> dict:
    monkeypatch.setenv("TRINITY_HOME", str(tmp_home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    from trinity_local.launchpad_data import build_page_data

    return build_page_data(live_review_path=None, recent_councils=[])


def _write_prod_layout(html: str, serve_root: Path, name: str) -> str:
    from trinity_local.vendor import publish_vendor_files

    pp = serve_root / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / name).write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return f"portal_pages/{name}"


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


_PROBE = """() => {
  const visible = (el) => {
    if (!el) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const byH2 = (re) => [...document.querySelectorAll('section h2')]
    .filter(h => re.test(h.textContent))
    .map(h => h.closest('section'))[0] || null;
  const trust = byH2(/side with when the labs split/i);
  const consolidateNudge = byH2(/then\\s|consolidate/i);
  const body = document.body.textContent || '';
  return {
    trustInDom: !!trust,
    trustVisible: visible(trust),
    consolidateNudgeVisible: visible(consolidateNudge),
    routingNudgeInDom: /Run a few councils to learn which model works best/.test(body),
    leak: /\\{\\{|\\}\\}/.test(document.body.innerText || ''),
  };
}"""

_TRUST_FIXTURE = {
    "trustworthy": True,
    "resolved": 128,
    "records": [
        {"lab": "Claude Opus 4.8", "win_pct": 68, "record": "28-13",
         "ci_excludes_half": True},
        {"lab": "GPT-5.5", "win_pct": 49, "record": "24-25",
         "ci_excludes_half": False},
    ],
}


def _probe_view(monkeypatch, tmp_path: Path, view: str, *, with_trust: bool) -> dict:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_template import render_launchpad_html

    page_data = _build_page_data(tmp_path / "home", monkeypatch)
    if with_trust:
        # Inject at the page_data boundary: seeding a real ledger needs a
        # council corpus; the card's contract with the template is this dict.
        page_data["trustData"] = dict(_TRUST_FIXTURE)
    html = render_launchpad_html(page_data=page_data, view=view)
    serve_root = tmp_path / f"serve-{view}-{with_trust}"
    rel = _write_prod_layout(html, serve_root, f"{view}.html")
    httpd, port = _serve(serve_root)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 1100, "height": 1400}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stubbed'});"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/{rel}",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_selector(
                    "section.stats-card", state="attached", timeout=10000
                )
                page.wait_for_timeout(300)
                state = page.evaluate(_PROBE)
                state["_errs"] = errs
            finally:
                browser.close()
    finally:
        httpd.shutdown()
    return state


def test_trust_card_visible_on_home(tmp_path, monkeypatch):
    s = _probe_view(monkeypatch, tmp_path, "home", with_trust=True)
    assert not s["_errs"], f"JS errors on home: {s['_errs'][:4]}"
    assert s["trustInDom"], "trust card missing from the home DOM despite trustData"
    assert s["trustVisible"], (
        "the trust card (the measured hero claim) must be VISIBLE on the home "
        "view — a stats-card re-tag would hide the product's one proof from the "
        "page everyone lands on"
    )
    assert not s["leak"], "petite-vue template leak on home"


def test_trust_card_still_visible_on_stats(tmp_path, monkeypatch):
    s = _probe_view(monkeypatch, tmp_path, "stats", with_trust=True)
    assert not s["_errs"], f"JS errors on stats: {s['_errs'][:4]}"
    assert s["trustVisible"], (
        "untagging the trust card must keep it on /stats too (both views), "
        "not trade one blindness for another"
    )
