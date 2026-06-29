"""Real-browser guard: the /stats browser-capture per-provider rows keep their
distribution BAR visible AND don't clip the "N unsynced" sync-diff pill at phone
widths — AND read the MODEL BRAND (Claude / GPT / Gemini), never the raw lowercase
capture-source directory slug.

Brand-label half (Iter 136): the capture rows iterate ``conversations/<slug>/`` dir
names — the web-era capture slugs ``claude`` / ``chatgpt`` / ``gemini``. The row used
to render ``{{ row.provider }}`` RAW, leaking a lowercase ``chatgpt`` / ``gemini``
while every other launchpad surface (the routing table, the Elo chart, the eval
cards — #275) read the brand trio via ``formatProviderLabel`` (which folds
chatgpt→codex→GPT, gemini→antigravity→Gemini, claude→Claude). The fix routes the
cell through ``formatProviderLabel(row.provider)``; this guard asserts the rendered
first cell of each row is the brand, never the raw slug. Mutation-proven: revert to
``{{ row.provider }}`` → the labels render ``['claude', 'chatgpt']`` and the
``'GPT' in labels`` assertion reds with that exact symptom.

The "Browser capture" card lists one row per provider:
``provider · count · BAR(1fr) · "N unsynced"``. The desktop row is a four-column
grid ``140px 60px 1fr 110px``. The fixed columns (140+60+110 + 3×8px gaps ≈ 334px)
eat the whole row, so at 375/393 the 1fr distribution BAR collapsed to **0px** AND
the right-edge "N unsynced" pill clipped **past the viewport** (the page itself
overflowed: docScroll 382 > 375 — the founder mobile-grid-clip shape, at element
level). The sibling of the eval-leaderboard fixed-grid-eats-the-1fr-bar class
(Iter 32) — same root cause, never generalized to this row.

Fix: the row uses a ``.bc-provider-row`` class; below 480px the grid tightens its
fixed columns (minmax-bounded) so the BAR keeps width and the row never exceeds the
viewport (the pill keeps its title tooltip).

This guard seeds real captures + a ``_sidebar.json`` listing MORE threads than on
disk (so missing_count > 0 → the "N unsynced" pill renders), renders the REAL
/stats, and at 375px asserts every provider bar has a non-zero rendered width AND
no element in the card clips past the viewport. Mutation-proven: revert the
``.bc-provider-row`` mobile grid (back to the fixed ``140px 60px 1fr 110px``) →
the bar width is 0 and the pill overflows the viewport → this reds with the exact
symptom.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _seed_captures(home: Path) -> None:
    """3 captured claude threads + a sidebar listing 7 → missing_count=4 (pill shows)."""
    conv = home / "conversations"
    (conv / "claude").mkdir(parents=True, exist_ok=True)
    (conv / "chatgpt").mkdir(parents=True, exist_ok=True)
    now = time.time()  # fresh mtimes → not "stale" (keeps the card in the normal path)
    for i in range(3):
        p = conv / "claude" / f"convclaude{i:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now, now))
    for i in range(2):
        p = conv / "chatgpt" / f"convgpt{i:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (now, now))
    (conv / "claude" / "_sidebar.json").write_text(
        json.dumps(
            {
                "url": "https://claude.ai/api/organizations/abc123def4567890/x",
                "sidebar": {
                    "data": [{"uuid": f"convclaude{i:02d}"} for i in range(3)]
                    + [{"uuid": f"missingclaude{i:02d}"} for i in range(4)]
                },
            }
        ),
        encoding="utf-8",
    )
    (conv / "chatgpt" / "_sidebar.json").write_text(
        json.dumps({"sidebar": {"items": [{"id": f"convgpt{i:02d}"} for i in range(2)]}}),
        encoding="utf-8",
    )


def _render_stats(home: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


def test_browser_capture_row_bar_and_pill_fit_at_phone_width():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    _seed_captures(home)
    page_path = _render_stats(home)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 375, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            # The browser-capture card lives inside a demoted <details>; on /stats it
            # opens by default, but force it open so the rows are laid out.
            page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
            page.wait_for_timeout(200)
            assert not errs, f"JS errors rendering the browser-capture card: {errs[:3]}"

            res = page.evaluate(
                """(vw) => {
                  const card = document.querySelector('.browser-capture-card');
                  if (!card) return {found: false};
                  const rows = [...card.querySelectorAll('.bc-provider-row')];
                  const barWidths = rows.map(li => {
                    const bar = li.querySelector('span[style*="position: relative"]');
                    return bar ? Math.round(bar.getBoundingClientRect().width) : -1;
                  });
                  // First cell of each row = the provider LABEL. It must read the
                  // model BRAND (Claude / GPT / Gemini), folded from the web-era
                  // capture-source dir slug, NOT the raw lowercase directory name.
                  const providerLabels = rows.map(li => {
                    const cell = li.querySelector(':scope > span:first-child');
                    return cell ? (cell.textContent || '').trim() : '';
                  });
                  const pill = [...card.querySelectorAll('span')]
                    .find(s => /unsynced/.test(s.textContent || ''));
                  // any element in the card clipping past the viewport (the pill overflow)?
                  let clip = null;
                  for (const el of card.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.right > vw + 1.5) {
                      clip = {right: Math.round(r.right), text: (el.textContent || '').trim().slice(0, 30)};
                      break;
                    }
                  }
                  return {
                    found: true,
                    rowCount: rows.length,
                    barWidths,
                    providerLabels,
                    pillPresent: !!pill,
                    clip,
                    rawLeak: /\\{\\{/.test(card.innerText || ''),
                  };
                }""",
                375,
            )

            assert res["found"], "the browser-capture card did not render (need has_data captures)"
            assert res["rowCount"] >= 2, f"expected >=2 provider rows, got {res['rowCount']}"
            assert res["pillPresent"], (
                "the 'N unsynced' sync-diff pill did not render — the missing_count>0 "
                "seed should surface it (precondition for the overflow check)"
            )
            assert not res["rawLeak"], "raw {{ }} leaked in the browser-capture card (petite-vue did not mount)"
            # #275 brand-label class: the capture rows must read the MODEL BRAND
            # (Claude / GPT / Gemini), folded through formatProviderLabel from the
            # web-era capture-source directory slug (claude / chatgpt / gemini) —
            # NEVER the raw lowercase dir name leaked straight from the filesystem.
            # The seed captures into conversations/claude (→ "Claude") and
            # conversations/chatgpt (→ normalize→codex→ "GPT").
            labels = res["providerLabels"]
            assert "GPT" in labels, (
                "the browser-capture card leaked the raw capture-source slug instead "
                "of the model brand — a 'chatgpt' capture dir must render as 'GPT' "
                f"(the #275 brand-trio), got {labels!r}"
            )
            assert "Claude" in labels, (
                f"the 'claude' capture dir must render as the brand 'Claude', got {labels!r}"
            )
            _RAW_CAPTURE_SLUGS = {"chatgpt", "claude", "gemini", "claude_ai", "codex", "antigravity"}
            leaked = [lbl for lbl in labels if lbl in _RAW_CAPTURE_SLUGS]
            assert not leaked, (
                "the browser-capture card rendered a RAW lowercase capture-source "
                f"directory slug {leaked!r} instead of the model brand — "
                "formatProviderLabel(row.provider) was bypassed (the #275 raw-slug leak)"
            )
            assert res["clip"] is None, (
                "a browser-capture row element clips past the 375px viewport — the "
                "right-edge 'N unsynced' sync-diff pill overflows the row (the page "
                f"itself scrolls horizontally on a phone): {res['clip']}"
            )
            for w in res["barWidths"]:
                assert w > 8, (
                    "the browser-capture per-provider distribution BAR collapsed to "
                    f"{w}px at 375px — the four-column grid's fixed columns ate the 1fr "
                    "bar, so the per-provider capture distribution is INVISIBLE on a phone"
                )
        finally:
            browser.close()
