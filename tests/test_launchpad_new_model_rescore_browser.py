"""Browser render guard for the launchpad new-models card's RE-SCORE framing.

The "New model 🎉 — Score it against YOUR taste" card fires for two distinct
cases that the COPY must NOT conflate:

  * a provider the user NEVER benchmarked (`last_evaluated is None`) — first
    score; "A number no lab can produce" is correct, and
  * a provider the user ALREADY benchmarked but whose model SHIPPED AN UPDATE
    (`last_evaluated == the old model id`) — a RE-SCORE.

For the re-score case the user already HAS a number — the eval leaderboard one
screen below still shows it, under the OLD model name (e.g. "Claude
(claude-opus-4-5) 0.79"). Without a per-entry distinction the card reads "score
Claude, you have no number" while the leaderboard says "Claude 0.79" — a direct
contradiction on one screen, and the only signal that the existing benchmark
went stale (`last_evaluated`) was computed by detect_new_models then DROPPED from
the launchpad payload. (UX sweep iter 14.)

This pins the DOM: the re-score entry leads with "your benchmark is for the
previous version" and the first-score entry does NOT. A string test can't see
this — both entries share the card; only the per-entry <p> differs, gated on
`nm.rescore` which is gated on the dropped `last_evaluated`.

It ALSO guards the iter-199 fix: the re-score line must NOT paint the recorded
`target_model` API id (`claude-opus-4-5`, `gpt-5.2-codex`) — that's a raw
lowercase-hyphenated model slug, an opaque code symbol leaked into a celebratory
nudge whose header one line up already shows the human display name. The old
model is NAMED by `nm.display` + the leaderboard row; the raw id is unactionable
and reads as a leak (sibling of the #184/#187 opaque-id-as-label fixes).

Slow + browser marked; skips when Playwright/chromium are absent. The card's
`_dispatchable` gate needs the provider CLI on PATH, so the test drops harmless
`claude`/`agy`/`codex` stub executables on the subprocess PATH — deterministic
on a CI runner with no real CLIs installed.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _result(target: str, model: str, score: float) -> dict:
    return {
        "target_provider": target,
        "target_model": model,
        "aggregate_score": score,
        "items_completed": 12,
        "items_total": 12,
        "eval_id": "eval_setA",
        "completed_at": "2026-06-15T00:00:00",
        "judge_provider": "claude",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.85, "count": 4}},
        "items": [{"judge_provider": "claude"}],
    }


def _seed_rescore(home: Path) -> None:
    """An eval set + a Claude run on a NOW-SUPERSEDED model (so claude is a
    re-score: current manifest model != claude-opus-4-5) + a codex run on the
    current model (so codex is NOT a new-model entry). Antigravity is never
    scored → a first-score entry. Result: the card carries one re-score entry
    (claude) and one first-score entry (antigravity)."""
    (home / "evals").mkdir(parents=True, exist_ok=True)
    (home / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "eval_setA", "stats": {"items": 12}}), encoding="utf-8"
    )
    results = home / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    # claude scored on the PREVIOUS model id → re-score nudge (rescore=True). The
    # old model id (claude-opus-4-5) must NOT paint in the copy (iter-199 leak guard).
    (results / "eval_setA__model_claude.json").write_text(
        json.dumps(_result("claude", "claude-opus-4-5", 0.79)), encoding="utf-8"
    )
    # codex scored on its CURRENT model → no nudge (keeps the card to claude+gemini).
    (results / "eval_setA__model_codex.json").write_text(
        json.dumps(_result("codex", "gpt-5.3-codex", 0.71)), encoding="utf-8"
    )


def _fake_bin(tmp: Path) -> Path:
    """A dir of harmless stub executables so `_dispatchable` resolves the provider
    CLIs without the real Claude/Codex/Antigravity binaries (CI determinism)."""
    binv = tmp / "fakebin"
    binv.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "codex", "agy"):
        f = binv / name
        f.write_text("#!/bin/sh\necho stub\n", encoding="utf-8")
        f.chmod(f.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return binv


def _render_portal(home: Path, fake_bin: Path) -> Path:
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "stats.html").exists()
    return pages


# Read each new-models entry's text + which one carries the re-score line. The
# entries are the `v-for` divs inside the card; the re-score line is the <p>
# containing "previous version".
_PROBE = """() => {
  const card = document.querySelector('.new-models-card');
  if (!card) return {found: false};
  // v-for entry divs: a leading bold name <div> then the per-entry copy.
  const chips = Array.from(card.querySelectorAll('button.suggestion-chip'))
    .map(b => (b.textContent || '').replace(/\\s+/g, ' ').trim());
  // Map each chip's nearest entry block to its text.
  const entries = Array.from(card.querySelectorAll('div'))
    .filter(d => d.querySelector(':scope > button.suggestion-chip'))
    .map(d => (d.textContent || '').replace(/\\s+/g, ' ').trim());
  const cardText = (card.textContent || '').replace(/\\s+/g, ' ');
  return {
    found: true,
    chips,
    claudeEntry: entries.find(t => /Claude/.test(t)) || '',
    geminiEntry: entries.find(t => /Gemini/.test(t)) || '',
    hasLeak: cardText.includes('{{'),
  };
}"""


def test_new_model_rescore_copy_distinguishes_first_score():
    """The re-score entry (Claude, scored on the superseded claude-opus-4-5) must
    LEAD with 'your benchmark is for the previous version' — so it doesn't read
    'you have no number' while the leaderboard one screen below shows the stale
    Claude score. The first-score entry (Gemini, never scored) must NOT carry that
    line. Bites if the payload drops `last_evaluated`/`rescore` or the template
    omits the per-entry re-score <p>. (UX sweep iter 14.)

    It ALSO bites the iter-199 leak: the re-score line must NOT paint the raw
    recorded model id (claude-opus-4-5) — an opaque API slug in user-facing copy
    whose human display name sits one line up (#184/#187 opaque-id-as-label class).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _seed_rescore(home)
    pages = _render_portal(home, _fake_bin(tmp))

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 1600}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            # /stats view (where the new-models card lives).
            page.goto(f"file://{pages / 'stats.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_PROBE)

            assert d["found"], (
                "new-models-card did not render — re-score seed produced no card "
                "(dispatchable gate or eval-set gate regressed)"
            )
            assert not errs, f"JS errors on the new-models card: {errs[:3]}"
            assert not d["hasLeak"], "raw {{ }} leaked in the new-models card"
            # The card must carry BOTH a re-score entry and a first-score entry.
            assert d["claudeEntry"], f"no Claude entry in the card: chips={d['chips']}"
            assert d["geminiEntry"], f"no Gemini entry in the card: chips={d['chips']}"
            # 1. Re-score entry LEADS with the previous-version answer.
            assert "previous version" in d["claudeEntry"], (
                "the Claude re-score entry does not say its benchmark is for the "
                f"previous version — re-score copy regressed (entry: {d['claudeEntry'][:160]!r}). "
                "Without it the card reads 'score Claude, no number yet' while the "
                "leaderboard one screen below shows the stale Claude score."
            )
            # 1b. iter-199 leak guard: the re-score line must NOT paint the raw
            #     recorded model id — that's an opaque lowercase-hyphenated API slug
            #     (`claude-opus-4-5`) leaked into a celebratory nudge whose human
            #     display name ("Claude Opus 4.8") sits one line up. The model is
            #     named by the header + leaderboard; the raw id is unactionable.
            assert "claude-opus-4-5" not in d["claudeEntry"], (
                "the re-score line LEAKED the raw recorded model id (claude-opus-4-5) "
                "back into user-facing copy — an opaque API slug in a celebratory nudge "
                "(#184/#187 opaque-id-as-label class). Drop the `({{ nm.previousModel }})` "
                f"parenthetical (entry: {d['claudeEntry'][:160]!r})"
            )
            # 2. First-score entry must NOT carry the re-score line (it has no
            #    prior number — that framing would be wrong the other direction).
            assert "previous version" not in d["geminiEntry"], (
                "the Gemini FIRST-score entry wrongly shows the re-score 'previous "
                "version' line — that copy must be gated on nm.rescore "
                f"(entry: {d['geminiEntry'][:160]!r})"
            )
        finally:
            browser.close()
