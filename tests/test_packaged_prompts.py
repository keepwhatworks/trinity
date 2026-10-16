"""lens-prompt and eval-prompt work on an install, not only in a checkout.

Both read their prompt from the repo's docs/, which no wheel ships, so on every install they
exited 1 and the launchpad's `trinity-local lens-prompt | pbcopy` button copied nothing
(reproduced on the v1.7.402 wheel, coherence review 2026-10-15). The prompts are package data
now. The first test asserts the package-data glob covers them, which is what a wheel ships.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROMPTS = ("lens-from-provider.md", "evals-from-provider.md")


def _package_data_globs() -> list[str]:
    import tomllib
    cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
    return cfg["tool"]["setuptools"]["package-data"]["trinity_local"]


@pytest.mark.parametrize("name", PROMPTS)
def test_the_wheel_ships_the_prompt(name):
    rel = f"data/prompts/{name}"
    assert (REPO / "src" / "trinity_local" / rel).is_file()
    assert any(fnmatch.fnmatch(rel, g) for g in _package_data_globs()), rel


@pytest.mark.parametrize("verb", ["lens-prompt", "eval-prompt"])
def test_the_verb_prints_the_prompt_from_package_data(verb, tmp_path):
    """Run from a directory with no docs/ tree, the way an install runs."""
    env = {"PATH": "/usr/bin:/bin", "TRINITY_HOME": str(tmp_path), "PYTHONPATH": str(REPO / "src"),
           "TRINITY_AUTOSCAN_DISABLED": "1", "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "trinity_local.main", verb], cwd=tmp_path,
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    assert len(r.stdout.strip()) > 200
