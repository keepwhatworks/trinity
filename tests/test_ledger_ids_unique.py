"""Both append-only ledgers must keep ids unique, or a finding silently disappears.

res_070 was assigned twice in one night — once to a `--max-clusters 0` defect and
again to the tension-MDL kill. Any reader that keys by id (every summariser does)
would have kept one and dropped the other, and nothing would have complained.
Append-only storage makes this permanent rather than transient.

The amendment ledger had the same defect at larger scale: amd_0168–amd_0182 were
each given to two DIFFERENT councils in August (found 2026-09-29). Plans cite the
second set, so renumbering would break references; the collisions are frozen
below and no new one may join them. A repeated amendment id is otherwise legal
only as a RESOLUTION row: same id, same council, carrying `resolution_of`.
"""

from __future__ import annotations

import collections
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESIDUAL = ROOT / "internal" / "experiments" / "residual_ledger.jsonl"
AMENDMENT = ROOT / "internal" / "amendment-ledger.jsonl"

pytestmark = pytest.mark.skipif(
    not (RESIDUAL.exists() and AMENDMENT.exists()),
    reason="internal/ is absent from the public export")

# Frozen 2026-09-29: id -> the two councils that share it. Never extend this.
LEGACY_AMENDMENT_COLLISIONS = {
    "amd_0168": {"council_8817ca0c57a2e4ff", "council_phase2_review_2026_08_13"},
    **{f"amd_{n:04d}": {"council_61705279e8409cfa", "council_c8ddd3e5743cb505"} for n in range(169, 173)},
    **{f"amd_{n:04d}": {"council_13cb6e152fd0a61f", "council_3448022415e7b7ea"} for n in range(173, 177)},
    **{f"amd_{n:04d}": {"council_3448022415e7b7ea", "council_6ca82ab0f6dbbd75"} for n in range(177, 179)},
    **{f"amd_{n:04d}": {"council_6ca82ab0f6dbbd75", "council_df5e0f2dcc5acc76"} for n in range(179, 181)},
    **{f"amd_{n:04d}": {"council_6ac26dafe733d16a", "council_df5e0f2dcc5acc76"} for n in range(181, 183)},
}


def _rows(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_every_residual_id_is_unique():
    ids = [r.get("id") for r in _rows(RESIDUAL)]
    dupes = {i: n for i, n in collections.Counter(ids).items() if n > 1}
    assert not dupes, (
        f"duplicate residual ids: {dupes}. The ledger is append-only, so a "
        "collision permanently hides one finding from any id-keyed reader. "
        "Renumber the LATER entry and carry a `renumbered_from` field."
    )


def test_amendment_ids_repeat_only_as_resolutions():
    by_id = collections.defaultdict(list)
    for r in _rows(AMENDMENT):
        by_id[r.get("id")].append(r)
    bad = {}
    for aid, rows in by_id.items():
        if len(rows) == 1:
            continue
        if aid in LEGACY_AMENDMENT_COLLISIONS:
            councils = {r.get("council_id") for r in rows}
            if not councils <= LEGACY_AMENDMENT_COLLISIONS[aid]:
                bad[aid] = f"a third council joined a frozen collision: {sorted(councils)}"
            continue
        first, later = rows[0], rows[1:]
        for r in later:
            if r.get("resolution_of") != aid or r.get("council_id") != first.get("council_id"):
                bad[aid] = ("repeated id without `resolution_of` on the same council; "
                            "a new amendment needs the next free id")
    assert not bad, f"amendment id collisions: {bad}"


def test_ids_are_well_formed_so_the_uniqueness_checks_cannot_pass_vacuously():
    for path, prefix in ((RESIDUAL, "res_"), (AMENDMENT, "amd_")):
        ids = [r.get("id") for r in _rows(path)]
        assert ids, f"{path.name}: no rows — the uniqueness assertion would hold trivially"
        bad = [i for i in ids if not (isinstance(i, str) and i.startswith(prefix))]
        assert not bad, f"{path.name}: malformed ids: {bad}"
