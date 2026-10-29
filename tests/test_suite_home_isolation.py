"""The suite never runs against the real ~/.trinity (AGENTS.md). Nothing enforced that until 2026-10-26
(internal/rule-enforcement.md): a plain `pytest -q`, as public CI and CONTRIBUTING run it, read and wrote the
real home. tests/conftest.py now gives such a run a temporary home and refuses one pointed at the real home."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = "tests/test_suite_home_isolation.py::test_this_run_has_an_isolated_home"


def test_this_run_has_an_isolated_home():
    home = os.environ.get("TRINITY_HOME")
    assert home and Path(home).expanduser().resolve() != (Path.home() / ".trinity").resolve(), home


def _inner(trinity_home: str | None) -> subprocess.CompletedProcess:
    """One probe test in a fresh pytest process, with TRINITY_HOME as given (None: unset)."""
    env = {k: v for k, v in os.environ.items() if k != "TRINITY_HOME"}
    if trinity_home is not None:
        env["TRINITY_HOME"] = trinity_home
    env["PYTHONPATH"] = str(REPO / "src")
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", PROBE], cwd=REPO,
                          env=env, capture_output=True, text=True, timeout=120)


def test_a_run_without_trinity_home_gets_a_temporary_one():
    r = _inner(None)
    assert r.returncode == 0, r.stdout[-400:]


def test_a_run_pointed_at_the_real_home_stops_before_any_test():
    # conftest refuses in pytest_configure, before collection: no test, and nothing else, touches the real home.
    r = _inner(str(Path.home() / ".trinity"))
    assert r.returncode == 4 and "real ~/.trinity" in r.stdout + r.stderr, (r.returncode, r.stdout[-300:])
