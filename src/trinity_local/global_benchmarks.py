"""Reference benchmark scores mapped to Trinity capability categories.

Loads from `data/reference_evals.json` (sourced from artificialanalysis.ai)
when present. Falls back to a small hardcoded baseline so the launchpad
always has something to render.
"""
from __future__ import annotations

import json
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _reference_evals_path() -> Path:
    return _project_root() / "data" / "reference_evals.json"


def _hardcoded_fallback() -> dict:
    return {
        "coding": {
            "benchmark": "HumanEval",
            "unit": "pass@1 (%)",
            "models": {"claude": 91.3, "codex": 81.0, "antigravity": 94.3},
        },
        "reasoning": {
            "benchmark": "GPQA Diamond",
            "unit": "accuracy (%)",
            "models": {"claude": 68.5, "codex": 71.2, "antigravity": 72.8},
        },
    }


def get_global_benchmarks() -> dict:
    """Reference evals for the providers Trinity routes today.

    Static reference numbers used to contextualise capability categories.
    Not community telemetry, not personalised.
    """
    path = _reference_evals_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            # guard_shape_not_just_parse: a non-dict reference_evals.json (a bad
            # refresh / partial write) would crash `.get` past the except; fall to
            # the always-valid hardcoded baseline instead.
            if isinstance(raw, dict):
                categories = raw.get("categories") or {}
                if categories:
                    return categories
        except (json.JSONDecodeError, OSError):
            pass
    return _hardcoded_fallback()


def get_reference_evals_meta() -> dict:
    """Metadata about the loaded reference evals (source, fetched_at, providers).

    Returns an empty dict if the JSON file isn't present (fallback in use).
    """
    path = _reference_evals_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):  # guard_shape_not_just_parse: .get below crashes on a non-dict
        return {}
    return {
        "source": raw.get("source"),
        "fetched_at": raw.get("fetched_at"),
        "attribution": raw.get("attribution"),
        "providers": raw.get("providers") or {},
    }
