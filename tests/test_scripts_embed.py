"""Tests for scripts/embed.py — the shebang-runnable embedding entry.

Phase 2 (council_ff3da1fa84906791): every script in scripts/ ships
with a dual interface (shebang + importable). This file pins:

  - The importable API (`embed_batch` function signature + return shape)
  - The CLI input/output JSON contract
  - The CLI bad-input handling
  - The audit-log invocation per CLI run
  - Tier-equivalence with the existing pip-tier function (same input
    → same vectors)

We don't hit the venv-bootstrap path here — the import-side path uses
deps already installed in the test environment. The venv path is the
first-time-skill-user code path; covered by scripts/test_runtime via
the sentinel-short-circuit test.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


REPO_ROOT = Path(__file__).resolve().parents[1]
EMBED_PY = REPO_ROOT / "scripts" / "embed.py"


def test_embed_batch_importable_returns_vectors_and_meta(isolated_home):
    """The pip-tier import path: `from scripts.embed import embed_batch`
    returns (vectors, meta) — vectors is a list of 768-d float lists,
    meta carries backend + dim + cached_count + embedded_count +
    total_count."""
    from scripts.embed import embed_batch

    texts = ["hello world", "another test phrase"]
    vectors, meta = embed_batch(texts, dim=768)
    assert len(vectors) == 2
    assert all(len(v) == 768 for v in vectors)
    assert all(isinstance(x, float) for v in vectors for x in v)
    assert meta["dim"] == 768
    assert meta["total_count"] == 2
    assert meta["backend"] in ("mlx", "tfidf")


def test_embed_batch_empty_input_returns_empty_list(isolated_home):
    """Edge case: empty input must not crash, returns empty vectors."""
    from scripts.embed import embed_batch
    vectors, meta = embed_batch([], dim=768)
    assert vectors == []
    assert meta["total_count"] == 0


def test_cli_round_trip_via_stdin_stdout(isolated_home):
    """Real CLI invocation: stdin JSON → stdout JSON, exit 0."""
    payload = json.dumps({"texts": ["hello world"], "dim": 768})
    result = subprocess.run(
        [sys.executable, str(EMBED_PY)],
        input=payload, capture_output=True, text=True,
        env={**__import__("os").environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             # Force TF-IDF backend in child subprocess — pins the CLI
             # I/O contract without paying the ~5s nomic model load.
             # Same fallback path that ships without `[mlx]` extras.
             "TRINITY_DISABLE_MLX": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=120,
    )
    assert result.returncode == 0, f"stderr: {result.stderr!r}"
    out = json.loads(result.stdout)
    assert len(out["vectors"]) == 1
    assert len(out["vectors"][0]) == 768
    assert out["backend"] in ("mlx", "tfidf")
    assert out["total_count"] == 1
    assert out["elapsed_ms"] >= 0


def test_cli_bad_input_missing_texts_field_returns_2(isolated_home):
    """Input JSON without a `texts` field: exit 2 with a clear stderr
    message + audit-log entry tagged bad_input."""
    payload = json.dumps({"wrong_field": "x"})
    result = subprocess.run(
        [sys.executable, str(EMBED_PY)],
        input=payload, capture_output=True, text=True,
        env={**__import__("os").environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             # Force TF-IDF backend in child subprocess — pins the CLI
             # I/O contract without paying the ~5s nomic model load.
             # Same fallback path that ships without `[mlx]` extras.
             "TRINITY_DISABLE_MLX": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=15,
    )
    assert result.returncode == 2
    assert "'texts' field" in result.stderr
    # Audit log entry
    audit_log = isolated_home / "audit.log"
    assert audit_log.exists()
    lines = audit_log.read_text().splitlines()
    assert lines, "audit.log should have at least one entry"
    last = json.loads(lines[-1])
    assert last["outcome"] == "bad_input"


def test_cli_bad_texts_type_returns_2(isolated_home):
    """`texts` not a list of strings: exit 2."""
    payload = json.dumps({"texts": [1, 2, 3]})
    result = subprocess.run(
        [sys.executable, str(EMBED_PY)],
        input=payload, capture_output=True, text=True,
        env={**__import__("os").environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             # Force TF-IDF backend in child subprocess — pins the CLI
             # I/O contract without paying the ~5s nomic model load.
             # Same fallback path that ships without `[mlx]` extras.
             "TRINITY_DISABLE_MLX": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=15,
    )
    assert result.returncode == 2
    assert "list of strings" in result.stderr


def test_cli_writes_audit_log_on_success(isolated_home):
    """Successful invocation appends an audit-log line with the
    script + operation + arg counts."""
    payload = json.dumps({"texts": ["test phrase"]})
    subprocess.run(
        [sys.executable, str(EMBED_PY)],
        input=payload, capture_output=True, text=True,
        env={**__import__("os").environ,
             "TRINITY_HOME": str(isolated_home),
             "TRINITY_SKIP_VENV_BOOTSTRAP": "1",
             # Force TF-IDF backend in child subprocess — pins the CLI
             # I/O contract without paying the ~5s nomic model load.
             # Same fallback path that ships without `[mlx]` extras.
             "TRINITY_DISABLE_MLX": "1",
             "PYTHONPATH": f"{REPO_ROOT}:{REPO_ROOT / 'src'}"},
        timeout=120,
    )
    audit_log = isolated_home / "audit.log"
    assert audit_log.exists()
    lines = audit_log.read_text().splitlines()
    success_entries = [json.loads(line) for line in lines
                       if json.loads(line).get("outcome") == "ok"]
    assert success_entries, "Expected at least one ok audit entry"
    entry = success_entries[-1]
    assert entry["script"] == "embed"
    assert entry["operation"] == "embed_batch"
    assert entry["args"]["n_texts"] == 1


def test_tier_equivalence_with_pip_tier(isolated_home):
    """The pip-tier call (`trinity_local.embeddings.embed_batch`) and
    the script-tier call (`scripts.embed.embed_batch`) must produce
    bit-identical outputs when invoked in the same process — because
    the script tier currently delegates to the pip tier.

    When v1.1 inverts the dependency (pip tier imports from scripts/),
    this test still passes for the same structural reason. The
    cross-BACKEND equivalence (cosine ≥ 0.9999 between MLX and torch-
    CPU) is a separate v1.1 invariant; this test is the same-backend
    floor."""
    from scripts.embed import embed_batch as script_embed
    from trinity_local.embeddings import embed_batch as pip_embed

    texts = ["the quick brown fox", "jumps over the lazy dog"]
    script_vectors, _ = script_embed(texts, dim=768)
    pip_vectors = pip_embed(texts, dim=768)
    assert script_vectors == pip_vectors, (
        "Script-tier and pip-tier embed outputs diverged in-process. "
        "Either the script wrapper introduced a transformation, or "
        "the pip tier's caching layer is non-deterministic."
    )
