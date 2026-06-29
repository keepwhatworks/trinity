"""Browser + data guard: the embedder card's DEGRADED (model-present, libs-broken) state.

UX sweep iter 44. The "Build deeper memory" card shows whenever embeddings aren't
FULLY functional (prompts indexed AND not (model_downloaded AND mlx_available) — the
#273 gate so a silent TF-IDF fallback surfaces). But "show" hides TWO genuinely
different remediations:

  - model MISSING → a real ~600 MB `huggingface-cli download`.
  - model PRESENT but the MLX libs (sentence-transformers / torch) aren't importable
    — a broken install or a venv switch (the EXACT #273 trigger the card was built to
    surface). The weights are ALREADY on disk; only the libs need reinstalling.

Before the fix the card rendered ONE pitch for both: headline "Build deeper memory
(~600 MB download)", body "the model … ~600 MB", a command
`pip install '…[mlx]' && huggingface-cli download <model>` that re-fetches the ~600 MB
the user already has, and a footer "One-time download." So a user whose libs broke was
told to re-download a model on disk and to expect a 600 MB download that won't happen —
a misleading remediation (the "do what's already done / wrong cure" shape). The fix
emits a `mode` ('download' vs 'reinstall-libs') so the card shows the honest cure for
each state.

Two layers:
  - data: `_embedder_status` carries mode='reinstall-libs' + a command WITHOUT
    `huggingface-cli download` when the model is cached but the libs are broken; and
    mode='download' + the full download command when the model is missing (scope guard).
  - browser: the REAL /stats render in the degraded state shows the reinstall copy
    (no "600 MB download", command does NOT re-download) AND the model-missing render
    keeps the download pitch (fix not over-broadened).

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _write_prompts(home: Path) -> None:
    prompts = home / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "prompt_nodes.jsonl").write_text(
        "\n".join(
            f'{{"node_id":"p{i}","text":"design a floor plan engine {i} for the cabin {i}"}}'
            for i in range(8)
        ),
        encoding="utf-8",
    )


def _seed_model_weights(fake_home: Path) -> None:
    """Drop a real weights file into the HF cache so modelDownloaded=True under the
    redirected HOME (Path.home() → fake_home)."""
    from trinity_local.embeddings.backend_mlx import MODEL_ID

    snapshot = (
        fake_home / ".cache" / "huggingface" / "hub"
        / f"models--{MODEL_ID.replace('/', '--')}"
        / "snapshots" / "abc123"
    )
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "model.safetensors").write_bytes(b"\x00" * 16)


# ─── Data layer ────────────────────────────────────────────────────────────────


class TestEmbedderModeHonest:
    """The two remediations must carry distinct mode + command."""

    @pytest.fixture
    def isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        return tmp_path

    def test_model_present_libs_broken_reinstalls_does_not_redownload(
        self, isolated_home, monkeypatch
    ):
        """Model cached + libs broken → mode 'reinstall-libs' and the command must
        NOT contain `huggingface-cli download` (re-fetching ~600 MB already on disk).
        Mutation: revert _embedder_status to the single `&& huggingface-cli download`
        command → the assertion that the command excludes a re-download fires."""
        from trinity_local.embeddings.backend_mlx import MODEL_ID
        from trinity_local.launchpad_data import _embedder_status

        _write_prompts(isolated_home)
        # model present
        monkeypatch.setattr(Path, "home", lambda: isolated_home)
        _seed_model_weights(isolated_home)
        # libs broken
        monkeypatch.setattr("trinity_local.embeddings.is_available", lambda: False)

        status = _embedder_status()
        assert status["modelDownloaded"] is True
        assert status["mlxAvailable"] is False
        assert status["show"] is True
        assert status["mode"] == "reinstall-libs", (
            "model cached + libs broken must be the 'reinstall-libs' remediation, not "
            "'download' — the weights are already on disk"
        )
        cmd = str(status["downloadCommand"])
        assert "huggingface-cli download" not in cmd, (
            "the embedder card told a libs-broken user (model ALREADY cached) to "
            f"`huggingface-cli download {MODEL_ID}` — that re-fetches ~600 MB they "
            f"already have; the cure is just the pip install. (command: {cmd!r})"
        )
        assert "pip install" in cmd, (
            "the reinstall-libs command must still install the [mlx] libs"
        )

    def test_model_missing_keeps_download_command(self, isolated_home, monkeypatch):
        """SCOPE GUARD: model genuinely missing → mode 'download' and the command
        DOES include the huggingface-cli download (the fix must not over-broaden into
        the real-download case). Stays green under the mutation above."""
        from trinity_local.launchpad_data import _embedder_status

        _write_prompts(isolated_home)
        # no HF cache → model missing
        monkeypatch.setattr(Path, "home", lambda: isolated_home)
        monkeypatch.setattr("trinity_local.embeddings.is_available", lambda: True)

        status = _embedder_status()
        assert status["modelDownloaded"] is False
        assert status["show"] is True
        assert status["mode"] == "download"
        assert "huggingface-cli download" in str(status["downloadCommand"]), (
            "a genuinely-missing model must still surface the download command"
        )


# ─── Browser layer ─────────────────────────────────────────────────────────────

pytestmark_browser = [pytest.mark.slow, pytest.mark.browser]

# Phrases that prove the misleading "fresh 600 MB download" framing leaked into the
# DEGRADED (libs-broken, model-cached) card. The cure there is a libs reinstall, not
# a download.
_DOWNLOAD_FRAMING = ("600 mb download", "one-time download")


def _render_degraded_stats(home: Path) -> Path:
    """Render REAL /stats with the model WEIGHTS present (under the redirected HOME)
    but the MLX libs forced off (TRINITY_DISABLE_MLX=1) → modelDownloaded=True,
    mlxAvailable=False → the degraded 'reinstall-libs' card, deterministic on any
    runner (mlx present or not)."""
    fake_home = home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    _seed_model_weights(fake_home)
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["HOME"] = str(fake_home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["TRINITY_DISABLE_MLX"] = "1"  # force is_available()=False
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "stats.html").exists()
    return pages


# Find the embedder card by its STABLE hook class (`embedder-status-card`),
# present in BOTH modes — the eyebrow text now differs by mode (the degraded
# 'reinstall-libs' card leads with "Embeddings degraded · using lexical fallback"
# instead of "Optional · deeper memory"), and the view-split class is mode-
# conditional (stats-card on 'download', untagged 'embedder-degraded-card' on
# 'reinstall-libs' so it shows on HOME too — UX sweep iter 81).
_PROBE = """() => {
  const card = document.querySelector('section.embedder-status-card');
  if (!card) return {found: false};
  if (card.offsetParent === null) return {found: false, hidden: true};
  const text = (card.textContent || '').replace(/\\s+/g, ' ').trim();
  const code = (card.querySelector('code') || {}).textContent || '';
  return {found: true, text, code, hasLeak: text.includes('{{')};
}"""


@pytest.mark.slow
@pytest.mark.browser
def test_degraded_card_does_not_pitch_a_redownload():
    """REAL render of the model-cached-libs-broken state: the card must NOT frame the
    cure as a ~600 MB download and must NOT tell the user to `huggingface-cli download`.
    Mutation: revert the data+template to one 'download' pitch → the card reads
    'Build deeper memory (~600 MB download)' + the re-download command → reds with the
    exact symptom."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _write_prompts(home)
    pages = _render_degraded_stats(home)

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
                "the embedder card did not render in the degraded (model-cached, "
                "libs-broken) state — the #273 show-gate regressed"
            )
            assert not errs, f"JS errors on the embedder card: {errs[:3]}"
            assert not d["hasLeak"], "raw {{ }} leaked in the embedder card"

            lower = d["text"].lower()
            for phrase in _DOWNLOAD_FRAMING:
                assert phrase not in lower, (
                    "the embedder card pitched a fresh "
                    f"{phrase!r} to a libs-broken user whose model is ALREADY cached "
                    "— a misleading remediation (the model is on disk; only the libs "
                    f"need reinstalling). (card text: {d['text'][:220]!r})"
                )
            assert "huggingface-cli download" not in lower, (
                "the embedder card told a libs-broken user (model ALREADY cached) to "
                "`huggingface-cli download` — re-fetching ~600 MB they already have. "
                f"(command: {d['code']!r})"
            )
            # The honest cure IS surfaced.
            assert "already downloaded" in lower or "no 600 mb re-download" in lower, (
                "the degraded card must tell the user the model is already cached and "
                f"no re-download is needed. (card text: {d['text'][:220]!r})"
            )
        finally:
            browser.close()


@pytest.mark.slow
@pytest.mark.browser
def test_model_missing_card_still_pitches_the_download():
    """SCOPE GUARD (real render): the model-MISSING state keeps the original
    download pitch + command — the fix must not strip the genuine-download case."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp())
    home = tmp / "trinity"
    home.mkdir(parents=True)
    _write_prompts(home)
    # empty fakehome → no HF cache → modelDownloaded=False
    fake_home = home / "fakehome"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["HOME"] = str(fake_home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["TRINITY_DISABLE_MLX"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"portal-html failed: {r.stderr[-400:]}"
    pages = home / "portal_pages"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_context(
                viewport={"width": 1280, "height": 1600}
            ).new_page()
            page.goto(f"file://{pages / 'stats.html'}")
            page.wait_for_timeout(1500)
            d = page.evaluate(_PROBE)
            assert d["found"], "the embedder card did not render in the model-missing state"
            lower = d["text"].lower()
            assert "600 mb download" in lower, (
                "the model-MISSING state must keep the ~600 MB download pitch — the "
                f"fix over-broadened into the genuine-download case. (text: {d['text'][:200]!r})"
            )
            assert "huggingface-cli download" in (d["code"] or "").lower(), (
                "the model-MISSING command must include the huggingface-cli download"
            )
        finally:
            browser.close()
