"""Real-browser guard: a CORRUPT non-ARRAY ``top_terms`` in topics.json must NOT
crash the memory viewer's Reader views — neither the topics Reader NOR the picks
Reader (which shares the same ``loadCrossMemoryMaps`` basin-label helper).

The CORRUPT-STATE-CRASH class (#258/#304), the un-guarded ``top_terms`` sibling.
``basins[*].top_terms`` is ``list[str]`` in the real builder (me/basins.py
``top_terms: list[str]``, written verbatim), but topics.json is a hand-editable
state file, so a truncated / old-schema / hand-mangled basin can carry a STRING
(``"design-arch-api"``) where the array is expected.

The crash site: ``loadCrossMemoryMaps`` (shared by ``renderPicksReader`` AND
``renderTopicsReader``) did ``const terms = (b && b.top_terms) || [];`` then
``terms.slice(0, 3).join(" · ")``. A STRING ``top_terms`` is truthy with a
``.length``, so it slipped past ``terms.length``; ``"design-arch-api".slice(0,3)``
returns the STRING ``"des"``, whose ``.join`` is ``undefined`` →
``TypeError: terms.slice(...).join is not a function`` — an UNCAUGHT pageerror
that BLANKED both Reader views (0 pick cards even when picks.json is perfectly
valid; 0 topology nodes). Every OTHER ``top_terms`` access in memory_viewer.py
already ``Array.isArray``-guards (lines ~1280 / ~2280 / ~2746 / ~2778); this was
the one un-guarded sibling. Fixed with
``const terms = Array.isArray(b && b.top_terms) ? b.top_terms : [];``.

Renders via ``portal-html`` (the production write path), then drives BOTH Readers
and asserts NO pageerror AND the surface still PAINTS: the valid picks card renders
its basin (so the no-crash assertion can't pass vacuously on a blank page).

Mutation-proven: restore ``const terms = (b && b.top_terms) || [];`` in
memory_viewer.py → re-render → both Readers throw
``terms.slice(...).join is not a function`` and render 0 cards/0 nodes, REDing the
"surface still paints" + "no pageerror" assertions with the founder symptom named.

Slow + browser marked; skips without Playwright/chromium; runs in CI ``browser``.
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

# topics.json where the FIRST basin's top_terms is a STRING (the corruption) and a
# SECOND basin keeps a clean list (the control that proves the page still paints).
_TOPICS_RAW = """{
  "basins": [
    {"id": "b00", "label": "Design", "top_terms": "design-arch-api", "size": 12, "prompt_ids": [1, 2, 3]},
    {"id": "b01", "label": "Debugging", "top_terms": ["trace", "stack", "repro"], "size": 8, "prompt_ids": [4, 5]}
  ]
}"""

# A perfectly VALID picks.json — its Reader must NOT be collaterally blanked by the
# shared loadCrossMemoryMaps helper crashing on topics.json's string top_terms.
_PICKS_RAW = """{
  "b00": {"winner": "claude", "count": 5, "margin": 0.42, "n_episodes": 5, "evidence": []},
  "b01": {"winner": "codex", "count": 4, "margin": 0.55, "n_episodes": 4, "evidence": []}
}"""


def _assert_precondition_corrupt() -> None:
    """Render-INDEPENDENT: the seeded top_terms is genuinely a STRING (not an
    array) in Python, so the fixture is corrupt BEFORE any render — a clean fixture
    would make the no-crash assertion pass vacuously."""
    topics = json.loads(_TOPICS_RAW)
    b00 = topics["basins"][0]
    assert isinstance(b00["top_terms"], str), (
        "fixture not corrupt: b00.top_terms must be a STRING to bite the un-guarded "
        ".slice().join() path"
    )
    assert b00["top_terms"], "the corrupt string must be truthy with a .length (else it'd skip the bite)"
    # The control basin keeps a real list, and picks.json is valid — so a paint is
    # genuinely possible if the helper doesn't crash.
    assert isinstance(topics["basins"][1]["top_terms"], list)
    picks = json.loads(_PICKS_RAW)
    assert picks["b00"]["winner"] == "claude"


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(_TOPICS_RAW, encoding="utf-8")
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(_PICKS_RAW, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, (
        "portal-html crashed rendering a topics.json with a string top_terms: "
        f"{r.stderr[-400:]}"
    )
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def test_corrupt_string_top_terms_does_not_blank_the_readers():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _assert_precondition_corrupt()

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1400}).new_page()

            # ---- picks Reader (shares loadCrossMemoryMaps with the topics Reader) ----
            picks_errs: list[str] = []
            page.on("pageerror", lambda e: picks_errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            pick_cards = page.eval_on_selector_all(
                ".pick-card",
                "els => els.map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())",
            )
            # The defect: a string top_terms in topics.json crashed loadCrossMemoryMaps,
            # so the picks Reader rendered ZERO cards even though picks.json is valid.
            if not pick_cards:
                failures.append(
                    "FOUNDER SYMPTOM: the picks Reader rendered ZERO cards on a VALID "
                    "picks.json — collaterally blanked because the shared "
                    "loadCrossMemoryMaps helper threw 'terms.slice(...).join is not a "
                    "function' on topics.json's STRING top_terms (the un-Array.isArray-"
                    f"guarded sibling). cards={pick_cards!r}"
                )
            if not any("Use Claude" in c for c in pick_cards):
                failures.append(
                    f"picks Reader did not paint the clean b00 pick ('Use Claude'); cards={pick_cards!r}"
                )
            for e in picks_errs:
                if "join is not a function" in e:
                    failures.append(
                        "FOUNDER SYMPTOM: picks Reader threw the uncaught "
                        f"'terms.slice(...).join is not a function' on a string top_terms: {e!r}"
                    )

            # ---- topics Reader ----
            topics_errs: list[str] = []
            page.on("pageerror", lambda e: topics_errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'memory.html'}?file=topics.json", wait_until="load")
            page.wait_for_timeout(1100)
            body_len = page.evaluate("() => document.body.innerText.length")
            # The control basin's name still renders somewhere on the page (Reader or
            # topology) — proves the topics view didn't blank.
            paints_control = page.evaluate(
                "() => /Debugging/.test(document.body.innerText)"
            )
            for e in topics_errs:
                if "join is not a function" in e:
                    failures.append(
                        "FOUNDER SYMPTOM: topics Reader threw the uncaught "
                        f"'terms.slice(...).join is not a function' on a string top_terms: {e!r}"
                    )
            if not paints_control:
                failures.append(
                    "topics Reader did not paint the clean control basin (Debugging) — "
                    f"the string top_terms blanked the view (body_len={body_len})."
                )
        finally:
            browser.close()

    assert not failures, (
        "memory viewer crashed/blanked on a corrupt string top_terms in topics.json:\n  "
        + "\n  ".join(failures)
    )
