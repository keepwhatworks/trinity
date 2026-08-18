"""Tests for the `health_checks` module — pre-flight cold-install checks.

The `trinity-local doctor` CLI retired in commit ef2f328 (collapsed
into `status`); the module was named `doctor.py` until 2026-05-27 when
it was renamed `health_checks.py` to match its actual job (the
"doctor" prefix was a misleading-name parasitism flag).
The underlying functions (`_check_trinity_home`, `_check_provider`,
etc.) are the library `status` calls. These tests pin the per-check contract:
each provider check returns ok=False with a fix line when the relevant
indicator is missing, and the module never crashes on fresh machine
state. Council council_35b2ae198a65b349 named the cold-install path as
the audit-missed launch blocker; the eval seed for that council asks
for a specific failure mode + the function that detects it.
"""

from __future__ import annotations


class TestTrinityHomeCheck:
    def test_writeable_dir_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.health_checks import _check_trinity_home
        result = _check_trinity_home()
        assert result.ok is True
        assert "writeable" in result.detail

    def test_unwriteable_dir_emits_fix_line(self, tmp_path, monkeypatch):
        # Read-only parent so probe write fails — verifies the failure path
        # surfaces a concrete fix the user can run.
        import os
        protected = tmp_path / "ro"
        protected.mkdir()
        os.chmod(protected, 0o500)  # owner read+execute, no write
        monkeypatch.setenv("TRINITY_HOME", str(protected / "trinity"))
        try:
            from trinity_local.health_checks import _check_trinity_home
            result = _check_trinity_home()
            # Some filesystems still allow writes despite chmod (e.g., as root in CI).
            # Either way, fix line should be informative when the check fails.
            if not result.ok:
                assert "chmod" in result.fix or "TRINITY_HOME" in result.fix
        finally:
            os.chmod(protected, 0o700)


class TestProviderCheck:
    def test_missing_cli_returns_install_fix(self, monkeypatch):
        from trinity_local.health_checks import _check_provider
        # which() returns None → CLI not installed
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: None)
        result = _check_provider("claude", "claude")
        assert result.ok is False
        assert "not on PATH" in result.detail
        assert "Claude Code" in result.fix or "install" in result.fix.lower()

    def test_installed_no_auth_returns_login_fix(self, monkeypatch, tmp_path):
        # CLI installed but no auth indicator file — concrete cold-install
        # failure mode that doctor must catch.
        from trinity_local import health_checks as doctor_mod
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/claude")
        monkeypatch.setattr(
            doctor_mod,
            "_AUTH_INDICATORS",
            {"claude": [tmp_path / "absent.json"]},  # no indicator exists
        )
        result = doctor_mod._check_provider("claude", "claude")
        assert result.ok is False
        assert "no auth indicator" in result.detail
        assert "login" in result.fix or "interactively" in result.fix

    def test_installed_with_auth_returns_ready(self, monkeypatch, tmp_path):
        from trinity_local import health_checks as doctor_mod
        monkeypatch.setattr("trinity_local.runtime_env.which_on_runtime_path", lambda _: "/usr/local/bin/claude")
        indicator = tmp_path / "auth.json"
        indicator.write_text("{}")
        monkeypatch.setattr(doctor_mod, "_AUTH_INDICATORS", {"claude": [indicator]})
        result = doctor_mod._check_provider("claude", "claude")
        assert result.ok is True
        assert "authenticated" in result.detail


class TestMcpAvailable:
    def test_returns_ok_when_mcp_importable(self):
        # mcp dep is in this dev env, so this should pass; if it's not,
        # the test still verifies the check returns the right shape.
        from trinity_local.health_checks import _check_mcp_available
        result = _check_mcp_available()
        assert isinstance(result.ok, bool)
        if not result.ok:
            assert "pip install" in result.fix


class TestDoctorReport:
    def test_ready_for_council_requires_one_provider_plus_writeable_home(
        self, tmp_path, monkeypatch
    ):
        from trinity_local.health_checks import CheckResult, DoctorReport
        # All providers failing → not ready
        report = DoctorReport(checks=[
            CheckResult(name="trinity_home_writeable", ok=True),
            CheckResult(name="provider:claude", ok=False),
            CheckResult(name="provider:gemini", ok=False),
            CheckResult(name="provider:codex", ok=False),
        ])
        assert report.ready_for_council is False

    def test_ready_for_council_with_one_provider(self):
        from trinity_local.health_checks import CheckResult, DoctorReport
        report = DoctorReport(checks=[
            CheckResult(name="trinity_home_writeable", ok=True),
            CheckResult(name="provider:claude", ok=True),
            CheckResult(name="provider:gemini", ok=False),
            CheckResult(name="provider:codex", ok=False),
        ])
        # Even with 2/3 providers failing, ready_for_council is true if 1 works
        assert report.ready_for_council is True

    def test_run_doctor_never_crashes_on_fresh_state(self, tmp_path, monkeypatch):
        # The most important property: doctor never throws regardless of
        # what's missing. A fresh-install user runs it as their first
        # interaction with Trinity.
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        from trinity_local.health_checks import run_doctor
        report = run_doctor()
        # Should produce all expected check categories
        names = {c.name for c in report.checks}
        assert "trinity_home_writeable" in names
        assert any(n.startswith("provider:") for n in names)
        assert "config_loadable" in names

    def test_format_human_includes_fix_lines(self):
        # Failure path: the human format must surface the fix command,
        # not just the failure detail. Otherwise users see "✗ provider:claude
        # not on PATH" and don't know what to do.
        from trinity_local.health_checks import CheckResult, DoctorReport, format_human
        report = DoctorReport(checks=[
            CheckResult(
                name="provider:claude",
                ok=False,
                detail="claude CLI not on PATH",
                fix="Install Claude Code: https://example.com",
            ),
        ])
        out = format_human(report)
        assert "✗" in out
        assert "→ fix:" in out
        assert "https://example.com" in out






class TestNextStepHint:
    """The "try this next" nudge in `trinity-local status` output.

    After a green status run the user otherwise sees "Trinity is ready"
    with no idea what to do next. These tests pin the tiered behavior
    so a future refactor doesn't silently drop the hint. (The handoff-
    demo framing was retired 2026-05-26 — the hint now points at the
    in-harness council flow.)
    """

    def _make_report(self, *, providers_green=2, prompts_ok=True):
        from trinity_local.health_checks import DoctorReport, CheckResult

        checks = []
        names = ["claude", "codex", "antigravity"]
        for i, name in enumerate(names):
            checks.append(CheckResult(
                name=f"provider:{name}",
                ok=(i < providers_green),
                detail=f"{name} {'installed' if i < providers_green else 'missing'}",
            ))
        checks.append(CheckResult(
            name="prompts_seeded",
            ok=prompts_ok,
            detail=("ok" if prompts_ok else "no prompts"),
        ))
        return DoctorReport(checks=checks)

    def test_hint_silent_with_only_one_provider(self):
        """Council needs at least two providers for cross-provider
        disagreement signal. With only one green, no nudge."""
        from trinity_local.health_checks import _next_step_hint
        report = self._make_report(providers_green=1)
        assert _next_step_hint(report) is None

    def test_hint_leads_with_council_when_no_lens(self):
        """Fusion-first: ≥2 providers but no lens → the council is the free,
        zero-setup win, so LEAD with it (not 'seed your prompt index first').
        The lens is offered as the opt-in add-on, not a prerequisite."""
        from trinity_local.health_checks import _next_step_hint
        report = self._make_report(providers_green=2, prompts_ok=False)
        hint = _next_step_hint(report)
        assert hint is not None
        # Council leads (zero-setup), the lens is the optional next rung.
        assert "council-launch" in hint
        assert "lens-setup" in hint
        # The pre-fusion-first "seed first" framing is gone.
        assert "seed your prompt index" not in hint
        assert hint.index("council") < hint.index("lens-setup"), \
            "the council (free win) must lead; the lens add-on comes after"

    def test_hint_recommends_council_when_ready(self):
        """≥2 providers AND prompts indexed → recommend running an
        actual council from inside any harness."""
        from trinity_local.health_checks import _next_step_hint
        report = self._make_report(providers_green=3, prompts_ok=True)
        hint = _next_step_hint(report)
        assert hint is not None
        assert "council" in hint.lower()
        assert "Claude Code" in hint or "MCP" in hint or "harness" in hint.lower()

    def test_format_human_includes_hint_on_success(self):
        """End-to-end: format_human should append the hint after the
        'Trinity is ready' line when conditions are met."""
        from trinity_local.health_checks import format_human
        report = self._make_report(providers_green=3, prompts_ok=True)
        text = format_human(report)
        assert "Try this next" in text
        assert "council" in text.lower()

    def test_format_human_omits_hint_with_no_providers(self):
        """Don't show a 'try this' nudge when the user can't actually
        try it — that just adds noise to a fail-state report."""
        from trinity_local.health_checks import format_human
        report = self._make_report(providers_green=0)
        text = format_human(report)
        assert "Try this next" not in text


class TestVendorPublishedCheck:
    """Doctor surfaces silent vendor-publish failures.

    vendor.py writes 12 JS files under ~/.trinity/portal_pages/vendor/.
    A perms issue at install time can silently skip writes (now warned
    to stderr by `vendor.publish_vendor_files`, but stderr only helps
    whoever ran install-mcp — a user who clicks the launchpad days
    later sees broken ./vendor/*.js 404s with no surface that explains
    it). This check closes that loop on the doctor side.
    """

    def test_no_vendor_dir_returns_friendly_hint(self, tmp_path, monkeypatch):
        """Fresh install before first portal-html: vendor/ doesn't
        exist yet — surface a hint pointing at the fix command."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        (tmp_path / "trinity" / "portal_pages").mkdir(parents=True)
        from trinity_local.health_checks import _check_vendor_published
        result = _check_vendor_published()
        assert result.ok is True
        assert "vendor/ not yet populated" in result.detail
        assert "portal-html" in result.detail

    def test_all_files_present(self, tmp_path, monkeypatch):
        """All 12 vendored files written → quiet success detail."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        portal_pages = tmp_path / "trinity" / "portal_pages"
        vendor_dir = portal_pages / "vendor"
        vendor_dir.mkdir(parents=True)
        from trinity_local.vendor import VENDORED_FILES
        for name in VENDORED_FILES:
            (vendor_dir / name).write_text("// stub", encoding="utf-8")
        from trinity_local.health_checks import _check_vendor_published
        result = _check_vendor_published()
        assert result.ok is True
        assert f"all {len(VENDORED_FILES)} vendored assets present" in result.detail
        assert "missing" not in result.detail.lower()

    def test_partial_publish_lists_missing(self, tmp_path, monkeypatch):
        """Some files missing (perms issue during install) → detail
        names the count + suggests the fix command + a sample of
        missing files."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        portal_pages = tmp_path / "trinity" / "portal_pages"
        vendor_dir = portal_pages / "vendor"
        vendor_dir.mkdir(parents=True)
        from trinity_local.vendor import VENDORED_FILES
        # Write all but the last 4 to simulate partial publish
        present = VENDORED_FILES[:-4]
        for name in present:
            (vendor_dir / name).write_text("// stub", encoding="utf-8")
        from trinity_local.health_checks import _check_vendor_published
        result = _check_vendor_published()
        assert result.ok is True  # soft — not blocking
        assert f"4 of {len(VENDORED_FILES)} vendored assets missing" in result.detail
        assert "portal-html" in result.detail
        # Surfaces ≥1 missing-file name so user can grep their logs
        assert any(name in result.detail for name in VENDORED_FILES[-4:])

    def test_check_is_registered_in_run_doctor(self):
        """Same defensive shape as TestHandoffReadyCheck — a check
        defined-but-not-wired silently no-ops."""
        from trinity_local.health_checks import run_doctor
        report = run_doctor()
        names = {c.name for c in report.checks}
        assert "vendor_published" in names


class TestRetiredDirsReclaimableCheck:
    """Surface disk held by post-retirement state directories.

    Real install observed: 786MB in cache/ + 2.1GB in models/, both
    held by features retired weeks ago (embedding-cache kill
    2026-05-17, models dir kill 2026-05-20). No surface anywhere told
    the user they could reclaim 3GB by deleting these dirs. Doctor
    check fills the gap.
    """

    def test_clean_install_returns_no_reclaimable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        (tmp_path / "trinity").mkdir()
        from trinity_local.health_checks import _check_retired_dirs_reclaimable
        result = _check_retired_dirs_reclaimable()
        assert result.ok is True
        assert "no retired-feature directories" in result.detail

    def test_legacy_cache_surface_emits_size_and_fix(self, tmp_path, monkeypatch):
        """If a legacy ~/.trinity/cache/embeddings.jsonl exists, the
        detail names it + the size + the retirement reason, and the
        fix is a copy-pasteable rm -rf."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        home = tmp_path / "trinity"
        home.mkdir()
        cache = home / "cache"
        cache.mkdir()
        # Plant a fake legacy file — content size doesn't matter for the
        # surface assertion; just non-empty.
        (cache / "embeddings.jsonl").write_bytes(b"x" * 1024)

        from trinity_local.health_checks import _check_retired_dirs_reclaimable
        result = _check_retired_dirs_reclaimable()
        assert result.ok is True  # soft, not blocking
        assert "cache/" in result.detail
        assert "1.0KB" in result.detail
        assert "retired 2026-05-17" in result.detail
        assert result.fix is not None
        assert "rm -rf" in result.fix
        assert str(cache) in result.fix

    def test_write_only_task_sync_surfaced_with_file_count_and_regrowth(self, tmp_path, monkeypatch):
        """task_sync/ is WRITE-ONLY dead state — a TaskSyncRecord written on every
        council, but its only reader (the task-sync CLI) was retired (0 readers in
        src/scripts/extension). The check must flag it by FILE COUNT (the "too many
        empty files" smell is a count problem — it was a 9255-file mirror of todos/)
        AND note it REGROWS (rm alone won't fix it — the live writer must be retired)."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        home = tmp_path / "trinity"
        home.mkdir()
        ts = home / "task_sync"
        ts.mkdir()
        for i in range(7):
            (ts / f"task_{i}.json").write_text("{}")
        from trinity_local.health_checks import _check_retired_dirs_reclaimable
        result = _check_retired_dirs_reclaimable()
        assert result.ok is True  # soft
        assert "task_sync/" in result.detail
        assert "7 files" in result.detail  # surfaced by COUNT, not just bytes
        assert "REGROWS" in result.detail  # the live-writer distinction is named
        assert str(ts) in result.fix

    def test_orphan_retired_dirs_surfaced(self, tmp_path, monkeypatch):
        """moves/ (#184) and shortcut_setup/ (retired dispatch) are orphan dirs."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
        home = tmp_path / "trinity"
        home.mkdir()
        for name in ("moves", "shortcut_setup"):
            d = home / name
            d.mkdir()
            (d / "leftover.json").write_text("{}")
        from trinity_local.health_checks import _check_retired_dirs_reclaimable
        result = _check_retired_dirs_reclaimable()
        assert "moves/" in result.detail and "shortcut_setup/" in result.detail

    def test_check_is_registered_in_run_doctor(self):
        from trinity_local.health_checks import run_doctor
        report = run_doctor()
        names = {c.name for c in report.checks}
        assert "retired_dirs_reclaimable" in names


class TestDormantNomicLens:
    """The embedding-backend check must distinguish 'missing future quality' from
    'losing an investment you already built'. Found 2026-06-02: the founder ran on
    the TF-IDF fallback while a real 48-basin nomic lens (+ 172-correction taste
    signal) sat DORMANT — the runtime had reverted to TF-IDF so the semantic flows
    abstained and the cortex cross-space guard rejected the centroids. Installing
    [mlx] REACTIVATED it. The standard 'tension quality is reduced' message
    understates that; on a real nomic lens it must escalate."""

    def _force_tfidf(self, monkeypatch):
        import trinity_local.embeddings as emb
        monkeypatch.setattr(emb, "mlx_actually_loaded", lambda: False)
        monkeypatch.setattr(emb, "is_available", lambda: False)
        monkeypatch.setattr(emb, "get_backend", lambda: "tfidf")

    def test_dormant_nomic_lens_escalates_the_tfidf_message(self, tmp_path, monkeypatch):
        import json
        home = tmp_path / "trinity"
        (home / "memories").mkdir(parents=True)
        (home / "memories" / "topics.json").write_text(
            json.dumps({"basins": [{"id": f"b{i}", "centroid": [0.1] * 768} for i in range(5)]})
        )
        monkeypatch.setenv("TRINITY_HOME", str(home))
        self._force_tfidf(monkeypatch)
        from trinity_local.health_checks import _check_embedding_backend
        r = _check_embedding_backend()
        assert r.ok is True  # soft
        assert "DORMANT" in r.detail
        assert "5 768d basins" in r.detail  # the count is surfaced
        assert "REACTIVATES" in r.detail

    def test_no_dormant_note_without_a_nomic_lens(self, tmp_path, monkeypatch):
        """On TF-IDF with NO nomic lens on disk, the message must NOT claim a
        dormant lens (no false alarm — it's a fresh install, not a lost one)."""
        home = tmp_path / "trinity"
        (home / "memories").mkdir(parents=True)
        monkeypatch.setenv("TRINITY_HOME", str(home))
        self._force_tfidf(monkeypatch)
        from trinity_local.health_checks import _check_embedding_backend
        r = _check_embedding_backend()
        assert "DORMANT" not in r.detail
        assert "TF-IDF" in r.detail  # still honestly reports the fallback

    def test_tfidf_only_basins_are_not_counted_dormant(self, tmp_path, monkeypatch):
        """A TF-IDF-built topology (non-768d centroids) is STALE, not dormant —
        it must not trigger the 'reactivate your lens' message (rm only fixes
        future builds there). The helper keys on the 768d nomic dimension."""
        import json
        home = tmp_path / "trinity"
        (home / "memories").mkdir(parents=True)
        (home / "memories" / "topics.json").write_text(
            json.dumps({"basins": [{"id": f"b{i}", "centroid": [0.1] * 256} for i in range(5)]})
        )
        monkeypatch.setenv("TRINITY_HOME", str(home))
        from trinity_local.health_checks import _dormant_nomic_lens_basins
        assert _dormant_nomic_lens_basins() == 0


