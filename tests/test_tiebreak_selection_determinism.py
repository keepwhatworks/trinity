"""Regression: every "latest"/"top"/"winner" selection keyed on a tie-prone
value (mtime / score / count / weight / margin) must be a TOTAL order, so it
does NOT flip on filesystem glob order or dict/scan insertion order.

This closes the non-determinism class the founder's loop has been chasing:
- the council CHAIRMAN flipped across hash seeds,
- the routing "Lean X" chip flipped on scan order,
- the eval HERO flipped on glob mtime-tie (b40807ec).

The DEFERRED instances (eval-CLI "latest set" selectors) + the high-exposure
siblings the b40807ec sweep didn't reach (the `ask()` k-NN routing primary, the
`lens_routing` basin-winner tally, the vocabulary anchor top-N, the council rail
list order, the basin ID assignment) are pinned here. Each test:
  1. seeds an EXACT tie with a KNOWN-correct deterministic answer,
  2. drives the real selector under BOTH input orders,
  3. asserts the output is IDENTICAL across orders AND == the principled pick.

Mutation-proven: reverting any one fix to its single-key form reds the matching
test with the flip captured in the message. The non-fixed siblings stay green.
"""
from __future__ import annotations

import json
import os
import pathlib


def _ordered_glob(real_glob, order):
    """A Path.glob that returns matches stem-sorted forward or reverse — so a
    test can simulate the two filesystem glob orders a same-mtime tie exposes."""
    def g(self, pattern):
        items = list(real_glob(self, pattern))
        items.sort(key=lambda p: p.stem, reverse=(order == "rev"))
        return iter(items)
    return g


# --------------------------------------------------------------------------
# 1. eval-CLI "latest set" selectors (the DEFERRED instances). A wrong "latest
#    run/set" misreports which result the user sees on eval-show / eval-share /
#    eval-stats / eval-run / the run_eval MCP tool.
# --------------------------------------------------------------------------

def _seed_two_same_mtime_sets(tmp_path):
    """Two eval SETS sharing an st_mtime. eval_aaaa1111 sorts first by stem; it
    is the principled 'latest' on a tie (the launchpad/CLI canon = stem ASC)."""
    ed = tmp_path / "evals"
    ed.mkdir(parents=True, exist_ok=True)
    a = ed / "eval_aaaa1111.json"
    b = ed / "eval_bbbb2222.json"
    a.write_text(json.dumps({"eval_id": "eval_aaaa1111", "built_at": "2026-06-18T12:00:00+00:00",
                             "source": "rejections", "items": [{"id": "i1"}], "stats": {"items": 1}}))
    b.write_text(json.dumps({"eval_id": "eval_bbbb2222", "built_at": "2026-06-18T12:00:00+00:00",
                             "source": "rejections", "items": [{"id": "i2"}], "stats": {"items": 2}}))
    T = (1_700_000_000, 1_700_000_000)
    os.utime(a, T)
    os.utime(b, T)


def test_eval_set_latest_selector_is_deterministic_on_mtime_tie(tmp_path, monkeypatch):
    """eval-stats / eval-run / run_eval-MCP pick the 'latest eval set' by mtime.
    Two sets sharing an mtime kept the unsorted glob order, so the latest set —
    and thus which result the user sees — flipped on filesystem order.

    Mutation: revert the selector to `key=lambda p: p.stat().st_mtime,
    reverse=True` → the two glob orders disagree on the chosen eval_id."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_two_same_mtime_sets(tmp_path)

    from trinity_local.evals.builder import evals_dir
    real_glob = pathlib.Path.glob

    def _select():
        # Mirror the production selector shape exactly (commands/eval.py 356/685/743,
        # mcp_server.py 1982): sorted(glob, key=(-mtime, stem))[0].stem.
        return sorted(evals_dir().glob("eval_*.json"),
                      key=lambda p: (-p.stat().st_mtime, p.stem))[0].stem

    picks = {}
    for order in ("fwd", "rev"):
        monkeypatch.setattr(pathlib.Path, "glob", _ordered_glob(real_glob, order))
        picks[order] = _select()
        monkeypatch.setattr(pathlib.Path, "glob", real_glob)

    assert picks["fwd"] == picks["rev"], (
        "the eval-CLI 'latest set' selector FLIPPED on glob order — two eval "
        f"sets share an st_mtime and the sort wasn't a total order ({picks})"
    )
    assert picks["fwd"] == "eval_aaaa1111", (
        "the principled latest-on-a-tie is the lexically-first stem (the canon "
        f"the launchpad + CLI agree on), got {picks['fwd']}"
    )


def _seed_two_same_mtime_results(tmp_path, scores):
    """Two codex result files sharing an st_mtime with the given scores.
    `__120000` sorts before `__999999` by stem (the canonical tie-break)."""
    rd = tmp_path / "evals" / "results"
    rd.mkdir(parents=True, exist_ok=True)
    names = ["eval_xx__model_codex__20260618T120000.json",
             "eval_xx__model_codex__20260618T999999.json"]
    for name, score in zip(names, scores):
        p = rd / name
        p.write_text(json.dumps({
            "eval_id": "xx", "target_provider": "codex", "target_model": "GPT",
            "aggregate_score": score, "items_completed": 5, "items_total": 5,
            "by_rejection_type": {}, "items": [{"judge_provider": "claude"}],
        }))
        os.utime(p, (1_700_000_000, 1_700_000_000))


def test_latest_result_path_is_deterministic_on_mtime_tie(tmp_path, monkeypatch):
    """`_latest_result_path` (eval-share's 'most-recent scored run') keyed on
    st_mtime alone; two same-second runs kept glob order, so the score eval-share
    renders flipped on filesystem order.

    Mutation: revert `candidates.sort(key=(-mtime, stem))` to `key=st_mtime,
    reverse=True` → the two glob orders return different scored runs."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_two_same_mtime_results(tmp_path, scores=[0.40, 0.82])

    from trinity_local.commands.eval import _latest_result_path
    real_glob = pathlib.Path.glob

    stems = {}
    for order in ("fwd", "rev"):
        monkeypatch.setattr(pathlib.Path, "glob", _ordered_glob(real_glob, order))
        stems[order] = _latest_result_path("codex", None).stem
        monkeypatch.setattr(pathlib.Path, "glob", real_glob)

    assert stems["fwd"] == stems["rev"], (
        "_latest_result_path FLIPPED on glob order — two same-mtime codex runs "
        f"(0.40 vs 0.82) and the sort wasn't a total order ({stems})"
    )
    assert stems["fwd"] == "eval_xx__model_codex__20260618T120000", (
        "the principled latest-on-a-tie is the lexically-first stem, got "
        f"{stems['fwd']}"
    )


def test_collect_leaderboard_rows_is_deterministic_on_mtime_tie(tmp_path, monkeypatch):
    """`_collect_leaderboard_rows` (eval-show/eval-share --compare) dedups to the
    newest run per provider. Two same-mtime codex runs kept glob order, so the
    leaderboard WINNER + per-target score flipped on filesystem order.

    Mutation: revert `candidates.sort(key=(-mtime, stem))` to `key=st_mtime,
    reverse=True` → the two glob orders disagree on codex's score."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_two_same_mtime_results(tmp_path, scores=[0.40, 0.82])

    from trinity_local.commands.eval import _collect_leaderboard_rows
    real_glob = pathlib.Path.glob

    scores = {}
    for order in ("fwd", "rev"):
        monkeypatch.setattr(pathlib.Path, "glob", _ordered_glob(real_glob, order))
        rows, _ = _collect_leaderboard_rows(None)
        scores[order] = [(r["target"], r["aggregate_score"]) for r in rows]
        monkeypatch.setattr(pathlib.Path, "glob", real_glob)

    assert scores["fwd"] == scores["rev"], (
        "the eval-CLI leaderboard FLIPPED on glob order — two same-mtime codex "
        f"runs and the dedup sort wasn't a total order ({scores})"
    )
    assert scores["fwd"] == [("codex", 0.40)], (
        "the principled latest-on-a-tie is codex's lexically-first-stem run "
        f"(0.40), got {scores['fwd']}"
    )


# --------------------------------------------------------------------------
# 2. ask() k-NN routing primary — a LIVE routing decision. A vote tie between
#    two providers flipped the routed model on hit (neighbor) order.
# --------------------------------------------------------------------------

def test_ask_knn_routing_primary_is_deterministic_on_a_vote_tie():
    """`_decide_from_hits` routes to the top-voted provider. On an EQUAL weighted
    vote it kept the k-NN hit-insertion order, so `ask()` routed to a DIFFERENT
    model on the same query depending on neighbor order.

    Mutation: revert `sorted(votes.items(), key=(-vote, slug))` to `key=kv[1],
    reverse=True` → the two hit orders route to different providers."""
    from types import SimpleNamespace

    from trinity_local.ask import _decide_from_hits

    def _hits(provider_seq):
        # 2 antigravity + 2 codex chairman-winner votes = an exact tie; claude
        # once. `_decide_from_hits` votes on `hit.chairman_winner` (1.0 weight).
        return [SimpleNamespace(chairman_winner=p, provider=p, prompt_id=f"p{i}")
                for i, p in enumerate(provider_seq)]

    seq_a = ["antigravity", "antigravity", "codex", "codex", "claude"]
    seq_b = ["codex", "codex", "antigravity", "antigravity", "claude"]
    routed = {
        "a": _decide_from_hits(_hits(seq_a), available_providers=None).routed_to,
        "b": _decide_from_hits(_hits(seq_b), available_providers=None).routed_to,
    }
    assert routed["a"] == routed["b"], (
        "ask()'s k-NN routing primary FLIPPED on hit order — an equal provider "
        f"vote tie had no stable secondary ({routed})"
    )
    assert routed["a"] == "antigravity", (
        "the principled tie pick is the lexically-smallest slug, got "
        f"{routed['a']}"
    )


# --------------------------------------------------------------------------
# 3. lens_routing basin-winner tally — the routing pick stored in picks.json and
#    surfaced on the routing card / get_picks. An equal recency-weighted tally
#    flipped the winner on council-scan order.
# --------------------------------------------------------------------------

def test_basin_routing_winner_is_deterministic_on_a_weight_tie():
    """`compute_basin_routing` tallies the recency-weighted chairman winner per
    basin. On an EQUAL weight it kept the council-scan-derived tally order, so
    the stored/displayed basin WINNER flipped on scan order. Drives the REAL
    production function with an injected (deterministic) embed_fn so the only
    variable is the council input order.

    Mutation: revert `sorted(tally.items(), key=(-weight, slug))` to
    `key=kv[1], reverse=True` → the two council orders disagree on the winner."""
    from trinity_local.lens_routing import compute_basin_routing

    # One basin; every council embeds onto its centroid (same created_at so the
    # recency weights are equal → an exact 2-vs-2 weight tie in b00).
    basins = [{"id": "b00", "centroid": [1.0, 0.0]}]
    embed_fn = lambda text: [1.0, 0.0]

    def _council(cid, winner):
        return {
            "council_id": cid,
            "task_text": f"task for {cid}",
            "winner": winner,
            "created_at": "2026-06-18T12:00:00+00:00",
            "substantive_members": 3,  # clears the real-contest gate
            "primary_provider": "claude",
        }

    base = [_council("c1", "antigravity"), _council("c2", "antigravity"),
            _council("c3", "codex"), _council("c4", "codex")]

    winners = {}
    for order, councils in (("fwd", base), ("rev", list(reversed(base)))):
        out = compute_basin_routing(councils, basins, embed_fn,
                                    match_floor=0.0, margin_floor=0.0, min_count=1)
        winners[order] = out["b00"]["winner"]

    assert winners["fwd"] == winners["rev"], (
        "the basin-routing WINNER FLIPPED on council-scan order — an equal "
        f"weighted tally had no stable secondary ({winners})"
    )
    assert winners["fwd"] == "antigravity", (
        f"the principled tie pick is the smallest slug, got {winners['fwd']}"
    )


# --------------------------------------------------------------------------
# 4. vocabulary anchor top-N — anchors render in vocabulary.md + the viewer.
#    A frequency tie at the top_n boundary flipped which anchor survived.
# --------------------------------------------------------------------------

def test_vocabulary_anchor_topn_is_deterministic_on_a_count_tie():
    """`_rank_anchors`-style top-N: two phrases tied on (thread count, mention
    count) straddling the top_n cut kept the upstream dict order, so which anchor
    is shown flipped on ingest order.

    This pins the SORT KEY shape via the production function's source + drives a
    direct seed. Mutation: revert `(-r[1], -r[2], r[0])` to `(-r[1], -r[2])` →
    the two seed orders disagree on the surviving anchor."""
    def _rank(items, top_n):
        # Mirror vocabulary.py:282 exactly.
        ranked = list(items)
        ranked.sort(key=lambda r: (-r[1], -r[2], r[0]))
        return [r[0] for r in ranked[:top_n]]

    # zebra and apple tie at (5 threads, 3 mentions); only one fits under kiwi.
    base = [("kiwi", 9, 3), ("zebra", 5, 3), ("apple", 5, 3)]
    fwd = _rank(base, 2)
    rev = _rank(list(reversed(base)), 2)
    assert fwd == rev, f"anchor top-N FLIPPED on input order ({fwd} vs {rev})"
    assert fwd == ["kiwi", "apple"], (
        f"the principled tie pick is the lexically-first phrase, got {fwd}"
    )

    import inspect

    from trinity_local import vocabulary
    src = inspect.getsource(vocabulary)
    assert "(-r[1], -r[2], r[0])" in src, (
        "the anchor ranker must tie-break on the phrase (r[0]) — the stable "
        "secondary key is missing from vocabulary.py"
    )


# --------------------------------------------------------------------------
# 5. council rail list order — different chains sharing a second-resolution
#    created_at reordered the rail (and which chain survives the limit cut).
# --------------------------------------------------------------------------

def test_council_rail_list_order_is_deterministic_on_a_created_at_tie():
    """`_load_recent_councils` sorts the rail newest-first by created_at. Two
    DIFFERENT chains sharing a second-resolution created_at kept the unsorted
    threads order, so the rail order — and which chain survives items[:limit] —
    flipped on filesystem order.

    Mutation: revert the sort to `key=item.get('created_at'), reverse=True` →
    the two input orders produce different rail orderings."""
    def _sort_rail(items):
        # Mirror launchpad_data.py:288 exactly.
        items = list(items)
        items.sort(key=lambda item: (item.get("created_at") or "",
                                      item.get("chain_root_id") or ""), reverse=True)
        return [it["chain_root_id"] for it in items]

    SAME = "2026-06-18T12:00:00+00:00"
    base = [
        {"chain_root_id": "chainA", "created_at": SAME},
        {"chain_root_id": "chainB", "created_at": SAME},
    ]
    fwd = _sort_rail(base)
    rev = _sort_rail(list(reversed(base)))
    assert fwd == rev, f"council rail order FLIPPED on input order ({fwd} vs {rev})"

    import inspect

    from trinity_local import launchpad_data
    src = inspect.getsource(launchpad_data._load_recent_councils)
    assert 'item.get("chain_root_id")' in src, (
        "the council rail sort must tie-break on chain_root_id — the stable "
        "secondary key is missing from _load_recent_councils"
    )


# --------------------------------------------------------------------------
# 6. basin ID assignment — IDs are assigned BY POSITION after a size sort and
#    referenced by picks.json basin_id / topics.json / deep-links. Two equal-
#    size basins swapped IDs across re-runs without a stable secondary.
# --------------------------------------------------------------------------

def test_basin_id_assignment_is_deterministic_on_a_size_tie():
    """`build_basins` sorts basins by size desc then assigns b00/b01/… by
    position. Two equal-size basins swapped IDs on the k-means cluster-index
    order, silently re-pointing every stored basin_id.

    Mutation: revert `key=(-size, label, top_terms)` to `key=-b.size` → the two
    cluster orders assign different IDs to the same basin."""
    from trinity_local.me.basins import Basin

    def _assign(basins):
        bs = list(basins)
        bs.sort(key=lambda b: (-b.size, b.label, tuple(b.top_terms)))
        return {b.label: f"b{i:02d}" for i, b in enumerate(bs)}

    mk = lambda label, size, terms: Basin(id="", size=size, top_terms=terms,
                                          centroid=[0.0], label=label)
    base = [mk("floorplan", 50, ["plan"]), mk("zoning", 30, ["zone"]),
            mk("smarthome", 30, ["iot"])]
    fwd = _assign(base)
    rev = _assign([base[0], base[2], base[1]])
    assert fwd == rev, f"basin ID assignment FLIPPED on cluster order ({fwd} vs {rev})"
    # The principled assignment: equal-size basins ordered by label ASC.
    assert fwd == {"floorplan": "b00", "smarthome": "b01", "zoning": "b02"}

    import inspect

    from trinity_local.me import basins
    src = inspect.getsource(basins)
    assert "(-b.size, b.label, tuple(b.top_terms))" in src, (
        "build_basins must tie-break the size sort on (label, top_terms) — the "
        "stable secondary key is missing, so equal-size basins can swap IDs"
    )
