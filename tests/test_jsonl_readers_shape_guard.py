"""guard_shape_not_just_parse for the remaining per-line JSONL readers.

Last iteration fixed the HOT-PATH per-line readers (the prompt corpus + drift). An AST
pass flagged the rest; empirical classification (does a non-dict line actually crash?)
found 4 genuine gaps — knn_analytics (load_advisory_log + the mark_suggestion_outcome
read-modify-write), me/lens_edits (load_recent_edits)
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


def test_lens_edits_load_skips_non_dict(home):
    from trinity_local.me import lens_edits as le
    _write(le.lens_edits_path(),
           [{"ts": "t1", "op": "add"}, {"ts": "t2", "op": "remove"}])
    edits = le.load_recent_edits()  # must not raise
    assert len(edits) == 2
