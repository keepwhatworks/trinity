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

        args = SimpleNamespace(dry_run=False, sample_size=80, k_basins=20)
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

        args = SimpleNamespace(dry_run=True, sample_size=80, k_basins=20)
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


class TestVocabularyFoldHooks:
    """The 2026-07-04 vocabulary fold: `lens` refreshes EVERY thinking memory.

    Closes the verb-coverage seam hit live 2026-07-03 — only dream's
    vocabulary phase wrote vocabulary.md, so a user who ran the recommended
    `lens --force` still saw the vocabulary-staleness nag (the warning's own
    advice couldn't clear it — the same shape as the ingest-marker false-stale
    fixed the same day)."""

    def test_lens_build_refreshes_vocabulary_before_distill(self, isolated_home, monkeypatch):
        from trinity_local.commands.me import handle_me_build
        from trinity_local.state_paths import lens_path
        from types import SimpleNamespace

        def _stub_lens_pipeline(**kwargs):
            lens_path().parent.mkdir(parents=True, exist_ok=True)
            lens_path().write_text("# Lens\n", encoding="utf-8")
            return (lens_path(), {"stages_run": "stub"})
        monkeypatch.setattr(
            "trinity_local.commands.me.build_me_via_lens_pipeline",
            _stub_lens_pipeline,
        )
        order: list[str] = []
        monkeypatch.setattr(
            "trinity_local.vocabulary.distill_vocabulary",
            lambda **kw: (order.append("vocabulary"), {"ok": True, "anchors_emitted": 3})[1],
        )
        monkeypatch.setattr(
            "trinity_local.distill.distill_via_chairman",
            lambda **kw: (order.append("distill"), {"ok": True})[1],
        )
        args = SimpleNamespace(dry_run=False, sample_size=80, k_basins=20)
        handle_me_build(args)
        assert "vocabulary" in order, (
            "lens-build no longer refreshes vocabulary.md — the 2026-07-03 "
            "verb-coverage seam is back (the staleness nag recommends `lens` "
            "but `lens` wouldn't clear it)"
        )
        assert order.index("vocabulary") < order.index("distill"), (
            "vocabulary must refresh BEFORE distill so core.md reads fresh anchors"
        )

    def test_dry_run_skips_vocabulary(self, isolated_home, monkeypatch):
        from trinity_local.commands.me import handle_me_build
        from types import SimpleNamespace
        monkeypatch.setattr(
            "trinity_local.commands.me.build_me_via_lens_pipeline",
            lambda **kw: ("/tmp/x", {"stages_run": "stage-1-only"}),
        )
        fired = []
        monkeypatch.setattr(
            "trinity_local.vocabulary.distill_vocabulary",
            lambda **kw: (fired.append(True), {"ok": True})[1],
        )
        args = SimpleNamespace(dry_run=True, sample_size=80, k_basins=20)
        handle_me_build(args)
        assert not fired, "dry-run must not touch vocabulary.md"


class TestDeepFlagDelegation:
    """`lens --deep` == the six-phase deep-mine engine; `dream` is only a
    compatibility alias for it (one concept, 2026-07-04)."""

    def test_lens_deep_delegates_to_the_deep_engine(self, isolated_home, monkeypatch):
        from types import SimpleNamespace
        from trinity_local.commands import dream as dream_cmd
        from trinity_local.commands.me import handle_me_build
        called = []
        monkeypatch.setattr(dream_cmd, "handle_dream",
                            lambda a: called.append(a) or 0)
        args = SimpleNamespace(deep=True, dry_run=False, sample_size=80,
                               k_basins=None, only_distill=False)
        handle_me_build(args)
        assert len(called) == 1, "lens --deep must delegate to the deep engine"
        ns = called[0]
        assert ns.skip_me_build is False and ns.only_distill is False

    def test_lens_only_distill_delegates(self, isolated_home, monkeypatch):
        from types import SimpleNamespace
        from trinity_local.commands import dream as dream_cmd
        from trinity_local.commands.me import handle_me_build
        called = []
        monkeypatch.setattr(dream_cmd, "handle_dream",
                            lambda a: called.append(a) or 0)
        args = SimpleNamespace(deep=False, only_distill=True, dry_run=False,
                               sample_size=80, k_basins=None)
        handle_me_build(args)
        assert len(called) == 1 and called[0].only_distill is True

    def test_dream_alias_help_names_lens_deep(self):
        """The alias must SAY it's an alias — one concept in every surface."""
        import argparse
        from trinity_local.commands import dream as dream_command

        # `main.build_parser()` deliberately hides the compatibility alias from
        # top-level discovery. Inspect its direct registration instead: `dream`
        # remains callable for existing scripts and must point them to lens.
        parser = argparse.ArgumentParser(prog="trinity-local")
        sub = parser.add_subparsers(dest="command")
        dream_command.register(sub)
        dream_help = next(ca.help for ca in sub._choices_actions
                          if ca.dest == "dream")
        assert "lens --deep" in dream_help and "alias" in dream_help.lower()


class TestLensSatelliteFlags:
    """One deep verb (Ousterhout closure, 2026-07-05): the satellite actions
    ride `lens` as flags; the standalone verbs are compat aliases."""

    def _spy(self, monkeypatch, name):
        from trinity_local.commands import me as me_cmd
        calls = []
        monkeypatch.setattr(me_cmd, name, lambda a: calls.append(True) or 0)
        return calls

    def test_each_flag_delegates(self, isolated_home, monkeypatch):
        from types import SimpleNamespace
        from trinity_local.commands.me import handle_me_build
        for flag, handler in [
            ("lens_acts", "handle_lens_acts"),
            ("lens_resync", "handle_lens_resync"),
            ("lens_stop", "handle_lens_stop"),
            ("lens_setup", "handle_lens_setup"),
            ("lens_generators", "handle_lens_generators"),
        ]:
            calls = self._spy(monkeypatch, handler)
            args = SimpleNamespace(deep=False, only_distill=False, dry_run=False,
                                   sample_size=80, k_basins=None,
                                   **{flag: True})
            handle_me_build(args)
            assert calls == [True], f"lens --{flag} did not delegate to {handler}"

    def test_alias_helps_name_the_flag(self):
        """Source guard (main.py collapses top-level help, so pseudo-actions
        aren't inspectable for module verbs): each satellite parser's help must
        declare itself an alias of the lens flag."""
        import inspect
        from trinity_local.commands import me as me_cmd
        src = inspect.getsource(me_cmd.register)
        for flag in ("--stop", "--setup", "--generators", "--resync", "--acts"):
            assert f"(Alias for `lens {flag}`.)" in src, (
                f"satellite verb for {flag} no longer declares itself an alias"
            )
        # and the lens parser itself must register every satellite flag
        for flag in ("--stop", "--setup", "--generators", "--resync", "--acts"):
            assert f'"{flag}"' in src, f"lens lost the {flag} flag"


class TestEffortReconciledAtLoad:
    """One mechanism for effort (Ousterhout closure): an inline
    `-c model_reasoning_effort=X` is lifted into config.effort at load and
    stripped from args — the 2026-07-04 stamp-bug class defined out of
    existence at the source."""

    def test_inline_effort_lifted_and_stripped(self, tmp_path, monkeypatch):
        import json
        cfg = {
            "providers": {
                "codex": {
                    "type": "codex", "command": ["codex", "exec"],
                    "args": ["--sandbox", "workspace-write", "-c",
                             'model_reasoning_effort="xhigh"'],
                    "model": "gpt-5.5", "effort": "high",
                },
            },
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        from trinity_local.config import load_config
        loaded = load_config(str(p))
        prov = loaded.providers["codex"]
        assert prov.effort == "xhigh", "inline args value must win (it wins at dispatch)"
        assert not any("model_reasoning_effort" in str(a) for a in prov.args), (
            "the inline mechanism must be stripped — one source of truth"
        )

    def test_effort_field_alone_passes_through(self, tmp_path):
        import json
        cfg = {"providers": {"claude": {
            "type": "cli", "command": ["claude", "-p"], "args": [],
            "model": "claude-opus-4-8", "effort": "high"}}}
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        from trinity_local.config import load_config
        assert load_config(str(p)).providers["claude"].effort == "high"
