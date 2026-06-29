"""`lens-build --dry-run` must surface junk-drawer topology health, not just
the top-10 basins.

Dogfooding the live corpus (2026-06-06): the dry-run emitted `basins: 48` plus a
10-row `basin_summary` with NO skew signal and no hidden-count — so an operator
running it for its stated purpose ("inspect the corpus topology before committing
to a rebuild") could not tell a healthy spread from a junk-drawering corpus. The
top-basin share is the exact metric the BUILD-time guard enforces (top basin
under ~20% by auto-sizing k, #245/#255), yet it was invisible at inspection time.
The dry-run now emits `clustered_prompts` + `top_basin_share` + `hidden_basins`
(mirrors the routing reader / cheat-sheet hidden-count honesty, #290).

Mutation: drop the new fields from the dry_run return → these fail.
"""
from __future__ import annotations

from trinity_local.me.basins import Basin


def _basin(bid: str, size: int) -> Basin:
    return Basin(id=bid, size=size, top_terms=["t"], centroid=[0.0])


def _run_dry_run(monkeypatch, tmp_path, basins):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    import trinity_local.me_builder as mb
    import trinity_local.me.pipeline as pipeline

    # Non-empty sample short-circuits the real embedder/search; stage1_basins is
    # the synthetic topology under test. dry_run=True returns right after Stage 1.
    monkeypatch.setattr(
        mb, "_sample_diverse_with_embeddings", lambda **kw: [{"prompt_id": "p1", "text": "x"}]
    )
    monkeypatch.setattr(pipeline, "stage1_basins", lambda **kw: basins)
    _path, result = mb.build_me_via_lens_pipeline(dry_run=True)
    return result


def test_dry_run_surfaces_junk_drawer_share_and_hidden_count(tmp_path, monkeypatch):
    # 11 basins: a dominant b00 (600 of 1000 prompts = 60% junk-drawer) + a tail
    # of 10. The build guard would flag 60% > 20%; the operator must see it.
    basins = [_basin("b00", 600)] + [_basin(f"b{i:02d}", 40) for i in range(1, 11)]
    result = _run_dry_run(monkeypatch, tmp_path, basins)

    assert result["basins"] == 11
    assert result["clustered_prompts"] == 1000
    assert result["top_basin_share"] == 0.6, (
        "the largest basin's prompt share (the junk-drawer metric) must be "
        "surfaced — an operator can't assess topology health without it"
    )
    # 11 basins, summary capped at 10 → exactly 1 hidden, stated not silently dropped.
    assert result["hidden_basins"] == 1
    assert len(result["basin_summary"]) == 10


def test_dry_run_share_is_zero_when_no_clustered_prompts(tmp_path, monkeypatch):
    # Degenerate: all-empty basins (size 0) must NOT ZeroDivisionError — a green
    # gate that crashes on empty data is worse than no gate (the green-gate rule).
    result = _run_dry_run(monkeypatch, tmp_path, [_basin("b00", 0)])
    assert result["top_basin_share"] == 0.0
    assert result["clustered_prompts"] == 0
    assert result["hidden_basins"] == 0
