"""Browser guard: the /stats cortex EVIDENCE CHIP must label itself with a human
ordinal ("council 1", "council 2", …), NOT an opaque council-id fragment.

UX-sweep find. The cheat-sheet's evidence chips (the "click a council to see the
work behind the recommendation" affordance) used to render
``cid.replace(/^(?:council_|bundle_)/, '').slice(0, 8)`` — an 8-char hex fragment
of the council id (e.g. ``council_1a5b74fb1df3fda`` -> "1a5b74fb"). That fragment
reads as a LEAKED INTERNAL ID and tells a touch user NOTHING: the full id hides in
the hover-only ``:title``, which is unreachable on the side panel / phones (the
same opaque-id-as-label class as the Iter-184 basin chip, where the human label
also hid in an unreachable tooltip). A council carries no short human label on this
surface (the rail resolves the prompt via extra I/O the cheat-sheet doesn't do), so
the chip now LEADS WITH WHAT IT IS — a positional "council N" scoped to the pick's
row — with the full id preserved in the ``:title`` + ``href`` for traceability.

A string-presence check is blind to this — the prior coverage
(``test_evidence_chip_label_strips_bundle_prefix``) only pinned the slice REGEX in
the template source, so it stayed green while the rendered chip showed gibberish.
This guard renders /stats with a REAL hex council id in the evidence, drives a real
browser, reads the PAINTED chip body, and asserts it is the ordinal — never a bare
hex fragment — with the full id still in the title.

MUTATION-PROVEN: revert the chip body to ``{{ cid.replace(...).slice(0,8) }}`` +
rebuild -> the painted label becomes "1a5b74fb" -> this reds with the founder
symptom. Slow + browser marked; skips without Playwright/chromium.
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

# A real ``stable_id``-shaped council id (16 hex chars after the prefix). The
# UN-FIXED chip would slice this to its first 8 hex chars ("1a5b74fb") — exactly
# the opaque fragment this guard forbids.
_HEX_CID = "council_1a5b74fb1df3fda9"
_HEX_FRAGMENT = "1a5b74fb"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _seed_home(tmp_path, monkeypatch) -> None:
    """Seed the synthetic home, then OVERWRITE one pick's evidence with a real-hex
    council id (+ two more) so the un-fixed slice produces an opaque fragment AND
    the ordinal increments across rows."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_synthetic_home", str(_repo_root() / "scripts" / "seed_synthetic_home.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.seed(tmp_path)

    from trinity_local.state_paths import scoreboard_dir

    picks = scoreboard_dir() / "picks.json"
    data = json.loads(picks.read_text(encoding="utf-8"))
    # Give the highest-margin basin THREE evidence councils, the first a real-hex id.
    first_basin = sorted(
        data, key=lambda b: data[b].get("margin", 0.0), reverse=True
    )[0]
    data[first_basin]["evidence"] = [
        _HEX_CID,
        "council_ccc222dd333ee44f",
        "council_eee333ff444aa55b",
    ]
    picks.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Re-render /stats off the mutated picks.json.
    from trinity_local.launchpad_page import render_stats_html
    from trinity_local.vendor import publish_vendor_files
    from trinity_local.state_paths import portal_pages_dir

    pp = portal_pages_dir()
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "stats.html").write_text(render_stats_html(), encoding="utf-8")
    publish_vendor_files(pp)


def test_evidence_chip_paints_an_ordinal_not_a_hex_fragment(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    _seed_home(tmp_path, monkeypatch)
    from playwright.sync_api import sync_playwright

    httpd, port = _serve(tmp_path)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            ctx = browser.new_context(viewport={"width": 393, "height": 1200})
            page = ctx.new_page()
            page.goto(f"{base}/portal_pages/stats.html", wait_until="networkidle")
            page.wait_for_timeout(700)

            # Evidence chips = anchors whose href carries the live-council thread_id.
            chips = page.eval_on_selector_all(
                "a.suggestion-chip",
                """els => els
                    .filter(a => /thread_id=/.test(a.getAttribute('href')||''))
                    .map(a => ({
                        txt: (a.textContent||'').trim(),
                        title: a.getAttribute('title')||'',
                        over: a.getBoundingClientRect().right > document.documentElement.clientWidth + 1,
                    }))""",
            )
            # PRECONDITION (non-vacuous): the chips actually rendered, and the real-hex
            # id reached the surface (its full form is in the title) — so the bite is
            # the LABEL, not a missing seed.
            assert chips, (
                "no cortex evidence chip rendered on /stats — the seed/render "
                "precondition failed, so a label assertion would be vacuous"
            )
            assert any(_HEX_CID in c["title"] for c in chips), (
                "the real-hex council id never reached the chip title — the "
                "discriminating fixture didn't land (label assertion would be vacuous)"
            )

            for c in chips:
                label = c["txt"]
                # THE BITE: the painted label must be a human ordinal, never the
                # opaque hex fragment the un-fixed slice produced.
                assert label != _HEX_FRAGMENT and not re.fullmatch(
                    r"[0-9a-f]{6,16}", label
                ), (
                    "the cortex evidence chip leaked an OPAQUE COUNCIL-ID FRAGMENT "
                    f"as its visible label ({label!r}) — a touch user (no hover for "
                    "the :title) sees a meaningless hex string instead of what the "
                    "chip IS. It must read 'council N'."
                )
                assert re.fullmatch(r"council \d+", label), (
                    "the cortex evidence chip must label itself with a human ordinal "
                    f"('council 1', 'council 2', …); got {label!r}"
                )
                assert not c["over"], (
                    f"the evidence chip {label!r} overflowed the 393px panel right edge"
                )

            # The ordinal must INCREMENT across the row's evidence (council 1/2/3),
            # not repeat — so each chip is a distinct, identifiable handle.
            ordinals = [c["txt"] for c in chips if c["txt"].startswith("council ")]
            assert "council 1" in ordinals and "council 2" in ordinals and "council 3" in ordinals, (
                "the evidence ordinals didn't increment across the 3-evidence pick "
                f"(council 1/2/3) — got {ordinals!r}"
            )
            browser.close()
    finally:
        httpd.shutdown()
