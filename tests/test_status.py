"""Tests for the status command."""
from __future__ import annotations

import contextlib
import json
from unittest.mock import patch

from trinity_local.commands.status import handle_status


class Args:
    def __init__(self, as_json: bool = False):
        self.as_json = as_json


class TestStatusCommand:
    """Test the status command outputs."""

    def test_status_json_output(self, tmp_path, monkeypatch, capsys):
        """Test status --json produces valid JSON."""
        # Isolate TRINITY_HOME so run_doctor() + signal helpers probe
        # tmp_path instead of the dev machine's real ~/.trinity/. Without
        # this the test took ~4s walking real state (40k+ transcripts);
        # with it the test runs in ~0.3s.
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        with patch("trinity_local.commands.status.state_dir") as mock_state:
            mock_state.return_value = tmp_path
            mock_state_dir = tmp_path / "test"
            mock_state_dir.mkdir()

            with patch("trinity_local.commands.status.tasks_dir") as mock_tasks:
                mock_tasks.return_value = mock_state_dir
                with patch(
                    "trinity_local.commands.status.check_all_adapters"
                ) as mock_adapters:
                    from trinity_local.adapters import AdapterStatus

                    mock_adapters.return_value = [
                        AdapterStatus(
                            provider="claude",
                            cli_name="claude",
                            installed=True,
                            version="1.0",
                            transcript_root=None,
                        )
                    ]
                    # (count_actions_by_status mock retired with the action
                    # store, #332 — nullcontext preserves the nesting depth.)
                    with contextlib.nullcontext():
                        with patch(
                            "trinity_local.commands.status.check_drift"
                        ) as mock_drift:
                            mock_drift.return_value = []

                            args = Args(as_json=True)
                            handle_status(args)

                            captured = capsys.readouterr()
                            output = json.loads(captured.out)
                            assert "trinity_home" in output
                            assert "adapters" in output
                            assert "drift_alerts" in output

    def test_status_human_output(self, tmp_path, monkeypatch, capsys):
        """Test status with human-readable output."""
        # Same TRINITY_HOME isolation as the JSON variant — keeps the
        # test from probing real ~/.trinity/ state.
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        with patch("trinity_local.commands.status.state_dir") as mock_state:
            mock_state.return_value = tmp_path
            mock_state_dir = tmp_path / "test"
            mock_state_dir.mkdir()

            with patch("trinity_local.commands.status.tasks_dir") as mock_tasks:
                mock_tasks.return_value = mock_state_dir
                with patch(
                    "trinity_local.commands.status.check_all_adapters"
                ) as mock_adapters:
                    from trinity_local.adapters import AdapterStatus

                    mock_adapters.return_value = [
                        AdapterStatus(
                            provider="claude",
                            cli_name="claude",
                            installed=True,
                            version="1.0",
                            transcript_root=None,
                        )
                    ]
                    # (count_actions_by_status mock retired with the action
                    # store, #332 — nullcontext preserves the nesting depth.)
                    with contextlib.nullcontext():
                        with patch(
                            "trinity_local.commands.status.check_drift"
                        ) as mock_drift:
                            mock_drift.return_value = []

                            args = Args(as_json=False)
                            handle_status(args)

                            captured = capsys.readouterr()
                            assert "Trinity Local — Status" in captured.out
                            assert "Adapters:" in captured.out


class TestStatusDriftMessageRender:
    """SURFACE-BINDING guard for the drift alert COPY that `status` paints.

    The drift figure + its window-phrase copy (the 3c8cb24f data-correctness
    class — "in the last week vs the prior week", DERIVED from the real window
    params, never the old hardcoded "prior 2 weeks") is exhaustively guarded at
    the PRIMITIVE (test_drift.py). But every test that drives `handle_status`
    mocks `check_drift` to `[]`, so the human-render binding at status.py L422-425
    — the ONLY surface that ever paints `alert.message` to a user (the JSON path
    emits only `len(drift_alerts)`, a bare count) — is exercised with a REAL alert
    by NOTHING. A reformat that dropped `.message`, printed a count-only line, or
    re-hardcoded the window phrase would ship a wrong/empty drift line while the
    whole suite stayed green: the recurring "value tested at the primitive, not at
    the surface binding that ships it" shape (loop_data_correctness_bug_shape).

    This drives the REAL `check_drift` (NOT mocked) inside `handle_status` over a
    discriminating seed and asserts the PAINTED line carries the right value,
    direction, and window phrase end-to-end (seed -> primitive -> printed pixels).
    """

    def _seed_discriminating_drop(self, home):
        """Seed outcomes.jsonl so check_drift emits exactly one -100% DROP for
        claude/coding: baseline (7,14] = 6 CLEAN completions (avg 1.0), current
        (<7d) = 4 FAILED (avg 0.0) -> delta_pct = (0-1)/1*100 = -100.0. Decoys
        aged 15-20d sit OUTSIDE the 14d baseline cutoff (proving the baseline is
        the SINGLE prior week, not two). Returns the expected window-phrase facts.
        """
        import json as _json
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        records = []
        for d in range(8, 14):  # baseline (7,14]: 6 clean
            records.append({"provider": "claude", "model_id": "claude-sonnet-4",
                            "task_type": "coding", "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=d)).isoformat()})
        for d in range(15, 21):  # decoys: OUTSIDE the 14d baseline cutoff
            records.append({"provider": "claude", "model_id": "claude-sonnet-4",
                            "task_type": "coding", "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=d)).isoformat()})
        for i in range(4):  # current (<7d): 4 failing -> 100% drop
            records.append({"provider": "claude", "model_id": "claude-sonnet-4",
                            "task_type": "coding", "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=3) + timedelta(hours=i)).isoformat()})
        (home / "outcomes.jsonl").write_text(
            "\n".join(_json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def test_status_paints_real_drift_message_with_correct_value_and_window(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed_discriminating_drop(tmp_path)

        # PRECONDITION-B (render-INDEPENDENT): the seed really IS the discriminating
        # drift state — one -100% DROP over the single prior week, decoys excluded.
        # Computed from the fixture via the primitive, NOT from the painted output,
        # so this test can't pass on a render that prints the wrong thing.
        from trinity_local.drift import check_drift
        alerts = check_drift()
        assert len(alerts) == 1, "fixture must produce exactly one drift alert"
        a = alerts[0]
        assert a.delta_pct == -100.0 and a.current_score == 0.0 and a.baseline_score == 1.0, (
            f"fixture is not the seeded 100% drop: {a.to_dict()}")
        assert a.baseline_sessions == 6 and a.current_sessions == 4, (
            f"decoys (15-20d) leaked into the baseline: {a.to_dict()}")

        # Drive the REAL render path: check_drift is NOT patched here — the binding
        # at status.py L86 (compute) -> L422-425 (paint) runs end-to-end. Stub only
        # the heavy/irrelevant siblings so the test stays fast + isolated.
        from trinity_local.adapters import AdapterStatus
        with patch("trinity_local.commands.status.state_dir", return_value=tmp_path), \
             patch("trinity_local.commands.status.tasks_dir", return_value=tmp_path), \
             patch("trinity_local.commands.status.check_all_adapters",
                   return_value=[AdapterStatus(provider="claude", cli_name="claude",
                                               installed=True, version="1.0",
                                               transcript_root=None)]):
            handle_status(Args(as_json=False))
        out = capsys.readouterr().out

        # Isolate the painted drift line (the "    · ..." row under the ⚠ header).
        # Key on a VERB-INDEPENDENT substring ("claude's coding quality") so a verb
        # flip (dropped->rose) still finds the line and reds the DIRECTION assertion
        # below rather than vanishing as "no line painted".
        drift_lines = [ln for ln in out.splitlines() if "claude's coding quality" in ln]
        assert len(drift_lines) == 1, (
            "status did not paint the drift alert message — the human render binding "
            "(status.py L422-425) dropped `alert.message`; the drift figure no longer "
            f"reaches the user.\nFull output:\n{out}")
        line = drift_lines[0]

        # VALUE: the seeded -100% drop must paint as "dropped 100%".
        assert "100%" in line, (
            f"painted drift % is wrong vs the seed (expected a 100% change): {line!r}")
        # DIRECTION: a DROP, not a rise. The seed is current_avg(0.0) < baseline(1.0),
        # so the painted verb must be "dropped" — an inverted sign would say "rose".
        assert "dropped" in line and "rose" not in line and "improved" not in line, (
            f"painted drift direction is wrong vs the seed (a drop, not a rise): {line!r}")
        # WINDOW PHRASE (the 3c8cb24f class): current=7d -> "last week"; baseline
        # span 14-7=7d -> "the prior week". NEVER the old hardcoded "prior 2 weeks".
        assert "in the last week vs the prior week" in line, (
            f"painted window phrase drifted from the real params: {line!r}")
        assert "2 weeks" not in line, (
            "status painted 'prior 2 weeks' while the figure compares against the SINGLE "
            f"prior week (the 3c8cb24f over-statement, now at the surface): {line!r}")
        # WHAT CHANGED: names the right provider + task.
        assert "claude's coding quality" in line, (
            f"painted drift line names the wrong provider/task vs the seed: {line!r}")


class TestActionableSignals:
    """Status surfaces action-takeable signals from the launchpad
    feature set (#140 lens edits, #141 conflicts, #150 capture-drift)
    so CLI-only users see them without opening the launchpad. Section
    silently hidden when all signal counts are zero."""

    def test_signals_section_hidden_when_all_zero(self, tmp_path, monkeypatch, capsys):
        """Steady-green install: no signals section at all. Keeps the
        common case terse."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        # Stub the three signal sources to all-zero responses
        from trinity_local.me import lens_edits as le_mod
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(le_mod, "pending_lens_edits_count", lambda: 0)
        monkeypatch.setattr(repair_mod, "detect_failure_patterns", lambda diag: [])

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        assert "Signals:" not in out, (
            "Signals section must be silent when all signal counts are zero"
        )

    def test_lens_edits_pending_surfaces_signal(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me import lens_edits as le_mod
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(le_mod, "pending_lens_edits_count", lambda: 3)
        monkeypatch.setattr(repair_mod, "detect_failure_patterns", lambda diag: [])

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        assert "Signals:" in out
        assert "lens.md edits" in out
        assert "3 pending" in out
        assert "trinity-local lens" in out

    def test_code_patch_pattern_surfaces_with_auto_repair_hint(self, tmp_path, monkeypatch, capsys):
        """The #150 code-patch pattern points at the auto-repair flow.
        User-action patterns get a separate signal line — they need
        manual cookie refresh, not a council dispatch."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me import lens_edits as le_mod
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(le_mod, "pending_lens_edits_count", lambda: 0)
        monkeypatch.setattr(repair_mod, "detect_failure_patterns", lambda diag: [
            {"fix_kind": "code-patch", "provider": "gemini", "pattern": "provider-extended-silence"},
            {"fix_kind": "user-action", "provider": "claude", "pattern": "stale-auth-cookie"},
        ])

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        assert "capture drift" in out
        assert "1 code-patch" in out
        assert "extension repair --auto" in out
        assert "auth-cookie stale" in out
        assert "refresh login" in out

    def test_browser_captures_section_silent_on_cold_install(
        self, tmp_path, monkeypatch, capsys
    ):
        """No conversations/ directories exist (cold install) →
        Captures section silent. Keeps a clean fresh install from
        rendering an empty/zero-noise block."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod

        # Stub the diagnose() result with no providers existing
        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": False, "captures": 0, "hours_since_last": None},
                "chatgpt": {"exists": False, "captures": 0, "hours_since_last": None},
                "gemini":  {"exists": False, "captures": 0, "hours_since_last": None},
            }},
        )

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        assert "Captures:" not in out, (
            "Captures section must stay silent when no extension data exists"
        )

    def test_browser_captures_section_renders_with_per_provider_rows(
        self, tmp_path, monkeypatch, capsys
    ):
        """When the extension is active, status shows per-provider
        counts + hours-since-last with the same shape as the launchpad
        browser-capture card."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": True, "captures": 50, "hours_since_last": 1.5,
                            "last_capture": "2026-05-24T00:00:00"},
                "chatgpt": {"exists": True, "captures": 12, "hours_since_last": 8.0,
                            "last_capture": "2026-05-23T18:00:00"},
                "gemini":  {"exists": True, "captures": 200, "hours_since_last": 0.2,
                            "last_capture": "2026-05-24T01:50:00"},
            }},
        )

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        assert "Captures:" in out
        # Total across the 3 providers
        assert "262" in out
        # Per-provider rows present
        for slug in ("claude", "chatgpt", "gemini"):
            assert slug in out
        # Last-capture time surfaced
        assert "1.5h ago" in out
        assert "0.2h ago" in out

    def test_browser_captures_directory_exists_but_empty(
        self, tmp_path, monkeypatch, capsys
    ):
        """If a provider's directory exists but has zero files (extension
        installed, never captured anything yet for that provider),
        surface the "installed but no captures yet" hint so the user
        knows to check for capture failures."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": True, "captures": 5, "hours_since_last": 1.0},
                "chatgpt": {"exists": True, "captures": 0, "hours_since_last": None},
                "gemini":  {"exists": False, "captures": 0, "hours_since_last": None},
            }},
        )

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        # chatgpt → installed but no captures yet
        assert "extension installed but no captures yet" in out
        # gemini → never captured (directory missing)
        assert "not yet captured" in out

    def test_captures_show_missing_from_sidebar_when_unsynced(
        self, tmp_path, monkeypatch, capsys
    ):
        """When a provider has captures but the sidebar shows more
        threads than the on-disk count (real production signal we
        observed: chatgpt 37 files, sidebar 38, 1 missing), the
        Captures: line must show the missing count so the user knows
        to run auto-sync. Same data source as the in-provider sync pill."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod
        from trinity_local import capture_host as capture_mod

        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": True, "captures": 30, "hours_since_last": 1.0},
                "chatgpt": {"exists": True, "captures": 37, "hours_since_last": 2.5},
                "gemini":  {"exists": True, "captures": 100, "hours_since_last": 0.5},
            }},
        )

        def fake_sync(payload):
            slug = payload.get("provider")
            # claude fully synced (0 missing); chatgpt has 5 missing;
            # gemini fully synced
            if slug == "chatgpt":
                return {"ok": True, "sidebar_count": 42, "on_disk_count": 37, "missing_count": 5}
            return {"ok": True, "sidebar_count": 30, "on_disk_count": 30, "missing_count": 0}

        monkeypatch.setattr(capture_mod, "_query_sync_status", fake_sync)

        args = Args(as_json=False)
        handle_status(args)
        out = capsys.readouterr().out
        # chatgpt: missing-from-sidebar suffix present
        assert "5 missing from sidebar" in out
        # claude + gemini: NO missing suffix (would be visual noise)
        # We check by reading the claude line; missing suffix absent
        for line in out.splitlines():
            if line.strip().startswith("✅ claude "):
                assert "missing from sidebar" not in line, (
                    "claude was fully synced — no missing-suffix should appear"
                )
            if line.strip().startswith("✅ gemini "):
                assert "missing from sidebar" not in line

    def test_signals_in_json_output_when_active(self, tmp_path, monkeypatch, capsys):
        """JSON output must include the same signals the human output
        renders — scripts/agents parsing JSON otherwise have no
        visibility into the action-takeable items.

        The `signals` key is always present (empty list when nothing
        fires), so callers can `len(status["signals"])` without an
        existence branch."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me import lens_edits as le_mod
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(le_mod, "pending_lens_edits_count", lambda: 7)
        monkeypatch.setattr(repair_mod, "detect_failure_patterns", lambda d: [
            {"fix_kind": "code-patch", "provider": "gemini"},
        ])
        monkeypatch.setattr(repair_mod, "diagnose", lambda: {"providers": {}})

        args = Args(as_json=True)
        handle_status(args)
        payload = json.loads(capsys.readouterr().out)

        assert "signals" in payload
        kinds = [s["kind"] for s in payload["signals"]]
        assert "lens_edits_pending" in kinds
        assert "capture_drift" in kinds

        # Per-signal payload carries count + fix_command
        edits = next(s for s in payload["signals"] if s["kind"] == "lens_edits_pending")
        assert edits["count"] == 7
        assert edits["fix_command"] == "trinity-local lens"

    def test_signals_empty_list_when_steady_green_in_json(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The empty case is `signals: []`, not absent. Scripts must be
        able to `len(...)` without `if "signals" in payload` branch."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me import lens_edits as le_mod
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(le_mod, "pending_lens_edits_count", lambda: 0)
        monkeypatch.setattr(repair_mod, "detect_failure_patterns", lambda d: [])
        monkeypatch.setattr(repair_mod, "diagnose", lambda: {"providers": {}})

        args = Args(as_json=True)
        handle_status(args)
        payload = json.loads(capsys.readouterr().out)
        assert payload["signals"] == []

    def test_captures_in_json_when_extension_active(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Browser-extension captures surface via JSON `captures` key
        when at least one provider directory exists. Absent (not empty)
        when no extension data has ever been captured — same shape as
        human surface's silent-when-cold."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": True, "captures": 12, "hours_since_last": 0.5},
                "chatgpt": {"exists": True, "captures": 5, "hours_since_last": 3.0},
                "gemini":  {"exists": True, "captures": 100, "hours_since_last": 0.1},
            }},
        )

        args = Args(as_json=True)
        handle_status(args)
        payload = json.loads(capsys.readouterr().out)
        assert "captures" in payload
        assert payload["captures"]["total"] == 117
        assert "claude" in payload["captures"]["by_provider"]
        assert payload["captures"]["by_provider"]["claude"]["captures"] == 12

    def test_captures_absent_in_json_when_no_extension_data(
        self, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import extension_repair as repair_mod

        monkeypatch.setattr(
            repair_mod, "diagnose",
            lambda: {"providers": {
                "claude":  {"exists": False, "captures": 0, "hours_since_last": None},
                "chatgpt": {"exists": False, "captures": 0, "hours_since_last": None},
                "gemini":  {"exists": False, "captures": 0, "hours_since_last": None},
            }},
        )

        args = Args(as_json=True)
        handle_status(args)
        payload = json.loads(capsys.readouterr().out)
        # Captures key intentionally absent (not empty dict) to keep
        # cold-install JSON terse — same as human side staying silent.
        assert "captures" not in payload

    def test_signal_block_failure_does_not_break_status(self, tmp_path, monkeypatch, capsys):
        """Each signal helper is wrapped in try/except — a bug in one
        must not break the whole status command. Steady-state diagnostic
        must always render."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me import lens_edits as le_mod

        def explode():
            raise RuntimeError("simulated lens_edits bug")
        monkeypatch.setattr(le_mod, "pending_lens_edits_count", explode)

        args = Args(as_json=False)
        handle_status(args)  # must not raise
        out = capsys.readouterr().out
        # Status still renders end-to-end
        assert "Trinity Local — Status" in out
        assert "State:" in out


# (TestActionsSuggestionFraming removed 2026-07-02 with the action store,
# #332 — the "Actions: N suggested" line it guarded no longer exists.)


class TestSoftDegradedSurfacing:
    """#273: a check that PASSES (ok=True) but carries a fix is degraded-but-
    functional — the embedding backend on the SHA-1 TF-IDF fallback is the
    load-bearing one. format_human only prints fixes for FAILING checks, and
    the one-line verdict says "green — N/N pass", so without surfacing these
    the degradation is silent ("all green while embeddings silently degraded").
    Status must print the soft-degraded detail + fix right under Health."""

    def _run(self, tmp_path, monkeypatch, capsys, checks):
        from trinity_local.health_checks import DoctorReport
        from trinity_local.commands import status as status_mod
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        with patch.object(status_mod, "run_doctor", lambda: DoctorReport(checks=checks)), \
             patch.object(status_mod, "state_dir", lambda: tmp_path), \
             patch.object(status_mod, "tasks_dir", lambda: tmp_path / "t"), \
             patch.object(status_mod, "check_all_adapters", lambda: []), \
             patch.object(status_mod, "check_drift", lambda: []):
            (tmp_path / "t").mkdir(exist_ok=True)
            handle_status(Args(as_json=False))
        return capsys.readouterr().out

    def test_tf_idf_degradation_flips_verdict_to_yellow_not_silent_green(
        self, tmp_path, monkeypatch, capsys
    ):
        from trinity_local.health_checks import CheckResult
        out = self._run(tmp_path, monkeypatch, capsys, [
            CheckResult(name="trinity_home_writeable", ok=True, detail="ok"),
            CheckResult(name="provider:claude", ok=True, detail="ready"),
            CheckResult(
                name="embedding_backend", ok=True,
                detail="running on the SHA-1 TF-IDF embedding fallback",
                fix="pip install -e '.[mlx]'",
            ),
        ])
        health_line = out.split("Health:")[1].split("\n")[0]
        # A soft-degraded (ok=True + fix) check must NOT be counted green: the
        # verdict goes yellow and counts the gap, so the header no longer
        # contradicts the ⚠ shown below it. (Was "green — 3/3 checks pass".)
        assert "yellow" in health_line, f"degradation hid under a green verdict: {health_line!r}"
        assert "green — 3/3" not in out, "soft-degraded check still counted as fully green"
        assert "2/3 green" in health_line and "1 optional gap" in health_line, health_line
        # ...and the degradation detail + fix still surface.
        assert "TF-IDF embedding fallback" in out
        assert "pip install -e '.[mlx]'" in out

    def test_header_gap_count_equals_displayed_warning_count(
        self, tmp_path, monkeypatch, capsys
    ):
        """The core invariant: the "(N optional gaps)" number in the Health
        header must equal the number of ⚠ nudges printed beneath it. Before the
        fix the header counted only FAILED checks while the block printed every
        soft-degraded (ok=True + fix) check — so a home with 3 soft-degraded
        checks read "(0 optional gaps)"/"green" above 3 warnings."""
        import re
        from trinity_local.health_checks import CheckResult
        out = self._run(tmp_path, monkeypatch, capsys, [
            CheckResult(name="trinity_home_writeable", ok=True, detail="ok"),
            CheckResult(name="provider:claude", ok=True, detail="ready"),
            CheckResult(name="lens_built", ok=True, detail="lens not built", fix="trinity-local lens"),
            CheckResult(name="core_distilled", ok=True, detail="core not distilled", fix="trinity-local dream"),
            CheckResult(name="vocab", ok=True, detail="vocab not built", fix="trinity-local dream"),
        ])
        health_block = out.split("Health:")[1].split("Schema:")[0]
        warning_count = health_block.count("⚠")
        m = re.search(r"\((\d+) optional gap", health_block)
        assert m, f"no gap count in header: {health_block!r}"
        header_gaps = int(m.group(1))
        assert header_gaps == warning_count == 3, (
            f"header claims {header_gaps} gaps but {warning_count} ⚠ shown "
            f"(3 soft-degraded checks): {health_block!r}"
        )

    def test_fully_healthy_install_shows_no_degradation_noise(
        self, tmp_path, monkeypatch, capsys
    ):
        """No soft check has a fix → nothing extra prints under Health."""
        from trinity_local.health_checks import CheckResult
        out = self._run(tmp_path, monkeypatch, capsys, [
            CheckResult(name="trinity_home_writeable", ok=True, detail="ok"),
            CheckResult(name="embedding_backend", ok=True, detail="MLX live"),
        ])
        health_block = out.split("Health:")[1].split("Schema:")[0]
        assert "⚠" not in health_block, "healthy install should not show degradation warnings"


class TestStatusLensFreshnessRender:
    """SURFACE-BINDING guard: `status` must PAINT lens-build staleness.

    The 2026-06-29 silent-freeze (lens frozen 18 days / 677 prompts behind while
    the refresh marker false-greened "done") was invisible because NO surface read
    ground-truth lens freshness. status.py now calls lens_freshness_status() and
    paints a ⚠️ line when 'stale'. This drives the real text render with a stale
    verdict and asserts the line + the manual escape hatch are painted — the
    'value computed but not surfaced' shape (loop_data_correctness_bug_shape)."""

    def test_stale_lens_is_painted_with_force_hint(self, tmp_path, monkeypatch, capsys):
        from contextlib import ExitStack
        from unittest.mock import patch

        from trinity_local.adapters import AdapterStatus
        from trinity_local.commands.status import handle_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # Force the ground-truth verdict to 'stale' (the freeze condition).
        monkeypatch.setattr(
            "trinity_local.cold_start.lens_freshness_status",
            lambda: ("stale", "677 new prompts, 424h since last build"),
        )
        with ExitStack() as st:
            mock_state = st.enter_context(patch("trinity_local.commands.status.state_dir"))
            mock_state.return_value = tmp_path
            (tmp_path / "test").mkdir(exist_ok=True)
            st.enter_context(patch("trinity_local.commands.status.tasks_dir")).return_value = tmp_path / "test"
            st.enter_context(patch("trinity_local.commands.status.check_all_adapters")).return_value = [
                AdapterStatus(provider="claude", cli_name="claude", installed=True, version="1.0", transcript_root=None)
            ]
            st.enter_context(patch("trinity_local.commands.status.check_drift")).return_value = []

            class _Args:
                as_json = False

            handle_status(_Args())

        out = capsys.readouterr().out
        assert "lens stale" in out, out[-1500:]
        assert "lens --force" in out  # the manual escape hatch must be offered


class TestStatusCorpusFreshnessRender:
    """SURFACE-BINDING guard: `status` paints corpus-ingest staleness — distinct
    from lens freshness. The 2026-06-29 stall (ingest 13d behind) was invisible
    because no surface read it; status now warns + offers the manual ingest. Reds
    if the print block is dropped (value-computed-but-not-surfaced shape)."""

    def test_stale_corpus_is_painted_with_ingest_hint(self, tmp_path, monkeypatch, capsys):
        from contextlib import ExitStack
        from unittest.mock import patch

        from trinity_local.adapters import AdapterStatus
        from trinity_local.commands.status import handle_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr("trinity_local.stale_pass.ingest_freshness",
                            lambda: ("stale", "last ingest 13d ago (>72h) — the corpus is missing recent transcripts"))
        with ExitStack() as st:
            st.enter_context(patch("trinity_local.commands.status.state_dir")).return_value = tmp_path
            (tmp_path / "test").mkdir(exist_ok=True)
            st.enter_context(patch("trinity_local.commands.status.tasks_dir")).return_value = tmp_path / "test"
            st.enter_context(patch("trinity_local.commands.status.check_all_adapters")).return_value = [
                AdapterStatus(provider="claude", cli_name="claude", installed=True, version="1.0", transcript_root=None)
            ]
            st.enter_context(patch("trinity_local.commands.status.check_drift")).return_value = []

            class _Args:
                as_json = False

            handle_status(_Args())

        out = capsys.readouterr().out
        assert "corpus stale" in out, out[-1500:]
        assert "ingest-recent" in out  # the manual escape hatch
