"""guard_shape_not_just_parse for the remaining per-line JSONL readers.

Last iteration fixed the HOT-PATH per-line readers (the prompt corpus + drift). An AST
pass flagged the rest; empirical classification (does a non-dict line actually crash?)
found 4 genuine gaps — knn_analytics (load_advisory_log + the mark_suggestion_outcome
read-modify-write), dispatch_health (compute_health), me/lens_edits (load_recent_edits)
— and 4 false positives already guarded by a `startswith("{")` pre-filter
(me/decisions ×2, me/arc_mining, me/turn_pairs). Each genuine reader did
`json.loads(line)` (catching only JSONDecodeError) then `.get(...)`, so a valid-JSON-
but-non-dict line (manual edit / partial write) crashed `.get` with an uncaught
AttributeError. These are lens-build / analytics internals (off the hot path), but they
are the same dangerous class, so the sweep is closed here.

Each test seeds the reader's real JSONL file with a good record + non-dict junk lines
and asserts the reader DOESN'T crash and keeps the good record. Mutation-proven: drop
the relevant isinstance guard → the non-dict line crashes → reds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    return tmp_path


_JUNK = ["[1, 2, 3]", "42", '"a bare string"', "null"]


def _write(path: Path, good: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(good[0])] + _JUNK + [json.dumps(g) for g in good[1:]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_knn_load_advisory_log_skips_non_dict(home):
    from trinity_local import knn_analytics as ka
    _write(ka._advisory_log_path(),
           [{"session_id": "s1", "provider": "claude"}, {"session_id": "s2", "provider": "codex"}])
    events = ka.load_advisory_log()  # must not raise
    assert {e.session_id for e in events} == {"s1", "s2"}


def test_knn_mark_suggestion_outcome_preserves_non_dict(home):
    """The read-modify-write path: a non-dict line must be PRESERVED on rewrite, not
    dropped or crashed."""
    from trinity_local import knn_analytics as ka
    p = ka._advisory_log_path()
    _write(p, [{"session_id": "s1", "provider": "claude"}, {"session_id": "s2", "provider": "codex"}])
    ka.mark_suggestion_outcome("s1", acted_on=True, later_switched=False)  # must not raise
    # The junk lines survive the rewrite (read-modify-write must not lose data).
    body = p.read_text(encoding="utf-8")
    assert "[1, 2, 3]" in body and "null" in body


def test_dispatch_compute_health_skips_non_dict(home):
    from trinity_local import dispatch_health as dh
    from trinity_local.state_paths import dispatch_outcomes_path
    _write(dispatch_outcomes_path(),
           [{"provider": "claude", "council_run_id": "c1", "failure_kind": "timeout"},
            {"provider": "codex", "council_run_id": "c2", "failure_kind": "rate_limit"}])
    dh.clear_health_cache()
    dh.compute_health()        # must not raise
    dh.unhealthy_providers()   # must not raise


def test_lens_edits_load_skips_non_dict(home):
    from trinity_local.me import lens_edits as le
    _write(le.lens_edits_path(),
           [{"ts": "t1", "op": "add"}, {"ts": "t2", "op": "remove"}])
    edits = le.load_recent_edits()  # must not raise
    assert len(edits) == 2
