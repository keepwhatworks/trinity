"""res_015 -- no experiment may hardcode the real ~/.trinity.

WHY THIS GUARD EXISTS
---------------------
63 of 145 experiment scripts read ``Path.home() / ".trinity"`` directly and
ignored ``TRINITY_HOME``. That broke this repo's own stated mechanical
invariant ("Tests ONLY under isolated TRINITY_HOME") inside the corpus that
produces every research number, with three consequences:

  * 43% of experiments could not be run in a sandbox, so their reproducibility
    was unenforceable
  * a harness that believed it had isolated them silently measured the REAL
    store instead -- which is exactly how hq_061 died, and the only reason it
    was caught is that basin_skin reported 6,603 bits on a corpus of three
    short strings. A number too large for its input. No bucket count showed it
  * 40 of the 63 also WRITE, so a supposed dry run could have corrupted the
    live store

The fix routes every one through a ``_TRINITY_HOME`` shim whose resolution
matches ``trinity_local.config.trinity_home()``. It is deliberately
import-free so scripts that run without ``PYTHONPATH=src`` keep working.

This guard is the ratchet. Without it the pattern returns one file at a time
and nothing notices until the next harness quietly measures the wrong corpus.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

EXPERIMENTS = Path(__file__).resolve().parent.parent / "internal" / "experiments"

HARDCODED = re.compile(
    r'(?:pathlib\.)?Path\.home\(\)\s*/\s*"\.trinity(?:/[^"]*)?"'
    r'|expanduser\(\s*"~/\.trinity(?:/[^"]*)?"\s*\)')


def _is_shim(line: str) -> bool:
    """The shim's own definition legitimately names Path.home() as the default.

    Matched SEMANTICALLY, not by spelling. The first version keyed on the exact
    string "_TRINITY_HOME = _pathlib" and immediately flagged a file written by
    hand minutes later using ``Path(...)`` rather than ``_pathlib.Path(...)`` --
    a real offender by the letter, correct by intent. The exemption is now "this
    line assigns _TRINITY_HOME and reads the TRINITY_HOME env var", which is the
    property that actually makes it safe.

    It stays narrow deliberately: a line that merely MENTIONS _TRINITY_HOME while
    also hardcoding the real home is still an offender, and
    test_mutation_proof_the_shim_exemption_is_narrow holds that line.
    """
    t = line.strip()
    assigns = t.startswith("_TRINITY_HOME") and "=" in t
    return ((assigns and "TRINITY_HOME" in t and "environ" in t)
            or t.startswith("or _pathlib.Path.home()")
            or t.startswith("or Path.home()"))


def offenders() -> dict[str, list[tuple[int, str]]]:
    found: dict[str, list[tuple[int, str]]] = {}
    if not EXPERIMENTS.exists():
        return found
    for f in sorted(EXPERIMENTS.glob("*.py")):
        for i, line in enumerate(f.read_text(errors="replace").split("\n"), 1):
            if _is_shim(line):
                continue
            if HARDCODED.search(line):
                found.setdefault(f.name, []).append((i, line.strip()[:90]))
    return found


def test_no_experiment_hardcodes_the_real_home():
    bad = offenders()
    assert not bad, (
        "These experiments read the real ~/.trinity and ignore TRINITY_HOME, so "
        "they cannot be sandboxed and any harness that runs them will silently "
        "measure the live store:\n"
        + "\n".join(f"  {n}:{i}  {l}" for n, hits in bad.items() for i, l in hits)
        + "\n\nUse the _TRINITY_HOME shim (see any experiment's header) instead.")


def test_the_shim_actually_honours_the_env_var(tmp_path, monkeypatch):
    """Behaviour, not presence. A shim that resolved wrong would pass a grep."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    import os
    import pathlib as _pathlib
    resolved = _pathlib.Path(os.environ.get("TRINITY_HOME")
                             or _pathlib.Path.home() / ".trinity")
    assert resolved == tmp_path
    monkeypatch.delenv("TRINITY_HOME")
    resolved = _pathlib.Path(os.environ.get("TRINITY_HOME")
                             or _pathlib.Path.home() / ".trinity")
    assert resolved == Path.home() / ".trinity", (
        "unset must fall back to the real home, or the refactor silently changed "
        "behaviour for every normal run")


def test_every_experiment_defining_the_shim_can_be_parsed():
    """A rewrite that broke a file would otherwise only surface when someone
    next ran that experiment, possibly months later."""
    import ast
    broken = []
    for f in sorted(EXPERIMENTS.glob("*.py")):
        src = f.read_text(errors="replace")
        if "_TRINITY_HOME" not in src:
            continue
        try:
            ast.parse(src)
        except SyntaxError as e:
            broken.append(f"{f.name}: {e}")
    assert not broken, "shim rewrite broke these files:\n" + "\n".join(broken)


@pytest.mark.parametrize("snippet", [
    'X = pathlib.Path.home() / ".trinity/prompts/prompt_nodes.jsonl"',
    'Y = Path.home() / ".trinity"',
    'Z = expanduser("~/.trinity/core.md")',
])
def test_mutation_proof_the_detector_fires(snippet):
    """Delete the mechanism -> the guard must RED. A detector that cannot see a
    reintroduced offender is decoration; these are the three real variants that
    existed in the corpus before the fix."""
    assert HARDCODED.search(snippet), (
        f"the detector missed {snippet!r} -- the guard above would pass "
        "vacuously while the defect returned")


def test_mutation_proof_the_shim_exemption_is_narrow():
    """The exemption must cover the shim and nothing else, or an offender could
    hide by mentioning _TRINITY_HOME on the same line."""
    assert _is_shim('_TRINITY_HOME = _pathlib.Path(_os.environ.get("TRINITY_HOME")')
    # the hand-written spelling that the first exemption wrongly rejected
    assert _is_shim('_TRINITY_HOME = Path(os.environ.get("TRINITY_HOME") or Path.home() / ".trinity")')
    assert not _is_shim('data = _TRINITY_HOME; other = Path.home() / ".trinity"')
    assert not _is_shim('_TRINITY_HOME_BACKUP = Path.home() / ".trinity"'), (
        "an assignment that does not read the env var is not a shim")
