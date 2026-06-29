"""Schema versioning + forward migration runner (#183).

Pins: missing marker → v0; the runner walks v0→SCHEMA_VERSION and persists
the marker; idempotent re-run is a no-op; a raised migration leaves the
marker at the last success (never half-advances); the v0→v1 migration
recovers the legacy preference stores into the unified ledger.
"""
from __future__ import annotations

import json

import pytest


@pytest.mark.usefixtures("patch_trinity_home")
class TestSchemaVersionMarker:
    def test_missing_marker_reads_v0(self):
        from trinity_local.migrations import current_schema_version
        assert current_schema_version() == 0

    def test_malformed_marker_reads_v0(self):
        from trinity_local.migrations import _version_path, current_schema_version
        p = _version_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not json", encoding="utf-8")
        assert current_schema_version() == 0

    def test_reads_recorded_version(self):
        from trinity_local.migrations import _version_path, current_schema_version
        p = _version_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        assert current_schema_version() == 1


@pytest.mark.usefixtures("patch_trinity_home")
class TestRunMigrations:
    def test_walks_to_current_and_persists(self):
        from trinity_local.migrations import (
            SCHEMA_VERSION,
            current_schema_version,
            run_migrations,
        )
        report = run_migrations()
        assert report["ok"] is True
        assert report["from"] == 0
        assert report["to"] == SCHEMA_VERSION
        assert current_schema_version() == SCHEMA_VERSION

    def test_idempotent_second_run_is_noop(self):
        from trinity_local.migrations import SCHEMA_VERSION, run_migrations
        run_migrations()
        report = run_migrations()
        assert report["from"] == SCHEMA_VERSION
        assert report["to"] == SCHEMA_VERSION
        assert report["applied"] == []

    def test_v0_to_v1_recovers_legacy_stores(self):
        # The v0→v1 migration seeds the unified ledger from legacy files.
        from trinity_local.me.basins import me_dir
        from trinity_local.me.preference_acts import load_preference_acts
        from trinity_local.migrations import run_migrations

        d = me_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "rejections.jsonl").write_text(
            json.dumps({"id": "r1", "type": "REFRAME", "model_quote": "m",
                        "user_substitute": "u"}) + "\n",
            encoding="utf-8",
        )
        run_migrations()
        assert any(a.id == "r1" for a in load_preference_acts())

    def test_failed_migration_does_not_advance_marker(self, monkeypatch):
        # A raised migration stops the walk and leaves the marker at the
        # last success (here: still 0), so the next launch retries.
        import trinity_local.migrations as migrations

        def _boom():
            raise RuntimeError("simulated migration failure")

        bad = migrations.Migration(0, 1, "boom", _boom)
        monkeypatch.setattr(migrations, "MIGRATIONS", [bad])
        monkeypatch.setattr(migrations, "SCHEMA_VERSION", 1)
        report = migrations.run_migrations()
        assert report["ok"] is False
        assert report["to"] == 0
        assert migrations.current_schema_version() == 0  # not advanced

    def test_runner_never_raises_on_migration_bug(self, monkeypatch):
        import trinity_local.migrations as migrations

        def _boom():
            raise ValueError("kaboom")

        monkeypatch.setattr(
            migrations, "MIGRATIONS",
            [migrations.Migration(0, 1, "boom", _boom)],
        )
        monkeypatch.setattr(migrations, "SCHEMA_VERSION", 1)
        # Must return a report, not propagate.
        report = migrations.run_migrations()
        assert report["ok"] is False and "kaboom" in (report["error"] or "")


@pytest.mark.usefixtures("patch_trinity_home")
class TestMainInvokesMigrations:
    def test_main_helper_runs_and_marks_version(self):
        from trinity_local.main import _run_schema_migrations
        from trinity_local.migrations import SCHEMA_VERSION, current_schema_version
        _run_schema_migrations()
        assert current_schema_version() == SCHEMA_VERSION
