"""The HOME taste-card "Spans" basin chips must NAME the basin (its top terms),
NOT leak the opaque internal id "b00" as the chip BODY.

Driving the REAL extension side panel @ 393px on the HOME view (2026-06-21): the
"Your taste, distilled" card renders, under each paired lens tension, a "Spans" row
of chips deep-linking into the topology view. Each chip used to render the raw basin
id (`{{ bid }}` → "b00", "b03") as its visible text, with the human label (the
basin's top TF-IDF terms, e.g. "design · arch") only in the `:title` tooltip.

WHY THAT WAS A SHIPPED DEFECT — on the side panel (and on touch phones) there is NO
hover, so the title tooltip is permanently unreachable: a touch user saw literally
`Spans  b00  b01  b02  b03` — opaque internal ids with no way to learn what they
mean (UNCLEAR / near-orphan-value). The cheat-sheet row ALREADY resolved this exact
problem (cheatSheetLabel + the _topology_basin_labels docstring's own admission "b03
alone is opaque … so the headline never resolves to a meaningless 'b00'"); the
taste-card Spans chips were the asymmetric sibling that still shipped the raw id.

The only prior coverage (test_lens_basin_chips.py) asserts the chip CLASS + href are
PRESENT in the template string — it never renders the chip, so it could not see that
the BODY was an opaque id. This guard drives the real panel and pins the RENDERED
chip body to the human basin label.

Mutation-provable: revert the chip body to `{{ bid }}` (or drop spansBasinLabel) and
this reds with the founder symptom; the string test stays green. Slow + browser
marked; skips without Playwright/chromium.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]
EXT = REPO / "browser-extension"
HOST = "local.trinity.capture"

# A bare basin id like "b00" / "b3" / "b12" — the opaque form the chip must NOT show.
_RAW_ID_RX = re.compile(r"^b\d+$")


def _boot_panel(p, tmp_path, monkeypatch, width=393):
    """Seed a synthetic home (lights the taste card with basins_spanned chips),
    stub the native host, load the real extension, open the side panel."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / "scripts"))
    import seed_synthetic_home  # noqa: E402

    seed_synthetic_home.seed(home)
    from trinity_local.launchpad_page import build_launchpad_payload

    payload = build_launchpad_payload()
    pl = tmp_path / "payload.json"
    pl.write_text(json.dumps({"ok": True, **payload}, default=str), encoding="utf-8")

    stub = (
        "#!/usr/bin/env python3\n"
        "import sys, struct, json\n"
        "raw = sys.stdin.buffer.read(4)\n"
        "msg = json.loads(sys.stdin.buffer.read(struct.unpack('<I',raw)[0]) or b'null') if len(raw)==4 else None\n"
        "qk = (msg or {}).get('query_kind')\n"
        f"out = open({str(pl)!r}).read().encode() if qk=='launchpad_data' else json.dumps({{'ok':True}}).encode()\n"
        "sys.stdout.buffer.write(struct.pack('<I',len(out))); sys.stdout.buffer.write(out); sys.stdout.buffer.flush()\n"
    )
    ud = tmp_path / "profile"
    nm = ud / "NativeMessagingHosts"
    nm.mkdir(parents=True)
    hp = ud / "stub.py"
    hp.write_text(stub, encoding="utf-8")
    hp.chmod(hp.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    try:
        ctx = p.chromium.launch_persistent_context(
            str(ud), headless=False,
            args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--headless=new"],
        )
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no launchable chromium: {exc}")

    sw = None
    for _ in range(50):
        if ctx.service_workers:
            sw = ctx.service_workers[0]
            break
        try:
            sw = ctx.wait_for_event("serviceworker", timeout=2000)
            break
        except Exception:
            time.sleep(0.1)
    assert sw, "extension service worker never registered (manifest invalid?)"
    ext_id = sw.url.split("/")[2]
    (nm / f"{HOST}.json").write_text(json.dumps({
        "name": HOST, "description": "stub", "path": str(hp), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{ext_id}/"],
    }), encoding="utf-8")

    page = ctx.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"chrome-extension://{ext_id}/sidepanel.html", wait_until="load", timeout=20000)
    page.wait_for_timeout(4000)  # iframe load + bridge fetch + mount
    return ctx, page


def _spans_chips(frame):
    """Return [{visible, title, href}] for every rendered taste-card Spans chip."""
    return frame.evaluate(
        """() => [...document.querySelectorAll('.lens-basins-row .lens-basin-chip')].map(a => ({
              visible: (a.innerText || '').trim(),
              title: a.getAttribute('title') || '',
              href: a.getAttribute('href') || '',
           }))"""
    )


def test_spans_chip_body_names_the_basin_not_the_raw_id(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, page = _boot_panel(p, tmp_path, monkeypatch)
        try:
            frame = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert frame, f"sandbox launchpad iframe never loaded; frames={[f.url for f in page.frames]}"

            chips = _spans_chips(frame)
            # PRECONDITION (non-vacuous): the Spans chips must actually render, with a
            # basin deep-link href. The synthetic home seeds basins_spanned, so >0.
            assert chips, (
                "no taste-card Spans chips rendered — the synthetic home seeds "
                "basins_spanned, so this guard would be vacuous; fix the fixture, "
                "not the assertion"
            )
            for c in chips:
                assert "topics.json&basin=" in c["href"], (
                    f"Spans chip href is not a topology basin deep-link: {c['href']!r}"
                )

            # THE BITE: the chip BODY must be the human basin label (top terms), never
            # the bare opaque id "b00". On the side panel there is NO hover, so the
            # :title is unreachable — the BODY is the only thing a touch user can read.
            for c in chips:
                assert not _RAW_ID_RX.match(c["visible"]), (
                    "the taste-card 'Spans' chip leaked the opaque internal basin id "
                    f"{c['visible']!r} as its VISIBLE BODY — on the touch side panel "
                    "(no hover) the human label hides in the unreachable :title "
                    f"({c['title']!r}), so the user sees a meaningless 'b00'. The "
                    "cheat-sheet already resolved this (cheatSheetLabel); the chip "
                    "BODY must NAME the basin (its top terms)."
                )
                # The title still carries the id for traceability ("Basin b00 — …").
                assert "Basin" in c["title"], (
                    f"Spans chip lost its traceability :title (got {c['title']!r})"
                )

            # The seeded fixture's first basin is design/arch — assert the human term
            # actually painted (proves spansBasinLabel resolved a real label, not just
            # that the body isn't an id).
            bodies = " | ".join(c["visible"] for c in chips)
            assert any(t in bodies for t in ("design", "debug", "refactor", "test")), (
                f"no human basin term painted in any Spans chip body (got {bodies!r}) "
                "— spansBasinLabel did not resolve topologyBasinLabels"
            )
        finally:
            ctx.close()


def test_spans_chips_do_not_overflow_narrow_panel(tmp_path, monkeypatch):
    """A multi-word basin label (now the chip body) must wrap inside the 320px panel
    rather than overflow — the regression the white-space:normal + overflow-wrap fix
    guards (a long nowrap chip body would push the panel sideways)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx, page = _boot_panel(p, tmp_path, monkeypatch, width=320)
        try:
            frame = next((f for f in page.frames if "sandbox/launchpad.html" in (f.url or "")), None)
            assert frame, "sandbox launchpad iframe never loaded"
            chips = _spans_chips(frame)
            assert chips, "no Spans chips rendered — fixture regressed (vacuous guard)"
            geo = frame.evaluate(
                """() => {
                  const docW = document.documentElement.scrollWidth;
                  const clientW = document.documentElement.clientWidth;
                  const rights = [...document.querySelectorAll('.lens-basins-row .lens-basin-chip')]
                    .map(a => Math.round(a.getBoundingClientRect().right));
                  return { docW, clientW, maxRight: Math.max(0, ...rights) };
                }"""
            )
            assert geo["docW"] <= geo["clientW"], (
                "the HOME taste card horizontally overflowed the 320px panel "
                f"(docW={geo['docW']} > clientW={geo['clientW']}) — a Spans chip body "
                "(now a multi-word basin label) is not wrapping; the white-space:normal "
                "/ overflow-wrap fix regressed"
            )
            assert geo["maxRight"] <= geo["clientW"] + 1, (
                f"a Spans chip's right edge ({geo['maxRight']}) ran past the 320px "
                f"panel (clientW={geo['clientW']}) — the chip body is not wrapping"
            )
        finally:
            ctx.close()
