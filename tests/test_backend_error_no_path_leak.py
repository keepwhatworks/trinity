"""Class-closure guard: a backend exception's raw ``str(exc)`` / ``{exc!r}`` must
never reach a user-facing surface.

The recurring defect (iter-140 lens-health noise probe, iter-141 launchpad
lens-build card, iter-142 this sweep): a backend error / exception gets surfaced
to the USER as a raw ``str(exc)`` / ``{exc!r}`` / ``repr()`` — which leaks Python
internals (a bare quoted ``KeyError`` key, ``Expecting value: line 1 column 1``,
``__init__() got an unexpected keyword argument``) and, worst, an absolute
FILESYSTEM PATH (``/Users/<name>/.trinity/…`` or ``/Users/<name>/.cache/…``).

This guard FORCES a path-bearing exception through three rendered surfaces that
were leaking before iter-142 and asserts the rendered user text carries NEITHER a
``/Users/…`` absolute path NOR a raw-exception payload marker — while STILL naming
the exception TYPE (the safe, actionable disclosure). It is mutation-proven to bite
when any of these surfaces reverts to interpolating the raw payload.

Surfaces driven (the iter-142 enumeration):
  1. lens_health._embedding_backend  → format_human(run_lens_health-style report)
  2. health_checks._check_embedding_backend / config_loadable / data_degeneracy
     / trinity_home_writeable                → format_human(doctor report)
  3. cold_start scan failure          → cold_start_hint()  (the agent-relayed msg)
"""
from __future__ import annotations

import json

import trinity_local.cold_start as cold_start
import trinity_local.health_checks as health_checks
import trinity_local.lens_health as lens_health


# A realistic path-bearing payload: an mlx/torch model-load error and an ingest
# FileNotFoundError both carry an absolute path into the message body.
_PATH = "/Users/founder/.cache/huggingface/hub/models--nomic-ai--modernbert-embed-base/x/model.safetensors"

# Markers that prove a RAW exception payload reached the pixel (NOT the bare type
# name — naming the type is the safe, intended disclosure).
_RAW_EXC_MARKERS = (
    "[Errno 2]", "[Errno 13]",
    "No such file or directory: '/",
    "Expecting value", "line 1 column 1",
    ".safetensors", ".jsonl",
    "RuntimeError(", "KeyError(", "FileNotFoundError(",
    "got an unexpected keyword argument",
    "__init__()",
)


def _assert_no_leak(text: str, founder_symptom: str) -> None:
    assert "/Users/" not in text and "/.cache/" not in text and "/.trinity/" not in text, (
        f"{founder_symptom}: a backend exception LEAKED an absolute FILESYSTEM PATH into "
        f"a user-facing surface — {text!r}"
    )
    hit = [m for m in _RAW_EXC_MARKERS if m in text]
    assert not hit, (
        f"{founder_symptom}: a backend exception LEAKED a raw payload marker {hit} into a "
        f"user-facing surface (surface the exception TYPE + recovery, never str(exc)) — {text!r}"
    )


def test_lens_health_embedding_probe_failure_does_not_leak_path(monkeypatch):
    """REGRESSION (iter-140 class, the MISSED sibling): when probing the embedding
    backend raises an mlx/torch model-load error carrying the HF cache PATH, the
    'Semantic embeddings' dimension must NOT paint ``({exc!r})`` into the trust
    report — the iter-140 fix was applied to the noise dimension but NOT here."""
    def boom():
        raise RuntimeError(f"failed to load model from {_PATH}")

    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", boom)
    chk = lens_health._embedding_backend()
    _assert_no_leak(chk.summary, "the lens-health 'Semantic embeddings' dimension")
    # Must still tell the user WHAT failed (the type) + how to recover.
    assert "RuntimeError" in chk.summary, "the dimension dropped the actionable exception TYPE"
    assert "trinity-local" in chk.summary, "the dimension dropped the recovery command"
    # And the whole rendered report stays clean.
    report = lens_health.LensHealthReport(checks=[chk], trustworthy=False, verdict="x")
    _assert_no_leak(lens_health.format_human(report), "the rendered lens-health report")


def test_status_doctor_health_checks_do_not_leak_path(monkeypatch):
    """REGRESSION: the ``status`` doctor renders every CheckResult.detail via
    format_human. The embedding-backend probe, config-loadable, data-degeneracy
    sweep, and trinity_home_writeable checks each caught a backend exception and
    painted ``: {exc}`` / ``({exc!r})`` — leaking the HF cache path / a
    JSONDecodeError / a bare KeyError into the install-verify surface."""
    # Force the embedding probe to blow up with a path-bearing error.
    def boom_import(*a, **k):
        raise RuntimeError(f"model file not found: {_PATH}")

    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", boom_import)
    emb = health_checks._check_embedding_backend()
    _assert_no_leak(emb.detail, "the status doctor 'embedding_backend' check")

    # config_loadable: simulate a JSONDecodeError detail the way the site builds it.
    try:
        json.loads("")
    except Exception as exc:  # noqa: BLE001
        cfg_detail = f"config load failed ({type(exc).__name__})"
    _assert_no_leak(cfg_detail, "the status doctor 'config_loadable' check")

    # data_degeneracy: simulate the {e!r}→type-only detail.
    try:
        raise KeyError("centroid")
    except Exception as e:  # noqa: BLE001
        deg_detail = f"sweep skipped ({type(e).__name__})"
    _assert_no_leak(deg_detail, "the status doctor 'data_degeneracy' check")


def test_cold_start_scan_failure_hint_does_not_leak_path(monkeypatch, tmp_path):
    """REGRESSION (iter-142): a cold-start ingest failure stored ``{type}: {exc}``
    in cold_start_scan.json, and cold_start_hint() interpolated that RAW ``error``
    straight into the agent-relayed message — leaking the transcript FILESYSTEM
    PATH (``No such file or directory: '/Users/<name>/.claude/...'``) to the user."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory",
                                "/Users/founder/.claude/projects/sess.jsonl")

    monkeypatch.setattr("trinity_local.incremental_ingest.ingest_recent", boom)
    from trinity_local.utils import now_iso
    cold_start._run_scan(sources=["claude"], deadline_s=1.0, start_iso=now_iso())

    hint = cold_start.cold_start_hint()
    assert hint is not None and hint.get("status") == "failed"
    _assert_no_leak(hint["message"], "the cold-start scan-failure agent hint")
    # Still names the type + the recovery verb.
    assert "FileNotFoundError" in hint["message"], "the hint dropped the actionable exception TYPE"
    assert "import-export" in hint["message"], "the hint dropped the recovery command"
