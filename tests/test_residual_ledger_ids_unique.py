"""The residual ledger is append-only, so a colliding id silently loses a finding.

res_070 was assigned twice in one night — once to a `--max-clusters 0` defect and
again to the tension-MDL kill. Any reader that keys by id (every summariser does)
would have kept one and dropped the other, and nothing would have complained.
Append-only storage makes this permanent rather than transient.
"""

from __future__ import annotations

import collections
import json
import pathlib

import pytest

LEDGER = (pathlib.Path(__file__).resolve().parent.parent
          / "internal" / "experiments" / "residual_ledger.jsonl")

pytestmark = pytest.mark.skipif(
    not LEDGER.exists(), reason="internal/ is absent from the public export")


def _rows():
    return [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]


def test_every_residual_id_is_unique():
    ids = [r.get("id") for r in _rows()]
    dupes = {i: n for i, n in collections.Counter(ids).items() if n > 1}
    assert not dupes, (
        f"duplicate residual ids: {dupes}. The ledger is append-only, so a "
        "collision permanently hides one finding from any id-keyed reader. "
        "Renumber the LATER entry and carry a `renumbered_from` field."
    )


def test_ids_are_well_formed_so_the_uniqueness_check_cannot_pass_vacuously():
    ids = [r.get("id") for r in _rows()]
    assert ids, "no rows — the uniqueness assertion above would hold trivially"
    bad = [i for i in ids if not (isinstance(i, str) and i.startswith("res_"))]
    assert not bad, f"malformed residual ids: {bad}"
