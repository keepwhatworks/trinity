"""Tests for scripts/descriptor.py — rejection-signal validators.

Phase 2 (council_ff3da1fa84906791). Validators are pure functions on
text — perfect for shebang-script extraction. Tests cover:

  - validate_signals() function signature + dual-list return shape
  - COMPRESSION rule (user word count ≤ model_text / 10)
  - REDIRECT rule (model is structurally multi-part)
  - SHARPENING rule (user shares ≥2 keywords with model)
  - REFRAME rule (next-turn persistence)
  - CLI round-trip via stdin/stdout
  - CLI bad input handling
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
DESCRIPTOR_PY = REPO_ROOT / "scripts" / "descriptor.py"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _sig(type_: str, prompt_id: str = "p1", id_: str = "s1") -> dict:
    return {
        "id": id_, "type": type_, "model_quote": "", "user_substitute": "",
        "why_signal": "", "prompt_id": prompt_id, "basin": None,
    }


def test_compression_passes_when_user_short(isolated_home):
    """User text ≤ model / 10 → COMPRESSION signal kept."""
    from scripts.descriptor import validate_signals

    signals = [_sig("COMPRESSION")]
    pairs = {"p1": {
        "assistant_text": " ".join(["word"] * 100),  # 100 words
        "user_text": " ".join(["w"] * 5),            # 5 words = 5%
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert len(kept) == 1
    assert len(rejected) == 0


def test_compression_drops_when_user_too_long(isolated_home):
    """User text > model / 10 → COMPRESSION rejected with reason."""
    from scripts.descriptor import validate_signals

    signals = [_sig("COMPRESSION")]
    pairs = {"p1": {
        "assistant_text": " ".join(["w"] * 10),   # 10 words
        "user_text": " ".join(["w"] * 5),         # 5 words = 50% (way over 10%)
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert kept == []
    assert len(rejected) == 1
    assert "user/model ratio" in rejected[0]["reason"]


def test_redirect_passes_when_user_steer_substantive(isolated_home):
    """REDIRECT is kept when the user's steer is substantive (≥3 words),
    REGARDLESS of the model's structure. The relaxed validator (76bba67d)
    dropped the old model-must-be-multi-part requirement — it was discarding
    real redirects of single-part answers (the taste signal lives in the user
    steering away, not in how the model happened to format its reply)."""
    from scripts.descriptor import validate_signals

    signals = [_sig("REDIRECT")]
    pairs = {"p1": {
        # Single-sentence model answer — the OLD validator would have dropped
        # this; the relaxed one keeps it because the user steer is substantive.
        "assistant_text": "One short single-sentence answer.",
        "user_text": "ignore that, focus on X",
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert len(kept) == 1


def test_redirect_drops_when_user_turn_too_thin(isolated_home):
    """A REDIRECT whose user turn is < 3 words is too thin to be a real steer →
    dropped. (Relaxed validator 76bba67d: REDIRECT no longer gates on the MODEL
    being multi-part; it gates on the USER steer being substantive.)"""
    from scripts.descriptor import validate_signals

    signals = [_sig("REDIRECT")]
    pairs = {"p1": {
        "assistant_text": "One short response.",
        "user_text": "x",
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert kept == []
    assert "thin" in rejected[0]["reason"]


def test_sharpening_passes_when_keyword_overlap_high(isolated_home):
    """SHARPENING wants user restating model's idea — needs ≥2 shared
    keywords."""
    from scripts.descriptor import validate_signals

    signals = [_sig("SHARPENING")]
    pairs = {"p1": {
        "assistant_text": "Trinity local councils enable cross-provider preference learning",
        "user_text": "Trinity councils cross-provider learning, but tighter",
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert len(kept) == 1


def test_sharpening_drops_when_no_overlap(isolated_home):
    """SHARPENING with <2 shared keywords → rejected (user pivoted)."""
    from scripts.descriptor import validate_signals

    signals = [_sig("SHARPENING")]
    pairs = {"p1": {
        "assistant_text": "the weather is nice today",
        "user_text": "do you like pizza",
        "next_user_text": "",
    }}
    kept, rejected = validate_signals(signals, pairs)
    assert kept == []
    assert "keyword overlap" in rejected[0]["reason"]


def test_cli_round_trip(isolated_home):
    """End-to-end CLI: stdin JSON → stdout JSON, exit 0, audit appended."""
    payload = json.dumps({
        "signals": [_sig("COMPRESSION")],
        "pairs": {"p1": {
            "assistant_text": " ".join(["word"] * 50),
            "user_text": "tiny",
            "next_user_text": "",
        }},
    })
    result = subprocess.run(
        [sys.executable, str(DESCRIPTOR_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    out = json.loads(result.stdout)
    assert out["kept_count"] == 1
    assert out["rejected_count"] == 0
    audit = json.loads((isolated_home / "audit.log").read_text().splitlines()[-1])
    assert audit["script"] == "descriptor"


def test_cli_bad_input_exits_2(isolated_home):
    """Missing 'signals' or 'pairs' field → exit 2."""
    payload = json.dumps({"signals": []})  # missing pairs
    result = subprocess.run(
        [sys.executable, str(DESCRIPTOR_PY)],
        input=payload, capture_output=True, text=True,
        env={**os.environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=10,
    )
    assert result.returncode == 2
    assert "'pairs'" in result.stderr
