"""Real-browser guard: a single eval result file whose ``by_rejection_type`` is a
valid-JSON-but-WRONG-TYPE value (a LIST or a STRING where the per-axis map
``{axis: {mean_score, count}}`` is expected) must NOT blank the whole launchpad.

The founder symptom (the recurring corrupt-state-file class — sibling of the bare-NaN
headline crash that blanked the launchpad, and #304 non-dict topics.json):
``_eval_summary`` read ``payload.get("by_rejection_type") or {}`` and then called
``.items()`` on it. A LIST is truthy, so the bare ``or {}`` let it through and
``.items()`` raised ``AttributeError: 'list' object has no attribute 'items'`` —
which bubbled out of ``build_page_data`` → the WHOLE launchpad render 500'd /
returned nothing, so petite-vue never mounted and the user saw a blank page (or raw
``{{ }}``). One hand-edited / half-migrated / schema-drifted eval file took down
EVERY card on the launchpad. Both read sites (the headline ``axes`` build AND the
cross-provider leaderboard loop) had the same ``(... or {}).items()`` shape.

The fix coerces a non-dict ``by_rejection_type`` to ``{}`` at the read boundary
(``x if isinstance(x, dict) else {}``), the same shape-guard ``load_council_outcome``
uses for wrong-type member_results / metadata and the topics reader uses for a
non-dict root — so one malformed file degrades to "no per-axis breakdown" while the
aggregate score + every other card still render.

Driven over the REAL launchpad (portal-html → stats.html → file:// in chromium):
asserts the page mounts with NO pageerror, the eval card paints, and the aggregate
score (0.81) is still on the page. Mutation-proven: revert either ``isinstance``
coercion in launchpad_data → this guard reds with a pageerror /
``'list' object has no attribute 'items'`` and a blank eval card; the valid-dict
control (a sibling well-formed run) stays green.

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


def _render(home: Path, payloads: list[dict]) -> Path:
    rd = home / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    for i, pl in enumerate(payloads):
        name = (
            f"eval_{pl.get('eval_id', 'ev')}__model_"
            f"{pl['target_provider']}__20260618T1200{i:02d}.json"
        )
        (rd / name).write_text(json.dumps(pl), encoding="utf-8")
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
    # A crash in build_page_data (the founder symptom) would surface here as a
    # non-zero exit with the AttributeError in stderr — assert the CLI render
    # itself survived the corrupt file before we even reach the browser.
    assert r.returncode == 0, (
        "portal-html CRASHED on a wrong-type by_rejection_type — a single corrupt "
        f"eval file blanked the launchpad at the data layer:\n{r.stderr[-600:]}"
    )
    page = home / "portal_pages" / "stats.html"
    assert page.exists(), "stats.html was not written"
    return page


# A LIST-shaped by_rejection_type (the realistic half-migrated / hand-edited shape)
# on the would-be HEADLINE provider — exercises site 1 (the axes build).
_LIST_AXIS_RUN = {
    "target_provider": "codex",
    "target_model": "gpt-5.5",
    "aggregate_score": 0.81,
    "eval_id": "setA",
    "items_completed": 12,
    "items_total": 12,
    "items_failed": 0,
    "by_rejection_type": [
        {"name": "REFRAME", "mean_score": 0.80, "count": 6},
        {"name": "COMPRESSION", "mean_score": 0.82, "count": 6},
    ],
    "items": [{"judge_provider": "claude"}],
    "completed_at": "2026-06-18T12:00:00+00:00",
    "started_at": "2026-06-18T12:00:00+00:00",
}

# A STRING-shaped by_rejection_type on a 2nd provider — exercises site 2 (the
# cross-provider leaderboard loop) so BOTH read sites are covered in one render.
_STRING_AXIS_RUN = {
    "target_provider": "claude",
    "target_model": "claude-opus-4-8",
    "aggregate_score": 0.79,
    "eval_id": "setA",
    "items_completed": 12,
    "items_total": 12,
    "items_failed": 0,
    "by_rejection_type": "garbled",
    "items": [{"judge_provider": "claude"}],
    "completed_at": "2026-06-18T11:00:00+00:00",
    "started_at": "2026-06-18T11:00:00+00:00",
}


def test_wrong_type_by_axis_does_not_blank_launchpad():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home, [_LIST_AXIS_RUN, _STRING_AXIS_RUN])

    with sync_playwright() as sp:
        try:
            browser = sp.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 2600})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{page_path}", wait_until="load")
            page.wait_for_timeout(900)

            res = page.evaluate(
                """() => {
                  const card = document.querySelector('.eval-summary-card');
                  const bodyText = (document.body.innerText || '');
                  // petite-vue must have MOUNTED (no raw moustache leak) and the
                  // aggregate score must still paint — the wrong-type axis only
                  // suppresses the per-axis breakdown, never the whole render.
                  return {
                    cardPainted: !!card && card.offsetParent !== null,
                    scoreShown: bodyText.includes('0.81'),
                    rawMoustache: bodyText.includes('{{') && bodyText.includes('}}'),
                    bodyLen: bodyText.length,
                  };
                }"""
            )
        finally:
            browser.close()

    # The blast-radius assertion: a corrupt eval file must NOT blank the launchpad.
    assert not errs, (
        "a wrong-type by_rejection_type raised a JS/render error that blanked the "
        f"launchpad (the corrupt-eval-file class): {errs[:3]}"
    )
    assert res["cardPainted"], (
        "the eval card did not paint — a wrong-type by_rejection_type took down the "
        "whole launchpad render (build_page_data AttributeError on .items())"
    )
    assert res["scoreShown"], (
        "the aggregate score (0.81) is gone — the wrong-type axis must only suppress "
        "the per-axis breakdown, not the headline number"
    )
    assert not res["rawMoustache"], (
        "raw {{ }} moustache leaked — petite-vue never mounted (the launchpad blanked "
        "on the corrupt eval file)"
    )
    assert res["bodyLen"] > 500, "the launchpad body is near-empty — it blanked"
