"""Real-browser guard: a CORRUPT non-STRING value at a string field in picks.json /
routing.json must NOT paint "Use 42" / "Use [object Object]" / a bare number into the
memory viewer's Reader cells — AND must NOT crash the launchpad render.

The STRING face of Iter 256's non-finite numeric class (the same raw-client-parse path).
picks.json `winner` and routing.json `best_per_task_type` values are STRING fields, but
picks.json is a hand-editable state file with a documented migration chain
(cortex/routing_patterns.json -> memories/picks.json -> scoreboard/picks.json), so a
half-migrated or hand-mangled entry can carry a NUMBER (`"winner": 42`), an OBJECT
(`{"primary": "claude"}`), null, an array, or an empty string where a slug is expected.

Two faces, both driven 2026-06-21 (Iter 257):

  1. SERVER-SIDE (launchpad): `_load_cortex_rules` / `_task_to_topology_basin` did
     `(p.get("winner") or "").strip()` — a NUMBER winner raised `'int' object has no
     attribute 'strip'` that bubbled out of build_page_data and BLANKED THE WHOLE
     LAUNCHPAD (portal-html returned non-zero). Fixed with `_safe_text` (the string
     analog of `_safe_number`).

  2. CLIENT-SIDE (memory viewer): the picks Reader did `"Use " + providerBrand(winner)`
     and the routing Reader `providerBrand(best[t])`, where providerBrand PASSED a
     non-string straight through (`if (typeof canon !== "string") return canon`). A
     truthy non-string winner painted:

         b00 · Use 42                 (NUMBER winner)
         b01 · Use [object Object]    (OBJECT winner)

     and the routing Best column painted "99" / "[object Object]". Fixed by making
     providerBrand coerce a non-string/empty input to "" (the string analog of
     isFiniteNumber) and gating each "Use X" / "Best" site on the BRANDED string, plus
     rejecting a non-string winner at the cross-memory bridge (so the topology xlink
     can't freeze "[object Object]" into pick.winner).

Renders via `portal-html` (exercises the SERVER fix: a numeric winner used to make this
return non-zero), then drives both Readers and asserts no number/[object Object]/null/
undefined token reaches either DOM AND the surface still PAINTS (the clean control rows
render — not a blank-page false pass).

Mutation-proven: restore `winner = (p.get("winner") or "").strip()` in launchpad_data.py
-> portal-html crashes (returncode != 0); restore providerBrand's
`if (typeof canon !== "string") return canon;` -> re-render -> the picks/routing
no-token assertion reds with the founder symptom "Use [object Object]" / Best "99".

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

# A half-migrated / hand-mangled picks.json: number, object, null, empty, array
# winners alongside one CLEAN slug (the b05 control that MUST still paint "Use Claude").
_PICKS_RAW = """{
  "b00": {"winner": 42, "count": 5, "margin": 0.42, "n_episodes": 5, "evidence": []},
  "b01": {"winner": {"primary": "claude"}, "count": 4, "margin": 0.70, "n_episodes": 4, "evidence": []},
  "b02": {"winner": null, "count": 3, "margin": 0.60, "n_episodes": 3, "evidence": []},
  "b03": {"winner": "", "count": 2, "margin": 0.55, "n_episodes": 2, "evidence": []},
  "b04": {"winner": [], "count": 2, "margin": 0.30, "n_episodes": 2, "evidence": []},
  "b05": {"winner": "claude", "count": 6, "margin": 0.50, "n_episodes": 6, "evidence": []}
}"""

# routing.json with corrupt best_per_task_type values (number / object) + one CLEAN slug
# (the Debugging control that MUST still paint "Claude" in the Best cell).
_ROUTING_RAW = """{
  "by_task_type": {
    "code_generation": {"claude": {"overall": 0.8, "n": 5}, "codex": {"overall": 0.6, "n": 4}},
    "data_analysis": {"claude": {"overall": 0.7, "n": 3}, "antigravity": {"overall": 0.5, "n": 3}},
    "debugging": {"codex": {"overall": 0.75, "n": 4}, "claude": {"overall": 0.6, "n": 2}}
  },
  "best_per_task_type": {"code_generation": 99, "data_analysis": {"slug": "claude"}, "debugging": "codex"},
  "pick_is_tie": {},
  "computed_at": "2026-06-21T00:00:00"
}"""

_BAD_TOKENS = ("[object Object]", "undefined", "null", "NaN")


def _render_portal(home: Path) -> Path:
    (home / "scoreboard").mkdir(parents=True)
    (home / "scoreboard" / "picks.json").write_text(_PICKS_RAW, encoding="utf-8")
    (home / "scoreboard" / "routing.json").write_text(_ROUTING_RAW, encoding="utf-8")
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    # SERVER face of the bug: a numeric `winner` made `(p.get("winner") or "").strip()`
    # raise `'int' object has no attribute 'strip'` and BLANK THE WHOLE LAUNCHPAD —
    # portal-html exited non-zero. The _safe_text fix must keep this green.
    assert r.returncode == 0, (
        "portal-html crashed on a corrupt non-string picks.json `winner` "
        f"(numeric/object winner blanked the launchpad render): {r.stderr[-400:]}"
    )
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists()
    return pages


def _assert_precondition_corrupt():
    """Render-INDEPENDENT: the seeded string fields are genuinely non-string in Python,
    so the fixture is corrupt BEFORE any render (a clean fixture would make the no-token
    assertion pass vacuously)."""
    picks = json.loads(_PICKS_RAW)
    assert not isinstance(picks["b00"]["winner"], str), "fixture not corrupt: b00.winner must be a NUMBER"
    assert isinstance(picks["b00"]["winner"], (int, float)), "fixture not corrupt: b00.winner must be a NUMBER"
    assert isinstance(picks["b01"]["winner"], dict), "fixture not corrupt: b01.winner must be an OBJECT"
    assert picks["b05"]["winner"] == "claude", "fixture must keep one CLEAN control winner"
    routing = json.loads(_ROUTING_RAW)
    best = routing["best_per_task_type"]
    assert isinstance(best["code_generation"], (int, float)), "fixture not corrupt: best code_generation must be a NUMBER"
    assert isinstance(best["data_analysis"], dict), "fixture not corrupt: best data_analysis must be an OBJECT"
    assert best["debugging"] == "codex", "fixture must keep one CLEAN control best"


def test_corrupt_nonstring_winner_renders_no_object_object_token():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _assert_precondition_corrupt()

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)  # also asserts the server render didn't crash

    failures: list[str] = []
    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1400, "height": 1400}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:160]))

            # ---- picks Reader ----
            page.goto(f"file://{pages / 'memory.html'}?file=picks.json", wait_until="load")
            page.wait_for_timeout(900)
            cards = page.eval_on_selector_all(
                ".pick-card",
                "els => els.map(c => (c.innerText || '').replace(/\\s+/g, ' ').trim())",
            )
            # (A) the surface PAINTS — the clean control b05 renders "Use Claude", so a
            # blank page can't pass the no-token assertion vacuously.
            if not any("Use Claude" in c for c in cards):
                failures.append(
                    "picks Reader did not paint the clean control (b05 'Use Claude') — "
                    f"can't trust the no-garbage assertion. cards={cards!r}"
                )
            # The corrupt winners must NOT have painted a "Use X" recommendation.
            for c in cards:
                if "Use 42" in c or "Use [object Object]" in c or "Lean 42" in c:
                    failures.append(f"picks Reader painted a corrupt winner recommendation: {c!r}")
            picks_text = page.evaluate("() => document.body.innerText")
            for tok in _BAD_TOKENS:
                if tok in picks_text:
                    failures.append(
                        f"picks Reader leaked the corrupt-string token {tok!r} from a "
                        f"non-string winner (42 / {{primary:…}} -> 'Use [object Object]'). "
                        f"cards={cards!r}"
                    )

            # ---- routing.json Reader ----
            page.goto(f"file://{pages / 'memory.html'}?file=routing.json", wait_until="load")
            page.wait_for_timeout(900)
            rows = page.eval_on_selector_all(
                "table tr",
                "els => els.map(r => (r.innerText || '').replace(/\\s+/g, ' ').trim())",
            )
            # (A) paints — the clean Debugging row (best 'codex' -> 'GPT') renders.
            if not any("Debugging" in r and "GPT" in r for r in rows):
                failures.append(
                    f"routing Reader did not paint the clean control row (Debugging -> GPT). rows={rows!r}"
                )
            # The corrupt-best rows must show "—", never the number / object.
            for r in rows:
                if "Code Generation" in r and ("99" in r.split()[-1] if r.split() else False):
                    failures.append(f"routing Reader painted a numeric Best value: {r!r}")
            routing_text = page.evaluate("() => document.body.innerText")
            for tok in _BAD_TOKENS:
                if tok in routing_text:
                    failures.append(
                        f"routing Reader leaked the corrupt-string token {tok!r} from a "
                        f"non-string best_per_task_type (99 / {{slug:…}} -> Best '[object Object]'). "
                        f"rows={rows!r}"
                    )

            if errs:
                failures.append(f"JS errors during viewer render: {errs[:3]}")
        finally:
            browser.close()

    assert not failures, (
        "memory viewer painted a corrupt non-string winner/best from a scoreboard file:\n  "
        + "\n  ".join(failures)
    )
