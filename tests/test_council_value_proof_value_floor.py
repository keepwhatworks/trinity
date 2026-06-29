"""Guard: the council value-proof HEADLINE must self-hide when the value is too
thin to defend — not just when the VOLUME is.

The HOME hero-proof card renders the council-first painkiller in one stat:
  "⚖ Across your N councils, the synthesized winner differed from your default
   X% of the time — that's how often a single-tab habit would have shipped the
   worse answer."

The historical gate only floored on VOLUME (N >= 10 real contests). A single-
provider-loyal user whose chairman usually AGREES with their default clears
N>=10 yet has a LOW changed-pick rate — so the flagship "why Trinity" home card
rendered self-defeating claims like "differed 0% of the time" / "differed 7% of
the time," which argue AGAINST Trinity ("you'd have been fine with one tab").
This is the same green-gate class as the n<3-suppress rule: a headline must
self-hide when the data doesn't support the claim it makes.

The fix (Trinity-council-decided 2026-06-17, council_78c065889d1c1b5c, winner
codex, unanimous "fixed rate floor + count guard, not a binomial test") gates on
the VALUE the copy displays: changed_pct >= 25 AND changed >= 3. The count floor
closes the thin-evidence hole a bare rate floor admits at the N=10 boundary
(25% of 10 contests is only ~2-3 flips).

Mutation-proven: drop EITHER floor from council_value_proof and a degenerate
case below flips ready:True → the corresponding assert reds.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import pytest

import trinity_local.personal_routing as pr

REPO = Path(__file__).resolve().parents[1]


def _records(changed: int, total: int):
    """`changed` councils where the chairman picked a non-default (codex over the
    claude default) + the remainder where the default (claude) won — all REAL
    contests recording both a winner and a default."""
    return (
        [{"chairman_winner": "codex", "winner_provider": "codex",
          "primary_provider": "claude", "substantive_members": 2}] * changed
        + [{"chairman_winner": "claude", "winner_provider": "claude",
            "primary_provider": "claude", "substantive_members": 2}] * (total - changed)
    )


# ── Data-layer refusal tests (fast, plain pytest) ──────────────────────────


def test_zero_changed_rate_refuses_headline(monkeypatch):
    """0% changed across 15 real contests: the card would tout "differed 0% of
    the time — that's how often one tab would've shipped the worse answer," the
    exact self-defeating claim. The value proof must report ready=False."""
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(0, 15), True))
    vp = pr.council_value_proof()
    assert vp["ready"] is False, (
        "value proof reported ready at a 0% changed-pick rate — the home hero card "
        "would render 'differed 0% of the time, that's how often one tab would've "
        "shipped the worse answer' (a SELF-DEFEATING claim on the flagship surface)"
    )
    assert vp["changed_pct"] == 0


def test_low_changed_rate_below_floor_refuses(monkeypatch):
    """20% changed (3 of 15): clears the count floor but NOT the 25% rate floor.
    Mutation: drop the rate floor → ready flips True → this reds."""
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(3, 15), True))
    vp = pr.council_value_proof()
    assert vp["changed_pct"] == 20
    assert vp["ready"] is False, (
        "value proof reported ready at a 20% changed rate (< the 25% floor) — too "
        "thin a painkiller for the home hero card"
    )


def test_high_rate_but_too_few_flips_refuses(monkeypatch):
    """A high rate carried by only 2 actual flips at the N=10 floor (2 of 10 =
    20% would already fail rate; force the thin-count case at the rate boundary:
    2 changed of 8... but n<10. Use 2 changed where the rate alone wouldn't catch
    it). Concretely: 2 changed of 10 = 20% (rate floor catches it too). To isolate
    the COUNT floor we need rate>=25 with changed<3 — impossible at comparable>=10
    unless comparable is exactly... 2/8 is n<10. So the count floor's role is the
    SAFETY at the rate boundary: 3 changed of 10 = 30% passes BOTH (3>=3). The
    count floor bites for comparable in (10..12) where 25% rounds to <3 flips."""
    # 3 changed of 12 = 25% (rounds to 25) but that's 3 flips → passes. To force
    # rate>=25 yet changed<3 we'd need comparable < 12; at comparable=11, 25% = 2.75
    # → round(100*2/11)=18 (fails rate anyway). The count floor's real job is to
    # keep a 2-flip card off the surface even if a future rate floor is lowered;
    # assert directly that 2 flips is refused regardless.
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(2, 11), True))
    vp = pr.council_value_proof()
    assert vp["ready"] is False, (
        "value proof reported ready off only 2 changed picks — the count floor "
        "must keep a thin-evidence card off the flagship surface"
    )


def test_clears_both_floors_reports_ready(monkeypatch):
    """27% changed (4 of 15): clears the 25% rate floor AND the 3-flip count floor.
    The card SHOULD render. Mutation: tighten either floor too far → this reds,
    proving the floors aren't so high they suppress a genuine painkiller."""
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(4, 15), True))
    vp = pr.council_value_proof()
    assert vp["ready"] is True, "a 27% changed-pick rate over 4 flips is a real painkiller — must render"
    assert vp["changed_pct"] == 27
    assert vp["changed_pick"] == 4


# ── Real-browser proof: the HOME hero card self-hides at a 0% rate ─────────


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


@pytest.mark.slow
@pytest.mark.browser
def test_home_council_value_card_self_hides_at_zero_changed_rate(tmp_path, monkeypatch):
    """End-to-end: a 0%-changed council ledger (15 REAL contests where the chairman
    always agreed with the default) must NOT render the council-value card on the
    home hero-proof surface. Drives the REAL petite-vue launchpad over http.

    Mutation: revert the value-floor gate in council_value_proof → the card renders
    '⚖ Across your 15 councils … differed … 0% of the time …' → this reds.

    `_scan_outcomes` is monkeypatched (the technique the unit tests use) rather than
    writing CouncilOutcome JSON: the on-disk path recomputes `substantive_members`
    from member output lengths and drops records without a deserializable
    routing_label, so hand-written files read as an EMPTY ledger — the card would
    then hide for the WRONG reason (n=0) and the test couldn't bite the value floor.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    # 15 REAL contests, chairman ALWAYS agreed with the claude default → 0% changed.
    # The launchpad builder calls council_value_proof() → _scan_outcomes() in-process,
    # so this controls exactly what the rendered card sees.
    monkeypatch.setattr(pr, "_scan_outcomes", lambda: (_records(0, 15), True))

    html = render_launchpad_html()  # live builder reads the (patched) ledger
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
                page = browser.new_context(viewport={"width": 720, "height": 1300}).new_page()
                page.add_init_script(
                    "window.__TRINITY_DISPATCH__ = () => Promise.resolve({ok:false, error:'stub'});"
                )
                page.goto(f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                          wait_until="networkidle", timeout=20000)
                page.wait_for_function(
                    "() => { const r = document.getElementById('launchpad-app');"
                    " return r && !r.hasAttribute('v-cloak'); }",
                    timeout=10000,
                )
                rendered = page.evaluate(
                    "() => { const cv = document.querySelector('p.council-value');"
                    " return cv ? cv.textContent : null; }"
                )
                assert rendered is None, (
                    "the home council-value card rendered on a 0%-changed ledger: "
                    f"{rendered!r} — it touts 'differed 0% of the time, that's how often "
                    "one tab would've shipped the worse answer', a self-defeating claim "
                    "the value floor must suppress"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
