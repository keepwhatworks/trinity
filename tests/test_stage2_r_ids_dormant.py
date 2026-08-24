"""Option (c) — deterministic r_* ids in stage 2 — ships dormant (amd_0186/0187).

The join problem it dissolves: every lens tension cites d_* evidence ids that
join to 0 of 80 palate-scorable rows (res_071), because stage-2 ids are
chairman-supplied or sequential — content-free, and re-minted differently on
every build (the res_077 orphaning class). Content-derived ids are joinable
(decisions export to the unified ledger keeping their ids), regen-proof (same
content, same id), and collision-safe (stable_id hashes content).

The dormancy contract from the council: OFF means byte-identical behaviour —
same ids, same order, no migration, no writes.
"""
from __future__ import annotations

import json

from trinity_local.me.decisions import parse_decisions


def _raw(rows):
    return "\n".join(json.dumps(r) for r in rows)


ROW = {"privileged": "measured artifact", "sacrificed": "confident narration",
       "verbatim": "show me the actual output", "valence": "correction",
       "prompt_id": "pnode_abc", "id": "d_001"}


class TestDormancy:
    def test_off_is_byte_identical_to_today(self):
        rows = [dict(ROW), {**ROW, "privileged": "second thing", "id": "d_002"}]
        got = parse_decisions(_raw(rows), [])
        assert [d.id for d in got] == ["d_001", "d_002"], (
            "default OFF must keep chairman-supplied ids untouched")


class TestArmed:
    def test_ids_are_content_derived_and_r_prefixed(self):
        got = parse_decisions(_raw([dict(ROW)]), [], mint_r_ids=True)
        assert len(got) == 1 and got[0].id.startswith("r_")
        assert got[0].id != "d_001", "the chairman-supplied id must be replaced"

    def test_same_content_mints_the_same_id_across_builds(self):
        """The regen-proof property — what dissolved res_077's orphaning."""
        a = parse_decisions(_raw([dict(ROW)]), [], mint_r_ids=True)[0].id
        b = parse_decisions(_raw([{**ROW, "id": "d_999"}]), [], mint_r_ids=True)[0].id
        assert a == b, "content-derived means the arbitrary incoming id is irrelevant"

    def test_different_content_cannot_collide(self):
        rows = [dict(ROW), {**ROW, "privileged": "a different pole"}]
        got = parse_decisions(_raw(rows), [], mint_r_ids=True)
        assert len(got) == 2 and got[0].id != got[1].id

    def test_exact_duplicates_dedup_to_the_first(self):
        rows = [dict(ROW), {**ROW, "id": "d_777"}]   # same content, different label
        got = parse_decisions(_raw(rows), [], mint_r_ids=True)
        assert len(got) == 1, "one underlying decision must not become two evidence ids"

    def test_the_id_matches_the_ledger_minting_scheme(self):
        """stable_id('r', ...) — the same constructor the r_* act corpus uses,
        so the id namespace is one namespace, not a lookalike."""
        from trinity_local.utils import stable_id

        got = parse_decisions(_raw([dict(ROW)]), [], mint_r_ids=True)[0]
        assert got.id == stable_id("r", ROW["privileged"][:200],
                                   ROW["sacrificed"][:200], ROW["prompt_id"])


class TestTheFlagIsNotArmedAnywhere:
    def test_shipped_config_does_not_set_it(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        for sub in ("src", "scripts"):
            for f in (root / sub).rglob("*"):
                if f.suffix not in {".py", ".sh", ".json", ".toml"} or not f.is_file():
                    continue
                t = f.read_text(errors="replace")
                for shape in ('export TRINITY_STAGE2_R_IDS',
                              'setenv("TRINITY_STAGE2_R_IDS"',
                              'environ["TRINITY_STAGE2_R_IDS"] ='):
                    assert shape not in t, (f, shape)
