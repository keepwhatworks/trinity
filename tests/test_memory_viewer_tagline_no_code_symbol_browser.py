"""Regression: the memory viewer's per-file TAGLINE (the `.meta` line painted under
each file's header — the first product prose a user reads about what a memory file
IS) must not leak a raw snake_case JSON key / code symbol into user-facing English.

Found 2026-06-20 by a code-symbol prose sweep of the rendered viewer: the picks.json
tab tagline read

    "Extracted model-selection rules per task_type. Written by consolidate; read by
     ask + chairman picker."

`task_type` is the snake_case JSON key of the routing schema — a raw code symbol
sitting in the middle of plain-English prose. Its own SIBLING tagline (routing.json)
already wrote the same concept as plain English ("Per-task-type provider track
record"), so the picks tagline was the lone un-branded outlier. CLAUDE.md's naming
rule is explicit: slugs / JSON keys stay in code / config / paths / literal CLI
commands; user-facing UI uses plain words. Same code-symbol-in-prose class as the
launchpad council card's `why_matters` leak (UX sweep iter 164).

Fix: "per task_type" -> "per task type" (memory_viewer.py ALLOWED_FILES). This guard
pins the WHOLE CLASS — it drives the REAL viewer, finds the painted tagline `.meta`
line under EVERY visible file header, and asserts none contains a snake_case
identifier (not just `task_type`). The Reader/Raw-JSON BODY is exempt (a Raw JSON view
legitimately shows the literal file with its real keys); only the human-written
tagline prose is scanned.

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# A snake_case identifier: a lowercase word, then >=1 `_word` chunk (e.g. task_type,
# why_matters, n_episodes). Two ASCII letter-runs joined by an underscore. This is the
# same _SNAKE shape the council-card prose guard uses.
_SNAKE = re.compile(r"\b[a-z]+_[a-z][a-z_]*\b")

# The four THINKING memories + the two scoreboards always render (generators is
# optional / hidden when absent). Seed picks.json + routing.json so the scoreboard
# taglines — the ones that carry the routing-schema concept the leak lived in — paint.
_PICKS = {
    "b00": {"winner": "claude", "count": 9, "margin": 0.42,
            "n_episodes": 9, "evidence": ["council_syn00"]},
}
_ROUTING = {
    "by_task_type": {
        "design": {"claude": {"n": 2, "overall": 8.0, "wins": 2},
                   "codex": {"n": 1, "overall": 5.0, "wins": 0}},
    },
    "best_per_task_type": {"design": "claude"},
    "pick_is_tie": {},
    "computed_at": "2026-06-20T00:00:00",
}


def _render_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(json.dumps(_PICKS), encoding="utf-8")
    (home / "scoreboard" / "routing.json").write_text(json.dumps(_ROUTING), encoding="utf-8")
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "memories" / "lens.md").write_text(
        "# Lens\n\n## Tensions\n\n- **concrete vs abstract**: leans concrete\n",
        encoding="utf-8",
    )
    (home / "memories" / "topics.json").write_text(
        json.dumps({"basins": [{"id": "b00", "label": "Design",
                                "top_terms": ["design"], "size": 5}]}),
        encoding="utf-8",
    )
    (home / "memories" / "vocabulary.md").write_text(
        "# Vocabulary\n\n## Anchors\n- ship\n", encoding="utf-8"
    )
    (home / "core.md").write_text(
        "# Core\n\nYou prefer concrete, action-first answers.\n", encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the tagline-prose test: {exc}")
    return sp, browser


# The visible file tabs whose tagline prose must read as plain English. (generators.md
# is optional + absent in this seed, so it never paints — not driven here.)
_FILES = ["core.md", "lens.md", "topics.json", "vocabulary.md", "picks.json", "routing.json"]


def test_viewer_taglines_have_no_code_symbol_leak():
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    leaks: list[tuple[str, str, list[str]]] = []
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        for fname in _FILES:
            page.goto(f"file://{pages / 'memory.html'}?file={fname}")
            page.wait_for_timeout(700)
            # The tagline is the `.meta` paragraph inside the content header — painted
            # as `brain · tagline`. Grab its rendered text (the BITE precondition:
            # it must actually be visible, not a vacuous empty string).
            tagline = page.evaluate(
                """() => {
                  const p = document.querySelector('.content-header p.meta');
                  return p && p.offsetParent !== null ? p.textContent : null;
                }"""
            )
            assert tagline and len(tagline) > 10, (
                f"the {fname} header tagline didn't paint (offsetParent null / empty) — "
                f"can't assert prose cleanliness on a tagline that isn't visible: {tagline!r}"
            )
            found = _SNAKE.findall(tagline)
            if found:
                leaks.append((fname, tagline, found))
    finally:
        browser.close()
        sp.stop()

    assert not leaks, (
        "REGRESSION: a memory-viewer file TAGLINE leaked a raw snake_case JSON key / "
        "code symbol into user-facing prose (the `task_type`-in-prose class). "
        "User-facing UI must use plain English; slugs/JSON keys stay in code. Leaks: "
        + "; ".join(f"{f}: {sym} in {txt!r}" for f, txt, sym in leaks)
    )


def test_vocabulary_tagline_names_its_runnable_verb_not_a_phase_number():
    """The vocabulary.md tagline's PROVENANCE clause must name the dedicated runnable
    verb (`vocabulary`), not a bare internal pipeline PHASE NUMBER the user can't run.

    Found 2026-06-21 (UX sweep iter 194): the vocabulary.md tab tagline read

        "Anchors (proper nouns) + homonyms + synonyms. Written by dream Phase 2.5."

    Every SIBLING tagline names a verb a user can actually run to (re)build that file
    — lens.md "Written by lens", generators.md "Written by lens-generators",
    picks.json "Written by consolidate". vocabulary.md was the lone outlier: it named
    the heavyweight umbrella verb (`dream`) plus the opaque internal phase number
    "Phase 2.5" — which maps to NO runnable command — while hiding the dedicated
    lightweight `trinity-local vocabulary` verb (commands/vocabulary.py) that rebuilds
    ONLY this file. A user wanting to refresh their vocabulary read a phase number they
    couldn't act on instead of the one-word verb that does exactly that. Same
    lead-with-the-answer / unclear-provenance class as the picks.json `task_type` leak
    (iter 165) above. Fix: "Written by dream Phase 2.5." -> "Written by vocabulary
    (also runs inside dream)." (memory_viewer.py ALLOWED_FILES).
    """
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file=vocabulary.md")
        page.wait_for_timeout(700)
        tagline = page.evaluate(
            """() => {
              const p = document.querySelector('.content-header p.meta');
              return p && p.offsetParent !== null ? p.textContent : null;
            }"""
        )
    finally:
        browser.close()
        sp.stop()

    # BITE precondition: the tagline must actually be painted, else the assertion below
    # is vacuous.
    assert tagline and len(tagline) > 10, (
        "the vocabulary.md header tagline didn't paint (offsetParent null / empty) — "
        f"can't assert provenance copy on a tagline that isn't visible: {tagline!r}"
    )

    # The provenance clause must NAME THE RUNNABLE VERB so the user can act on it.
    assert "vocabulary" in tagline.lower(), (
        "REGRESSION: the vocabulary.md tagline's provenance clause doesn't name its "
        "dedicated runnable verb `vocabulary` — a user can't tell which command "
        f"rebuilds this file. Tagline painted: {tagline!r}"
    )
    # It must NOT lead the provenance on a bare internal phase number the user can't run
    # (the "dream Phase 2.5" outlier). Every sibling tagline names a verb, not a phase.
    assert "Phase 2.5" not in tagline, (
        "REGRESSION: the vocabulary.md tagline names the internal pipeline phase number "
        "'Phase 2.5' — an opaque sub-step that maps to NO runnable command — instead of "
        "the dedicated `vocabulary` verb. Name the verb the user can run, not the phase "
        f"number. Tagline painted: {tagline!r}"
    )


def test_picks_tagline_describes_basin_keying_not_stale_task_type():
    """The picks.json tab tagline must describe picks.json's ACTUAL post-#298 shape —
    a per-lens-BASIN chairman-winner tally — not the pre-#298 "per task type" framing
    that the data no longer matches.

    Found 2026-06-21 (UX sweep iter 216) by driving the REAL picks.json reader at 320px:
    the tab tagline (the `.meta` line under the file header) read

        "Extracted model-selection rules per task type. Written by consolidate; read
         by ask + chairman picker."

    but the rows the reader paints DIRECTLY BELOW it are keyed by lens BASIN — "b00 ·
    Design", "b01 · Debug" (the `renderPicksReader` docstring: "the flat lens-basin
    tally {basin_id: {winner, count, margin, ...}}"). Post the cortex collapse (#298),
    picks.json is the recency-weighted chairman winner placed per LENS BASIN (topics.json
    centroids), NOT per task type — that is routing.json's keying (and routing.json's
    SIBLING tagline correctly says "Per-task-type provider track record"). The picks
    tagline named the wrong key space, contradicting the basin-labelled rows under it.
    The launchpad cortex card — the OTHER surface that renders the same picks.json —
    already says it right ("tallied from your own N basins ... the chairman pick for that
    basin"); the viewer tagline was the lone stale outlier. Same shipped-label-derived-
    from-a-stale-model class as the routing.json/picks.json snake_case-enum fixes (iters
    165/191/196). `_SNAKE` above does NOT catch this — "per task type" is grammatically
    clean English; the defect is semantic (wrong key space), so it needs its own pin.

    Fix: "...rules per task type. Written by consolidate..." -> "The recency-weighted
    chairman winner per lens basin. Written by consolidate..." (memory_viewer.py
    ALLOWED_FILES). Mutation-provable: revert the tagline and "task type" reappears in
    the painted picks tagline → this reds.
    """
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file=picks.json")
        page.wait_for_timeout(700)
        tagline = page.evaluate(
            """() => {
              const p = document.querySelector('.content-header p.meta');
              return p && p.offsetParent !== null ? p.textContent : null;
            }"""
        )
    finally:
        browser.close()
        sp.stop()

    # BITE precondition: the picks tagline must actually be painted, else the asserts
    # below are vacuous.
    assert tagline and len(tagline) > 10, (
        "the picks.json header tagline didn't paint (offsetParent null / empty) — "
        f"can't assert keying copy on a tagline that isn't visible: {tagline!r}"
    )

    low = tagline.lower()
    # The picks tagline must name the BASIN keying (picks.json is keyed by lens basin
    # post-#298) so it agrees with the basin-labelled rows the reader paints below it.
    assert "basin" in low, (
        "REGRESSION: the picks.json tagline doesn't name its actual key space (lens "
        "BASIN). picks.json is the per-lens-basin chairman-winner tally post-#298; the "
        "rows below it are labelled by basin (b00 · Design). The tagline must say so. "
        f"Tagline painted: {tagline!r}"
    )
    # And it must NOT claim "task type" — that is routing.json's key space (its own
    # sibling tagline already owns "Per-task-type provider track record"); on the picks
    # tagline it contradicts the basin-labelled rows directly under it.
    assert "task type" not in low and "task_type" not in low, (
        "REGRESSION: the picks.json tagline claims picks are keyed 'per task type' — the "
        "stale pre-#298 framing. Post the cortex collapse picks.json is keyed by lens "
        "BASIN (task-type keying belongs to routing.json). The tagline contradicts the "
        f"basin-labelled rows the reader paints below it. Tagline painted: {tagline!r}"
    )


@pytest.mark.parametrize("fname", ["lens.md", "topics.json"])
def test_lens_built_tagline_names_canonical_verb_not_stale_alias(fname):
    """The lens.md / topics.json tab tagline's PROVENANCE clause must name the SAME
    canonical build verb (`lens`) the header's own rebuild chip + empty-state copy —
    not the legacy compatibility alias `lens-build`.

    Found 2026-06-21 (UX sweep iter 217) by driving the REAL viewer header: the lens.md
    tagline read "...Written by lens-build." and the topics.json tagline read
    "...Written by lens-build Stage 1." — while the rebuild chip rendered IN THE SAME
    HEADER copies `trinity-local lens` (suggestionFor() maps both files -> "lens") and
    the cold empty-state says "Run `trinity-local lens` to generate it." So one header
    showed a user TWO different command names for the identical build operation: the
    `.meta` provenance line said `lens-build`, the chip + empty-state said `lens`.

    Per CLAUDE.md the advertised CLI surface is `trinity-local lens` (+ council / dream
    / status / install); `lens-build` is an explicit *compatibility alias* kept for old
    scripts/launchpad-dispatch — not the name a first-time user should be taught. These
    two taglines (written 2026-05-15, before the #213 Q4 surface-collapse that made
    `lens` canonical) were the lone user-facing prose still teaching the alias. Same
    label-contradicts-its-own-affordance class as the picks.json "per task type" tagline
    above (iter 216) and the vocabulary.md phase-number tagline (iter 194): a `.meta`
    provenance line that names something other than what the controls beside it do.

    Fix: "Written by lens-build." -> "Written by lens." and "Written by lens-build
    Stage 1." -> "Written by lens (Stage 1)." (memory_viewer.py ALLOWED_FILES).
    Mutation-provable: revert either tagline to "lens-build" and the painted tagline
    disagrees with the chip's `trinity-local lens` -> this reds.
    """
    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    sp, browser = _browser()
    try:
        page = browser.new_context(viewport={"width": 1280, "height": 1000}).new_page()
        page.goto(f"file://{pages / 'memory.html'}?file={fname}")
        page.wait_for_timeout(700)
        painted = page.evaluate(
            """() => {
              const meta = document.querySelector('.content-header p.meta');
              const chip = document.querySelector('.viewer-rebuild-chip');
              return {
                tagline: meta && meta.offsetParent !== null ? meta.textContent : null,
                chipTitle: chip && chip.offsetParent !== null ? chip.title : null,
              };
            }"""
        )
    finally:
        browser.close()
        sp.stop()

    tagline = painted["tagline"]
    chip_title = painted["chipTitle"]
    # BITE precondition 1: the tagline must actually paint (else the prose asserts are
    # vacuous).
    assert tagline and len(tagline) > 10, (
        f"the {fname} header tagline didn't paint (offsetParent null / empty) — can't "
        f"assert provenance copy on a tagline that isn't visible: {tagline!r}"
    )
    # BITE precondition 2: the rebuild chip must paint AND copy the canonical verb. This
    # pins what the tagline is being checked AGAINST — the chip is the source of truth
    # for the build command, so if Trinity ever renames the canonical verb the chip moves
    # first and this precondition (not a false alarm on the tagline) flags it.
    assert chip_title and "trinity-local lens" in chip_title, (
        f"the {fname} rebuild chip didn't paint or stopped copying `trinity-local lens` "
        f"(the canonical build verb the tagline must agree with): chip title={chip_title!r}"
    )

    # The tagline must NOT teach the legacy compatibility alias `lens-build` while the
    # chip beside it copies the canonical `lens` — one header, one command name.
    assert "lens-build" not in tagline, (
        "REGRESSION: the "
        f"{fname} tagline names the legacy compatibility alias `lens-build` in its "
        "provenance clause, but the rebuild chip in the SAME header copies "
        "`trinity-local lens` (the canonical verb per CLAUDE.md) and the empty-state says "
        "`trinity-local lens`. A first-time user is shown two different command names for "
        f"the identical build operation. Tagline painted: {tagline!r}"
    )
    # And it must positively name the canonical verb so the provenance is actionable and
    # matches the chip.
    assert re.search(r"\bWritten by lens\b", tagline), (
        f"the {fname} tagline must name the canonical `lens` verb (matching its rebuild "
        f"chip `trinity-local lens`) in its provenance clause. Tagline painted: {tagline!r}"
    )
