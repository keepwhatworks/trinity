"""The gate must say what its green does not cover.

`launch-check.sh` prints "All gates passed" after a run in which browser-marked tests
SKIP without Chrome. Measured on this repo's own history (hq_099b n=192, hq_099c n=178,
two disjoint stratified draws): 30% and 29% of fix-with-guard commits put their ENTIRE
guard in that tier. A green that reads as full coverage while a third of fix guards sat
out is this repo's #1 bug shape pointed at its own gate.

These pin the two properties that make the disclosure trustworthy rather than decorative:
it reports the REAL counts, and it REFUSES when it cannot establish them instead of
printing a reassuring zero.

Mutation-proven 2026-08-17: making `parse_counts` return (0, 0) on no match REDs
`test_unparseable_output_refuses_rather_than_reporting_zero`; deleting the
`tests collected` regex REDs the parsing tests; removing the disclosure call from
launch-check.sh REDs `test_launch_check_actually_calls_the_disclosure`.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Load the script by PATH rather than by mutating sys.path. A module-level
# sys.path.insert runs at COLLECTION, before any test does, so it leaks into every
# other module in the suite -- the anti-pattern test_no_module_level_env_mutation.py
# exists to catch, and which it duly caught in the first version of this file.
_spec = importlib.util.spec_from_file_location(
    "coverage_disclosure", REPO / "scripts" / "coverage_disclosure.py")
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)


class TestParsing:
    def test_reads_the_deselected_form(self):
        assert cd.parse_counts("498/4706 tests collected (4208 deselected) in 5.4s") == \
            (498, 4706)

    def test_unparseable_output_refuses_rather_than_reporting_zero(self):
        """A zero here would read as 'nothing is uncovered', which is the exact
        false-reassurance this disclosure exists to prevent."""
        assert cd.parse_counts("") is None
        assert cd.parse_counts("ERROR: no tests ran") is None

    def test_a_bare_collected_line_is_not_usable(self):
        """`N tests collected` with no denominator means nothing was deselected, so the
        share this disclosure reports cannot be computed from it."""
        assert cd.parse_counts("4706 tests collected in 5.4s") is None


class TestRendering:
    def test_states_both_shares_because_one_alone_misleads(self):
        out = cd.render((498, 4706))
        assert "498" in out
        assert "11%" in out, "the test-count share"
        assert cd.FIX_GUARD_SHARE in out, "the fix-guard share, which is 3x larger"
        assert "-m browser" in out, "must carry the command that closes the gap"

    def test_refusal_is_explicit_not_silent(self):
        out = cd.render(None)
        assert "UNAVAILABLE" in out
        assert "does not attest" in out


class TestWiring:
    def test_launch_check_actually_calls_the_disclosure(self):
        """A disclosure nothing invokes is decoration. This is the wire, not the logic."""
        sh = (REPO / "scripts" / "launch-check.sh").read_text(encoding="utf-8")
        assert "coverage_disclosure.py" in sh, \
            "launch-check.sh must print the coverage disclosure with its verdict"

    def test_the_script_runs_and_says_something_about_coverage(self):
        r = subprocess.run([sys.executable, str(REPO / "scripts" / "coverage_disclosure.py")],
                           capture_output=True, text=True, timeout=300, cwd=REPO)
        assert r.returncode == 0
        assert "coverage:" in r.stdout
