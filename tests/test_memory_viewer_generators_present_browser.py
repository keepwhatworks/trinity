"""Real-browser guard: the generators.md PRESENT view renders the lens "lift" CARDS
as structured HTML (not a raw-markdown dump) and paints clean at narrow width.

generators.md is the OPTIONAL on-demand tier (written by `lens-generators`); its nav
tab is hidden until built. The ABSENT empty-state is guarded by
test_memory_viewer_absent_optional_file_browser. The markdown-BODY structure guard
(test_memory_viewer_markdown_structure_browser) seeds a synthetic lens.md — it never
exercises the ACTUAL generators-card shape (`render_generators_cards`: `## Generators`
+ `### N. <imperative>` + `- **domain** — example` bullets + an italic
`*Projects task-tensions: …*`). So the one view a user reaches AFTER running the
multi-minute `lens-generators` lift had no real-browser render guard.

The failure shape this bites: `marked` fails to load (vendor 404 / a future vendor
rename) → renderMarkdown falls to `el("pre","body", mdText)` — a raw `<pre>` dump of
the literal `## ` / `### ` / `**domain**` markdown. That `<pre>` is 1000+ chars, so a
naive length check stays GREEN while the user reads raw markdown syntax. This guard
asserts the cards rendered as REAL elements (h2/h3/strong/li, no literal markdown in
the rendered text) at 1280 AND 375, with no document h-overflow.

Seeds generators.md from the REAL `render_generators_cards` (so the guard tracks the
product's actual emitted shape, not a hand-written fixture — a change to the card
renderer that breaks the view is caught), renders via the production `portal-html`
CLI (which publishes vendor/marked.min.js — asserted present, else this guard would
pass for the wrong reason).

Mutation-proven 2026-06-17: delete `window.marked` before render (the genuine
marked-failed mode, via add_init_script — no source edit) → renderMarkdown takes the
raw-`<pre>` branch → the structure asserts (h2>=2, h3>=2, strong>=2, li>=2,
rawLeak==[]) RED with the exact "raw markdown leaked into the generators view" symptom.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _generators_md() -> str:
    """The REAL card markdown the `lens-generators` lift writes to generators.md."""
    sys.path.insert(0, str(REPO / "src"))
    from trinity_local.me.generators import render_generators_cards

    gens = [
        {
            "name": "green-gate honesty",
            "imperative": "Refuse the green check that attests a proxy instead of the invariant it claims",
            "tension": "surface-pass vs invariant-attestation",
            "projections": {
                "software": "a `len(out) > 60` test stays green on a raw <pre> dump that lost every heading",
                "materials": "a tensile spec certifies the coupon average while the failure lives in the weld variance",
                "finance": "a backtest Sharpe greens on survivorship-filtered tickers, a proxy for the real universe",
            },
            "task_tensions": [1, 4, 7],
        },
        {
            "name": "data sampling",
            "imperative": "Sample the real distribution, not the aggregate — eyeball the raw rows",
            "tension": "coverage/skew vs the mean",
            "projections": {
                "software": "the corpus is 86% one batch sweep; the mean prompt-length hides the moat tail",
                "finance": "portfolio beta reads 1.0 while two offsetting concentrated bets carry all the tail risk",
            },
            "task_tensions": [2, 9],
        },
    ]
    return render_generators_cards(gens)


def _seed_and_render(home: Path, *, lens_md: str | None = None) -> Path:
    mem = home / "memories"
    mem.mkdir(parents=True)
    (mem / "generators.md").write_text(_generators_md(), encoding="utf-8")
    # Minimal companions so the nav is populated and portal-html doesn't bail.
    (home / "core.md").write_text("# Core\nidentity paragraph\n", encoding="utf-8")
    # `lens_md=""` seeds the GENERATORS-ONLY edge state (lens re-extracted to
    # empty while an older generators.md persists). Default = a real tension.
    if lens_md is None:
        lens_md = "# /me\n### 1. ship-speed ↔ verified-correctness\n"
    (mem / "lens.md").write_text(lens_md, encoding="utf-8")
    (mem / "topics.json").write_text('{"basins": []}', encoding="utf-8")
    (mem / "vocabulary.md").write_text("# Vocabulary\n\n## Anchors\n- ship\n", encoding="utf-8")

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    mv = home / "portal_pages" / "memory.html"
    assert mv.exists(), "portal-html didn't write memory.html"
    assert (home / "portal_pages" / "vendor" / "marked.min.js").exists(), (
        "vendored marked.min.js missing — renderMarkdown would fall to the raw <pre> "
        "fallback, so this guard would pass for the wrong reason"
    )
    return mv


_PROBE = """() => {
  const md = document.querySelector('.markdown-body');
  if (!md) return {nomd: true};
  const n = (s) => md.querySelectorAll(s).length;
  const txt = md.innerText || '';
  // Literal markdown syntax surviving into the RENDERED text = the raw <pre>
  // fallback (marked failed) or a renderer regression.
  const rawLeak = [];
  if (/(^|\\n)#{2,3} /.test(txt)) rawLeak.push('heading-hash');
  if (/\\*\\*[A-Za-z]/.test(txt)) rawLeak.push('bold-stars');
  // Page-level h-overflow (a <pre> may scroll INSIDE its own container; the page
  // itself must not get a horizontal scrollbar).
  const docOverflow = document.documentElement.scrollWidth
                    - document.documentElement.clientWidth;
  return {
    h2: n('h2'), h3: n('h3'), strong: n('strong'), em: n('em'), li: n('li'),
    rawLeak, docOverflow, txtLen: txt.length,
  };
}"""


@pytest.mark.parametrize("width", [1280, 375])
def test_generators_present_renders_cards_not_raw_markdown(tmp_path, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    mv = _seed_and_render(home)

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{mv}?file=generators.md", wait_until="load")
            page.wait_for_timeout(700)

            info = page.evaluate(_PROBE)
            assert info is not None
            if info.get("nomd"):
                failures.append("no .markdown-body rendered for generators.md (PRESENT)")
            else:
                h2 = int(info.get("h2") or 0)
                h3 = int(info.get("h3") or 0)
                strong = int(info.get("strong") or 0)
                li = int(info.get("li") or 0)
                raw_leak = info.get("rawLeak") or []
                doc_overflow = float(info.get("docOverflow") or 0)
                # The two generators (### 1./### 2.) + the `## Generators` heading.
                if h3 < 2:
                    failures.append(f"generators cards didn't render as headings (h3={h3}, want >=2) — raw markdown leaked")
                if h2 < 1:
                    failures.append(f"the `## Generators` section heading didn't render (h2={h2}, want >=1)")
                if strong < 2:
                    failures.append(f"the `- **domain**` projection bullets didn't bold (strong={strong}, want >=2)")
                if li < 2:
                    failures.append(f"the projection bullets didn't render as list items (li={li}, want >=2)")
                if raw_leak:
                    failures.append(f"raw markdown leaked into the generators view (the marked-failed raw <pre> dump): {raw_leak}")
                if doc_overflow > 1:
                    failures.append(f"generators.md @{width} overflows the document horizontally by {doc_overflow}px")
            if errs:
                failures.append(f"JS errors rendering generators.md: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        f"generators.md PRESENT view regressed @{width}:\n  " + "\n  ".join(failures)
    )


def test_generators_only_empty_lens_still_shows_tab_and_renders_cards(tmp_path):
    """GENERATORS-ONLY edge state: generators.md is fully populated but lens.md is
    EMPTY (the user re-extracted their lens — lens.md went to "" / "no tensions
    yet" — while an older generators.md persists on disk). The standard seed never
    produces this shape, and the sibling generators-present guard seeds a NON-empty
    lens.md, so this cell (the lift surviving an emptied lens) had no browser guard.

    Two invariants this pins:
      1. the generators.md nav tab stays VISIBLE — _visible_files keys tab presence
         on the FILE EXISTING, not on the lens being built — so a re-extracted lens
         doesn't strand the lift behind a hidden tab the user can't reach;
      2. the generators VIEW still paints CARDS (not the "Not built yet" empty-state
         and not a hollow header) — renderMarkdown reads generators.md directly,
         independent of lens.md's state.

    Founder symptom this reds with: a user runs `lens` to refresh, lens.md is briefly
    empty, and their generators tab VANISHES or paints a void — "the lift disappeared
    the moment I rebuilt my lens." Mutation target: gating the generators tab/view on
    lens-built state instead of generators.md existence.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = tmp_path / "trinity"
    home.mkdir()
    # The DISCRIMINATING seed: generators.md populated, lens.md EMPTY.
    mv = _seed_and_render(home, lens_md="")

    # Precondition B, checked RENDER-INDEPENDENTLY on the fixture (not the DOM):
    # lens.md is empty AND generators.md carries real cards. So a generators tab
    # or card here can ONLY be driven by generators.md, never by lens content.
    lens_text = (home / "memories" / "lens.md").read_text(encoding="utf-8")
    gen_text = (home / "memories" / "generators.md").read_text(encoding="utf-8")
    assert lens_text.strip() == "", "fixture not discriminating: lens.md must be empty"
    assert "### 1." in gen_text and "## Generators" in gen_text, (
        "fixture not discriminating: generators.md must carry real cards"
    )

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{mv}?file=generators.md", wait_until="load")
            page.wait_for_timeout(700)

            # Precondition A: the view PAINTS — a .markdown-body mounted and there
            # is no raw petite-vue mustache leak (an un-mounted page would make the
            # card asserts non-bite).
            body_text = page.inner_text("body")
            if "{{" in body_text or "}}" in body_text:
                failures.append("raw template mustache leaked — page did not mount")

            # Invariant 1: the generators.md nav tab is present even with empty lens.
            tab_present = page.evaluate(
                "() => Array.from(document.querySelectorAll('a')).some(a => "
                "(a.getAttribute('href')||'').includes('generators.md'))"
            )
            if not tab_present:
                failures.append(
                    "the generators.md nav tab VANISHED when lens.md is empty — the "
                    "lift got stranded behind a hidden tab the moment the lens was "
                    "re-extracted (tab visibility must key on the file existing)"
                )

            info = page.evaluate(_PROBE)
            if info.get("nomd"):
                failures.append(
                    "generators.md PAINTED THE EMPTY-STATE/VOID with empty lens.md — "
                    "the lift view must render from generators.md regardless of lens state"
                )
            else:
                h3 = int(info.get("h3") or 0)
                raw_leak = info.get("rawLeak") or []
                if h3 < 2:
                    failures.append(
                        f"generators cards didn't render with empty lens (h3={h3}, want >=2)"
                    )
                if raw_leak:
                    failures.append(f"raw markdown leaked in the generators-only view: {raw_leak}")

            # The empty-state copy must NOT be on the generators view here.
            if "Not built yet" in body_text and "trinity-local lens-generators" in body_text:
                failures.append(
                    "generators view showed 'Not built yet — run lens-generators' "
                    "despite a fully-populated generators.md (empty lens.md must not "
                    "suppress the lift)"
                )
            if errs:
                failures.append(f"JS errors rendering generators-only view: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "GENERATORS-ONLY (populated generators.md + EMPTY lens.md) regressed:\n  "
        + "\n  ".join(failures)
    )
