"""Browser render guard: a HOLLOW (0-item) eval set on disk must NOT flip the
launchpad eval card into its "run it" state, nor un-suppress the new-models
`eval-run` chips.

The first-run prerequisite chain is `lens` → `eval-build` → `eval-run`. The
launchpad gates the `eval-build` step on `rejections_available` (the
preference-act ledger has >=1 act) and the `eval-run` step on
`eval_set_available` (an `eval_*.json` exists). But "the ledger has an act" is
NOT "the ledger has a SCOREABLE model-miss rejection": a ledger of only
self_expressed (decision) acts — or model_miss acts that all collapse degenerate
/unresolved — builds a 0-item eval set. `build_eval_set` raises only on a MISSING
ledger, not an empty RESULT, so the hollow `eval_*.json` lands on disk.

Pre-fix, `_eval_set_available()` returned True for ANY `eval_*.json`, so the
hollow set flipped the eval card to State C (the `eval-run --target X` chips) and
un-suppressed the new-models card's `eval-run` chips — both steering the user to
dispatch a benchmark with zero items (real councils, real quota, no signal). The
fix makes `_eval_set_available()` honest (stats.items > 0), so a hollow set keeps
the card in State B (`eval-build`) and the new-models card suppressed. (UX sweep:
green-while-the-eval-is-empty class — Trinity's #1 bug shape.)

This drives the REAL petite-vue render (a string test can't see the v-if branch
that actually mounts). Slow + browser marked; skips when Playwright/chromium are
absent. The new-models `_dispatchable` gate needs the provider CLIs on PATH, so
harmless `claude`/`codex`/`agy` stubs are dropped on the subprocess PATH — that
makes the test STRICTER (the card would surface if the gate were the only thing
suppressing it), so the suppression we assert is the eval-set gate, not a missing
CLI.
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


def _seed_hollow(home: Path) -> None:
    """A ledger with acts (rejections_available=True) + a 0-item eval set on disk
    (eval_set "exists" but is hollow). This is the exact state after a user runs
    `eval-build` against a decision-only / all-degenerate ledger."""
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    # Two self_expressed (decision) acts — the ledger is non-empty, but eval-build
    # consumes only model_miss → the built set has 0 items.
    (me / "preference_acts.jsonl").write_text(
        json.dumps({"id": "d1", "trigger": "self_expressed",
                    "privileged": "ship the simple thing",
                    "sacrificed": "the elaborate thing", "kind": "simplicity"}) + "\n"
        + json.dumps({"id": "d2", "trigger": "self_expressed",
                      "privileged": "measure first", "sacrificed": "guess",
                      "kind": "rigor"}) + "\n",
        encoding="utf-8",
    )
    (home / "evals").mkdir(parents=True, exist_ok=True)
    # The hollow set as `eval-build` would write it: present on disk, stats.items 0.
    (home / "evals" / "eval_hollow.json").write_text(
        json.dumps({"eval_id": "eval_hollow", "source": "rejections",
                    "stats": {"items": 0}, "items": []}),
        encoding="utf-8",
    )


def _fake_bin(tmp: Path) -> Path:
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


# Read the eval-card's currently-mounted empty-state branch (which chips render)
# + whether the new-models card surfaced at all.
_PROBE = """() => {
  const evalCard = document.querySelector('.eval-leaderboard-card')
    || document.querySelector('section.card'); // fallback if class shifts
  // Find the eval first-run card by its heading text (stable copy).
  const cards = Array.from(document.querySelectorAll('section.card'));
  const card = cards.find(c => /Score the 3 providers on YOUR corpus/i.test(c.textContent || ''));
  const chips = card
    ? Array.from(card.querySelectorAll('button.suggestion-chip'))
        .map(b => (b.textContent || '').replace(/\\s+/g, ' ').trim())
    : [];
  const newModelsCard = document.querySelector('.new-models-card');
  return {
    foundEvalCard: !!card,
    chips,
    evalCardText: card ? (card.textContent || '').replace(/\\s+/g, ' ').trim() : '',
    newModelsCardPresent: !!newModelsCard,
    leak: (document.body.textContent || '').includes('{{'),
  };
}"""


def test_hollow_eval_set_does_not_offer_eval_run():
    """A 0-item eval set on disk must keep the eval card in State B
    (`trinity-local eval-build`) — it must NOT show the State-C
    `eval-run --target X` chips (which would dispatch a hollow benchmark),
    and the new-models card must stay suppressed. Bites if
    `_eval_set_available()` regresses to counting any `eval_*.json` as runnable
    (the green-while-the-eval-is-empty bug)."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _seed_hollow(home)
    pages = _render_portal(home, _fake_bin(tmp))

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 393, "height": 1800}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'stats.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_PROBE)

            assert d["foundEvalCard"], (
                "the eval first-run card did not render on /stats — seed regressed"
            )
            assert not errs, f"JS errors on /stats: {errs[:3]}"
            assert not d["leak"], "raw {{ }} leaked on /stats"

            chips_joined = " | ".join(d["chips"])
            # The card must stay in State B: offer `eval-build`, NOT `eval-run`.
            assert "eval-build" in chips_joined, (
                "with rejections in the ledger but a HOLLOW (0-item) eval set, the "
                "card must stay in State B (`trinity-local eval-build`) — it did not. "
                f"chips were: {d['chips']}"
            )
            # The load-bearing assertion: a 0-item set must NOT flip the card to
            # the runnable State C. Seeing a `--target` chip means the launchpad
            # told the user to dispatch a benchmark with zero items.
            assert "--target" not in chips_joined, (
                "HOLLOW eval set (0 items) wrongly flipped the eval card to its "
                "RUN-IT state — the launchpad is offering `eval-run --target X` "
                "against a set with nothing to score (real councils, real quota, no "
                f"signal). chips were: {d['chips']}. `_eval_set_available()` must "
                "gate on stats.items > 0, not on the file merely existing."
            )
            # The new-models card's CTA is also `eval-run --target X`; it is
            # suppressed until a RUNNABLE eval set exists. A hollow set must not
            # un-suppress it (the CLIs are stubbed on PATH, so the only thing that
            # can suppress it here is the eval-set gate).
            assert not d["newModelsCardPresent"], (
                "HOLLOW eval set un-suppressed the new-models card, whose CTA is "
                "`eval-run --target X` — same dispatch-a-hollow-benchmark trap. "
                "The new-models gate (_eval_set_available) must reject a 0-item set."
            )
        finally:
            browser.close()
