"""Browser guard: the #252 "Your timeline" life-chapters card on /stats.

Coverage gap this fills (UX sweep iter — DRIFT/"what changed" cell): every
existing timeline guard is DATA- or STRING-level — `TestTimelineForLaunchpad`
pins `_timeline_for_launchpad`'s sort/filter logic, and
`test_timeline_card_binding_in_template` / `test_timeline_copy_matches_
chronological_display` assert the binding + copy as raw substrings in the HTML.
NONE drives the RENDERED card through the real `build_page_data` →
`_timeline_for_launchpad` → petite-vue mount, so the two load-bearing
USEFULNESS invariants were end-to-end unguarded:

  1. VIEW-GATING (MISPLACED guard) — the timeline is a `.stats-card`, demoted to
     the diagnostics tier by the value-first redesign. It must be HIDDEN on the
     minimal HOME view (where only the council/Ask painkiller belongs) and shown
     only on /stats. A class rename, or dropping the
     `.lp-view-home .stats-card { display:none }` rule, would crowd the minimal
     home with an analytics card — invisible to every string-level test.
  2. HONEST RENDER — on /stats the card mounts visible, renders its life-chapter
     rows (range · label · prompts) from the real builder, leaks no `{{ }}`
     template directive, and doesn't overflow the narrow (393) column.

Seeds a realistic life-chapter set via `me.chapters.detect_chapters` (the same
seam the data tests use) so the rows flow through the REAL render path — and
verifies the dev/thin chapters are filtered out of the rendered card too.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _serve(directory: Path):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# A realistic life-chapter set: two substantive non-dev arcs that survive the
# `min_prompts=80` floor + the `_TIMELINE_DEV_TERMS` filter, plus a dev chapter
# and a thin chapter that MUST be filtered out of the rendered card.
_CHAPTERS = [
    SimpleNamespace(
        label="floor plan, layout, rooms",
        start_month="2025-09", end_month="2025-11", months=3, total_prompts=412,
    ),
    SimpleNamespace(
        label="lease, tenant, rent",
        start_month="2026-01", end_month="2026-03", months=3, total_prompts=247,
    ),
    SimpleNamespace(  # dev/agent-ops → filtered (a `_TIMELINE_DEV_TERMS` token)
        label="loop, run, commit",
        start_month="2026-05", end_month="2026-05", months=1, total_prompts=900,
    ),
    SimpleNamespace(  # below the substance floor → filtered
        label="tiny, thin",
        start_month="2024-01", end_month="2024-01", months=1, total_prompts=30,
    ),
]


def _render(view: str, tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    import trinity_local.me.chapters as chmod

    monkeypatch.setattr(chmod, "detect_chapters", lambda *a, **k: list(_CHAPTERS))

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    html = render_launchpad_html(view=view)
    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    out = pp / f"{view}.html"
    out.write_text(html, encoding="utf-8")
    publish_vendor_files(pp)
    return out


def test_timeline_card_view_gated_and_honest(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Render both views from the same seeded life-chapter set.
    _render("home", tmp_path, monkeypatch)
    _render("stats", tmp_path, monkeypatch)

    httpd, port = _serve(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                sel = "section.stats-card:has-text('Your timeline')"

                # ── HOME: the timeline card must be HIDDEN (MISPLACED guard) ──
                home = browser.new_context(
                    viewport={"width": 1280, "height": 1600}
                ).new_page()
                errs: list[str] = []
                home.on("pageerror", lambda e: errs.append("pageerror: " + str(e)[:200]))
                home.on(
                    "console",
                    lambda m: errs.append("console.error: " + m.text[:200])
                    if m.type == "error"
                    and "favicon" not in m.text.lower()
                    and "woff" not in m.text.lower()
                    else None,
                )
                home.goto(
                    f"http://127.0.0.1:{port}/portal_pages/home.html",
                    wait_until="networkidle", timeout=20000,
                )
                home.wait_for_timeout(1000)
                assert not errs, f"timeline-seeded HOME threw JS errors: {errs[:4]}"
                card_home = home.query_selector(sel)
                # In the DOM (single template) but CSS-hidden on the minimal home.
                assert card_home is not None and not card_home.is_visible(), (
                    "the 'Your timeline' analytics stats-card is VISIBLE on the minimal "
                    "HOME view — it must stay demoted to /stats (a class rename or a "
                    "dropped `.lp-view-home .stats-card{display:none}` rule would crowd "
                    "the council-only home with diagnostics)"
                )
                home.close()

                # ── /stats: the card mounts, renders honest rows, no leak ──
                for width in (1280, 393):
                    stats = browser.new_context(
                        viewport={"width": width, "height": 1800}
                    ).new_page()
                    serrs: list[str] = []
                    stats.on(
                        "pageerror",
                        lambda e: serrs.append("pageerror: " + str(e)[:200]),
                    )
                    stats.on(
                        "console",
                        lambda m: serrs.append("console.error: " + m.text[:200])
                        if m.type == "error"
                        and "favicon" not in m.text.lower()
                        and "woff" not in m.text.lower()
                        else None,
                    )
                    stats.goto(
                        f"http://127.0.0.1:{port}/portal_pages/stats.html",
                        wait_until="networkidle", timeout=20000,
                    )
                    stats.wait_for_timeout(1000)
                    assert not serrs, f"/stats @{width} threw JS errors: {serrs[:4]}"

                    card = stats.query_selector(sel)
                    assert card is not None and card.is_visible(), (
                        f"the 'Your timeline' card did NOT render on /stats @{width} from "
                        "a seeded life-chapter corpus — the #252 timeline surface is broken"
                    )
                    text = card.inner_text()

                    # USEFULNESS: the substantive non-dev arcs render with their
                    # range + prompt count; the user sees real, datable life-chapters.
                    assert "Floor Plan, Layout, Rooms" in text and "412" in text, (
                        f"the timeline card @{width} dropped the substantive 'Floor Plan' "
                        f"life-chapter (or its prompt count) — got: {text!r}"
                    )
                    assert "Lease, Tenant, Rent" in text, (
                        f"the timeline card @{width} dropped the 'Lease' life-chapter — "
                        f"got: {text!r}"
                    )

                    # HONESTY: dev/agent-ops + thin chapters must NOT reach the card
                    # (it's the user's HISTORY, not the tool building itself).
                    assert "Loop, Run, Commit" not in text, (
                        f"the timeline card @{width} surfaced a dev/agent-ops chapter "
                        "('Loop, Run, Commit') — the `_TIMELINE_DEV_TERMS` filter no "
                        "longer reaches the rendered card"
                    )
                    assert "Tiny, Thin" not in text, (
                        f"the timeline card @{width} surfaced a sub-floor 'Tiny, Thin' "
                        "chapter — the substance floor no longer reaches the render"
                    )

                    # PAINT: no template leak, no horizontal overflow of the card rows.
                    assert "{{" not in text and "}}" not in text, (
                        f"the timeline card @{width} leaked a raw `{{{{ }}}}` template "
                        f"directive: {text!r}"
                    )
                    box = card.bounding_box()
                    assert box is not None and (box["x"] + box["width"]) <= width + 1.0, (
                        f"the timeline card @{width} overflows the viewport "
                        f"(right edge {box['x'] + box['width']:.1f} > {width}) — the "
                        "narrow-column life-chapter rows clip"
                    )

                    # ── PAINT + DEAD-AFFORDANCE (UX sweep iter 45) ──
                    # The "undefined CSS var falls back to a hardcoded hex, bypassing
                    # the AA-pushed design token" class. The range date used
                    # `var(--muted, #888)` — `--muted` is undefined (the token is
                    # `--text-muted` #616a73), so it rendered at #888 = 3.42:1 on the
                    # #fafbfc card surface, BELOW the 4.5:1 WCAG-AA body floor. Also pin
                    # that the static rows carry no FALSE interactive promise.
                    probe = stats.evaluate(
                        r"""() => {
                          function rgb(s){const m=s.match(/[\d.]+/g);return m?m.map(Number):null;}
                          function lum(c){const f=c.slice(0,3).map(v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);});return 0.2126*f[0]+0.7152*f[1]+0.0722*f[2];}
                          function ratio(a,b){const L1=lum(a),L2=lum(b);return (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);}
                          const cards = Array.from(document.querySelectorAll('section.card'));
                          const card = cards.find(c => /Your timeline/i.test(c.textContent || ''));
                          if (!card) return {found:false};
                          const range = card.querySelector('span.mono');
                          const row = range ? range.closest('div') : null;
                          let cardBg = rgb(getComputedStyle(card).backgroundColor);
                          if (!cardBg || (cardBg.length===4 && cardBg[3]<1)) cardBg = rgb(getComputedStyle(document.body).backgroundColor);
                          const rc = (range && cardBg) ? ratio(rgb(getComputedStyle(range).color), cardBg) : null;
                          // The "Your lens" memory-file <code> chips: pin they render
                          // the AA-deep teal `--accent-deep` (#34666b), NOT inherited
                          // body text. Two regressions this catches: (1) the original
                          // `var(--accent-warm)` undefined-var bug (no token by that
                          // name) → invalid color → fall back to inherited body text;
                          // (2) a revert to the brand-only `--accent` (#4f9095), which
                          // is sub-AA as functional teal text — the chip color was moved
                          // to `--accent-deep` for WCAG AA (commit aa666587).
                          const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent-deep').trim();
                          const memChips = Array.from(document.querySelectorAll('a.memory-chip code'))
                            .map(c => ({text: (c.textContent||'').trim(), color: getComputedStyle(c).color}));
                          return {
                            found: true,
                            rangeColor: range ? getComputedStyle(range).color : null,
                            rangeContrast: rc,
                            rowCursor: row ? getComputedStyle(row).cursor : null,
                            rowInteractive: row ? (!!row.querySelector('a, button, [role="button"], [onclick]') || row.hasAttribute('onclick')) : null,
                            accentToken: accent,
                            memChips: memChips,
                          };
                        }"""
                    )
                    assert probe["found"], "timeline card vanished from the contrast probe"
                    rc = probe["rangeContrast"]
                    assert rc is not None, (
                        f"the timeline range date @{width} had no measurable contrast "
                        f"(rangeContrast=None — the range-date element was not found). "
                        f"Rendered color: {probe['rangeColor']}."
                    )
                    assert float(rc) >= 4.5, (
                        f"the timeline range date @{width} renders BELOW WCAG-AA body "
                        f"contrast ({float(rc):.2f}:1 < 4.5:1) — the `--muted` CSS var is "
                        "undefined and falls back to the low-contrast #888 hex instead of "
                        "the AA-pushed `--text-muted` (#616a73) token. Rendered color: "
                        f"{probe['rangeColor']}."
                    )
                    # The timeline is a STATIC list: a row must not look/act clickable
                    # (a pointer cursor or an interactive child with no destination is a
                    # dead affordance).
                    assert str(probe["rowCursor"]) != "pointer", (
                        f"a timeline chapter row @{width} shows a pointer cursor but has "
                        "no handler — a dead affordance. The timeline is a static list; "
                        "wire a real target or drop the cursor."
                    )
                    assert not probe["rowInteractive"], (
                        f"a timeline chapter row @{width} sprouted an interactive child "
                        "(a/button/[role]/onclick) with no destination — a dead affordance."
                    )
                    # The memory-file <code> chips must render the AA-deep teal
                    # (`--accent-deep`), not inherited body text (the `--accent-warm`
                    # undefined-var regression) and not the sub-AA brand `--accent`.
                    accent = str(probe["accentToken"]).strip().lstrip("#").lower()
                    r_, g_, b_ = (
                        int(accent[0:2], 16),
                        int(accent[2:4], 16),
                        int(accent[4:6], 16),
                    )
                    want = f"rgb({r_}, {g_}, {b_})"
                    chips = probe["memChips"]
                    assert chips, (
                        f"the 'Your lens' memory-file <code> chips did not render @{width}"
                    )
                    off = [c for c in chips if str(c["color"]) != want]
                    assert not off, (
                        f"the memory-file <code> chips @{width} (core.md / lens.md / "
                        f"topics.json / vocabulary.md) did NOT render in the AA-deep teal "
                        f"`--accent-deep` {want} — either the `--accent-warm` CSS var is "
                        "undefined (no token by that name) so the declaration was invalid "
                        "and the chips fell back to inherited body text, or the color was "
                        "reverted to the sub-AA brand `--accent` (#4f9095). Off-color "
                        f"chips: {off}."
                    )
                    stats.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()


# Worst-case narrow-width chapters: multi-month ranges ("YYYY-MM → YYYY-MM" = the
# 17-char wide range) + 4-digit prompt counts ("1240 prompts" = the widest meta
# span). These are exactly the rows that overran the 320px column.
_WIDE_CHAPTERS = [
    SimpleNamespace(
        label="real estate, prefab",
        start_month="2024-01", end_month="2024-06", months=6, total_prompts=412,
    ),
    SimpleNamespace(
        label="frontend, design system",
        start_month="2025-01", end_month="2025-04", months=4, total_prompts=1240,
    ),
    SimpleNamespace(
        label="epistemology, method",
        start_month="2025-09", end_month="2026-02", months=6, total_prompts=802,
    ),
]


def test_timeline_card_no_horizontal_overflow_at_320(tmp_path, monkeypatch):
    """UX sweep iter 92 — the #252 timeline card must not push horizontal
    overflow at the narrow 320px breakpoint.

    The DEFECT: the chapter row was `display:flex` with NO `flex-wrap`, a range
    column hard-pinned at `min-width:130px; flex-shrink:0`, and a prompts `.meta`
    span at `flex-shrink:0`. At 320px the card's inner box is ~284px, so the three
    non-shrinking columns (130 range + 12 gap + label + 12 gap + ~89 "N prompts")
    couldn't coexist — the prompts span painted to x≈375, **55px past the 320
    viewport**, and `document.scrollWidth` grew to 375. (The iter-45 note
    misattributed this to an "unrelated" /stats card; driving the timeline card in
    ISOLATION proved it WAS the offender.) The CARD's own bounding box stayed inside
    320 (cardRight≈302), so the existing `card.bounding_box() <= width` guard at 393
    was BLIND to it — the overflow was a CHILD span escaping the card, not the card
    growing. This guard asserts the DOCUMENT doesn't overflow AND that no descendant
    of the timeline card has a right edge past the viewport.

    The fix (`flex-wrap:wrap` + a range `min-width:clamp(0px,130px,100%)`) lets the
    prompts count wrap to a second in-card line instead of spilling off-screen.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    import trinity_local.me.chapters as chmod

    monkeypatch.setattr(chmod, "detect_chapters", lambda *a, **k: list(_WIDE_CHAPTERS))

    from trinity_local.launchpad_page import render_launchpad_html
    from trinity_local.vendor import publish_vendor_files

    pp = tmp_path / "portal_pages"
    pp.mkdir(parents=True, exist_ok=True)
    (pp / "stats.html").write_text(
        render_launchpad_html(view="stats"), encoding="utf-8"
    )
    publish_vendor_files(pp)

    httpd, port = _serve(tmp_path)
    width = 320
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # pragma: no cover - env-dependent
                pytest.skip(f"no launchable chromium: {exc}")
            try:
                page = browser.new_context(
                    viewport={"width": width, "height": 2000}
                ).new_page()
                page.goto(
                    f"http://127.0.0.1:{port}/portal_pages/stats.html",
                    wait_until="networkidle", timeout=20000,
                )
                page.wait_for_timeout(1000)

                probe = page.evaluate(
                    r"""(width) => {
                      const cards = Array.from(document.querySelectorAll('section.card'));
                      const card = cards.find(c => /Your timeline/i.test(c.textContent || ''));
                      if (!card) return {found:false};
                      // every descendant whose right edge passes the viewport
                      const escapees = [];
                      card.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.right > width + 0.5) {
                          escapees.push({
                            tag: el.tagName,
                            cls: (el.className||'').toString().slice(0,30),
                            right: Math.round(r.right),
                            text: (el.innerText||'').slice(0,24).replace(/\n/g,' '),
                          });
                        }
                      });
                      return {
                        found: true,
                        docScrollW: document.documentElement.scrollWidth,
                        docClientW: document.documentElement.clientWidth,
                        nRows: card.querySelectorAll('div').length,
                        escapees: escapees,
                      };
                    }""",
                    width,
                )
                assert probe["found"], (
                    "the 'Your timeline' card did not render @320 from the seeded "
                    "wide-range life-chapters"
                )
                # The card must actually have its rows (not an accidentally-empty
                # render that would pass the overflow checks vacuously).
                assert probe["nRows"] >= 3, (
                    f"the timeline card @320 rendered only {probe['nRows']} inner "
                    "divs — the seeded wide chapters did not flow through"
                )
                # (1) the document itself must not overflow horizontally.
                assert probe["docScrollW"] <= probe["docClientW"] + 1, (
                    f"the #252 timeline card pushed horizontal page overflow @320 "
                    f"(scrollWidth {probe['docScrollW']} > clientWidth "
                    f"{probe['docClientW']}) — the non-wrapping flex row with a "
                    "hard 130px range min-width spilled the 'N prompts' span "
                    "55px off-screen. Restore flex-wrap + the range min-width clamp."
                )
                # (2) no CHILD span may paint past the viewport (the card box itself
                #     stayed within 320, so only a child-escape probe catches this).
                assert not probe["escapees"], (
                    f"a timeline card descendant @320 escapes the viewport "
                    f"(right edge past {width}): {probe['escapees'][:4]} — the "
                    "'N prompts' .meta span overran the card and the screen. This "
                    "is the off-screen-child overflow the card-bounding-box guard "
                    "at 393 is blind to."
                )
                page.close()
            finally:
                browser.close()
    finally:
        httpd.shutdown()
