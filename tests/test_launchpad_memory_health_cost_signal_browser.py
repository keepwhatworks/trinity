"""The memory-health card's one-click dispatch buttons must disclose their REAL
cost — they are the Auto-chain cost-blind sibling.

USEFULNESS / HONESTY defect (2026-06-17 UX sweep iter 54): the memory-health
card carries two ONE-CLICK dispatch buttons that look like a free refresh —
"Refresh memory" and "Repair extension" — but each silently spends the user's
OWN subscription quota (the product's entire cost basis: "free, local, on the
subscriptions you already pay for"):

  • Refresh memory → dispatches `trinity-local dream`. dream's own docstring +
    capture_host's allowlist comment: ~10+ flagship model calls over SEVERAL
    MINUTES (Phase 2 one call per cluster + Phase 3 one per basin + Phase 4
    three calls + Phase 5 distill).
  • Repair extension → dispatches `extension repair --auto` →
    dispatch_repair_council() runs a FULL council (every installed provider +
    chairman synthesis) via run_council.

The OLD copy labeled both buttons on value alone ("Refresh memory" /
"Repair extension") with ZERO cost signal — exactly the cost-blind shape the
live-council Auto-chain button had (UX sweep iter 53). A control that dispatches
real provider calls on the user's subscription must lead with an honest cost cue.

The fix added a co-located cost line on the card that discloses BOTH costs
(dream = ~10+ model calls over several minutes; Repair = a full council) and that
both run on the user's own subscription, plus per-button title tooltips.

This drives the REAL petite-vue render with a seeded stale-memory-health
page_data and asserts the cost signal is present in the rendered card. The card
binding (`memoryHealth.issues`) only resolves in the JS render, so this is a
real-browser geometry/content assertion, not a string-presence check.
Mutation-provable: delete the cost line from the template → the in-card content
assertion reds with the exact founder symptom.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_memory_health_cost_signal_lives_in_template():
    """CI-runnable canary (no browser): the memory-health card must carry a
    co-located, RENDERED cost line (a `<p>`, not an HTML comment or a title
    tooltip) that names BOTH the dream cost (~10+ model calls / several minutes)
    and the council cost (a full council), and that they run on the user's own
    subscription. Keying on the rendered `<p>` (with HTML comments + the buttons'
    title attributes stripped) keeps the canary non-vacuous — removing the
    visible line must red this, even though the phrases also appear in nearby
    explanatory comments/tooltips."""
    import re as _re

    src = (REPO / "src" / "trinity_local" / "launchpad_template.py").read_text(encoding="utf-8")
    anchor = 'class="card memory-health-card"'
    assert anchor in src, "memory-health card markup changed — re-anchor this guard"
    block = src[src.index(anchor):]
    end = block.index("memor{{{{")  # the "N memories need attention" heading
    card = block[:end]
    # Strip HTML comments AND title="..." attributes so the canary keys ONLY on
    # the visible, rendered cost line (the founder reads pixels, not comments).
    visible = _re.sub(r"<!--.*?-->", "", card, flags=_re.DOTALL)
    visible = _re.sub(r'title="[^"]*"', "", visible)
    low = visible.lower()
    assert "several minutes" in low and "model call" in low, (
        "the memory-health card no longer renders a VISIBLE cost line disclosing "
        "that 'Refresh memory' runs dream — ~10+ model calls over several minutes; "
        "it reads like a free one-click refresh (the Auto-chain cost-blind shape, "
        "UX sweep iter 53)"
    )
    assert "full council" in low, (
        "the memory-health card no longer renders a VISIBLE line disclosing that "
        "'Repair extension' dispatches a full council on the user's subscription"
    )
    assert "your own subscription" in low or "own subscription" in low, (
        "the memory-health card cost signal no longer says these dispatches run "
        "on the user's OWN subscription — the product's entire cost basis"
    )


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_memory_health_card_discloses_dispatch_cost(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import build_launchpad_payload
    from trinity_local.launchpad_template import render_launchpad_html

    # INNER pageData (not the {pageData:...} wrapper) so `memoryHealth` resolves
    # in the template (the known false-alarm shape, [[ux-sweep]] iter 4 lesson).
    page_data = build_launchpad_payload()["pageData"]
    # Seed a stale memory-health state so the card (and its dispatch buttons)
    # render. Both an issue (core.md stale) and the card itself only show when
    # issues is non-empty.
    page_data["memoryHealth"] = {
        "issues": [
            {
                "name": "core.md",
                "status": "stale",
                "hint": "A memory file changed since the last dream.",
                "command": "trinity-local dream",
                "href": None,
            },
        ],
        "ok_count": 6,
        "total_count": 7,
    }
    html = render_launchpad_html(page_data=page_data, view="stats")

    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "serve" / "portal_pages"
    pp.mkdir(parents=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    httpd, port = _serve(tmp_path / "serve")
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 393, "height": 1100}).new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(f"PAGEERROR {e}"))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle",
                    timeout=20000,
                )
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                probe = page.evaluate(
                    "() => {"
                    " const card = document.querySelector('section.memory-health-card');"
                    " if (!card) return { card: false };"
                    " const text = (card.innerText || '').toLowerCase();"
                    " const btns = Array.from(card.querySelectorAll('button'));"
                    " const refresh = btns.find(b => /refresh memory/i.test(b.textContent));"
                    " const repair = btns.find(b => /repair extension/i.test(b.textContent));"
                    " return { card: true,"
                    "   visible: card.offsetParent !== null,"
                    "   text,"
                    "   refreshTitle: refresh ? (refresh.getAttribute('title')||'') : null,"
                    "   repairTitle: repair ? (repair.getAttribute('title')||'') : null }; }"
                )
                assert probe["card"], "memory-health card not rendered (seed shape wrong?)"
                assert probe["visible"], "memory-health card not visible on /stats"
                # The two dispatch buttons must exist (non-vacuous precondition).
                assert probe["refreshTitle"] is not None, "Refresh memory button missing"
                assert probe["repairTitle"] is not None, "Repair extension button missing"

                text = probe["text"]
                # The co-located cost line must disclose BOTH costs + the
                # subscription cost basis — the founder symptom is that these
                # one-click buttons read like a free refresh.
                assert "several minutes" in text and "model call" in text, (
                    "FOUNDER SYMPTOM: the memory-health 'Refresh memory' button "
                    "dispatches `dream` (~10+ model calls over several minutes on "
                    "your own subscription) but the card discloses NO cost — it "
                    "reads like a free one-click refresh (the Auto-chain cost-blind "
                    "shape). Rendered card text was: " + text[:300]
                )
                assert "full council" in text, (
                    "FOUNDER SYMPTOM: the 'Repair extension' button dispatches a "
                    "FULL council (every provider) on the user's subscription, but "
                    "the card frames it as a free one-click fix with no cost cue."
                )
                assert "own subscription" in text, (
                    "the memory-health cost signal no longer says these dispatches "
                    "spend the user's OWN subscription — the product's cost basis."
                )
                # Per-button tooltips also carry the cost (defense-in-depth).
                assert "subscription" in (probe["refreshTitle"] or "").lower(), (
                    "Refresh memory button tooltip lost its cost disclosure"
                )
                assert "council" in (probe["repairTitle"] or "").lower(), (
                    "Repair extension button tooltip lost its full-council disclosure"
                )
                # Paint stays clean — the new line must not overflow the panel.
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth"
                    " - document.documentElement.clientWidth"
                )
                assert overflow <= 1, f"cost line caused horizontal overflow: {overflow}px"
                assert not errors, f"page errors after render: {errors}"
            finally:
                browser.close()
    finally:
        httpd.shutdown()
