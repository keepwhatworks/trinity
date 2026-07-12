"""Real-browser VALUE guard: a floor-refused eval run must not paint its
withdrawn number anywhere on the rendered launchpad.

Surface-binding discipline (the drift-label class): the hour-3 unit test
asserted `_eval_summary()`'s DICT skips the refused run — but the value the
user reads is the one petite-vue binds into stats.html. This drives the REAL
render (portal-html → stats.html → file:// chromium) with a DISCRIMINATING
fixture: the refused run is NEWER and HIGHER (0.91) than the clean run
(0.52). If the launchpad skip is deleted — or the template binds latest_run /
leaderboard rows from an unskipped path — the withdrawn 0.91 paints and this
reds. The clean 0.52 must be the hero; 0.91 must appear NOWHERE in the body.

Slow + browser marked; skips without Playwright/chromium.
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


def _run_payload(target: str, model: str, score: float, when: str, *,
                 refused: bool) -> dict:
    payload = {
        "target_provider": target,
        "target_model": model,
        "aggregate_score": score,
        "eval_id": "setF",
        "items_completed": 12,
        "items_total": 12,
        "items_failed": 0,
        "n_scored": 12,
        "by_rejection_type": {
            "REFRAME": {"mean_score": score, "count": 6,
                        "min_score": score, "max_score": score},
            "COMPRESSION": {"mean_score": score, "count": 6,
                            "min_score": score, "max_score": score},
        },
        "items": [{"judge_provider": "antigravity"}],
        "completed_at": when,
        "started_at": when,
    }
    if refused:
        payload["baseline_floor"] = {
            "real_aggregate": score,
            "baselines": {"echo_rejected": {"name": "echo_rejected",
                                            "aggregate": score - 0.02,
                                            "n_scored": 8}},
            "margin": 0.02,
            "worst_negative": "echo_rejected",
            "recognition": 0.05,
            "judge_ok": False,
            "discriminates": False,
            "trustworthy": False,
            "reason": "EVAL DEGENERATE: probe",
        }
    return payload


def _render(home: Path) -> Path:
    rd = home / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    clean = rd / "eval_setF__model_codex__20260710T1200.json"
    clean.write_text(json.dumps(_run_payload(
        "codex", "gpt-5.5", 0.52, "2026-07-10T12:00:00+00:00", refused=False,
    )), encoding="utf-8")
    refused = rd / "eval_setF__model_claude__20260711T1200.json"
    refused.write_text(json.dumps(_run_payload(
        "claude", "claude-opus-4-8", 0.91, "2026-07-11T12:00:00+00:00", refused=True,
    )), encoding="utf-8")
    # refused strictly NEWER so it would win both the per-provider-latest slot
    # and the hero max if the floor skip ever stopped firing
    os.utime(clean, (1_000_000, 1_000_000))
    os.utime(refused, (2_000_000, 2_000_000))

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed:\n{r.stderr[-600:]}"
    page = home / "portal_pages" / "stats.html"
    assert page.exists(), "stats.html was not written"
    return page


def test_floor_refused_number_never_paints():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    (home / "evals").mkdir(parents=True)
    page_path = _render(home)

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
                  const body = (document.body.innerText || '');
                  return {
                    cleanShown: body.includes('0.52'),
                    refusedLeaked: body.includes('0.91'),
                    rawMoustache: body.includes('{{') && body.includes('}}'),
                    bodyLen: body.length,
                  };
                }"""
            )
        finally:
            browser.close()

    assert not errs, f"pageerror during render: {errs}"
    assert not res["rawMoustache"], "petite-vue failed to mount"
    assert res["cleanShown"], (
        "the clean run's 0.52 did not paint — the hero lost its only "
        f"trustworthy run (bodyLen {res['bodyLen']})"
    )
    assert not res["refusedLeaked"], (
        "REGRESSION: the floor-refused 0.91 painted on the launchpad — a "
        "withdrawn headline leaked through a template binding the summary "
        "skip does not cover"
    )
