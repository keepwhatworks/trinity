"""Green-gate guard for the combined mega-basin splitter (council #308).

`me.basin_split.split_mega_basins` is a split-only post-process: it refines ONLY
the oversized + incoherent "mega" basins into coherent sub-basins and leaves
every small / coherent basin byte-identical. It is a green emitter in the
green-gate sense — a "split" is a directive (the topology now claims these are
distinct domains), so it must FIRE on healthy multi-modal data and be REFUSED on
degenerate / coherent / unimodal data (docs/green-gate-checklist.md).

What this file pins (each a real assertion, not a smoke):

1. SPLIT FIRES — a synthetic mega of two clearly-separated 768-d gaussian blobs
   (size >= MEGA_SIZE_FLOOR, mean pairwise cosine < INCOHERENCE_FLOOR) splits
   into >= 2 coherent sub-clusters, AND every sub-cluster is tighter than the
   parent (the stop-gate invariant the green attests).
2. SPLIT-ONLY (#308) — a small basin (size < floor) and a large-but-coherent
   basin are both left UNTOUCHED (returned object identity preserved).
3. ABSTAIN, never crash — empty / single-point / single-distinct / missing-
   embedding megas return the basins UNCHANGED.
4. DETERMINISTIC — same input => same child ids + same membership, twice.
5. COVERAGE INVARIANT — the union of the sub-basins' prompt_ids equals the
   parent's prompt_ids exactly: no prompt is moved across basins, dropped to
   noise, or duplicated.
6. KNOB OFF by default — `split_mega_basins(basins)` with no `enabled=` and the
   env knob unset is a no-op (behaviour-preserving default).
7. MUTATION-PROOF — `test_split_call_is_load_bearing` proves that removing the
   `split_mega_basins(...)` call from `compute_basins` (the integration point)
   would red this suite: with the knob enabled, the synthetic mega is split, and
   the assertion FAILS if the splitter is a no-op.

The splitter reads embeddings via `memory.store.iter_prompt_nodes` (read-only);
we monkeypatch THAT source binding because `_load_basin_matrix` does a late
`from ..memory.store import iter_prompt_nodes` inside the function.
"""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import numpy as np
import pytest

from trinity_local.me import basin_split as bs
from trinity_local.me.basins import Basin

_DIM = 768


# --------------------------------------------------------------------------- #
# fixture builders                                                            #
# --------------------------------------------------------------------------- #
def _blob(rng, axis: int, n: int, *, sd: float = 0.15, scale: float = 5.0):
    """n points clustered around the unit axis `axis`, with isotropic noise.

    Two blobs on orthogonal axes are well-separated on the unit sphere (cosine
    ~0), so a correct splitter recovers exactly two coherent sub-clusters."""
    pts = np.zeros((n, _DIM), dtype=np.float32)
    pts[:, axis] = scale
    pts += rng.normal(0.0, sd, size=(n, _DIM)).astype(np.float32)
    return pts


def _nodes_for(ids: list[str], matrix: np.ndarray) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id=ids[i], embedding=matrix[i].tolist())
        for i in range(matrix.shape[0])
    ]


def _basin(bid: str, ids: list[str], *, size: int | None = None) -> Basin:
    return Basin(
        id=bid,
        size=size if size is not None else len(ids),
        top_terms=["x"],
        centroid=[0.0] * _DIM,
        prompt_ids=list(ids),
        representatives=[],
        thread_count=len(ids),
    )


def _patch_store(monkeypatch, nodes) -> None:
    """Patch the SOURCE binding the splitter imports late."""
    monkeypatch.setattr(
        "trinity_local.memory.store.iter_prompt_nodes",
        lambda *a, **k: iter(list(nodes)),
    )


def _two_blob_mega(rng, *, per_blob: int = 520):
    """A canonical splittable mega: two orthogonal-axis blobs, size >= floor."""
    a = _blob(rng, 0, per_blob)
    b = _blob(rng, 1, per_blob)
    matrix = np.vstack([a, b])
    ids = [f"p{i}" for i in range(matrix.shape[0])]
    return ids, matrix


# --------------------------------------------------------------------------- #
# 1. SPLIT FIRES + tightening invariant                                        #
# --------------------------------------------------------------------------- #
def test_splits_two_gaussian_blobs_into_coherent_subclusters(monkeypatch):
    rng = np.random.default_rng(0)
    ids, matrix = _two_blob_mega(rng)
    _patch_store(monkeypatch, _nodes_for(ids, matrix))

    mega = _basin("b00", ids)
    out = bs.split_mega_basins([mega], enabled=True)

    assert len(out) >= 2, "a clearly bimodal mega must split into >=2 sub-basins"
    # child ids are the parent id + a letter suffix (#308 derivation)
    assert all(b.id.startswith("b00") and b.id != "b00" for b in out), [b.id for b in out]

    # The green's INVARIANT: every sub-cluster is tighter than the parent mega.
    parent_coh = bs._mean_pairwise_cosine(bs._l2_normalize(matrix))
    assert parent_coh < bs.INCOHERENCE_FLOOR, "fixture must trip the incoherence guard"
    id_to_row = {ids[i]: matrix[i] for i in range(matrix.shape[0])}
    for child in out:
        rows = np.asarray([id_to_row[p] for p in child.prompt_ids], dtype=np.float32)
        child_coh = bs._mean_pairwise_cosine(bs._l2_normalize(rows))
        assert child_coh > parent_coh, (
            f"sub-basin {child.id} (coh={child_coh:.3f}) is no tighter than the "
            f"parent mega (coh={parent_coh:.3f}) — the stop-gate let through a "
            "split that doesn't attest its green"
        )


# --------------------------------------------------------------------------- #
# 2. SPLIT-ONLY (#308): small + coherent basins untouched                       #
# --------------------------------------------------------------------------- #
def test_small_basin_left_untouched(monkeypatch):
    """size < MEGA_SIZE_FLOOR => never a candidate, even if internally bimodal."""
    rng = np.random.default_rng(1)
    a = _blob(rng, 0, 200)
    b = _blob(rng, 1, 200)
    matrix = np.vstack([a, b])  # bimodal, but only 400 < 1000
    ids = [f"s{i}" for i in range(matrix.shape[0])]
    _patch_store(monkeypatch, _nodes_for(ids, matrix))

    small = _basin("b01", ids)
    out = bs.split_mega_basins([small], enabled=True)

    assert out == [small], "a sub-floor basin must pass through untouched"
    assert out[0] is small, "small basin must be the SAME object (no rebuild)"


def test_large_but_coherent_basin_left_untouched(monkeypatch):
    """size >= floor but mean pairwise cosine >= INCOHERENCE_FLOOR => keep whole."""
    rng = np.random.default_rng(2)
    matrix = _blob(rng, 0, 1100, sd=0.05)  # single tight cone
    ids = [f"c{i}" for i in range(matrix.shape[0])]
    _patch_store(monkeypatch, _nodes_for(ids, matrix))

    coh = bs._mean_pairwise_cosine(bs._l2_normalize(matrix))
    assert coh >= bs.INCOHERENCE_FLOOR, "fixture must be coherent enough to be kept whole"

    big_coherent = _basin("b02", ids)
    out = bs.split_mega_basins([big_coherent], enabled=True)

    assert out == [big_coherent], "a coherent mega must be kept whole (no spurious split)"
    assert out[0] is big_coherent


# --------------------------------------------------------------------------- #
# 3. ABSTAIN on degenerate input — never crash                                 #
# --------------------------------------------------------------------------- #
def test_abstains_on_empty_basin(monkeypatch):
    _patch_store(monkeypatch, [])
    empty = _basin("b03", [], size=1500)  # size claims mega, but no prompt_ids
    out = bs.split_mega_basins([empty], enabled=True)
    assert out == [empty]


def test_abstains_on_missing_embeddings(monkeypatch):
    """size says mega, but the store resolves NONE of the prompt_ids."""
    _patch_store(monkeypatch, [])  # store returns nothing
    ids = [f"m{i}" for i in range(1500)]
    miss = _basin("b04", ids)
    out = bs.split_mega_basins([miss], enabled=True)
    assert out == [miss]


def test_abstains_on_single_distinct_point(monkeypatch):
    """1500 prompts that all share ONE embedding => < 2 distinct points => abstain."""
    v = np.zeros(_DIM, dtype=np.float32)
    v[3] = 1.0
    ids = [f"q{i}" for i in range(1500)]
    nodes = [SimpleNamespace(id=i, embedding=v.tolist()) for i in ids]
    _patch_store(monkeypatch, nodes)
    one_point = _basin("b05", ids)
    out = bs.split_mega_basins([one_point], enabled=True)
    assert out == [one_point]


def test_abstains_when_only_one_embedding_resolvable(monkeypatch):
    """Only a single prompt_id resolves to an embedding => < 2 points => abstain."""
    v = np.zeros(_DIM, dtype=np.float32)
    v[3] = 1.0
    ids = [f"q{i}" for i in range(1500)]
    _patch_store(monkeypatch, [SimpleNamespace(id="q0", embedding=v.tolist())])
    b = _basin("b06", ids)
    out = bs.split_mega_basins([b], enabled=True)
    assert out == [b]


def test_no_op_on_empty_basin_list(monkeypatch):
    assert bs.split_mega_basins([], enabled=True) == []


# --------------------------------------------------------------------------- #
# 4. DETERMINISM                                                               #
# --------------------------------------------------------------------------- #
def test_deterministic_same_input_same_labels(monkeypatch):
    rng = np.random.default_rng(0)
    ids, matrix = _two_blob_mega(rng)
    _patch_store(monkeypatch, _nodes_for(ids, matrix))
    mega = _basin("b00", ids)

    out_a = bs.split_mega_basins([mega], enabled=True)
    out_b = bs.split_mega_basins([mega], enabled=True)

    sig_a = [(c.id, tuple(c.prompt_ids)) for c in out_a]
    sig_b = [(c.id, tuple(c.prompt_ids)) for c in out_b]
    assert sig_a == sig_b, "the splitter must be deterministic run-to-run"
    # crc32-seeded (not salted hash()) => stable child count + sizes too
    assert [c.size for c in out_a] == [c.size for c in out_b]


# --------------------------------------------------------------------------- #
# 5. COVERAGE INVARIANT — no prompt moved / dropped / duplicated               #
# --------------------------------------------------------------------------- #
def test_coverage_preserved_union_equals_original(monkeypatch):
    rng = np.random.default_rng(3)
    ids, matrix = _two_blob_mega(rng, per_blob=600)
    _patch_store(monkeypatch, _nodes_for(ids, matrix))

    mega = _basin("b00", ids)
    out = bs.split_mega_basins([mega], enabled=True)
    assert len(out) >= 2, "precondition: this fixture must actually split"

    child_ids = list(itertools.chain.from_iterable(c.prompt_ids for c in out))
    # exact set equality: nothing lost, nothing invented
    assert set(child_ids) == set(ids), "union of sub-basin prompt_ids must equal the parent's"
    # no duplication: a prompt lands in exactly one sub-basin
    assert len(child_ids) == len(ids), "a prompt must appear in exactly one sub-basin"
    assert sorted(child_ids) == sorted(ids)
    # size bookkeeping matches the membership
    assert sum(c.size for c in out) == len(ids)


def test_other_basins_in_the_list_are_untouched(monkeypatch):
    """Splitting b00 must not perturb a sibling small basin sharing the call."""
    rng = np.random.default_rng(4)
    ids, matrix = _two_blob_mega(rng)
    small_ids = [f"s{i}" for i in range(50)]
    small_mat = _blob(rng, 5, 50, sd=0.05)
    _patch_store(monkeypatch, _nodes_for(ids, matrix) + _nodes_for(small_ids, small_mat))

    mega = _basin("b00", ids)
    sibling = _basin("b01", small_ids)
    out = bs.split_mega_basins([mega, sibling], enabled=True)

    assert sibling in out, "the small sibling must survive identity-preserved"
    surviving = next(b for b in out if b is sibling)
    assert surviving.prompt_ids == small_ids


# --------------------------------------------------------------------------- #
# 6. KNOB OFF by default                                                       #
# --------------------------------------------------------------------------- #
def test_knob_off_by_default_is_noop(monkeypatch):
    monkeypatch.delenv(bs.SPLIT_ENV_VAR, raising=False)
    rng = np.random.default_rng(0)
    ids, matrix = _two_blob_mega(rng)
    _patch_store(monkeypatch, _nodes_for(ids, matrix))
    mega = _basin("b00", ids)

    # enabled=None => reads env knob => off => returns the SAME list object
    out = bs.split_mega_basins([mega])
    assert out == [mega]
    assert out[0] is mega, "default-off must not rebuild basins"


def test_env_knob_enables_split(monkeypatch):
    monkeypatch.setenv(bs.SPLIT_ENV_VAR, "1")
    rng = np.random.default_rng(0)
    ids, matrix = _two_blob_mega(rng)
    _patch_store(monkeypatch, _nodes_for(ids, matrix))
    mega = _basin("b00", ids)
    out = bs.split_mega_basins([mega])  # enabled=None -> reads env -> ON
    assert len(out) >= 2


# --------------------------------------------------------------------------- #
# 7. MUTATION-PROOF: the splitter call in compute_basins is load-bearing       #
# --------------------------------------------------------------------------- #
def test_split_call_is_load_bearing_in_compute_basins(monkeypatch):
    """End-to-end through compute_basins with split_megas=True: a bimodal mega
    that clusters into ONE basin must come out SPLIT.

    Mutation guard: if the `split_mega_basins(...)` line at the end of
    compute_basins is removed (or made a no-op), compute_basins returns the raw
    single mega and this assertion reds. We force a single global basin (k=1)
    over a clearly-bimodal corpus so the ONLY way to get >1 basin out is the
    splitter post-process firing.
    """
    rng = np.random.default_rng(7)
    a = _blob(rng, 0, 700)
    b = _blob(rng, 1, 700)
    matrix = np.vstack([a, b])
    ids = [f"e{i}" for i in range(matrix.shape[0])]
    # distinct, user-facing text per node so the dedup + scaffolding filters keep
    # them, and a unique transcript_id each so they cluster as 1400 threads.
    nodes = [
        SimpleNamespace(
            id=ids[i],
            transcript_id=f"t{i}",
            text=f"please refactor the billing module variant number {i}",
            embedding=matrix[i].tolist(),
        )
        for i in range(matrix.shape[0])
    ]
    # compute_basins reads its corpus AND the splitter reads its embeddings from
    # the same store binding — patch both source paths.
    monkeypatch.setattr(
        "trinity_local.me.basins.iter_prompt_nodes", lambda *a, **k: iter(list(nodes))
    )
    monkeypatch.setattr(
        "trinity_local.memory.store.iter_prompt_nodes", lambda *a, **k: iter(list(nodes))
    )

    from trinity_local.me.basins import compute_basins

    # k=1 forces a single global basin; with the splitter wired + enabled, that
    # mega is sub-clustered back into >=2 coherent basins.
    split_on = compute_basins(k=1, split_megas=True)
    assert len(split_on) >= 2, (
        "compute_basins(split_megas=True) over a bimodal mega returned a single "
        "basin — the split_mega_basins post-process is not wired / not firing"
    )

    # Control: the SAME corpus with the knob OFF stays a single basin. This is the
    # mutation discriminator — if the splitter call were unconditionally applied
    # (or the knob ignored), this would also split and the green would be a proxy.
    split_off = compute_basins(k=1, split_megas=False)
    assert len(split_off) == 1, (
        "compute_basins(split_megas=False) must be behaviour-preserving (one basin) "
        "— the knob is not gating the splitter"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
