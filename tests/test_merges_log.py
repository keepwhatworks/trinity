"""Tick #44 — `~/.trinity/me/merges.jsonl` writer.

Seeds the unified merge corpus the v1.5+ direction-of-preference
vectors / collapsed lens-build will read. v1.0 ships with one row
type (council_winner) so the lifecycle (write → read → schema) can
be exercised end-to-end before more types get added.
"""
from __future__ import annotations


import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


class TestRecordMerge:
    """`record_merge` appends one row + stamps ts if absent.
    Cold-install path: directory created lazily, no error."""

    def test_creates_file_on_first_call(self, isolated_home):
        from trinity_local.merges import record_merge, merges_path
        assert not merges_path().exists(), "cold install starts without the file"
        record_merge({"type": "council_winner", "chosen": "claude"})
        assert merges_path().exists(), "first call must create the file"

    def test_appends_one_row_per_call(self, isolated_home):
        from trinity_local.merges import record_merge, merges_path
        record_merge({"type": "council_winner", "chosen": "claude"})
        record_merge({"type": "council_winner", "chosen": "antigravity"})
        lines = merges_path().read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2, f"expected 2 rows, got {len(lines)}"

    def test_stamps_ts_when_missing(self, isolated_home):
        from trinity_local.merges import record_merge
        out = record_merge({"type": "council_winner", "chosen": "claude"})
        assert "ts" in out, "record_merge didn't stamp ts"
        # ISO-8601 sanity: starts with year-month-day separators
        assert out["ts"][:4].isdigit() and out["ts"][4] == "-", (
            f"ts not ISO-8601: {out['ts']!r}"
        )

    def test_preserves_caller_ts_when_provided(self, isolated_home):
        from trinity_local.merges import record_merge
        out = record_merge({
            "type": "council_winner",
            "chosen": "claude",
            "ts": "2026-01-01T00:00:00",
        })
        assert out["ts"] == "2026-01-01T00:00:00", "caller ts must win"


class TestIterMergeRecords:
    """Read-side: malformed lines silently skipped, missing file
    returns empty iterator (cold install path)."""

    def test_cold_install_returns_empty(self, isolated_home):
        from trinity_local.merges import iter_merge_records
        # No file written yet — must iterate cleanly.
        assert list(iter_merge_records()) == []

    def test_round_trip(self, isolated_home):
        from trinity_local.merges import record_merge, iter_merge_records
        record_merge({"type": "council_winner", "chosen": "claude", "task_type": "coding"})
        record_merge({"type": "council_winner", "chosen": "antigravity", "task_type": "writing"})
        rows = list(iter_merge_records())
        assert len(rows) == 2
        assert rows[0]["chosen"] == "claude"
        assert rows[1]["task_type"] == "writing"

    def test_malformed_lines_skipped(self, isolated_home):
        from trinity_local.merges import record_merge, iter_merge_records, merges_path
        record_merge({"type": "council_winner", "chosen": "claude"})
        # Hand-corrupt: append a malformed line + a blank line.
        with merges_path().open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
            f.write("\n")
            f.write('{"type": "council_winner", "chosen": "antigravity"}\n')
        rows = list(iter_merge_records())
        # The malformed line is silently dropped — but the good rows
        # before AND after it both come through.
        assert len(rows) == 2, f"good rows lost; got {len(rows)}"
        assert rows[0]["chosen"] == "claude"
        assert rows[1]["chosen"] == "antigravity"


class TestSummarizeMerges:
    """Tick #47 — first concrete consumer of the merge corpus.
    summarize_merges() walks the log and returns counts per type
    + per-signal-type subset for in_thread_overwrite. Demonstrates
    the compute-view-on-demand pattern: no separate aggregation
    file, no cron — read on call."""

    def test_empty_log_returns_zero(self, isolated_home):
        from trinity_local.merges import summarize_merges
        summary = summarize_merges()
        assert summary["total"] == 0
        assert summary["by_type"] == {}
        assert summary["by_signal_type"] == {}
        assert summary["first_ts"] is None
        assert summary["last_ts"] is None

    def test_counts_by_type(self, isolated_home):
        from trinity_local.merges import record_merge, summarize_merges
        record_merge({"type": "council_winner", "chosen": "claude"})
        record_merge({"type": "council_winner", "chosen": "antigravity"})
        record_merge({"type": "cortex_override", "basin_id": "coding"})
        summary = summarize_merges()
        assert summary["total"] == 3
        assert summary["by_type"]["council_winner"] == 2
        assert summary["by_type"]["cortex_override"] == 1

    def test_in_thread_overwrite_breaks_down_by_signal_type(self, isolated_home):
        from trinity_local.merges import record_merge, summarize_merges
        record_merge({"type": "in_thread_overwrite", "signal_type": "COMPRESSION", "signal_id": "a"})
        record_merge({"type": "in_thread_overwrite", "signal_type": "COMPRESSION", "signal_id": "b"})
        record_merge({"type": "in_thread_overwrite", "signal_type": "REDIRECT", "signal_id": "c"})
        record_merge({"type": "council_winner", "chosen": "claude"})  # different type
        summary = summarize_merges()
        # Top-level type count: 3 overwrites + 1 winner = 4
        assert summary["by_type"]["in_thread_overwrite"] == 3
        assert summary["by_type"]["council_winner"] == 1
        # Signal-type breakdown applies ONLY to in_thread_overwrite rows.
        assert summary["by_signal_type"]["COMPRESSION"] == 2
        assert summary["by_signal_type"]["REDIRECT"] == 1
        # The council_winner row didn't pollute the signal-type bucket.
        assert sum(summary["by_signal_type"].values()) == 3

    def test_first_and_last_ts(self, isolated_home):
        from trinity_local.merges import record_merge, summarize_merges
        record_merge({"type": "council_winner", "ts": "2026-05-13T10:00:00"})
        record_merge({"type": "council_winner", "ts": "2026-05-13T09:00:00"})
        record_merge({"type": "council_winner", "ts": "2026-05-13T11:00:00"})
        summary = summarize_merges()
        # ISO-8601 sorts lexically; min/max give the real bounds.
        assert summary["first_ts"] == "2026-05-13T09:00:00"
        assert summary["last_ts"] == "2026-05-13T11:00:00"


class TestPageDataMergeLog:
    """Tick #48 — page_data exposes the merge summary so launchpad
    Vue surfaces (and any other downstream consumer of build_page_data)
    can render "Trinity has captured N tacit-record acts" without
    re-walking the log."""

    def test_page_data_contains_merge_log(self, isolated_home, tmp_path):
        from trinity_local.launchpad_data import build_page_data
        from trinity_local.merges import record_merge
        record_merge({"type": "council_winner", "chosen": "claude"})
        record_merge({"type": "cortex_override", "basin_id": "coding"})
        data = build_page_data(
            live_review_path=tmp_path / "live_council.html",
            recent_councils=[],
        )
        assert "mergeLog" in data, "page_data missing mergeLog key"
        assert data["mergeLog"]["total"] == 2
        assert data["mergeLog"]["by_type"]["council_winner"] == 1
        assert data["mergeLog"]["by_type"]["cortex_override"] == 1

    def test_cold_install_returns_zero_filled_summary(self, isolated_home, tmp_path):
        # Even with no merges yet, the key must exist with the standard
        # shape — frontend templates with v-if don't have to guard for
        # the missing case.
        from trinity_local.launchpad_data import build_page_data
        data = build_page_data(
            live_review_path=tmp_path / "live_council.html",
            recent_councils=[],
        )
        merge_log = data["mergeLog"]
        assert merge_log["total"] == 0
        assert merge_log["by_type"] == {}
        assert merge_log["by_signal_type"] == {}


class TestCouncilWinnerSchema:
    """Pin the schema for the council_winner row type so future
    consumers (direction-of-preference vector, lens-build collapse)
    can rely on these keys being present.

    Note: the MCP-side wiring writes the actual rows; this test
    fixtures a representative row directly to lock the contract."""

    def test_canonical_row_has_required_fields(self, isolated_home):
        from trinity_local.merges import record_merge, iter_merge_records
        record_merge({
            "type": "council_winner",
            "council_id": "council_abc",
            "task_type": "coding",
            "chosen": "claude",
            "rejected": ["antigravity", "codex"],
            "chairman_winner": "claude",
            "answer_label": "thumbs_up",
        })
        rows = list(iter_merge_records())
        row = rows[0]
        # Required keys downstream consumers will read:
        for key in ("type", "council_id", "task_type", "chosen", "rejected", "chairman_winner", "ts"):
            assert key in row, f"required key {key!r} missing from canonical row"
        assert row["rejected"] == ["antigravity", "codex"], "rejected provider list must round-trip"
