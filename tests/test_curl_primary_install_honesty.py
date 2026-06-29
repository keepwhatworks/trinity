"""Curl-primary install honesty guards (#226 + #232).

Founder decision (2026-05-29): go curl-primary, drop uvx. These guards pin
the three honesty fixes from #232 plus the uvx removal from #226 so the
install path can't silently regress into a lie:

  #226 — no naked `uvx trinity-local` left in README / install.py; the
         README Codex section uses the correct `[mcp_servers.trinity-local]`
         TOML header and the real `install-mcp` config shape (module-mode
         python, not a PyPI runner).

  #232a — install.sh's closing note can't recommend a bare
          `trinity-local download-embedder`, which 100% fails on a clean box
          (no [mlx] extras + the HF_HUB_OFFLINE=1 pin). It must print the
          working incantation (pip [mlx] + HF_HUB_OFFLINE=0) and say the
          bare verb fails.

  #232b — install.sh can't claim "Auto-updates via Chrome Web Store" while
          registry.CHROME_WEB_STORE_URL is empty (nothing published). The
          claim must be conditional on the URL.

  #232c — health_checks Stage 2/3 must warn that a pre-wired (canonical-id)
          manifest is PROVISIONAL — a sideloaded extension's different id
          means capture is dead even though the manifest "is written".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
README = REPO_ROOT / "README.md"
INSTALL_PY = REPO_ROOT / "src" / "trinity_local" / "commands" / "install.py"


# ─── #226: uvx removal + README config-shape correctness ───────────


class TestUvxRemoved:
    def test_readme_has_no_naked_uvx(self):
        assert "uvx" not in README.read_text(encoding="utf-8"), (
            "README must not reference uvx (curl-primary, #226)."
        )

    def test_install_py_has_no_uvx_upsell(self):
        text = INSTALL_PY.read_text(encoding="utf-8")
        assert "uvx" not in text, (
            "install.py must not print the retired uvx upsell block (#226)."
        )

    def test_readme_codex_uses_correct_toml_header(self):
        text = README.read_text(encoding="utf-8")
        # The correct Codex MCP table header is `[mcp_servers.trinity-local]`,
        # NOT the old broken `[mcp.trinity-local]`.
        assert "[mcp_servers.trinity-local]" in text
        assert "[mcp.trinity-local]" not in text

    def test_readme_shows_real_install_mcp_config_shape(self):
        text = README.read_text(encoding="utf-8")
        # install.py writes args = ["-m", "trinity_local.main", "--mcp"].
        assert '"-m", "trinity_local.main", "--mcp"' in text
        # And TOML mirror.
        assert 'args = ["-m", "trinity_local.main", "--mcp"]' in text

    def test_readme_install_command_canonical_block_intact(self):
        text = README.read_text(encoding="utf-8")
        # The doc-consistency canary block must survive the rewrite.
        assert "<!-- canonical:install_command -->" in text
        assert "<!-- /canonical -->" in text
        assert "scripts/install.sh | bash" in text


# ─── #232a: download-embedder honesty ──────────────────────────────


class TestEmbedderPrefetchHonest:
    @pytest.fixture
    def tail(self) -> str:
        return "\n".join(INSTALL_SH.read_text().splitlines()[-30:])

    def test_offline_pin_override_is_printed(self, tail):
        # A bare download-embedder fails because main() pins
        # HF_HUB_OFFLINE=1. The note must show the HF_HUB_OFFLINE=0 override.
        assert "HF_HUB_OFFLINE=0" in tail, (
            "install.sh must show the HF_HUB_OFFLINE=0 override — a bare "
            "download-embedder hits the offline pin and fails."
        )

    def test_mlx_extras_install_is_printed(self, tail):
        # The real model also needs the [mlx] extras, which install.sh
        # does NOT install by default. The note must show how to add them.
        assert "sentence-transformers" in tail and "torch" in tail, (
            "install.sh must show the [mlx] extras pip install — without "
            "them download-embedder returns 'MLX dependencies not installed'."
        )

    def test_bare_verb_is_called_out_as_failing(self, tail):
        lowered = tail.lower()
        assert "fail" in lowered, (
            "install.sh must say the bare download-embedder fails on a "
            "clean box, not present it as a working one-liner."
        )

    def test_tf_idf_fallback_is_mentioned_so_omitting_is_safe(self, tail):
        lowered = tail.lower()
        assert "tf-idf" in lowered or "fall back" in lowered or "fallback" in lowered, (
            "install.sh should note the embedder is optional (TF-IDF "
            "fallback) so users don't think the failing verb blocks them."
        )


# ─── #232a-followon: "real embeddings" claim gates on the INVARIANT ─
# Found 2026-06-05 by running install.sh in an isolated HOME: the closing banner
# said "Embedding runtime present — real embeddings available" gated only on
# `import mlx_embeddings`, but real embeddings also need the MODEL WEIGHTS (which
# install.sh never pulls), so on every fresh install it CONTRADICTED `status`,
# which correctly reports the TF-IDF fallback. The claim must gate on the same
# mlx_actually_loaded() probe-embed that health_checks uses.


class TestRealEmbeddingsClaimGatesOnProbe:
    @pytest.fixture
    def text(self) -> str:
        return INSTALL_SH.read_text(encoding="utf-8")

    @pytest.fixture
    def printed(self) -> str:
        # Only the lines the USER sees — strip shell comments, which legitimately
        # quote the old false claim for documentation (and would false-trip below).
        return "\n".join(
            ln for ln in INSTALL_SH.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")
        )

    def test_no_false_embeddings_ready_claim_on_bare_runtime(self, printed):
        # Installing the RUNTIME (mlx/sentence-transformers) does NOT pull the
        # ~600MB model, so neither "available" nor "enabled" may be printed off a
        # bare runtime install — it contradicts `status` (TF-IDF fallback).
        for lie in ("real embeddings available", "real embeddings enabled"):
            assert lie not in printed, (
                f"install.sh prints '{lie}' off a bare runtime install — but the "
                "model isn't downloaded yet, so this contradicts `status`."
            )

    def test_uses_the_invariant_probe(self, text):
        assert "mlx_actually_loaded" in text, (
            "install.sh must gate the 'real embeddings' claim on "
            "mlx_actually_loaded() (a real probe-embed) — the same invariant "
            "trinity-local status reports — so the two can't disagree."
        )

    def test_has_honest_runtime_present_model_absent_state(self, text):
        # The common fresh-install case: runtime installed, model not pulled.
        assert "isn't downloaded yet" in text, (
            "install.sh must have an honest middle state for runtime-present + "
            "model-absent (the common fresh install), not collapse it into "
            "'real embeddings available'."
        )


# ─── #232b: Chrome Web Store claim conditional on the URL ──────────


class TestWebStoreClaimConditional:
    def test_unconditional_auto_update_claim_removed(self):
        text = INSTALL_SH.read_text()
        # The old line printed "Auto-updates via Chrome Web Store"
        # unconditionally. It must now be gated on CHROME_WEB_STORE_URL.
        assert "Auto-updates via Chrome Web Store" not in text, (
            "install.sh must not claim Web Store auto-update unconditionally "
            "while CHROME_WEB_STORE_URL is empty (nothing published)."
        )

    def test_claim_is_gated_on_registry_url(self):
        text = INSTALL_SH.read_text()
        assert "CHROME_WEB_STORE_URL" in text, (
            "install.sh must read registry.CHROME_WEB_STORE_URL to decide "
            "whether the Web Store path exists."
        )
        # The conditional branch must exist.
        assert 'if [[ -n "$WEB_STORE_URL" ]]' in text

    def test_sideload_path_is_the_honest_default(self):
        text = INSTALL_SH.read_text()
        # While unpublished, the honest path is Load-unpacked.
        assert "Load unpacked" in text
        assert "Developer mode" in text

    def test_registry_url_still_empty_so_sideload_branch_is_live(self):
        # If this flips, the conditional copy starts advertising the store —
        # which is correct, but the guard documents the current reality.
        from trinity_local.registry import CHROME_WEB_STORE_URL
        assert CHROME_WEB_STORE_URL == "", (
            "CHROME_WEB_STORE_URL is no longer empty — publish has happened. "
            "Update this guard + confirm install.sh advertises the store URL."
        )


# ─── #232c: provisional pre-wire warning in health_checks ──────────


def _write_macos_manifest(home_dir, allowed_origins):
    manifest_dir = (
        home_dir / "Library" / "Application Support" / "Google" / "Chrome"
        / "NativeMessagingHosts"
    )
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "local.trinity.capture.json").write_text(
        json.dumps({"allowed_origins": allowed_origins})
    )


class TestProvisionalPrewireWarning:
    @pytest.fixture
    def fake_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        monkeypatch.setattr(
            "trinity_local.runtime_env.which_on_runtime_path",
            lambda _: "/usr/local/bin/trinity-local-capture-host",
        )
        monkeypatch.setattr(sys, "platform", "darwin")
        home = tmp_path / "fake_home"
        home.mkdir()
        monkeypatch.setattr(
            "trinity_local.health_checks.Path.home",
            classmethod(lambda cls: home),
        )
        return home

    def test_canonical_id_manifest_flagged_provisional(self, fake_home):
        from trinity_local.health_checks import _check_browser_capture
        from trinity_local.registry import CANONICAL_EXTENSION_ID

        _write_macos_manifest(
            fake_home,
            [f"chrome-extension://{CANONICAL_EXTENSION_ID}/"],
        )
        result = _check_browser_capture()
        assert result.ok is True  # still soft
        assert "PROVISIONAL" in result.detail
        assert "sideload" in result.detail.lower()
        assert "install-extension --extension-id" in result.detail

    def test_non_canonical_id_manifest_uses_generic_message(self, fake_home):
        from trinity_local.health_checks import _check_browser_capture

        # A real sideloaded id (32 a-p chars, not the canonical one).
        _write_macos_manifest(
            fake_home,
            ["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"],
        )
        result = _check_browser_capture()
        assert "PROVISIONAL" not in result.detail
        assert "no captures yet" in result.detail

    def test_unparseable_manifest_does_not_crash(self, fake_home):
        from trinity_local.health_checks import _check_browser_capture

        manifest_dir = (
            fake_home / "Library" / "Application Support" / "Google"
            / "Chrome" / "NativeMessagingHosts"
        )
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "local.trinity.capture.json").write_text("{not json")
        result = _check_browser_capture()
        assert result.ok is True
        assert "no captures yet" in result.detail
