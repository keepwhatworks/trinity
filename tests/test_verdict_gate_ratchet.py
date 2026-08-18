"""The gate cannot be skipped by writing a new instrument that ignores it.

`verdict_gate.gated_verdict` forces an instrument to hand over its UNITS, so
composition is computed rather than assumed, and to declare `provenance` and a
reachability witness. None of that helps if the next instrument simply prints
"PASS" on its own.

142 instruments predate the gate. Retrofitting all of them is not the point and
a grandfather list that large would make the guard decoration, so the list is
FROZEN: it may only shrink. Two failure directions are both red —

  * a NEW verdict-emitter that does not route through the gate, and
  * a LISTED file that has since adopted the gate but was not struck off,

so the baseline cannot be used as a parking space for new work, and adopting the
gate is a one-way door.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO / "internal" / "experiments"
BASELINE = EXPERIMENTS / "verdict_gate_baseline.txt"

pytestmark = pytest.mark.skipif(
    not EXPERIMENTS.is_dir(), reason="internal/ is not present in the public export"
)


def _detectors():
    """Load by spec rather than by sys.path.

    A module-level `sys.path.insert` is collected before any test runs and
    stays for the rest of the session, which is exactly the invisible-armor
    shape tests/test_no_module_level_env_mutation.py exists to catch.
    """
    if "verdict_gate" in sys.modules:
        vg = sys.modules["verdict_gate"]
    else:
        spec = importlib.util.spec_from_file_location(
            "verdict_gate", EXPERIMENTS / "verdict_gate.py")
        vg = importlib.util.module_from_spec(spec)
        sys.modules["verdict_gate"] = vg      # @dataclass needs this pre-exec
        spec.loader.exec_module(vg)
    return vg.emits_verdict, vg.routes_through_gate


def _baseline() -> set[str]:
    return {
        line.strip()
        for line in BASELINE.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def _scan() -> tuple[set[str], set[str]]:
    """(emitters that skip the gate, emitters that route through it)."""
    emits_verdict, routes_through_gate = _detectors()
    skipping, routed = set(), set()
    for f in sorted(EXPERIMENTS.glob("*.py")):
        if f.stem == "verdict_gate":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if not emits_verdict(text):
            continue
        (routed if routes_through_gate(text) else skipping).add(f.stem)
    return skipping, routed


class TestTheGateCannotBeSkipped:
    def test_no_new_instrument_emits_a_verdict_outside_the_gate(self):
        skipping, _ = _scan()
        new = skipping - _baseline()
        assert not new, (
            "These instruments emit a verdict without routing through "
            "verdict_gate.gated_verdict:\n  "
            + "\n  ".join(sorted(new))
            + "\n\nA bare print('PASS') asserts a composition it never computed. "
            "Route the verdict through gated_verdict(units, ..., provenance=...), "
            "which refuses to score units it was not given. The baseline in "
            f"{BASELINE.name} is frozen and may not be appended to."
        )

    def test_baseline_carries_no_file_that_already_adopted_the_gate(self):
        _, routed = _scan()
        stale = _baseline() & routed
        assert not stale, (
            "These files route through verdict_gate but are still listed as "
            "grandfathered:\n  "
            + "\n  ".join(sorted(stale))
            + f"\n\nStrike them from {BASELINE.name}. The list only shrinks — "
            "leaving an adopter on it re-opens a slot for a future skipper."
        )

    def test_the_baseline_only_ever_shrinks(self):
        # A count, not just a set-difference: pairing one removal with one
        # addition would satisfy both tests above while leaving the debt flat.
        assert len(_baseline()) <= 142, (
            f"The grandfather list grew to {len(_baseline())}. It was frozen at "
            "142 on 2026-08-18 and may only shrink."
        )


class TestTheDetectorIsNotVacuous:
    """A ratchet whose detector matches nothing passes forever."""

    def test_it_recognises_a_printed_verdict(self):
        emits_verdict, _ = _detectors()
        assert emits_verdict('print(f"VERDICT: PASS ({rate:.0%})")')
        assert emits_verdict('json.dump({"verdict": "KILL"}, fh)')
        assert not emits_verdict("rate = hits / total\nprint(rate)")

    def test_the_baseline_still_matches_real_files(self):
        # If a refactor renames the instruments, the baseline silently stops
        # covering anything and every new skipper reads as grandfathered.
        stems = {f.stem for f in EXPERIMENTS.glob("*.py")}
        missing = _baseline() - stems
        assert not missing, (
            "Baseline names no longer on disk (renamed or deleted):\n  "
            + "\n  ".join(sorted(missing))
            + f"\n\nUpdate {BASELINE.name} — a baseline pointing at ghosts "
            "grandfathers nothing and hides new skippers."
        )
