"""Browser render guard for the launchpad "Build deeper memory" (embedder) card COPY.

The card upsells the ~600 MB modernbert-embed model. Its COPY must do two things
the static-render sweep didn't pin (UX sweep iter 16):

  1. NOT name a RETIRED command. The old copy said "Replay history and the council
     launchpad already work without it." — but `replay-history` / `commands.replay`
     was RETIRED 2026-05-27 (retired_names.py). Telling a user a dead command "works
     without it" is a credibility leak on a launch-credibility surface.

  2. LEAD WITH WHAT REAL EMBEDDINGS BUY + state what DEGRADES under fallback. The old
     copy framed the model as merely what "lens-build, dream, and vocabulary commands
     use to find topic basins" and reassured everything "already works without it" —
     it never told the user the actual trade: without the model the embedder falls
     back to lexical TF-IDF, the semantic flows (taste/correction lens, noise filter)
     ABSTAIN (correction_lens.py returns {"ready": False, "reason": "needs real
     embeddings"}), and the basins are coarser. A USEFULNESS card that hides the very
     thing it upsells is orphan copy.

The card only renders when prompts are indexed AND the model is absent, so this seeds
a prompt index and points the subprocess HOME at an empty temp dir (no HF cache →
modelDownloaded=False → show=True). Drives the REAL petite-vue render so a `{{ }}`
leak or a v-if regression that hides the card also reds.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# Phrases that prove the retired command leaked back into the copy. The card must
# never claim a RETIRED verb (replay-history) "works without" the embedder.
RETIRED_PHRASES = ("replay history", "replay-history")


def _seed_prompts(home: Path) -> None:
    """A prompt_nodes.jsonl with real records so `_embedder_status` sees
    promptsIndexed=True (the card's show-gate)."""
    prompts = home / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "prompt_nodes.jsonl").write_text(
        "\n".join(
            f'{{"node_id":"p{i}","text":"design a floor plan engine {i} for the cabin {i}"}}'
            for i in range(8)
        ),
        encoding="utf-8",
    )


def _render_portal(home: Path) -> Path:
    """Render the REAL /stats page via the portal-html CLI. HOME points at an
    empty temp dir so `hf_cache_model_path()` (Path.home()/.cache/...) finds no
    model → modelDownloaded=False → the embedder card surfaces deterministically,
    regardless of whether the CI runner has the real model cached."""
    fake_home = home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["HOME"] = str(fake_home)  # redirect hf_cache_model_path's Path.home()
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "stats.html").exists()
    return pages


# Read the embedder card's rendered text (after petite-vue mounts) + whether
# raw {{ }} leaked. This test exercises the DOWNLOAD mode (model missing) — there
# the card keeps the `stats-card` view-class + the "Optional · deeper memory"
# eyebrow. Find it by the stable `embedder-status-card` hook (present in both
# modes since UX sweep iter 81).
_PROBE = """() => {
  const card = document.querySelector('section.embedder-status-card');
  if (!card) return {found: false};
  const text = (card.textContent || '').replace(/\\s+/g, ' ').trim();
  return {found: true, text, hasLeak: text.includes('{{')};
}"""


def test_embedder_card_copy_is_honest_and_useful():
    """The embedder card must NOT name the retired replay-history command AND must
    tell the user the real trade — what deeper memory buys (semantic flows /
    sharper basins) and what falls back to lexical without it. A card that upsells
    the model while hiding what it does for you is orphan copy. (UX sweep iter 16.)"""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _seed_prompts(home)
    pages = _render_portal(home)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1600}
            ).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / 'stats.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_PROBE)

            assert d["found"], (
                "the embedder 'Build deeper memory' card did not render — the "
                "show-gate (promptsIndexed AND model-missing) regressed, or the "
                "eyebrow text changed"
            )
            assert not errs, f"JS errors on the embedder card: {errs[:3]}"
            assert not d["hasLeak"], "raw {{ }} leaked in the embedder card"

            lower = d["text"].lower()

            # 1. The RETIRED command must NOT appear. (retired_names.py: replay-history
            #    cut 2026-05-27.) Naming a dead verb as "works without it" is a leak.
            for phrase in RETIRED_PHRASES:
                assert phrase not in lower, (
                    f"the embedder card names the RETIRED command {phrase!r} "
                    "('Replay history ... already works without it') — replay-history "
                    "was retired 2026-05-27; a launch surface must not cite a dead verb. "
                    f"(card text: {d['text'][:200]!r})"
                )

            # 2. The card must say WHAT real embeddings BUY (semantic / meaning /
            #    sharper basins) — lead with the answer, don't just say "download a
            #    model".
            assert any(k in lower for k in ("semantic", "meaning")), (
                "the embedder card never says the model reads MEANING / enables the "
                "SEMANTIC flows — it upsells a ~600 MB download without telling the "
                f"user what it buys (USEFULNESS orphan). (card text: {d['text'][:200]!r})"
            )

            # 3. The card must say what DEGRADES without it — the fallback to lexical
            #    keyword matching (the honest trade). embed() falls back to TF-IDF and
            #    the semantic flows abstain; the user deserves to know.
            assert any(k in lower for k in ("lexical", "keyword", "coarser")), (
                "the embedder card never states the FALLBACK — without the model the "
                "embedder drops to lexical keyword matching and the basins are coarser; "
                "the card must be honest about what degrades, not just reassure 'works "
                f"without it'. (card text: {d['text'][:200]!r})"
            )
        finally:
            browser.close()
