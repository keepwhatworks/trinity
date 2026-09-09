"""CLI verb usage is logged, so a retirement decision can be made on data.

61 subcommands are registered and 6 advertised. On 2026-09-08 an audit of
retirement candidates read zero for every one of them — and zero for `lens`,
`status` and `trust` too, which are certainly used. Trinity logged no CLI
usage at all, so the zeros meant nothing and no verb was cut. This is the
instrument that makes the next attempt decidable.
"""
from __future__ import annotations

import argparse
import json


def _run(monkeypatch, tmp_path, verb="status"):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local import main as M
    ns = argparse.Namespace(command=verb)
    M._record_verb_use(ns)
    f = tmp_path / "analytics" / "verb_uses.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


class TestItRecords:
    def test_one_row_per_invocation(self, monkeypatch, tmp_path):
        rows = _run(monkeypatch, tmp_path, "plan")
        assert len(rows) == 1 and rows[0]["verb"] == "plan" and rows[0]["at"]

    def test_rows_accumulate(self, monkeypatch, tmp_path):
        _run(monkeypatch, tmp_path, "plan")
        rows = _run(monkeypatch, tmp_path, "verify")
        assert [r["verb"] for r in rows] == ["plan", "verify"]


class TestItRecordsNothingElse:
    """An argument can carry a task, a filename, or a person."""

    def test_only_verb_and_timestamp(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local import main as M
        M._record_verb_use(argparse.Namespace(
            command="plan", task="a private thing about a named person",
            diff="/Users/someone/secret.patch", query="pii"))
        row = json.loads((tmp_path / "analytics" / "verb_uses.jsonl").read_text().strip())
        assert set(row) == {"at", "verb"}
        blob = json.dumps(row)
        for leak in ("private", "secret", "Users", "pii", "task", "diff"):
            assert leak not in blob


class TestItNeverCrashesACommand:
    def test_no_command_attribute_is_silent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local import main as M
        M._record_verb_use(argparse.Namespace())          # no `command`
        M._record_verb_use(argparse.Namespace(command=None))
        assert not (tmp_path / "analytics" / "verb_uses.jsonl").exists()

    def test_an_unwritable_home_is_swallowed(self, monkeypatch, tmp_path):
        from trinity_local import main as M
        monkeypatch.setattr(M, "_record_verb_use", M._record_verb_use)
        monkeypatch.setenv("TRINITY_HOME", "/proc/nonexistent-and-unwritable")
        M._record_verb_use(argparse.Namespace(command="status"))   # must not raise


class TestItIsWired:
    def test_main_records_before_dispatching(self):
        import inspect
        from trinity_local import main as M
        src = inspect.getsource(M.main)
        assert "_record_verb_use(args)" in src, "main() must record the verb"
        assert src.index("_record_verb_use(args)") < src.index("rc = args.handler(args)"), (
            "record BEFORE dispatch — a verb that crashes is still a verb that was used")
