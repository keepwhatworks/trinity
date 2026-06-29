"""Regression: the launchpad eval leaderboard must canonicalize web-era capture
slugs before the per-target dedup.

Found 2026-06-01 by eyeballing the real launchpad eval card: the cross-provider
leaderboard showed TWO rows both labeled "Gemini" (one from an eval result stored
under the web-era capture slug `target_provider="gemini"`, one under the
dispatch slug `target_provider="antigravity"`) — indistinguishable to the user,
and the single-card `eval-show --target <slug>` command could splice a slug that
won't dispatch. `_eval_summary` keyed the dedup by the RAW target_provider, so
the two slugs for the SAME provider never collapsed. The fix folds the slug via
`normalize_provider_slug` at the read boundary (symmetric to the council-outcome /
MCP-resource canonicalization).
"""
from __future__ import annotations

import json
import os


def _result(target, model, score):
    return {
        "target_provider": target,
        "target_model": model,
        "aggregate_score": score,
        "items_completed": 5,
        "items_total": 5,
        "eval_id": "setA",
        "completed_at": "2026-06-01T00:00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": score, "count": 5}},
        "items": [{"judge_provider": "claude"}],
    }


def _seed_and_summarize(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    # An eval set on disk so eval_set_available is True.
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8"
    )
    # Same provider (Gemini) under the web-era slug AND the dispatch slug, plus a
    # second provider so the leaderboard view renders (>=2 targets).
    seeds = [
        ("eval_setA__model_gemini__1.json", _result("gemini", "gemini-3.1-pro-preview", 0.44), (1000, 1000)),
        ("eval_setA__model_antigravity__2.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50), (2000, 2000)),
        ("eval_setA__model_claude__3.json", _result("claude", "claude-opus-4-8", 0.80), (3000, 3000)),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)  # deterministic mtime-desc ordering

    from trinity_local.launchpad_data import _eval_summary
    return _eval_summary()


def test_web_era_gemini_merges_into_one_antigravity_row(tmp_path, monkeypatch):
    summary = _seed_and_summarize(tmp_path, monkeypatch)
    comparison = summary.get("comparison") or []
    from collections import Counter

    by_display = Counter(r.get("target_display") for r in comparison)
    assert by_display["Gemini"] == 1, (
        f"web-era 'gemini' didn't merge with 'antigravity' — duplicate rows: {by_display}"
    )
    # No row may carry the un-dispatchable web-era slug as its target.
    assert all(r.get("target") != "gemini" for r in comparison), (
        "the web-era 'gemini' slug leaked as a leaderboard row target (un-dispatchable)"
    )
    gemini_row = next(r for r in comparison if r.get("target_display") == "Gemini")
    assert gemini_row["target"] == "antigravity", (
        f"the merged Gemini row's target must be the dispatch slug: {gemini_row['target']!r}"
    )
    # Two distinct providers after the merge (Claude + the merged Gemini),
    # one row each — NOT three (the pre-fix bug split Gemini into two rows).
    assert len(comparison) == 2
    assert set(by_display) == {"Claude", "Gemini"}


def test_leaderboard_sorted_descending_by_score_not_recency(tmp_path, monkeypatch):
    """The cross-provider leaderboard claims 'ranked by how each model scores on
    your kind of question' (launchpad_template ~1455). Pin the
    descending-by-aggregate_score sort so a refactor of the sort key can't
    silently mis-rank it — the verified-correct-but-unguarded pattern (#293
    family). The seeds are arranged so a naive mtime/insertion sort would give
    the WRONG order: the weakest model (antigravity 0.50) is the NEWEST run and
    the strongest (claude 0.82) is the OLDEST, so only a score-desc sort puts
    claude first and antigravity last. Mutation: drop `reverse=True` or change
    the key in launchpad_data._eval_summary's `comparison = sorted(...)` → reds.
    (Dogfooded 2026-06-06: on the founder's real data the leaderboard correctly
    showed Claude 0.818 > GPT 0.777 > Gemini 0.496 — this locks that in.)"""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    # Strongest model = OLDEST mtime; weakest = NEWEST mtime. A recency sort
    # would invert the leaderboard; a score sort gives claude → codex → antigravity.
    seeds = [
        ("eval_setA__model_claude__1.json", _result("claude", "claude-opus-4-8", 0.82), (1000, 1000)),
        ("eval_setA__model_codex__2.json", _result("codex", "gpt-5.5", 0.77), (2000, 2000)),
        ("eval_setA__model_antigravity__3.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50), (3000, 3000)),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)

    from trinity_local.launchpad_data import _eval_summary
    comparison = _eval_summary().get("comparison") or []
    scores = [r.get("aggregate_score") for r in comparison]
    assert scores == sorted(scores, key=lambda s: s if s is not None else -1.0, reverse=True), (
        f"leaderboard not sorted descending by aggregate_score: {scores}"
    )
    assert comparison and comparison[0].get("target") == "claude", (
        f"top leaderboard row must be the highest scorer (claude 0.82), got "
        f"{[(r.get('target'), r.get('aggregate_score')) for r in comparison]}"
    )
    # The weakest model is the NEWEST run — proving the order is by score, not mtime.
    assert comparison[-1].get("aggregate_score") == 0.50, (
        "the newest-but-weakest run must sort LAST — the leaderboard ranks by "
        "score, not recency"
    )


def test_single_card_target_is_canonicalized(tmp_path, monkeypatch):
    # The single-card target feeds an `eval-show --target <slug>` command; a
    # web-era slug there won't dispatch. (claude is the most-recent here, so the
    # single card targets claude — but the assertion holds for any web-era slug:
    # the stored target must be a dispatch slug.)
    summary = _seed_and_summarize(tmp_path, monkeypatch)
    assert summary["target"] in ("claude", "codex", "antigravity"), (
        f"single-card target is not a dispatch slug: {summary['target']!r}"
    )


def test_eval_summary_headline_skips_degenerate_null_score(tmp_path, monkeypatch):
    """SIBLING-INSTANCE guard (the live_council_two_pollers / test_the_boundary
    pattern). The skip-degenerate-null-score rule has THREE copies:
    commands/eval._latest_result_path (share single-card) and
    _collect_leaderboard_rows (share --compare) are both guarded in
    test_eval_share.py — but launchpad_data._eval_summary (the LAUNCHPAD card)
    has its OWN inline copy (launchpad_data.py ~957) that the share tests only
    *reference* in a docstring, never exercise. A null-score placeholder must NOT
    become the benchmark-card headline while a real scored run sits on disk (else
    the card shows 'ran, no score' over real numbers — the live 2026-05-31 bug).
    Post-#303 the headline is `max(scored, key=aggregate)` and `scored` only
    collects runs whose `aggregate_score is not None`, so the degenerate run is
    excluded from selection entirely. Mutation: drop that
    `if data.get('aggregate_score') is not None` filter when building `scored`
    and the null-score run leaks into `latest_scored`/selection → this reds."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")

    # A real scored claude run (older mtime) ...
    scored = results / "eval_setA__model_claude__scored.json"
    scored.write_text(json.dumps(_result("claude", "claude-opus-4-8", 0.80)), encoding="utf-8")
    os.utime(scored, (1000, 1000))
    # ... masked by a STRICTLY-NEWER degenerate placeholder (aggregate_score null).
    degen = results / "eval_setA__model_claude__degen.json"
    degen.write_text(json.dumps({
        "target_provider": "claude", "target_model": "claude-opus-4-8",
        "aggregate_score": None, "items_completed": 3, "items_total": 5,
        "eval_id": "setA", "completed_at": "2026-06-02T00:00:00",
        "by_rejection_type": {}, "items": [],
    }), encoding="utf-8")
    os.utime(degen, (2000, 2000))

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()
    # The headline must be the SCORED run (0.80), not the newer null-score one.
    assert summary.get("aggregate_score") == 0.80, (
        f"the eval-card headline took the null-score placeholder "
        f"(aggregate_score={summary.get('aggregate_score')!r}); _eval_summary must "
        f"skip it and headline the most-recent SCORED run (0.80)"
    )
    assert summary.get("has_results") is True


def test_eval_summary_reports_ran_no_score_when_only_degenerate(tmp_path, monkeypatch):
    """When ONLY a degenerate (null-score) run exists, _eval_summary must still
    fall back to it (has_results True, aggregate_score None) — 'ran, no score'
    over the cold CTA — not hide the run or crash (launchpad_data.py ~969)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    degen = results / "eval_setA__model_claude__degen.json"
    degen.write_text(json.dumps({
        "target_provider": "claude", "target_model": "claude-opus-4-8",
        "aggregate_score": None, "items_completed": 3, "items_total": 5,
        "eval_id": "setA", "completed_at": "2026-06-02T00:00:00",
        "by_rejection_type": {}, "items": [],
    }), encoding="utf-8")
    os.utime(degen, (2000, 2000))

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()
    assert summary.get("has_results") is True, (
        "a score-less run still happened — the card must report 'ran, no score', "
        "not fall back to the cold empty state as if no eval ran"
    )
    assert summary.get("aggregate_score") is None


def test_eval_headline_features_winner_not_latest_weaker_run(tmp_path, monkeypatch):
    """#303 value-proof: the eval-card HEADLINE must feature the STRONGEST scored
    run (your best model on YOUR hardest questions), not merely the most recent.
    The live failure shape: the user benchmarks a new model (Gemini 0.50) AFTER
    an older strong run (Claude 0.80) is on disk → the card headlined 0.50 and
    buried the proof a journalist screenshots. The freshness moves to
    `latest_run`. Seeds put the WEAKEST model as the NEWEST run, so only a
    winner-headline (not recency) lands Claude 0.80 in the hero. Mutation: revert
    the headline selection in _eval_summary from `max(scored, key=aggregate)`
    back to `scored[0]` (most-recent) → this reds (headline becomes Gemini 0.50)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    # Strong Claude run is OLDER; weak Gemini run is the NEWEST (just scored).
    seeds = [
        ("eval_setA__model_claude__1.json", _result("claude", "claude-opus-4-8", 0.80), (1000, 1000)),
        ("eval_setA__model_antigravity__2.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50), (2000, 2000)),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()
    # Headline = the WINNER (Claude 0.80), not the newest-but-weaker Gemini 0.50.
    assert summary.get("aggregate_score") == 0.80, (
        f"eval headline took the most-recent run instead of the strongest — #303 "
        f"value-proof regression: {summary.get('target_display')} "
        f"{summary.get('aggregate_score')}"
    )
    assert summary.get("target") == "claude"
    # The most-recent (weaker) run survives as latest_run so the "you just scored
    # a new model" freshness isn't lost from the headline-the-winner flip.
    lr = summary.get("latest_run")
    assert lr is not None, "latest_run must carry the most-recent run when it isn't the winner"
    assert lr.get("aggregate_score") == 0.50
    assert lr.get("target_display") == "Gemini"


def test_eval_latest_run_none_when_latest_is_winner(tmp_path, monkeypatch):
    """latest_run is None when the most-recent run IS the headline winner — no
    redundant 'most recent: X' line repeating the hero number. Seeds: the
    strongest model is also the NEWEST run."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    seeds = [
        ("eval_setA__model_antigravity__1.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50), (1000, 1000)),
        ("eval_setA__model_claude__2.json", _result("claude", "claude-opus-4-8", 0.80), (2000, 2000)),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()
    assert summary.get("aggregate_score") == 0.80
    assert summary.get("latest_run") is None, (
        "latest_run must be None when the most-recent run is itself the winner "
        "(no redundant secondary line)"
    )


def test_eval_headline_uses_per_provider_latest_not_stale_historical_max(tmp_path, monkeypatch):
    """#303 corner (dogfooded 2026-06-06): the headline must be the LEADERBOARD
    winner — each provider's MOST-RECENT scored run, sorted by score — NOT a
    stale historical max. Real failure: Claude had an OLD 1.0 on a tiny 2-item
    set AND a CURRENT 0.82 on the real set; a naive `max(all scored runs)`
    headline showed 1.0 and CONTRADICTED the leaderboard (correctly 0.82)
    rendered right below it. Seeds: Claude's OLDEST run is 1.0, its NEWER run
    0.82 — only a per-provider-latest headline lands 0.82 == comparison[0].
    Mutation: revert the per-provider-latest dedup in _eval_summary to
    `max(scored, key=aggregate)` → reds (headline becomes the stale 1.0)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    (tmp_path / "evals" / "eval_setOld.json").write_text(
        json.dumps({"eval_id": "setOld", "stats": {"items": 2}}), encoding="utf-8")

    def _result_set(target, model, score, eval_id):
        r = _result(target, model, score)
        r["eval_id"] = eval_id
        return r

    seeds = [
        # Claude's STALE 1.0 on a tiny old set (OLDEST mtime).
        ("eval_setOld__model_claude__old.json", _result_set("claude", "claude-opus-4-8", 1.0, "setOld"), (1000, 1000)),
        # Claude's CURRENT 0.82 on the real set (newer than the stale 1.0).
        ("eval_setA__model_claude__cur.json", _result_set("claude", "claude-opus-4-8", 0.82, "setA"), (2000, 2000)),
        # A second provider on the same current set so a leaderboard renders.
        ("eval_setA__model_codex__cur.json", _result_set("codex", "gpt-5.5", 0.77, "setA"), (2500, 2500)),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)

    from trinity_local.launchpad_data import _eval_summary
    s = _eval_summary()
    # Headline = Claude's CURRENT 0.82, NOT the stale historical 1.0.
    assert s.get("aggregate_score") == 0.82, (
        f"headline took the stale historical max (1.0) instead of Claude's "
        f"most-recent run (0.82): {s.get('aggregate_score')}"
    )
    # And it must EQUAL the leaderboard #1 — no headline/leaderboard contradiction.
    comp = s.get("comparison") or []
    assert comp and comp[0]["target"] == "claude" and comp[0]["aggregate_score"] == 0.82
    assert s.get("target") == comp[0]["target"]
    assert s.get("aggregate_score") == comp[0]["aggregate_score"]


def test_eval_headline_is_deterministic_when_two_runs_share_an_mtime(tmp_path, monkeypatch):
    """The eval-card HERO (the 'your best model scored X' number a journalist
    screenshots) + the leaderboard rows must NOT change with filesystem glob
    order. `_eval_summary` sorted results `key=st_mtime, reverse=True` — NOT a
    total order: two result files with an IDENTICAL mtime (a same-second
    eval-build, or a copy) keep their unsorted glob order on the tie, so the
    'most-recent run per provider' dedup — and therefore the leaderboard WINNER
    and the hero score — flipped purely on glob order. Proven: two same-mtime
    codex runs at 0.40 and 0.82 plus an antigravity 0.50 headlined 'Gemini
    0.500' one glob order and 'GPT 0.820' the reverse. The fix tie-breaks the
    sort on the stem so the order is total and the headline is the same render
    every time. Mutation: revert the sort key to `key=st_mtime, reverse=True`
    → this reds (the two glob orders disagree on the headline)."""
    import pathlib

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True)
    (tmp_path / "evals" / "eval_setA.json").write_text(
        json.dumps({"eval_id": "setA", "stats": {"items": 5}}), encoding="utf-8")
    # TWO codex runs at the SAME mtime, DIFFERENT scores (which one is "latest"
    # is ambiguous on a tie), plus a second provider so the leaderboard renders.
    T = (1_700_000_000, 1_700_000_000)
    seeds = [
        ("eval_setA__model_codex_lo.json", _result("codex", "gpt-5.5", 0.40), T),
        ("eval_setA__model_codex_hi.json", _result("codex", "gpt-5.5", 0.82), T),
        ("eval_setA__model_antigravity.json", _result("antigravity", "Gemini 3.1 Pro (High)", 0.50), T),
    ]
    for name, payload, mtime in seeds:
        p = results / name
        p.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(p, mtime)

    from trinity_local.launchpad_data import _eval_summary

    real_glob = pathlib.Path.glob

    def _ordered_glob(order):
        def g(self, pattern):
            items = list(real_glob(self, pattern))
            items.sort(key=lambda p: p.stem, reverse=(order == "rev"))
            return iter(items)
        return g

    outcomes = {}
    for order in ("fwd", "rev"):
        monkeypatch.setattr(pathlib.Path, "glob", _ordered_glob(order))
        s = _eval_summary()
        comp = s.get("comparison") or []
        outcomes[order] = {
            "headline_target": s.get("target"),
            "headline_score": s.get("aggregate_score"),
            "leaderboard": [(r["target"], r["aggregate_score"]) for r in comp],
        }
        monkeypatch.setattr(pathlib.Path, "glob", real_glob)

    assert outcomes["fwd"] == outcomes["rev"], (
        "the eval-card HERO + leaderboard FLIPPED on filesystem glob order — two "
        "result files share an st_mtime and the sort wasn't a total order "
        f"(fwd={outcomes['fwd']} vs rev={outcomes['rev']})"
    )
    # And the result is the principled, reproducible one: codex's latest is the
    # lexically-first stem (codex_hi < codex_lo) at 0.82, so codex headlines.
    assert outcomes["fwd"]["headline_target"] == "codex"
    assert outcomes["fwd"]["headline_score"] == 0.82
