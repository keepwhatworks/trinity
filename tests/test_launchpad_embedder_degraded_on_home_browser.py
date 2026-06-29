"""Browser guard: the SILENTLY-DEGRADED embedder card must show on the HOME launchpad.

UX sweep iter 81. Trinity's #1 bug shape is a green/headline (or, here, SILENCE) on
the primary surface while the backend is degraded (the #106/#109 abstain-gate + #273
"don't let degradation be silent" lineage).

The embedder-status card exposes two modes:
  - mode 'download'      = the model was NEVER fetched → a pure opt-in UPSELL. Living
    on /stats only (the home/stats split) is correct: home stays a clean council
    surface; the lens cards carry a one-line "see stats" pointer.
  - mode 'reinstall-libs' = the weights ARE cached but the libs broke / a venv
    switched → Trinity "silently fell back to lexical keyword matching" (the EXACT
    #273 silent-degradation). This is a REGRESSION, not an upsell.

THE DEFECT this guard pins: the card was tagged `stats-card`, so the home/stats
split hid BOTH modes on home. In the 'reinstall-libs' state the home user saw NO
actionable signal — only the same static "Sharper meaning needs the embedder — see
stats" pointer that a FULLY-WORKING install also shows. So the silent-degradation
state was rendered INDISTINGUISHABLE from the all-good state on the surface the user
actually looks at — re-burying exactly what #273 surfaced.

THE FIX: the view-class is now MODE-CONDITIONAL. 'reinstall-libs' drops `stats-card`
(untagged → shows on home AND stats) and leads with a red "Embeddings degraded ·
using lexical fallback" eyebrow; 'download' keeps `stats-card` (stats-only).

This guard drives the REAL home + stats renders in BOTH modes and reads the rendered
DOM (offsetParent visibility), NOT a source string. Mutation: revert the card's
`:class` to the static `card stats-card` → the degraded-on-home assertion REDS (the
card is hidden on home), reproducing the founder symptom ("my embeddings silently
broke and the home page said nothing").
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

pytestmark = [pytest.mark.slow, pytest.mark.browser]


def _write_prompts(home: Path) -> None:
    prompts = home / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "prompt_nodes.jsonl").write_text(
        "\n".join(
            f'{{"node_id":"p{i}","text":"design a resilient floor plan engine {i}"}}'
            for i in range(8)
        ),
        encoding="utf-8",
    )


def _seed_model_weights(fake_home: Path) -> None:
    """Drop a real weights file into the HF cache so modelDownloaded=True under the
    redirected HOME → with libs forced off, _embedder_status emits mode
    'reinstall-libs' (the silent-degradation state)."""
    from trinity_local.embeddings.backend_mlx import MODEL_ID

    snapshot = (
        fake_home / ".cache" / "huggingface" / "hub"
        / f"models--{MODEL_ID.replace('/', '--')}"
        / "snapshots" / "abc123"
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "model.safetensors").write_bytes(b"\x00" * 16)


def _render(home: Path, *, model_cached: bool) -> Path:
    """Render the REAL portal pages (home launchpad.html + stats.html) with the MLX
    libs forced off (TRINITY_DISABLE_MLX=1). model_cached toggles the HF cache so we
    get the 'reinstall-libs' (cached) vs 'download' (missing) mode deterministically
    on any runner."""
    fake_home = home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    if model_cached:
        _seed_model_weights(fake_home)
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["HOME"] = str(fake_home)  # redirect hf_cache_model_path's Path.home()
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["TRINITY_DISABLE_MLX"] = "1"  # force is_available()=False → the degraded path
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "launchpad.html").exists() and (pages / "stats.html").exists()
    return pages


# Is the embedder card present AND actually painted (offsetParent != null catches the
# `.lp-view-home .stats-card { display:none }` hide)? Also report its eyebrow so the
# degraded-mode "Embeddings degraded" lead is verifiable.
_PROBE = """() => {
  const card = document.querySelector('section.embedder-status-card');
  if (!card) return {present: false, visible: false, eyebrow: ''};
  const visible = card.offsetParent !== null
      && getComputedStyle(card).display !== 'none';
  const eb = card.querySelector('.eyebrow');
  return {present: true, visible,
          eyebrow: ((eb && eb.textContent) || '').replace(/\\s+/g, ' ').trim()};
}"""


def _probe(pages: Path, page_name: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(viewport={"width": 1280, "height": 1600}).new_page()
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)[:200]))
            page.goto(f"file://{pages / page_name}")
            page.wait_for_timeout(1200)
            d = page.evaluate(_PROBE)
            d["errors"] = errs
            return d
        finally:
            browser.close()


def test_degraded_embedder_card_is_visible_on_home():
    """mode 'reinstall-libs' (model cached, libs broken → SILENT TF-IDF fallback): the
    card must be VISIBLE on the HOME launchpad, not buried on /stats. Reproduces the
    founder symptom — embeddings silently degraded and home said nothing."""
    pytest.importorskip("playwright.sync_api")
    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _write_prompts(home)
    pages = _render(home, model_cached=True)

    home_v = _probe(pages, "launchpad.html")
    assert not home_v["errors"], f"JS errors on home: {home_v['errors'][:3]}"
    assert home_v["present"], (
        "the embedder-status card did not render at all in the degraded "
        "(model-cached, libs-broken) state — the #273 show-gate regressed"
    )
    assert home_v["visible"], (
        "the SILENTLY-DEGRADED embedder card (mode 'reinstall-libs' — Trinity fell "
        "back to lexical keyword matching) is HIDDEN on the HOME launchpad. A user "
        "whose embeddings silently broke sees NO actionable signal on the surface "
        "they actually look at — re-burying exactly what #273 surfaced. It must show "
        "on home (drop the `stats-card` view-class for this mode)."
    )
    # The degraded card must lead with a problem framing, not "Optional".
    assert "degraded" in home_v["eyebrow"].lower(), (
        "the degraded-on-home card still leads with the 'Optional · deeper memory' "
        f"eyebrow — a silent regression must not read as an optional extra. (eyebrow: {home_v['eyebrow']!r})"
    )
    # And it's still on /stats (the analytics surface keeps the full diagnostic).
    stats_v = _probe(pages, "stats.html")
    assert stats_v["present"] and stats_v["visible"], (
        "the degraded embedder card vanished from /stats — it must show on BOTH views"
    )


def test_download_upsell_card_stays_off_home():
    """SCOPE GUARD: mode 'download' (model NEVER fetched) is a pure opt-in UPSELL — it
    must stay OFF the simple home (stats-card) so the fix doesn't crowd the clean
    council home with an optional pitch. The lens cards' 'see stats' pointer covers
    home for this mode."""
    pytest.importorskip("playwright.sync_api")
    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _write_prompts(home)
    pages = _render(home, model_cached=False)  # → mode 'download'

    home_v = _probe(pages, "launchpad.html")
    assert not home_v["errors"], f"JS errors on home: {home_v['errors'][:3]}"
    # Card is in the DOM (present) but hidden on home via stats-card.
    assert home_v["present"], "the download-upsell card should still be in the home DOM"
    assert not home_v["visible"], (
        "the optional 'Build deeper memory' DOWNLOAD upsell is showing on the simple "
        "home launchpad — it belongs on /stats (the home/stats split); only the "
        "silently-DEGRADED 'reinstall-libs' mode should surface on home."
    )
    # It IS on /stats.
    stats_v = _probe(pages, "stats.html")
    assert stats_v["present"] and stats_v["visible"], (
        "the download-upsell card must still render on /stats"
    )
