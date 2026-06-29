"""Tests for model drift detection."""
from __future__ import annotations

import json

from trinity_local.drift import (
    OutcomeRecord,
    _score_outcome,
    check_drift,
)


class TestScoreOutcome:
    def test_clean_completion(self):
        rec = OutcomeRecord("claude", "model-1", "coding", completed=True, error_count=0, session_seconds=60.0, timestamp="")
        assert _score_outcome(rec) == 1.0

    def test_completion_with_errors(self):
        rec = OutcomeRecord("claude", "model-1", "coding", completed=True, error_count=1, session_seconds=60.0, timestamp="")
        assert _score_outcome(rec) == 0.7

    def test_completion_with_many_errors(self):
        rec = OutcomeRecord("claude", "model-1", "coding", completed=True, error_count=5, session_seconds=60.0, timestamp="")
        assert _score_outcome(rec) == 0.3

    def test_not_completed(self):
        rec = OutcomeRecord("claude", "model-1", "coding", completed=False, error_count=0, session_seconds=60.0, timestamp="")
        assert _score_outcome(rec) == 0.0


class TestCheckDrift:
    def test_no_data_no_alerts(self, monkeypatch, tmp_path):
        """No outcomes → no drift alerts."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
        alerts = check_drift()
        assert alerts == []

    def test_drift_detected(self, monkeypatch, tmp_path):
        """When current quality drops below baseline, alert is emitted."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)

        outcomes_path = tmp_path / "outcomes.jsonl"
        records: list[dict] = []

        # Baseline (8–14 days ago): all successful
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        for i in range(6):
            ts = (now - timedelta(days=10) + timedelta(hours=i)).isoformat()
            records.append({
                "provider": "claude", "model_id": "claude-sonnet-4",
                "task_type": "coding", "completed": True,
                "error_count": 0, "timestamp": ts,
            })

        # Current (last 7 days): all failing
        for i in range(4):
            ts = (now - timedelta(days=3) + timedelta(hours=i)).isoformat()
            records.append({
                "provider": "claude", "model_id": "claude-sonnet-4",
                "task_type": "coding", "completed": False,
                "error_count": 0, "timestamp": ts,
            })

        with outcomes_path.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        alerts = check_drift(min_current=3, min_baseline=5)
        assert len(alerts) == 1
        assert alerts[0].provider == "claude"
        assert alerts[0].delta_pct < -20

    def test_no_drift_when_stable(self, monkeypatch, tmp_path):
        """When quality is stable, no alerts."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)

        outcomes_path = tmp_path / "outcomes.jsonl"
        records: list[dict] = []

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        # Both windows: all successful
        for i in range(6):
            ts = (now - timedelta(days=10) + timedelta(hours=i)).isoformat()
            records.append({
                "provider": "gemini", "model_id": "gemini-2.5-pro",
                "task_type": "research", "completed": True,
                "error_count": 0, "timestamp": ts,
            })
        for i in range(4):
            ts = (now - timedelta(days=3) + timedelta(hours=i)).isoformat()
            records.append({
                "provider": "gemini", "model_id": "gemini-2.5-pro",
                "task_type": "research", "completed": True,
                "error_count": 0, "timestamp": ts,
            })

        with outcomes_path.open("w") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        alerts = check_drift(min_current=3, min_baseline=5)
        assert alerts == []

    def _write_outcomes(self, tmp_path, records: list[dict]) -> None:
        import json as _json
        with (tmp_path / "outcomes.jsonl").open("w") as f:
            for rec in records:
                f.write(_json.dumps(rec) + "\n")

    def test_below_min_sample_emits_no_false_alert(self, monkeypatch, tmp_path):
        """Green-gate: a steep quality DROP on a thin baseline must NOT alert. With a
        baseline below `min_baseline`, "your taste is degrading" would be a confident
        claim on essentially no data — the #1 bug shape (a signal while the data is
        degenerate). check_drift abstains; existing tests only cover 0 outcomes and a
        SUFFICIENT corpus, never the just-below-threshold boundary. Mutation: lower
        min_baseline to <=4 in check_drift → the drop clears the gate → this reds."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        records: list[dict] = []
        # Only 4 baseline (< min_baseline=5), all good; 4 current, all failing — a
        # 100% drop, but the thin baseline must suppress the alert.
        for i in range(4):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=10) + timedelta(hours=i)).isoformat()})
        for i in range(4):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=3) + timedelta(hours=i)).isoformat()})
        self._write_outcomes(tmp_path, records)
        assert check_drift(min_current=3, min_baseline=5) == [], (
            "drift alerted on a baseline below min_baseline — a false 'taste degrading' "
            "claim on too few sessions"
        )

    def test_all_zero_baseline_abstains_no_division_crash(self, monkeypatch, tmp_path):
        """Division-safety + green-gate: a baseline whose every session scored 0
        (e.g. an outage week — all councils failed) makes baseline_avg == 0. The
        `baseline_avg <= 0` guard must abstain — both to avoid a ZeroDivisionError on
        the delta_pct ratio (which feeds the launchpad/status drift card) AND because
        a percentage drop off a zero baseline is meaningless. Mutation: drop the
        `baseline_avg <= 0: continue` guard → ZeroDivisionError → this reds."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        records: list[dict] = []
        for i in range(6):  # baseline: all failed → score 0 → baseline_avg == 0
            records.append({"provider": "codex", "model_id": "m", "task_type": "debug",
                            "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=10) + timedelta(hours=i)).isoformat()})
        for i in range(4):  # current: also failed
            records.append({"provider": "codex", "model_id": "m", "task_type": "debug",
                            "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=3) + timedelta(hours=i)).isoformat()})
        self._write_outcomes(tmp_path, records)
        # Must NOT raise (no ZeroDivision) and must emit no alert.
        assert check_drift(min_current=3, min_baseline=5) == []

    def test_alert_message_baseline_span_matches_the_data_not_2_weeks(self, monkeypatch, tmp_path):
        """DATA-CORRECTNESS: the alert COPY must describe the window the figure is
        actually computed over. The baseline bucket holds records aged
        (current_window_days, baseline_window_days] = (7, 14] with the defaults — a
        SINGLE prior week, not two. The old copy claimed "this week vs prior 2 weeks",
        overstating the baseline span by 2× (a drop measured against ~6 sessions in the
        prior WEEK was advertised as "vs prior 2 WEEKS").

        Seed a known fixture: 6 baseline sessions placed in the (7,14]-day bucket PLUS
        6 decoy sessions aged 15-20 days that fall OUTSIDE the baseline cutoff. If the
        baseline were truly 2 weeks the decoys would be counted (baseline_sessions==12);
        they're excluded (==6), proving the comparison is over one prior week — so the
        message must say "prior week", never "prior 2 weeks".

        Mutation: restore the hardcoded `f"this week vs prior 2 weeks "` in
        drift.check_drift (or break `_window_phrase` to return "2 weeks" for 7) → the
        "2 weeks" assertion fires + the "prior week" assertion fires → this reds."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        records: list[dict] = []
        # Baseline window (7,14]: 6 successful sessions, one per day at days 8-13.
        for d in range(8, 14):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=d)).isoformat()})
        # DECOYS aged 15-20 days — OLDER than the 14-day baseline cutoff. Counted only
        # if the baseline were really 2 weeks wide; they must be ignored.
        for d in range(15, 21):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=d)).isoformat()})
        # Current window: 4 failing sessions → 100% drop.
        for i in range(4):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=3) + timedelta(hours=i)).isoformat()})
        self._write_outcomes(tmp_path, records)

        alerts = check_drift(min_current=3, min_baseline=5)
        assert len(alerts) == 1, "the seeded 100% drop must produce exactly one alert"
        a = alerts[0]
        # The decoys (15-20d) are excluded → the baseline is the SINGLE prior week.
        assert a.baseline_sessions == 6, (
            f"baseline counted {a.baseline_sessions} sessions; the 6 decoys aged 15-20d "
            "should be OUTSIDE the 14-day baseline cutoff — the baseline span is one prior "
            "week (7-14d), not two"
        )
        assert "2 weeks" not in a.message, (
            "drift alert claimed 'prior 2 weeks' while the figure compares against the "
            f"single prior week (baseline_sessions={a.baseline_sessions} from the 7-14d "
            f"bucket only). Message: {a.message!r}"
        )
        assert "prior week" in a.message, (
            f"drift alert must name the actual baseline span (the prior week): {a.message!r}"
        )

    def test_alert_phrase_widens_to_2_weeks_only_when_the_window_actually_does(
        self, monkeypatch, tmp_path
    ):
        """The span phrase is DERIVED from the window parameters, so a TRUE 2-week
        baseline (baseline_window_days=21 → span 21−7=14) does say "2 weeks". This
        pins that "2 weeks" is reserved for an honest 2-week comparison — it can't be
        re-hardcoded. Mutation: hardcode the phrase back to "week"/"2 weeks" → one of
        the two span tests reds."""
        monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        records: list[dict] = []
        # Baseline (7,21] days: 6 successful sessions spread across days 8-19.
        for d in (8, 10, 12, 14, 16, 18):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": True, "error_count": 0,
                            "timestamp": (now - timedelta(days=d)).isoformat()})
        for i in range(4):
            records.append({"provider": "claude", "model_id": "m", "task_type": "coding",
                            "completed": False, "error_count": 0,
                            "timestamp": (now - timedelta(days=3) + timedelta(hours=i)).isoformat()})
        self._write_outcomes(tmp_path, records)
        alerts = check_drift(min_current=3, min_baseline=5, baseline_window_days=21)
        assert len(alerts) == 1
        assert "prior 2 weeks" in alerts[0].message, (
            "with baseline_window_days=21 the baseline span is a genuine 2 weeks (21−7) — "
            f"the phrase must reflect it: {alerts[0].message!r}"
        )


def test_load_outcomes_skips_non_dict_lines(monkeypatch, tmp_path):
    """guard_shape_not_just_parse: a valid-JSON-but-non-dict line in outcomes.jsonl
    (a manual edit / partial write) must be SKIPPED, not crash _load_outcomes via
    `raw.get(...)` — check_drift runs on the launchpad drift card. Mutation: drop the
    `isinstance(raw, dict)` guard → the non-dict line crashes → this reds."""
    import json as _json
    monkeypatch.setattr("trinity_local.state_paths.trinity_home", lambda: tmp_path)
    from trinity_local.drift import _load_outcomes
    (tmp_path / "outcomes.jsonl").write_text("\n".join([
        _json.dumps({"provider": "claude", "completed": True, "timestamp": "t"}),
        "[1, 2, 3]", "42", "null",
        _json.dumps({"provider": "codex", "completed": False, "timestamp": "t2"}),
    ]) + "\n", encoding="utf-8")
    recs = _load_outcomes()  # must not raise
    assert [r.provider for r in recs] == ["claude", "codex"]
