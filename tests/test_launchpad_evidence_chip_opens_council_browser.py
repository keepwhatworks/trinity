"""Browser guard: a cortex cheat-sheet EVIDENCE CHIP must open a live council
page that HYDRATES — not a dead "Council failed" / "Could not load council
outcome" banner.

The /stats cortex cheat-sheet renders, next to each routing pick, compact
evidence chips — one per ``council_run_id`` the pick was tallied from
(``_load_cortex_rules`` -> ``r.evidence`` -> ``evidenceUrl(cid)`` ->
``../review_pages/live_council.html?thread_id=<council_run_id>``). The chip is
the "click a council to see the work behind the recommendation" affordance: the
ONLY path from a routing pick back to the deliberation that produced it.

Those evidence IDs are bare ``council_run_id`` values (lens_routing._council_records
pulls them straight from ``council_outcomes/*.json``), NOT ``bundle_<hash>``
thread-manifest ids. So opening one takes the live page's NO-MANIFEST fallback
(``loadThread`` -> empty ``thread_manifest`` -> ``_loadOutcomeIntoSegment`` against
the ``<council_run_id>.js`` JSONP sidecar that ``save_council_outcome`` writes
atomically beside every ``.json``). If ``evidenceUrl`` dropped the thread_id, or
that fallback / the ``.js`` sidecar regressed, every evidence chip would silently
land on the live page's red "Council failed" banner while the EXISTING coverage —
which is entirely string-level (``test_evidence_chip_label_strips_bundle_prefix``)
and data-level (``test_load_cortex_rules_dedupes_evidence``) — stayed green. The
"green while the value is gone" shape: the chip renders, the link looks right, the
deliberation behind the pick is unreachable.

This drives the REAL round trip: render /stats over http, find the evidence chip
(title "Open council <id>"), navigate to its href, assert the live council page
HYDRATES (synthesis verdict + routing label present, NO failure banner, no raw
``{{`` leak, zero pageerrors).

Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import threading
from urllib.parse import urljoin

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_home(tmp_path, monkeypatch):
    """Seed the synthetic home (picks.json with evidence -> seeded council
    outcomes, both .json AND .js sidecars via save_council_outcome) and render
    the portal + live-council pages. Returns nothing — the http server reads the
    rendered tree off disk."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_synthetic_home",
        str(_repo_root() / "scripts" / "seed_synthetic_home.py"),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.seed(tmp_path)


def _repo_root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


def test_cortex_evidence_chip_opens_a_hydrating_council(tmp_path, monkeypatch):
    """A cortex evidence chip on /stats must open a live council that renders the
    synthesis + routing label — never a dead 'Council failed' banner."""
    pytest.importorskip("playwright.sync_api")
    _seed_home(tmp_path, monkeypatch)
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 1400})
                stats = ctx.new_page()
                stats.goto(f"{base}/portal_pages/stats.html", wait_until="networkidle")
                stats.wait_for_timeout(700)

                # The cortex EVIDENCE chip carries title "Open the council behind
                # this pick (<id>)" (the recent-council RAIL links carry the question
                # text as their title, so the title prefix disambiguates the two link
                # classes). The chip's VISIBLE label is a human ordinal ("council 1"),
                # not the id — the id lives in the title + href.
                chips = stats.eval_on_selector_all(
                    "a",
                    """els => els
                        .filter(a => (a.getAttribute('title')||'').startsWith('Open the council behind this pick')
                                  && (a.getAttribute('href')||'').includes('live_council.html')
                                  && (a.getAttribute('href')||'').includes('thread_id='))
                        .map(a => a.getAttribute('href'))""",
                )
                assert chips, (
                    "no cortex evidence chip rendered on /stats — the "
                    "'click a council to see the work behind the recommendation' "
                    "affordance is missing (picks.json evidence -> evidenceUrl)"
                )
                href = chips[0]
                assert "thread_id=" in href, (
                    "evidence chip href dropped thread_id — the live page can't "
                    f"resolve the council ({href!r})"
                )

                target = urljoin(f"{base}/portal_pages/stats.html", href)
                council = ctx.new_page()
                errs: list[str] = []
                council.on("pageerror", lambda e: errs.append(str(e)[:160]))
                council.goto(target, wait_until="networkidle")
                # Let loadThread fall through to the council_id outcome sidecar.
                council.wait_for_timeout(1800)

                text = council.inner_text("body")
                assert not errs, f"JS pageerrors opening the evidence council: {errs[:3]}"
                # THE load-bearing invariant: the chip must NOT land on a dead
                # failure banner (the symptom a broken evidenceUrl / .js sidecar
                # / council_id fallback would produce).
                assert "Council failed" not in text, (
                    "a cortex evidence chip opened a live council that shows the red "
                    "'Council failed' banner — the deliberation behind the routing "
                    "pick is UNREACHABLE (evidenceUrl/thread_id or the outcome .js "
                    "sidecar fallback regressed)"
                )
                assert "Could not load council outcome" not in text, (
                    "the evidence chip's council couldn't load its outcome — the "
                    "council_id -> <id>.js JSONP sidecar fallback is broken"
                )
                # And it must actually HYDRATE (not a blank shell): the synthesis
                # verdict + routing label render for a loaded council.
                has_routing = council.evaluate(
                    "() => !!document.querySelector('.routing-label-grid')"
                )
                assert has_routing, (
                    "the evidence chip's council page rendered no routing-label panel "
                    "— it didn't hydrate the outcome the chip points at"
                )
                assert "the answer you'd have picked" in text, (
                    "the winner verdict is absent — the evidence council never "
                    "reached the completed/hydrated state"
                )
                assert "{{" not in text, "raw petite-vue mustache leaked on the council page"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
