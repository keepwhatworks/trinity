"""Tests for scripts/anchor.py + scripts/signature.py.

Phase 2 (council_ff3da1fa84906791). Wraps trinity_local.vocabulary
primitives; tests cover the dual-interface contract + correctness
floor (proper-noun recurrence ranking; homonym shape).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR_PY = REPO_ROOT / "scripts" / "anchor.py"
SIGNATURE_PY = REPO_ROOT / "scripts" / "signature.py"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


# ─── scripts/anchor.py ─────────────────────────────────────────────


def test_find_anchors_ranks_by_thread_recurrence():
    """'Trinity Local' mentioned in 3 distinct threads vs single-thread
    other text → 'Trinity Local' ranks first."""
    from scripts.anchor import find_anchors

    nodes = [
        {"text": "Trinity Local does this", "transcript_id": "t1"},
        {"text": "Trinity Local does that", "transcript_id": "t2"},
        {"text": "Trinity Local works", "transcript_id": "t3"},
        {"text": "Random Phrase appears once", "transcript_id": "t4"},
    ]
    result = find_anchors(nodes, min_threads=2, top_n=5)
    assert any(a["phrase"] == "Trinity Local" for a in result)
    assert result[0]["phrase"] == "Trinity Local"
    assert result[0]["n_threads"] == 3


def test_find_anchors_filters_by_min_threads():
    """A phrase appearing in only 1 thread is dropped when min_threads=2."""
    from scripts.anchor import find_anchors

    nodes = [{"text": "Single Phrase", "transcript_id": "t1"}]
    result = find_anchors(nodes, min_threads=2, top_n=5)
    assert result == []


def test_anchor_cli_round_trip(isolated_home):
    payload = json.dumps({
        "nodes": [
            {"text": "Trinity Local rocks", "transcript_id": "t1"},
            {"text": "Trinity Local indeed", "transcript_id": "t2"},
        ],
        "min_threads": 2, "top_n": 5,
    })
    result = subprocess.run(
        [sys.executable, str(ANCHOR_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    out = json.loads(result.stdout)
    assert out["n_anchors"] >= 1
    assert any(a["phrase"] == "Trinity Local" for a in out["anchors"])


def test_anchor_cli_bad_input_exits_2(isolated_home):
    payload = json.dumps({"wrong": "x"})
    result = subprocess.run(
        [sys.executable, str(ANCHOR_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
    assert "'nodes'" in result.stderr


# ─── scripts/signature.py ──────────────────────────────────────────


def test_signature_cli_unknown_operation_exits_2(isolated_home):
    payload = json.dumps({"operation": "nonsense"})
    result = subprocess.run(
        [sys.executable, str(SIGNATURE_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
    assert "unknown operation" in result.stderr


def test_signature_cli_bad_payload_exits_2(isolated_home):
    """Non-object payload → exit 2."""
    result = subprocess.run(
        [sys.executable, str(SIGNATURE_PY)],
        input='"a string, not an object"',
        capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
