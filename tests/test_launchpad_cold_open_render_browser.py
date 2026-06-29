"""Browser guard: the #212 cold-start *aha* card — the launchpad's flagship
first-run wow ("🪞 ONE surprising true tension the instant your lens has signal")
— must actually MOUNT and INTERPOLATE on the rendered home, not just exist as a
string in the HTML.

Coverage gap this fills: `test_cold_open.py` covers the DATA layer
(`cold_open_tension()`) thoroughly, but its launchpad coverage is pure
STRING-PRESENCE — `"pageData.coldOpen" in html` and `"cold-open" in html`. Nothing
ever RENDERS the card in a browser. So if the `v-if="pageData.coldOpen"` binding or
the `🪞 {{ pageData.coldOpen }}` interpolation regressed (a v-cloak/mount break, a
CSS rule hiding it, an un-escaped brace leaking raw `{{ }}`, or the data builder
dropping `coldOpen` from the payload), every existing test would stay green while
the differentiated #212 cold-start aha — the make-or-break first-run moment, the
thing no chat tab can do — silently vanished or leaked the raw template.

Seeds a lens registry + cached taste signature so `coldOpen` is POPULATED (the
LONGEST line: three-word signature + dominant-tension proof with curly quotes),
renders the REAL home via the live `render_launchpad_html()` → `build_page_data()`
path over http, and pins (mutation-proven against the real binding):
  • the `.cold-open` card MOUNTS and is VISIBLE (offsetParent != null),
  • it INTERPOLATES the seeded tension poles (proves the binding evaluated real
    data — not an empty card, not raw `{{ pageData.coldOpen }}`),
  • no `{{ }}` / undefined leak in the card text,
  • no horizontal overflow of the card at 375px (the long curly-quote line wraps
    inside the column, doesn't push past the viewport).

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# Distinctive seeded poles — proves the rendered text is the INTERPOLATED data,
# not an empty card or a raw `{{ }}` directive.
POLE_A = "executable artifact"
POLE_B = "explanatory description"


def _seed_cold_open() -> None:
    """Seed a registry tension + cached taste signature so cold_open_tension()
    returns the longest (signature + proof) line."""
    from trinity_local.me.correction_lens import _taste_signature_path
    from trinity_local.me.lens_registry import RegistryEntry, save_registry
    from trinity_local.utils import now_iso

    ts = now_iso()
    save_registry([RegistryEntry(
        tension_id="t1", pole_a=POLE_A, pole_b=POLE_B,
        evidence_ids=[f"e{i}" for i in range(17)],
        basins_spanned=["b00", "b01", "b02"],
        first_seen=ts, last_confirmed=ts,
    )])
    p = _taste_signature_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"adjectives": ["uncompromising", "decisive", "action-oriented"], "n": 41}),
        encoding="utf-8",
    )


def test_cold_open_aha_card_mounts_and_interpolates(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_cold_open()

    html = render_launchpad_html()  # full live builder → build_page_data() → coldOpen
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                # 375px — the narrow phone width where the long curly-quote line
                # is most likely to overflow if the box doesn't wrap inside the column.
                page = browser.new_context(viewport={"width": 375, "height": 1400}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                assert not errs, f"cold-open render threw JS errors: {errs[:4]}"

                info = page.evaluate(
                    """() => {
                      const el = document.querySelector('.cold-open');
                      if (!el) return {present: false};
                      const r = el.getBoundingClientRect();
                      const txt = el.textContent.trim();
                      return {
                        present: true,
                        visible: el.offsetParent !== null,
                        right: r.right,
                        vw: window.innerWidth,
                        overflows: r.right > window.innerWidth + 0.5,
                        leakBrace: txt.includes('{{') || txt.includes('}}'),
                        leakUndef: txt.includes('undefined') || txt.includes('[object Object]'),
                        txt: txt,
                      };
                    }"""
                )

                # MOUNTED + VISIBLE — the #212 aha card actually paints. If the
                # v-if binding or petite-vue mount regressed, the differentiated
                # cold-start wow silently vanishes; this reds it.
                assert info["present"] and info["visible"], (
                    "the #212 cold-start aha card (.cold-open '🪞 ONE surprising true "
                    "tension') did NOT mount/paint on a home WITH lens signal — the "
                    "launchpad's flagship first-run wow is invisible "
                    f"(present={info.get('present')}, visible={info.get('visible')})"
                )

                # INTERPOLATED the real data — not an empty card, not raw `{{ }}`.
                assert POLE_A in info["txt"] and POLE_B in info["txt"], (
                    "the cold-open card mounted but did NOT interpolate the seeded "
                    f"tension poles ('{POLE_A}' / '{POLE_B}') — the "
                    "`{{ pageData.coldOpen }}` binding evaluated to empty/garbage; "
                    f"got: {info['txt'][:120]!r}"
                )
                assert not info["leakBrace"] and not info["leakUndef"], (
                    "the cold-open card leaked a raw template directive / undefined "
                    f"into the visible text: {info['txt'][:120]!r}"
                )

                # NO horizontal overflow — the long curly-quote line wraps inside
                # the column, doesn't push the card past the 375px viewport.
                assert not info["overflows"], (
                    "the cold-open card overflows the 375px viewport "
                    f"(right={info['right']} > vw={info['vw']}) — the long "
                    "signature+proof line doesn't wrap inside the column"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()


def test_cold_open_proof_does_not_overclaim_a_winner_on_the_tension(tmp_path, monkeypatch):
    """The proof half of the cold-open signature line must NOT assert a
    DIRECTIONAL pick-tally on the both-defensible tension axis.

    The lens tension is symmetric: pole_a/pole_b are the canonical
    (first-registered) phrasing — NOT a winner — and the tension's support_count
    backs the AXIS (both poles), not a count of times the user chose pole_a. The
    rest of Trinity renders the same tension symmetrically ("pole_a ↔ pole_b",
    "pole_a vs pole_b — the tension you keep navigating") and cold_open_tension's
    own docstring forbids "claiming 'you always pick X' would overclaim."

    The #35 overclaim that bit here: the signature+proof branch rendered
    "Across {n} decisions you've reached for "{pole_a}" over "{pole_b}"" — a
    fabricated directional pick-count the data does not encode, on the launchpad's
    flagship first-run hero, contradicting the symmetric ↔ framing two cards down.
    This guard reds that copy. The directional steer the "in your voice" payoff
    rests on lives in the ADJECTIVES (the correction lens — legitimately
    directional), not in a manufactured tension winner.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    _seed_cold_open()  # populated tension (>=3 support) + cached adjectives → signature+proof branch

    html = render_launchpad_html()
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "launchpad.html").write_text(html, encoding="utf-8")
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(viewport={"width": 1280, "height": 1400}).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/launchpad.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1200)

                info = page.evaluate(
                    """() => {
                      const el = document.querySelector('.cold-open');
                      if (!el) return {present: false};
                      return {present: true, txt: el.textContent.trim()};
                    }"""
                )

                # Precondition (non-vacuous): the populated signature+proof card
                # painted with BOTH seeded poles — so an absent overclaim means the
                # copy is honest, not that the card failed to render.
                assert info["present"], (
                    "the cold-open card didn't render on a home WITH a populated "
                    "tension — precondition for the no-overclaim assert failed"
                )
                txt = info["txt"]
                assert POLE_A in txt and POLE_B in txt, (
                    "the cold-open card did not interpolate the seeded tension poles "
                    f"— precondition failed; got: {txt[:140]!r}"
                )

                # THE BITE: the proof must not fabricate a directional winner on a
                # both-defensible tension. Match the exact overclaim shapes — the
                # poles joined by "over" / a "reached for"/"leans … over" pick-tally.
                low = txt.lower()
                assert "reached for" not in low, (
                    "the cold-open proof claims the user 'reached for' one pole of a "
                    "both-defensible tension — a fabricated directional pick-tally "
                    "(pole_a is just the first-registered phrasing, support_count "
                    "backs the AXIS not a pick of pole_a). #35 overclaim on the "
                    f"flagship first-run hero. Rendered: {txt[:180]!r}"
                )
                assert (f"{POLE_A}” over “{POLE_B}".lower() not in low
                        and f'{POLE_A}" over "{POLE_B}'.lower() not in low
                        and f"{POLE_A} over {POLE_B}".lower() not in low), (
                    "the cold-open proof asserts one tension pole 'over' the other — "
                    "a directional winner the symmetric lens tension does not encode "
                    "(everywhere else it renders 'pole_a ↔ pole_b' / 'pole_a vs "
                    f"pole_b — the tension you keep navigating'). Rendered: {txt[:180]!r}"
                )
                # Positive: the honest axis framing is present (so a future edit can't
                # satisfy the bite by simply dropping the proof and saying nothing).
                assert ("vs" in low or "navigat" in low or "tension" in low), (
                    "the cold-open proof dropped the honest axis framing entirely — it "
                    "should name the tension as a navigated 'pole_a vs pole_b' axis, "
                    f"not a winner. Rendered: {txt[:180]!r}"
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
