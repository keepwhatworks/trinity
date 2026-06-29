"""Tests for the `distill` module — Phase 5 of dream.

The `trinity-local distill` CLI was retired in commit c9b1f9d
(`dream` Phase 5 now refreshes core.md as part of the cold-start
flow), but the underlying `distill_via_chairman()` function survives
as the library called from dream + tested here. Distill reads the
three thinking core memories (lens, topics, vocabulary) under
~/.trinity/memories/ and writes a single paragraph to
~/.trinity/core.md. picks.json and routing.json are model-selection
memories (a scoreboard, not cognitive shape) and are intentionally
excluded from the distillation. The chairman reads core.md FIRST on
every council; this test suite pins the contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _bypass_embedder_gate(monkeypatch):
    """TestAutoDistillHooks exercises handle_me_build, which gates on
    the ~600 MB nomic model being in the HF cache. These tests stub the
    lens pipeline entirely; the gate is irrelevant noise. CI runs
    without the HF cache, so the gate would otherwise fail-closed.

    Dedicated gate coverage lives in test_embedder_cli_gate.py."""
    from trinity_local import embeddings
    monkeypatch.setattr(embeddings, "require_embedder_ready", lambda: None)


def _seed_memory(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestDistillSkipsWhenNoMemories:
    def test_skips_when_no_memories_present(self, isolated_home):
        """Cold install — no lens, no picks, no anything. Distill must
        NOT call a provider (cheap fail), should report skipped+reason."""
        from trinity_local.distill import distill_via_chairman

        # Guard: any provider invocation in this state is a bug.
        with patch("trinity_local.providers.make_provider") as make:
            report = distill_via_chairman(provider="claude")

        assert report["ok"] is False
        assert report.get("skipped") is True
        assert "no core memories" in report.get("reason", "").lower()
        make.assert_not_called()


class TestDistillPromptComposition:
    def test_prompt_includes_each_present_thinking_memory(self, isolated_home):
        """build_distill_prompt() must reflect every THINKING memory file
        that exists on disk and skip the ones that don't. picks/routing are
        model-selection memories — they're excluded even when present."""
        from trinity_local.distill import build_distill_prompt
        from trinity_local.state_paths import (
            lens_path, picks_path, routing_path, vocabulary_path,
        )

        _seed_memory(lens_path(), "# Lens\n→ leverage over ownership.\n")
        _seed_memory(vocabulary_path(), "# Vocabulary\n- `leverage`: 42 uses\n")
        # picks/routing seeded but MUST NOT appear in the prompt.
        _seed_memory(picks_path(), json.dumps({"system_design": {"primary": "codex"}}))
        _seed_memory(routing_path(), json.dumps({"coding": {"claude": 0.9}}))

        prompt = build_distill_prompt()

        assert "LENS" in prompt
        assert "leverage over ownership" in prompt
        assert "VOCABULARY" in prompt
        # Model-selection memories: excluded by design (not cognitive shape).
        assert "PICKS" not in prompt
        assert "ROUTING" not in prompt
        assert "codex" not in prompt
        # Topics not seeded — must not appear.
        assert "TOPICS" not in prompt

    def test_prompt_asks_for_second_person_paragraph(self, isolated_home):
        """The chairman should be instructed to write in second person ('You
        ship...') so the output reads like a manifesto, not a report."""
        from trinity_local.distill import build_distill_prompt
        from trinity_local.state_paths import lens_path

        _seed_memory(lens_path(), "# Lens\n→ tension example.")
        prompt = build_distill_prompt()

        assert "second person" in prompt.lower()
        assert "single-paragraph" in prompt.lower() or "single paragraph" in prompt.lower()


class TestDistillEndToEnd:
    def test_writes_core_md_with_provider_output(self, isolated_home):
        """When a memory exists and the provider returns text, distill
        writes that text verbatim to ~/.trinity/core.md."""
        from trinity_local.distill import distill_via_chairman
        from trinity_local.state_paths import core_path, lens_path

        _seed_memory(lens_path(), "# Lens\n→ leverage over ownership.")

        fake_result = type("R", (), {"stdout": "You ship leverage over structural ownership.", "stderr": ""})()

        # Mock the provider chain at the entrypoint we actually call.
        with patch("trinity_local.providers.make_provider") as make:
            make.return_value.run.return_value = fake_result
            report = distill_via_chairman(provider="claude")

        assert report["ok"] is True
        assert report["provider"] == "claude"
        assert Path(report["path"]) == core_path()
        assert core_path().exists()
        assert core_path().read_text(encoding="utf-8").startswith(
            "You ship leverage over structural ownership."
        )

    def test_empty_provider_output_does_not_overwrite_core(self, isolated_home):
        """If the provider returns empty stdout, distill must NOT clobber
        an existing core.md with whitespace. Force re-distill so the
        staleness skip doesn't short-circuit before the provider call."""
        from trinity_local.distill import distill_via_chairman
        from trinity_local.state_paths import core_path, lens_path

        # core.md must EXIST so the "doesn't overwrite" path is meaningful.
        core_path().write_text("Existing manifesto.\n", encoding="utf-8")
        _seed_memory(lens_path(), "# Lens\n→ x.")

        fake_result = type("R", (), {"stdout": "   ", "stderr": ""})()
        with patch("trinity_local.providers.make_provider") as make:
            make.return_value.run.return_value = fake_result
            # force=True bypasses the staleness check so we actually exercise
            # the empty-output guard.
            report = distill_via_chairman(provider="claude", force=True)

        assert report["ok"] is False
        assert "empty" in report.get("error", "").lower()
        assert core_path().read_text(encoding="utf-8") == "Existing manifesto.\n"


class TestStalenessSkip:
    def test_returns_ok_skipped_when_core_already_fresh(self, isolated_home):
        """If core.md is newer than every source memory, distill MUST NOT
        burn a flagship call. It returns ok=True, skipped=True — so a
        watchdog sees 'no work needed, but no error'."""
        from trinity_local.distill import distill_via_chairman
        from trinity_local.state_paths import core_path, lens_path
        import time

        # Source memory first.
        _seed_memory(lens_path(), "# Lens\n→ leverage.")
        time.sleep(0.05)  # ensure distinct mtime
        # Core newer than every source.
        _seed_memory(core_path(), "You ship leverage.")

        # Guard: ANY provider call here is a bug — distill should skip.
        with patch("trinity_local.providers.make_provider") as make:
            report = distill_via_chairman(provider="claude")

        make.assert_not_called()
        assert report["ok"] is True
        assert report.get("skipped") is True
        assert "fresh" in report.get("reason", "").lower()

    def test_re_distills_when_a_source_is_newer(self, isolated_home):
        """If a lens-build / consolidate has touched a memory since the last
        distill, the next distill call MUST run."""
        from trinity_local.distill import distill_via_chairman
        from trinity_local.state_paths import core_path, lens_path
        import time

        # Distill an older core.md first, then update the lens.
        _seed_memory(core_path(), "old paragraph")
        time.sleep(0.05)
        _seed_memory(lens_path(), "# Lens\n→ newer evidence")

        fake_result = type("R", (), {"stdout": "you ship leverage now", "stderr": ""})()
        with patch("trinity_local.providers.make_provider") as make:
            make.return_value.run.return_value = fake_result
            report = distill_via_chairman(provider="claude")

        assert report["ok"] is True
        assert report.get("skipped") is not True
        assert core_path().read_text(encoding="utf-8").startswith("you ship leverage now")

    def test_force_overrides_freshness_check(self, isolated_home):
        from trinity_local.distill import distill_via_chairman
        from trinity_local.state_paths import core_path, lens_path
        import time

        _seed_memory(lens_path(), "# Lens\n→ x.")
        time.sleep(0.05)
        _seed_memory(core_path(), "fresh paragraph")

        fake_result = type("R", (), {"stdout": "forced rewrite", "stderr": ""})()
        with patch("trinity_local.providers.make_provider") as make:
            make.return_value.run.return_value = fake_result
            report = distill_via_chairman(provider="claude", force=True)

        assert report["ok"] is True
        assert report.get("skipped") is not True
        assert "forced rewrite" in core_path().read_text(encoding="utf-8")


class TestAutoDistillHooks:
    def test_lens_build_triggers_distill_when_stale(self, isolated_home, monkeypatch):
        """After lens-build writes a fresh lens.md, the distill auto-fire
        must run (core.md is now older than lens.md → stale → distill
        runs)."""
        from trinity_local.commands.me import handle_me_build
        from trinity_local.state_paths import lens_path
        from types import SimpleNamespace

        # Stub the heavy lens-build itself — we're testing the auto-distill
        # hook, not the lens pipeline.
        def _stub_lens_pipeline(**kwargs):
            lens_path().write_text("# Lens\n→ leverage.", encoding="utf-8")
            return (lens_path(), {"stages_run": "stub"})
        monkeypatch.setattr(
            "trinity_local.commands.me.build_me_via_lens_pipeline",
            _stub_lens_pipeline,
        )

        # Spy on distill_via_chairman to confirm it got called.
        fired = []
        def _fake_distill(**kwargs):
            fired.append(True)
            return {"ok": True, "skipped": False, "path": "/x/core.md"}
        monkeypatch.setattr(
            "trinity_local.distill.distill_via_chairman", _fake_distill,
        )

        args = SimpleNamespace(
            legacy=False, dry_run=False, budget_chars=2000,
            sample_size=80, k_basins=20,
        )
        handle_me_build(args)
        assert fired == [True], "lens-build must auto-trigger distill after writing lens.md"

    def test_lens_build_dry_run_does_not_distill(self, isolated_home, monkeypatch):
        """Dry-run never writes anything, so triggering distill would just
        burn a flagship call for nothing."""
        from trinity_local.commands.me import handle_me_build
        from types import SimpleNamespace

        def _stub_lens_pipeline(**kwargs):
            return ("/tmp/x", {"stages_run": "stage-1-only"})
        monkeypatch.setattr(
            "trinity_local.commands.me.build_me_via_lens_pipeline",
            _stub_lens_pipeline,
        )
        fired = []
        def _fake_distill(**kwargs):
            fired.append(True)
            return {"ok": True}
        monkeypatch.setattr(
            "trinity_local.distill.distill_via_chairman", _fake_distill,
        )

        args = SimpleNamespace(
            legacy=False, dry_run=True, budget_chars=2000,
            sample_size=80, k_basins=20,
        )
        handle_me_build(args)
        assert fired == [], "dry-run must skip auto-distill"


class TestMigration:
    def test_legacy_me_md_migrates_to_memories_lens(self, isolated_home):
        """Files at ~/.trinity/me.md should move to ~/.trinity/memories/lens.md
        on first access to memories_dir() (or its derivatives)."""
        from trinity_local.state_paths import memories_dir, lens_path

        legacy = isolated_home / "me.md"
        legacy.write_text("legacy lens content", encoding="utf-8")

        # Trigger the migration by accessing memories_dir.
        memories_dir()

        assert lens_path().exists()
        assert lens_path().read_text(encoding="utf-8") == "legacy lens content"
        assert not legacy.exists(), "legacy me.md should have been moved"

    def test_legacy_cortex_routing_patterns_migrates_to_picks(self, isolated_home):
        from trinity_local.state_paths import memories_dir, picks_path

        legacy = isolated_home / "cortex" / "routing_patterns.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text('{"k": "v"}', encoding="utf-8")

        memories_dir()

        assert picks_path().exists()
        assert picks_path().read_text(encoding="utf-8") == '{"k": "v"}'

    def test_migration_idempotent_when_new_path_already_exists(self, isolated_home):
        """If the user already has memories/lens.md, the migration must NOT
        overwrite it with the (presumably stale) legacy me.md."""
        from trinity_local.state_paths import memories_dir, lens_path

        legacy = isolated_home / "me.md"
        legacy.write_text("old stale content", encoding="utf-8")
        # Seed new path with fresh content.
        memories_dir()  # ensures memories/ exists
        lens_path().write_text("fresh content", encoding="utf-8")

        # Re-trigger the migration. New file must win.
        memories_dir()

        assert lens_path().read_text(encoding="utf-8") == "fresh content"
