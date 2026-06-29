"""Real-browser guard: the cross-provider eval leaderboard's per-row JUDGE column
renders the MODEL BRAND (Claude / GPT / Gemini), never the raw dispatch slug
(codex / antigravity).

THE BUG (found Iter 78, reproduced in the rendered DOM): the leaderboard row name
two columns left already brands the provider ("GPT", "Gemini" via target_display)
and the "Most recent run: GPT scored 0.80" line above it is branded too — but the
trailing "judge: …" column rendered `row.judge`, the RAW dispatch slug stored in
each item's judge_provider. So a real run judged by codex/antigravity rendered
"judge: codex" / "judge: antigravity" on the SAME public /stats card that says
"GPT" / "Gemini" everywhere else — the same provider named two ways on one surface.
That's the #275 raw-slug-vs-brand class the council surfaces already closed; the
eval leaderboard judge column was the un-fixed sibling.

The existing string guard (test_eval_leaderboard_disclosure.test_per_row_judge_label_present)
asserted only that "judge:" + "row.judge" appear in the SOURCE — it never rendered a
non-self-branding judge, so it could not catch the slug leak (and the one existing
leaderboard browser test seeds judge_provider="claude", which brands to itself).

FIX: launchpad_data carries a branded `judge_display = provider_model_brand(judge)`
on each comparison row (the slug `judge` stays for any consumer that needs the
dispatchable name), and the template renders `row.judge_display || row.judge`.

This guard seeds TWO scored providers whose judges are codex (→GPT) and antigravity
(→Gemini) — the two slugs that DIFFER from their brand — renders the REAL /stats,
and reads the RENDERED judge column text. Mutation-proven: revert the template to
`row.judge` (or drop the judge_display field) → the column reads "codex"/"antigravity"
→ this reds with the exact symptom.

Slow + browser marked; skips without Playwright/chromium; runs in CI `browser`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _run(
    eval_id: str,
    provider: str,
    model: str,
    score: float,
    axes: dict,
    judge: str,
    self_judge: bool = False,
) -> dict:
    return {
        "target_provider": provider,
        "target_model": model,
        "aggregate_score": score,
        "eval_id": eval_id,
        "items_completed": 12,
        "items_total": 12,
        "items_failed": 0,
        "by_rejection_type": {
            ax: {"count": c, "mean_score": m, "min_score": 0.5, "max_score": 0.95}
            for ax, (m, c) in axes.items()
        },
        # The run's judge — stored as the raw dispatch slug. codex (→GPT) and
        # antigravity (→Gemini) are the two slugs that DIFFER from their brand.
        "items": [{"judge_provider": judge}],
        # Self-judge transparency flag (scorer.self_judge): True when judge slug
        # == target slug. The leaderboard footer's absolute "a model never grades
        # itself" claim is false on such a row.
        "self_judge": self_judge,
        "completed_at": "20260617T120000",
        "started_at": "20260617T120000",
    }


def _render_stats(home: Path, runs: list[dict]) -> Path:
    rd = home / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_personalA.json").write_text(
        json.dumps({"eval_id": "personalA", "items": [{"id": f"i{i}"} for i in range(12)]}),
        encoding="utf-8",
    )
    for i, run in enumerate(runs):
        name = f"eval_{run['eval_id']}__model_{run['target_provider']}__20260617T12000{i}.json"
        (rd / name).write_text(json.dumps(run), encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists()
    return page


def test_leaderboard_judge_column_is_brand_not_slug():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        # claude judged by codex → judge column must read "GPT", not "codex"
        _run("personalA", "claude", "claude-opus-4-8", 0.812,
             {"REFRAME": (0.85, 5), "REDIRECT": (0.78, 4), "SHARPENING": (0.80, 3)}, "codex"),
        # codex judged by antigravity → judge column must read "Gemini", not "antigravity"
        _run("personalA", "codex", "gpt-5.5", 0.799,
             {"COMPRESSION": (0.82, 4), "SHARPENING": (0.77, 4), "REDIRECT": (0.79, 4)}, "antigravity"),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            # 1280 desktop — the .eval-lb-judge column is dropped below 560px
            # (its info moves to the footer), so the judge text is only present
            # to read at desktop width.
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval leaderboard: {errs[:3]}"

            res = page.evaluate(
                """() => {
                  const lb = [...document.querySelectorAll('.eyebrow')]
                    .find(e => /Cross-provider leaderboard/i.test(e.textContent || ''));
                  if (!lb) return {found: false};
                  const card = lb.closest('section');
                  const rows = [...card.querySelectorAll('.eval-lb-row')];
                  const judges = rows.map(li =>
                    [...li.querySelectorAll('.eval-lb-judge')]
                      .map(s => (s.textContent || '').trim())
                      .filter(Boolean).join(' '));
                  return {found: true, rowCount: rows.length, judges,
                          rawLeak: /\\{\\{/.test(card.innerHTML)};
                }"""
            )

            assert res["found"], "the cross-provider leaderboard did not render (need >=2 scored providers)"
            assert res["rowCount"] >= 2, f"expected >=2 leaderboard rows, got {res['rowCount']}"
            assert not res["rawLeak"], "raw {{ }} leaked in the leaderboard card (petite-vue did not mount)"
            judge_blob = " ".join(res["judges"])
            # The judges we seeded brand to GPT (codex) and Gemini (antigravity).
            assert "judge: GPT" in judge_blob, (
                "the leaderboard judge column did not brand codex → 'GPT' — "
                f"rendered judges: {res['judges']!r}"
            )
            assert "judge: Gemini" in judge_blob, (
                "the leaderboard judge column did not brand antigravity → 'Gemini' — "
                f"rendered judges: {res['judges']!r}"
            )
            # The raw dispatch slug must NOT leak into the public judge column —
            # the row name + the 'Most recent run' line two columns over already
            # say 'GPT'/'Gemini'; 'judge: codex'/'judge: antigravity' contradicts
            # them (the #275 raw-slug-vs-brand class on one public surface).
            assert "codex" not in judge_blob, (
                "the leaderboard judge column LEAKED the raw dispatch slug 'codex' — "
                "it must brand to 'GPT' to match the row name (the #275 raw-slug-vs-"
                f"brand class on a public /stats card): {res['judges']!r}"
            )
            assert "antigravity" not in judge_blob, (
                "the leaderboard judge column LEAKED the raw dispatch slug 'antigravity' — "
                "it must brand to 'Gemini' to match the row name (#275): "
                f"{res['judges']!r}"
            )
        finally:
            browser.close()


def test_self_judged_row_marked_and_footer_drops_never_grades_itself_claim():
    """A SELF-JUDGED leaderboard row (judge slug == target slug, e.g.
    `eval-run --target claude --judge claude`) must render a "(self)" marker AND
    the footer must DROP its absolute "a model never grades itself" claim — else
    the public /stats card asserts the opposite of what its own "judge: Claude"
    row two columns left already shows.

    THE BUG (found Iter 121, reproduced in the rendered DOM): the leaderboard data
    builder read each run's `judge` but ignored the persisted `self_judge` flag, and
    the footer unconditionally said "Judges are rotated (a model never grades
    itself)." A user can force `eval-run --target claude --judge claude` (which the
    CLI explicitly allows and discloses as "self-judge — same family as target");
    that run then appears as the rank-1 winner reading "1. Claude … judge: Claude"
    while the footer claimed no model grades itself — a direct contradiction on a
    journalist-screenshottable card. The CLI surface disclosed this; the web
    leaderboard hid it and overclaimed the opposite (the green-while-degenerate /
    overclaim-on-public-surface class).

    FIX: launchpad_data carries `self_judge` per comparison row + an aggregate
    `any_self_judge`; the template appends a "(self)" tag on a self-judged row's
    judge cell and gates the "never grades itself" footer on `!any_self_judge`,
    disclosing the self-judge relationship otherwise (mirrors the CLI note).

    Mutation-proven: revert the footer to the unconditional claim (or drop the
    self_judge row field) → the footer keeps "a model never grades itself" beside a
    "judge: Claude (self)" row → this reds with the exact symptom.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        # claude judged by claude → SELF-JUDGE, the rank-1 winner. judge column
        # must read "Claude (self)" and the footer must NOT claim no model grades
        # itself.
        _run("personalA", "claude", "claude-opus-4-8", 0.842,
             {"REFRAME": (0.88, 5), "REDIRECT": (0.80, 4), "SHARPENING": (0.84, 3)},
             "claude", self_judge=True),
        # codex judged by claude → cross-judged (a clean second row, so the
        # leaderboard renders with >=2 rows).
        _run("personalA", "codex", "gpt-5.5", 0.781,
             {"COMPRESSION": (0.80, 4), "SHARPENING": (0.76, 4), "REDIRECT": (0.78, 4)},
             "claude", self_judge=False),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval leaderboard: {errs[:3]}"

            res = page.evaluate(
                """() => {
                  const lb = [...document.querySelectorAll('.eyebrow')]
                    .find(e => /Cross-provider leaderboard/i.test(e.textContent || ''));
                  if (!lb) return {found: false};
                  const card = lb.closest('section');
                  const rows = [...card.querySelectorAll('.eval-lb-row')];
                  // The winner (rank 1) is the self-judged claude row.
                  const topJudge = rows.length
                    ? [...rows[0].querySelectorAll('.eval-lb-judge')]
                        .map(s => (s.textContent || '').trim()).filter(Boolean).join(' ')
                    : '';
                  // The footer judge-policy <p> (the one that names eval-run).
                  const footers = [...card.querySelectorAll('p.meta')]
                    .map(p => (p.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(t => /most-recent/.test(t) && /eval-run/.test(t));
                  return {
                    found: true,
                    rowCount: rows.length,
                    topJudge,
                    footer: footers[0] || '',
                    rawLeak: /\\{\\{/.test(card.innerHTML),
                  };
                }"""
            )

            assert res["found"], "the cross-provider leaderboard did not render (need >=2 scored providers)"
            assert res["rowCount"] >= 2, f"expected >=2 leaderboard rows, got {res['rowCount']}"
            assert not res["rawLeak"], "raw {{ }} leaked in the leaderboard card (petite-vue did not mount)"

            # (1) the self-judged winner row must carry the "(self)" marker beside
            #     its judge label — without it the row reads as a clean cross-judged
            #     result.
            assert "(self)" in res["topJudge"], (
                "the self-judged winner row did NOT render the '(self)' marker on its "
                "judge column — it reads as a clean cross-judged result while a model "
                f"graded its own family. Rendered judge cell: {res['topJudge']!r}"
            )

            # (2) THE BITE — the footer must NOT make the absolute "a model never
            #     grades itself" claim when a self-judged row is on the board. That
            #     line contradicts the very 'judge: Claude (self)' row it sits under
            #     (overclaim-on-a-public-card; the CLI eval-run already discloses the
            #     self-judge case).
            assert "never grades itself" not in res["footer"], (
                "the eval leaderboard footer still claims 'a model never grades itself' "
                "while a SELF-JUDGED row (judge slug == target slug) is on the board — "
                "the public /stats card asserts the opposite of what its own "
                f"'judge: Claude (self)' winner row shows. Footer: {res['footer']!r}"
            )
            # And it must positively disclose the self-judge relationship (the honest
            # replacement, mirroring the CLI note).
            assert "(self)" in res["footer"], (
                "the eval leaderboard footer dropped the 'never grades itself' claim "
                "but failed to DISCLOSE the self-judge relationship — the user is left "
                f"with no explanation of the '(self)' tag. Footer: {res['footer']!r}"
            )
        finally:
            browser.close()


@pytest.mark.parametrize("width", [375, 320])
def test_self_judge_marker_survives_the_mobile_reflow(width: int):
    """At phone widths (<560px) the leaderboard drops the wide ".eval-lb-judge"
    column to keep the score BAR visible — but that column carried the ONLY
    "(self)" marker. So on a phone a SELF-JUDGED winner row showed NO "(self)"
    tag while the footer still said "a row tagged (self) was graded by its own
    model family" — the footer referenced a tag NO ROW displayed, leaving a phone
    reader unable to tell the WINNING row (the 0.88 headline of the whole "YOUR
    BENCHMARK" card) was self-graded.

    This is the mobile-reflow hole in the iter-446/448 self-judge disclosure class:
    the per-row transparency marker vanished at the exact widths a phone reader
    sees, defeating the disclosure on a public, screenshottable /stats card.

    FIX: a compact ".eval-lb-self-compact" "(self)" flag rides the NAME cell
    (the column that survives the reflow) — display:none on desktop (the judge
    column already shows "(self)" there, no double-tag), display:inline below
    560px. So the self-judge case stays IDENTIFIABLE at every width.

    Mutation-proven: drop the ".eval-lb-self-compact { display: inline; }" rule
    from the <560px media block (or the name-cell flag span) → at 375/320 NO
    "(self)" marker is visible on any row while the judge column is hidden and the
    footer still references "(self)" → this reds with the exact founder symptom
    ("the disclosure points at a tag no row shows on a phone").
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    runs = [
        # claude judged by claude → SELF-JUDGE, the rank-1 winner (0.842).
        _run("personalA", "claude", "claude-opus-4-8", 0.842,
             {"REFRAME": (0.88, 5), "REDIRECT": (0.80, 4), "SHARPENING": (0.84, 3)},
             "claude", self_judge=True),
        # codex cross-judged → a clean second row so the leaderboard renders >=2.
        _run("personalA", "codex", "gpt-5.5", 0.781,
             {"COMPRESSION": (0.80, 4), "SHARPENING": (0.76, 4), "REDIRECT": (0.78, 4)},
             "claude", self_judge=False),
    ]
    page_path = _render_stats(home, runs)

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": width, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)
            assert not errs, f"JS errors rendering the eval leaderboard: {errs[:3]}"

            res = page.evaluate(
                """() => {
                  const visible = (el) => {
                    if (!el) return false;
                    const cs = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return cs.display !== 'none' && cs.visibility !== 'hidden'
                           && r.width > 0 && r.height > 0;
                  };
                  const lb = [...document.querySelectorAll('.eyebrow')]
                    .find(e => /Cross-provider leaderboard/i.test(e.textContent || ''));
                  if (!lb) return {found: false};
                  const card = lb.closest('section');
                  const rows = [...card.querySelectorAll('.eval-lb-row')];
                  // Precondition: we must actually be in the mobile reflow where the
                  // wide judge column is dropped — otherwise this guard is vacuous.
                  const judgeCells = [...card.querySelectorAll('.eval-lb-judge')];
                  const judgeColVisible = judgeCells.some(visible);
                  // The winner (rank 1) is the self-judged claude row. Is ANY
                  // "(self)" marker actually VISIBLE on it at this width? Check the
                  // LEAF element that OWNS the "(self)" text — not a parent whose
                  // textContent merely bubbles a display:none child's text (the
                  // name cell's textContent includes the hidden compact flag, which
                  // would make a naive textContent check non-discriminating: it
                  // passes even when every "(self)"-bearing element is hidden).
                  const ownsSelf = (el) =>
                    /\\(self\\)/.test(el.textContent || '') &&
                    ![...el.children].some(c => /\\(self\\)/.test(c.textContent || ''));
                  const winnerSelfVisible = rows.length
                    ? [...rows[0].querySelectorAll('*')]
                        .some(el => ownsSelf(el) && visible(el))
                    : false;
                  // The footer still references the "(self)" tag.
                  const footers = [...card.querySelectorAll('p.meta')]
                    .map(p => (p.textContent || '').replace(/\\s+/g, ' ').trim())
                    .filter(t => /most-recent/.test(t) && /eval-run/.test(t));
                  return {
                    found: true,
                    rowCount: rows.length,
                    judgeColVisible,
                    winnerSelfVisible,
                    footerRefsSelf: /\\(self\\)/.test(footers[0] || ''),
                    rawLeak: /\\{\\{/.test(card.innerHTML),
                    docOverflow: document.documentElement.scrollWidth
                                 > document.documentElement.clientWidth,
                  };
                }"""
            )

            assert res["found"], "the cross-provider leaderboard did not render (need >=2 scored providers)"
            assert res["rowCount"] >= 2, f"expected >=2 leaderboard rows, got {res['rowCount']}"
            assert not res["rawLeak"], "raw {{ }} leaked in the leaderboard card (petite-vue did not mount)"
            assert not res["docOverflow"], (
                f"the /stats page overflowed horizontally at {width}px (the bar/grid "
                "reflow regressed)"
            )
            # PRECONDITION (no vacuous pass): we must be in the mobile reflow where
            # the wide judge column — the marker's desktop home — is hidden.
            assert not res["judgeColVisible"], (
                f"at {width}px the wide '.eval-lb-judge' column is still visible — the "
                "mobile reflow this guard targets did not engage, so the assertion below "
                "would be vacuous (the desktop test already covers that width)."
            )
            # PRECONDITION: the footer still references the "(self)" tag, so a missing
            # row marker leaves a DANGLING reference (the actual founder symptom).
            assert res["footerRefsSelf"], (
                "the footer no longer references the '(self)' tag — the dangling-"
                "reference symptom this guard reproduces is not present; re-check the seed."
            )
            # THE BITE — a "(self)" marker must be VISIBLE on the self-judged winner
            # row at phone width, or the footer's "a row tagged (self)…" disclosure
            # dangles: it names a tag no row shows, so a phone reader can't tell the
            # 0.88 headline winner was self-graded (iter-446/448 disclosure-inversion
            # class, defeated by the <560px reflow).
            assert res["winnerSelfVisible"], (
                f"at {width}px NO '(self)' marker is visible on the self-judged WINNER "
                "row while the judge column is dropped and the footer still references "
                "'(self)' — the self-judge disclosure dangles on a phone: it points at a "
                "tag no row displays, so the reader can't identify which row (the public "
                "/stats card hides that its 0.88 headline winner graded its own family)."
            )
        finally:
            browser.close()
