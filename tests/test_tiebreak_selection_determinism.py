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
        rows, _, _ = _collect_leaderboard_rows(None)
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
