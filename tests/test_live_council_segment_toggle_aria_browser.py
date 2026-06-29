"""Browser guard: the live council page's collapsible ROUND DIVIDER is a
disclosure widget — it MUST expose ``aria-expanded`` so assistive tech can
announce the collapsed/expanded state, and that attribute MUST stay in lockstep
with the visible chevron / "· collapsed" label as the user toggles it.

THE BUG (found 2026-06-20 driving a real 2-round ?thread_id= chain in the UX
sweep): each ``.chain-segment-divider`` in a multi-round chain is a hand-rolled
disclosure — ``role="button"`` + ``tabindex=0`` + Enter/Space handlers + a
chevron that flips ▾/▸ and a "· collapsed" label — and ``toggleSegment`` hides
that round's whole content (status / synthesis / routing / member sections, all
gated on ``seg.expanded``). But the divider carried NO ``aria-expanded``
attribute. A screen reader announced it as a bare "button" with no programmatic
expanded/collapsed state — the exact WCAG 4.1.2 (Name, Role, Value) gap the
codebase ALREADY closed for the IDENTICAL disclosure pattern in the memory
viewer's expandable thread card (``memory_viewer.py`` line ~2270 sets
``role=button`` + ``aria-expanded`` in lockstep, with a comment naming the
pattern). The live council page's sibling disclosure was the one that was missed.

THE FIX: bind ``:aria-expanded="seg.expanded ? 'true' : 'false'"`` on the divider
so petite-vue keeps the ARIA state synced with ``seg.expanded`` on every toggle.

This guard DRIVES the real surface: serves a real 2-round chain manifest +
outcome JSONP over http, opens ``?thread_id=``, reads the RENDERED
``aria-expanded`` attribute, then clicks a divider and asserts the attribute
flips (true → false → true) in lockstep with the chevron — never absent.
Mutation-proven to bite on the un-fixed (no-attribute) render.

Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _outcome(cid: str, round_no: int) -> dict:
    return {
        "council_run_id": cid,
        "primary_provider": "claude",
        "primary_model": "claude-opus-4-8",
        "member_results": [
            {"provider": "claude", "model": "claude-opus-4-8",
             "output_text": "Claude answer.", "output_html": "<p>Claude answer.</p>"},
            {"provider": "antigravity", "model": "gemini-3.1-pro",
             "output_text": "Gemini answer.", "output_html": "<p>Gemini answer.</p>"},
        ],
        "routing_label": {"winner": "claude", "confidence": "high",
                          "agreed_claims": ["x"], "disagreed_claims": []},
        "synthesis_text": f"Round {round_no} synthesis.",
        "synthesis_html": f"<p>Round {round_no} synthesis.</p>",
        "metadata": {
            "council_id": cid, "round_number": round_no, "task_text": "Should I ship?",
            "chairman_provider": "claude", "members": ["claude", "antigravity"],
            "synthesis": {
                "status": "done", "response_text": f"Round {round_no} synthesis.",
                "response_html": f"<p>Round {round_no} synthesis.</p>",
                "routing_label": {"winner": "claude", "confidence": "high",
                                  "agreed_claims": ["x"], "disagreed_claims": []},
            },
        },
    }


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_two_round_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    from trinity_local import vendor as _vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    _vendor.publish_vendor_files(review_pages_dir())
    rp = review_pages_dir()
    co = rp.parent / "council_outcomes"  # outcomeScriptBaseUrl = "../council_outcomes"
    co.mkdir(parents=True, exist_ok=True)
    portal_pages_dir()

    thread_id = "bundle_ariatoggle"
    cids = [("c_at1", 1), ("c_at2", 2)]
    for cid, rnd in cids:
        (co / f"{cid}.js").write_text(
            "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
            f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(cid)}] = "
            f"{json.dumps(_outcome(cid, rnd))};\n",
            encoding="utf-8",
        )
    manifest = {
        "thread_id": thread_id, "task_text": "Should I ship?",
        "segments": [
            {"council_id": "c_at1", "round_number": 1, "running": False},
            {"council_id": "c_at2", "round_number": 2, "running": False},
        ],
    }
    (co / f"_thread_{thread_id}.js").write_text(
        "window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {};\n"
        f"window.__TRINITY_COUNCIL_THREAD__[{json.dumps(thread_id)}] = "
        f"{json.dumps(manifest)};\n",
        encoding="utf-8",
    )
    return rp, thread_id


def test_segment_divider_exposes_aria_expanded_in_lockstep(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    rp, thread_id = _seed_two_round_chain(tmp_path, monkeypatch)
    httpd, port = _serve(rp.parent)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": 393, "height": 900}
                ).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?thread_id={thread_id}"
                )
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                dividers = page.query_selector_all(".chain-segment-divider")
                assert len(dividers) == 2, (
                    f"expected a 2-round chain (2 segment dividers), got {len(dividers)} "
                    "— the disclosure-toggle precondition didn't render; the aria guard "
                    "below would be vacuous."
                )

                def aria(i):
                    return dividers[i].get_attribute("aria-expanded")

                # PRECONDITION (the BITE): every divider exposes aria-expanded at all.
                # On the un-fixed render this attribute is ABSENT (None) — a role=button
                # disclosure with no programmatic expanded/collapsed state (WCAG 4.1.2).
                before = [aria(0), aria(1)]
                assert before == ["true", "true"], (
                    "the live council ROUND DIVIDER is a role=button disclosure but did NOT "
                    "expose aria-expanded='true' while expanded — a screen reader announces "
                    "it as a bare 'button' with no collapsed/expanded state (WCAG 4.1.2, the "
                    "same disclosure-widget gap the memory viewer's expandable thread card "
                    f"already closed). aria-expanded values were {before!r}."
                )

                # Collapse round 1 → its aria-expanded MUST flip to 'false' in lockstep
                # with the chevron, and ONLY that segment changes.
                dividers[0].click()
                page.wait_for_timeout(300)
                chev0 = dividers[0].query_selector(".segment-toggle-chevron").inner_text()
                after_collapse = [aria(0), aria(1)]
                assert after_collapse == ["false", "true"], (
                    "collapsing round 1 did not flip its aria-expanded to 'false' (or it "
                    f"leaked onto round 2) — aria-expanded values were {after_collapse!r}; "
                    "the disclosure's ARIA state is not in lockstep with the toggle."
                )
                assert chev0 == "▸", (
                    f"chevron/aria desync: aria-expanded='false' but chevron is {chev0!r} "
                    "(expected the collapsed glyph ▸)."
                )

                # Re-expand → back to 'true'.
                dividers[0].click()
                page.wait_for_timeout(300)
                after_expand = [aria(0), aria(1)]
                assert after_expand == ["true", "true"], (
                    "re-expanding round 1 did not restore aria-expanded='true' — "
                    f"values were {after_expand!r}."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
