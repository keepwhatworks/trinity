"""Model-launch detection (#218) + user-facing provider aliases (Q5)."""
from __future__ import annotations

import json

import pytest


class TestProviderAlias:
    def test_gemini_resolves_to_antigravity_slug(self):
        from trinity_local.council_schema import resolve_provider_alias
        assert resolve_provider_alias("gemini") == "antigravity"

    def test_gpt_and_chatgpt_resolve_to_codex(self):
        from trinity_local.council_schema import resolve_provider_alias
        assert resolve_provider_alias("gpt") == "codex"
        assert resolve_provider_alias("chatgpt") == "codex"
        assert resolve_provider_alias("openai") == "codex"

    def test_canonical_slug_passes_through(self):
        from trinity_local.council_schema import resolve_provider_alias
        for slug in ("claude", "codex", "antigravity"):
            assert resolve_provider_alias(slug) == slug

    def test_case_insensitive_and_trimmed(self):
        from trinity_local.council_schema import resolve_provider_alias
        assert resolve_provider_alias("  Gemini ") == "antigravity"
        assert resolve_provider_alias("GPT") == "codex"

    def test_unknown_and_nonstr_pass_through(self):
        from trinity_local.council_schema import resolve_provider_alias
        assert resolve_provider_alias("mlx") == "mlx"
        assert resolve_provider_alias(None) is None


class TestModelsManifest:
    def test_manifest_has_the_three_providers(self):
        from trinity_local.models import current_models
        models = current_models()
        assert {"claude", "codex", "antigravity"} <= set(models)
        # Each carries a non-empty model string (the diff key).
        for info in models.values():
            assert info.get("model")

    def test_claude_model_matches_default(self):
        from trinity_local.models import current_models
        assert current_models()["claude"]["model"] == "claude-opus-4-8"


@pytest.mark.usefixtures("patch_trinity_home")
class TestDetectNewModels:
    def _write_run(self, provider: str, model: str):
        from trinity_local.evals.builder import results_dir
        d = results_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"eval_x__model_{provider}__20260528.json").write_text(
            json.dumps({"target_provider": provider, "target_model": model}),
            encoding="utf-8",
        )

    def test_cold_install_every_provider_is_new(self):
        from trinity_local.models import detect_new_models
        events = detect_new_models()
        slugs = {e.slug for e in events}
        assert {"claude", "codex", "antigravity"} <= slugs

    def test_provider_scored_on_current_model_is_not_new(self):
        from trinity_local.models import current_models, detect_new_models
        # Score claude on its CURRENT manifest model → no longer a nudge.
        self._write_run("claude", current_models()["claude"]["model"])
        events = detect_new_models()
        assert "claude" not in {e.slug for e in events}
        # The others (unscored) still surface.
        assert "codex" in {e.slug for e in events}

    def test_provider_scored_on_old_model_is_new(self):
        from trinity_local.models import detect_new_models
        self._write_run("claude", "claude-opus-4-7")  # stale model
        events = [e for e in detect_new_models() if e.slug == "claude"]
        assert len(events) == 1
        assert events[0].last_evaluated == "claude-opus-4-7"
        assert events[0].model == "claude-opus-4-8"

    def test_nudge_names_slug_and_command(self):
        from trinity_local.models import detect_new_models
        ev = next(e for e in detect_new_models() if e.slug == "antigravity")
        assert "eval-run --target antigravity" in ev.nudge()

    def test_norm_model_id_casefolds_and_collapses(self):
        from trinity_local.models import _norm_model_id
        assert _norm_model_id("Gemini 3.1 Pro (High)") == _norm_model_id(
            "gemini 3.1 pro (high)"
        )
        assert _norm_model_id("  a  b  ") == "a b"
        assert _norm_model_id(None) == ""

    def test_scored_model_case_or_space_variant_is_not_new(self):
        # The manifest records the model one way; an eval run may have recorded
        # the same model+effort with different case/spacing — the real founder
        # data had "Gemini 3.1 Pro (high)" (manifest) vs "Gemini 3.1 Pro (High)"
        # (recorded). A case/space-only diff must NOT re-fire the nudge for a
        # model the user already scored.
        from trinity_local.models import current_models, detect_new_models
        canonical = current_models()["antigravity"]["model"]
        variant = f"  {canonical.upper().replace(' ', '  ')}  "  # cased + padded + internal-space
        assert variant != canonical  # the exact-match path would have nudged
        self._write_run("antigravity", variant)
        events = detect_new_models()
        assert "antigravity" not in {e.slug for e in events}, (
            f"case/space-only diff should not nudge: {canonical!r} vs {variant!r}"
        )


@pytest.mark.usefixtures("patch_trinity_home")
class TestLaunchpadNewModelsBanner:
    def test_suppressed_without_eval_set(self, patch_trinity_home):
        """Cold home (no eval set) → the new-models card is SUPPRESSED. Its CTA is
        `eval-run --target X`, which fails without a built eval set; on a cold home
        every provider is "new", so an ungated card hands a brand-new user three
        commands that all error. (Found via the 2026-06-02 cold-home browser test;
        the eval-leaderboard card teaches the `eval-build` cold path instead.)"""
        from trinity_local.launchpad_data import _new_models_for_launchpad
        assert _new_models_for_launchpad() == []

    def test_payload_shape_once_eval_set_exists(self, patch_trinity_home, monkeypatch):
        """With an eval set built, the card returns each detected new model with a
        copy-ready, ACTUALLY-runnable eval-run command. (Pretend every provider CLI
        is dispatchable so the shape assertion doesn't depend on the test machine.)"""
        from trinity_local import runtime_env
        monkeypatch.setattr(runtime_env, "which_on_runtime_path", lambda b: "/usr/bin/" + b)
        from trinity_local.launchpad_data import _new_models_for_launchpad
        evals = patch_trinity_home / "evals"
        evals.mkdir(parents=True, exist_ok=True)
        # A REAL (non-hollow) eval set carries stats.items > 0 — a 0-item set is
        # not actually runnable, so _eval_set_available() now treats it as absent.
        (evals / "eval_smoke.json").write_text(
            '{"stats": {"items": 3}}', encoding="utf-8")
        items = _new_models_for_launchpad()
        assert items  # every provider is still new; now each is runnable
        for it in items:
            assert set(it) >= {"slug", "display", "command"}
            assert it["command"] == f"trinity-local eval-run --target {it['slug']}"

    def test_card_excludes_undispatchable_providers(self, patch_trinity_home, monkeypatch):
        """Regression (category-sweep 2026-06-16): the card's CTA is
        `eval-run --target X`, which dispatches to X's CLI. A user who can't run X
        (no CLI on PATH) must NOT be nudged to score it — otherwise the CTA errors,
        the model never gets scored, and detect_new_models re-surfaces it forever
        (a stuck, unactionable 'green' nudge). Only dispatchable providers appear."""
        evals = patch_trinity_home / "evals"
        evals.mkdir(parents=True, exist_ok=True)
        (evals / "eval_smoke.json").write_text(
            '{"stats": {"items": 3}}', encoding="utf-8")
        # Only `claude` resolves; codex (codex) + antigravity (agy) do not.
        from trinity_local import runtime_env
        monkeypatch.setattr(runtime_env, "which_on_runtime_path",
                            lambda b: "/usr/bin/claude" if b == "claude" else None)
        from trinity_local.launchpad_data import _new_models_for_launchpad
        slugs = {it["slug"] for it in _new_models_for_launchpad()}
        assert slugs == {"claude"}, f"only dispatchable providers should be nudged, got {slugs}"

    def test_banner_renders_in_template(self):
        from trinity_local.launchpad_template import render_launchpad_html
        html = render_launchpad_html(page_data={})
        assert "New model 🎉" in html
        assert "pageData.newModels" in html
