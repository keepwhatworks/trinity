"""Browser guard: the live-council Auto-chain control must set HONEST cost expectations.

The "Continue the thread" card offers three chain actions on a completed council:

    [Continue (one round)]  [Auto-chain (up to 3 rounds)]   …and a Refine directive.

Auto-chain dispatches ``council_auto_chain`` with ``max_rounds: 3`` → ``auto_chain_council``
runs up to THREE ``run_consensus_round`` calls. Each consensus round re-dispatches EVERY
member (each model sees the others' prior answers and refines) PLUS a chairman synthesis —
i.e. each round is a FULL council. So one Auto-chain click can cost up to **3× a single
council's** member + chairman dispatches against the user's own subscription quota (the
product's cost basis — "free, on the subscriptions you already pay for").

The button sits one space from "Continue (one round)", which reads as a cheap single action.
The old explanatory copy framed Auto-chain purely on the QUALITY upside ("keeps going until
the models converge — useful when you suspect the first round missed something") with ZERO
cost signal — so a cost-blind multi-dispatch action looked roughly as cheap as Continue.

Found 2026-06-17 driving the COMPLETED ``?status_token=`` live council page (the path EVERY
launchpad-launched council takes) in the UX sweep, under the USEFULNESS lens. The fix added
an honest per-round cost signal to the chain-actions copy ("Each round is a fresh full
council — every model runs again — so it uses your subscription quota each time. Auto-chain
runs up to 3 such rounds … can cost up to 3× a single round.").

This guard pins that cost signal in the RENDERED chain-actions paragraph. It serves an
isolated, PII-free synthetic completed council over http (file:// can't carry the
``?status_token=`` query reliably). Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import threading

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


_TOKEN = "tok_auto_chain_cost"

# A COMPLETED 2-member council reached via the poll (?status_token=) path. The
# chain-actions ("Continue the thread") card renders only when the last segment is
# completed with a councilId (canChainNext), so both members must be done + the
# synthesis present so the live page flips to the post-completion chain surface.
_COMPLETED_STATUS = {
    "status": "completed",
    "status_token": _TOKEN,
    "task_text": "How should we model the council state machine?",
    "council_id": "c_auto_chain_cost",
    "memberOrder": ["claude", "antigravity"],
    "members": {
        "claude": {
            "status": "done",
            "model": "claude-opus-4-8",
            "response_text": "Use an explicit state enum.",
            "response_html": "<p>Use an explicit state enum.</p>",
        },
        "antigravity": {
            "status": "done",
            "model": "gemini-3.1-pro",
            "response_text": "A tagged union models it cleanly.",
            "response_html": "<p>A tagged union models it cleanly.</p>",
        },
    },
    "synthesis": {
        "status": "done",
        "response_text": "Synthesis verdict.",
        "response_html": "<p>Synthesis verdict.</p>",
        "routing_label": {"winner": "claude", "runner_up": "antigravity", "confidence": "high"},
    },
    "metadata": {
        "chairman_provider": "claude",
        "council_id": "c_auto_chain_cost",
        "members": ["claude", "antigravity"],
    },
}


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_auto_chain_copy_discloses_per_round_council_cost(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
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
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(_TOKEN)}] = "
        f"{json.dumps(_COMPLETED_STATUS)};\n"
    )
    (status_dir / f"council_status_{_TOKEN}.js").write_text(sidecar, encoding="utf-8")

    from playwright.sync_api import sync_playwright

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
                # A stub dispatcher so nothing reaches a real council (the whole
                # point of this guard is that auto-chain WOULD be expensive).
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = {"
                    "  dispatch: () => Promise.resolve({ok:true}),"
                    "  probe: () => Promise.resolve('ready'),"
                    "  onStateChange: () => {},"
                    "  state: 'ready'"
                    "};"
                )
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html?status_token={_TOKEN}"
                )
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                probe = page.evaluate(
                    """() => {
                      const card = document.querySelector('.chain-actions');
                      if (!card) return {present: false};
                      const para = card.querySelector('p.meta');
                      const autoBtn = Array.from(card.querySelectorAll('button'))
                        .find((b) => /auto-chain/i.test(b.innerText));
                      const continueBtn = Array.from(card.querySelectorAll('button'))
                        .find((b) => /continue/i.test(b.innerText));
                      const docOverflow =
                        document.documentElement.scrollWidth - document.documentElement.clientWidth;
                      return {
                        present: true,
                        paraText: para ? para.innerText.trim() : null,
                        autoBtn: autoBtn ? autoBtn.innerText.trim() : null,
                        continueBtn: continueBtn ? continueBtn.innerText.trim() : null,
                        docOverflow,
                      };
                    }"""
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    # Precondition: the chain-actions card + both buttons must render, else the
    # guard would be vacuous.
    assert probe["present"], "the 'Continue the thread' chain-actions card did not render"
    assert probe["autoBtn"] and re.search(r"up to 3 rounds", probe["autoBtn"]), (
        f"the Auto-chain button is missing or lost its '(up to 3 rounds)' label: {probe['autoBtn']!r}"
    )
    assert probe["continueBtn"], "the Continue button did not render"

    para = (probe["paraText"] or "").lower()
    # THE HONESTY INVARIANT: the chain-actions copy must disclose BOTH halves of
    # the Auto-chain cost the founder couldn't see —
    #   (a) each round is a FULL council that re-runs every model (the per-round cost), and
    #   (b) Auto-chain can cost UP TO 3x a single round (the multiplier on one click).
    assert "fresh full council" in para and "quota" in para, (
        "the live-council chain-actions copy does NOT disclose that each round is a fresh "
        "full council that uses your subscription quota — Auto-chain (which dispatches up to "
        "THREE full councils on one click) reads as cheap as 'Continue (one round)' right "
        f"beside it. got paragraph: {probe['paraText']!r}"
    )
    assert "3×" in (probe["paraText"] or "") or "3x" in para, (
        "the live-council chain-actions copy does NOT disclose that Auto-chain can cost up to "
        "3x a single round — the cost-blind multi-dispatch action has no quota multiplier "
        f"signal. got paragraph: {probe['paraText']!r}"
    )
    # Paint must stay clean with the longer copy.
    assert probe["docOverflow"] <= 1, (
        f"the longer Auto-chain cost copy introduced horizontal overflow: {probe['docOverflow']}px"
    )


if __name__ == "__main__":  # pragma: no cover - manual harness
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
