"""Residual-over-time log (compression-loop gap #3, passive half). Records a
prediction-quality snapshot per lens build; pure recording, never crashes a build.
"""
from __future__ import annotations

import json

from trinity_local.me.residual_log import load_log, record_snapshot, residual_log_path, snapshot


def test_snapshot_is_broad_guarded_on_empty_home(tmp_path):
    """A snapshot on a corpus-less home returns just the timestamp, never raises —
    a logging failure must never fail a build."""
    snap = snapshot(home=str(tmp_path))
    assert "at" in snap
    assert "resolved" not in snap  # no ledger -> field simply absent, no crash


def test_snapshot_reads_available_state(tmp_path):
    (tmp_path / "disagreement_ledger").mkdir(parents=True)
    (tmp_path / "disagreement_ledger" / "summary.json").write_text(json.dumps(
        {"resolved": 119, "k3_chairman_agreement": 0.672, "records": {"a": {}, "b": {}}}))
    (tmp_path / "memories").mkdir(parents=True)
    (tmp_path / "memories" / "topics.json").write_text(json.dumps(
        {"basins": [{"size": 30}, {"size": 70}]}))
    snap = snapshot(home=str(tmp_path))
    assert snap["resolved"] == 119 and snap["k3"] == 0.672 and snap["n_model_cells"] == 2
    assert snap["n_basins"] == 2 and snap["top_basin_share"] == 0.7


def test_record_and_load_roundtrip(tmp_path):
    assert load_log(home=str(tmp_path)) == []
    record_snapshot(home=str(tmp_path))
    record_snapshot(home=str(tmp_path))
    rows = load_log(home=str(tmp_path))
    assert len(rows) == 2 and all("at" in r for r in rows)
    assert residual_log_path(home=str(tmp_path)).exists()
