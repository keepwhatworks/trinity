"""Corpus-based eval harness (task #122)."""
from __future__ import annotations

from .builder import build_eval_set, load_eval_set, EvalItem, EvalSet, evals_dir, results_dir
from .runner import run_eval, save_run_result, load_run_result, EvalRunResult, EvalItemRun
from .scorer import score_run, REJECTION_AXIS_RUBRIC

__all__ = [
    "build_eval_set", "load_eval_set", "EvalItem", "EvalSet",
    "evals_dir", "results_dir",
    "run_eval", "save_run_result", "load_run_result",
    "EvalRunResult", "EvalItemRun",
    "score_run", "REJECTION_AXIS_RUBRIC",
]
