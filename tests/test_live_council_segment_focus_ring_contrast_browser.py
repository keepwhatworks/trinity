"""Browser guard: the live council page's collapsible ROUND DIVIDER is a
keyboard-focusable disclosure widget (``role="button"`` + ``tabindex=0``), and
its ``:focus-visible`` indicator MUST clear the WCAG 2.4.7 / 1.4.11 non-text
contrast floor of 3:1 against the adjacent backgrounds it abuts.

THE BUG (found 2026-06-23 keyboard-driving a real 2-round ``?thread_id=`` chain
in the UX sweep — the focus-indicator-contrast vein): the divider's
``:focus-visible`` rule SUPPRESSED the global accent outline (``outline: none``)
and drew the focus ring ONLY as ``box-shadow: 0 0 0 3px rgba(79,144,149,0.2)`` —
a 3px outset teal ring at 0.2 alpha. The divider's ancestors are transparent, so
that outset ring composites over the page body (#eaecef) to ~1.21:1, and over
the divider's own teal-tinted card background to ~1.17:1 — FAR under the 3:1
non-text floor. A keyboard-only user tabbing through a multi-round chain's
dividers got essentially NO visible focus indicator. (Same class as the
launchpad ``.icon-action:focus-visible`` 1.36:1 ring fixed earlier in the
sweep — a low-alpha focus-state indicator that the resting-state global
contrast-AA guard never checks.)

THE FIX: paint a SOLID ``var(--action)`` (#3f777c) ring instead of the 0.2-alpha
teal. Solid #3f777c clears 3:1 on the body (4.29:1), the divider's card tint
(4.12:1) AND its hover tint (3.94:1) — where even the lighter global ``--accent``
default would fail (2.83–2.96:1) over those teal-tinted backgrounds, which is why
the fix must be a solid ring, not merely un-suppressing the global outline.

This guard DRIVES the real surface: serves a real 2-round chain manifest +
outcome JSONP over http, opens ``?thread_id=``, Tabs to the divider with the REAL
keyboard, reads the COMPUTED ``box-shadow`` the browser paints, parses its
rgba(), composites it over the real ancestor body background, and asserts the
ring clears 3:1. Mutation-proven to bite on the un-fixed (0.2-alpha) render.

Slow-marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import functools
import http.server
import json
import re
import threading
from pathlib import Path

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


def _seed_two_round_chain(tmp_path, monkeypatch) -> tuple[Path, str]:
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

    thread_id = "bundle_focusringguard"
    for cid, rnd in (("c_frg1", 1), ("c_frg2", 2)):
        (co / f"{cid}.js").write_text(
            "window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};\n"
            f"window.__TRINITY_COUNCIL_OUTCOME__[{json.dumps(cid)}] = "
            f"{json.dumps(_outcome(cid, rnd))};\n",
            encoding="utf-8",
        )
    manifest = {
        "thread_id": thread_id, "task_text": "Should I ship?",
        "segments": [
            {"council_id": "c_frg1", "round_number": 1, "running": False},
            {"council_id": "c_frg2", "round_number": 2, "running": False},
        ],
    }
    (co / f"_thread_{thread_id}.js").write_text(
        "window.__TRINITY_COUNCIL_THREAD__ = window.__TRINITY_COUNCIL_THREAD__ || {};\n"
        f"window.__TRINITY_COUNCIL_THREAD__[{json.dumps(thread_id)}] = "
        f"{json.dumps(manifest)};\n",
        encoding="utf-8",
    )
    return rp, thread_id


_HEX = re.compile(r"#([0-9a-fA-F]{6})")
_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")


def _parse_color(token: str) -> tuple[float, float, float, float]:
    """Return (r, g, b, a) from a CSS rgb()/rgba()/#hex token."""
    m = _RGBA.search(token)
    if m:
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return float(m.group(1)), float(m.group(2)), float(m.group(3)), a
    h = _HEX.search(token)
    assert h, f"unparseable color token: {token!r}"
    v = h.group(1)
    return (float(int(v[0:2], 16)), float(int(v[2:4], 16)), float(int(v[4:6], 16)), 1.0)


def _composite(fg: tuple[float, float, float, float],
               bg: tuple[float, float, float]) -> tuple[float, float, float]:
    a = fg[3]
    return (
        fg[0] * a + bg[0] * (1 - a),
        fg[1] * a + bg[1] * (1 - a),
        fg[2] * a + bg[2] * (1 - a),
    )


def _lum(c: tuple[float, float, float]) -> float:
    def f(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2])


def _ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_segment_divider_focus_ring_clears_non_text_contrast(tmp_path, monkeypatch):
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
                page = browser.new_context(viewport={"width": 1024, "height": 900}).new_page()
                errs: list[str] = []
                page.on("pageerror", lambda e: errs.append(str(e)[:200]))
                page.goto(
                    f"http://127.0.0.1:{port}/review_pages/live_council.html"
                    f"?thread_id={thread_id}"
                )
                page.wait_for_timeout(2600)
                assert not errs, f"JS pageerrors: {errs[:3]}"

                dividers = page.query_selector_all(".chain-segment-divider.clickable")
                assert len(dividers) == 2, (
                    f"expected a 2-round chain (2 clickable segment dividers), got "
                    f"{len(dividers)} — the keyboard-focus precondition didn't render; the "
                    "focus-ring contrast assertion below would be vacuous."
                )

                # Drive the REAL keyboard to land focus on a divider (so :focus-visible
                # matches — a programmatic .focus() alone may not in every engine).
                page.keyboard.press("Tab")
                landed = False
                for _ in range(20):
                    active = page.evaluate(
                        "() => ({cls: (document.activeElement.className||''), "
                        "fv: (() => { try { return document.activeElement.matches(':focus-visible'); } "
                        "catch(e){ return false; } })()})"
                    )
                    if "chain-segment-divider" in active["cls"] and active["fv"]:
                        landed = True
                        break
                    page.keyboard.press("Tab")
                assert landed, (
                    "real-keyboard Tab never landed :focus-visible on a .chain-segment-divider "
                    "— cannot read the focus indicator the browser paints."
                )

                painted = page.evaluate(
                    """() => {
                        const d = document.activeElement;
                        const cs = getComputedStyle(d);
                        // Walk to the first opaque (non-transparent) ancestor background
                        // — the 3px OUTSET box-shadow ring composites over THAT.
                        let bg = null, el = d.parentElement;
                        while (el) {
                            const c = getComputedStyle(el).backgroundColor;
                            const m = /rgba?\\(([^)]+)\\)/.exec(c);
                            if (m) {
                                const parts = m[1].split(',').map(s => parseFloat(s));
                                const a = parts.length > 3 ? parts[3] : 1;
                                if (a > 0) { bg = [parts[0], parts[1], parts[2]]; break; }
                            }
                            el = el.parentElement;
                        }
                        return {
                            outlineStyle: cs.outlineStyle,
                            outlineWidth: cs.outlineWidth,
                            outlineColor: cs.outlineColor,
                            boxShadow: cs.boxShadow,
                            ancestorBg: bg,
                        };
                    }"""
                )

                # The global :focus-visible outline is suppressed here, so the box-shadow
                # IS the focus indicator. (If a future change instead relies on a SOLID
                # outline, that's fine too — but it must not be the faint 0.2-alpha ring.)
                box = painted["boxShadow"]
                assert box and box != "none", (
                    "the round divider's :focus-visible drew NO box-shadow ring and the "
                    f"outline is {painted['outlineStyle']!r} — no visible focus indicator at all."
                )

                ring = _parse_color(box)
                assert painted["ancestorBg"], "could not find an opaque ancestor background"
                bg = tuple(painted["ancestorBg"])  # the page body the outset ring paints over
                composited = _composite(ring, bg)
                contrast = _ratio(composited, bg)

                assert contrast >= 3.0, (
                    "the live council ROUND DIVIDER's :focus-visible ring fails the WCAG "
                    f"2.4.7 / 1.4.11 non-text 3:1 floor — box-shadow {box!r} composites to "
                    f"{composited} over the page body {bg}, contrast {contrast:.3f}:1. "
                    "A keyboard-only user tabbing a multi-round chain gets no visible focus "
                    "indicator on the round dividers (the old rgba(79,144,149,0.2) teal ring). "
                    "Paint a SOLID --action ring instead."
                )
            finally:
                browser.close()
    finally:
        httpd.shutdown()
