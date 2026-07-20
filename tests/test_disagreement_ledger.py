"""Disagreement ledger — the LLM-free core of the `trust` surface.

Covers: load (excludes virtual synthesis_only, folds slugs to labs), the
retrieval (cross-provider only, ranked, recurrence count, real-embedder gate),
and the tally's trustworthiness gate. Mutation proof: drop the K3 upper bound in
aggregate_tally → test_degenerate_tally_is_withheld RED (a resolver that parrots
the chairman, K3==1.0, must NOT be called trustworthy).

Synthetic fixtures only — never founder corpus text.
"""
from __future__ import annotations

import json

import pytest

from trinity_local.disagreement_ledger import (
    DisagreementPattern, _ledger_dir, _parse_resolution, aggregate_tally, build_ledger,
    load_disagreements, reaggregate_ledger, retrieve_recurring,
)


def _council(home, cid, claims, winner, mode=None):
    d = home / "council_outcomes"
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "council_run_id": cid,
        "created_at": "2026-05-01T12:00:00+00:00",
        "metadata": {"task_text": "how should we design the router", **({"mode": mode} if mode else {})},
        "routing_label": {"winner": winner, "disagreed_claims": claims},
    }
    (d / f"{cid}.json").write_text(json.dumps(rec))


def test_load_excludes_virtual_and_folds_slugs(tmp_path):
    _council(tmp_path, "council_a", [
        {"claim": "event-driven invalidation guarantees freshness",
         "why_matters": "correctness", "providers_for": ["claude"], "providers_against": ["gemini", "codex"]},
    ], winner="claude")
    _council(tmp_path, "council_virtual", [
        {"claim": "x", "why_matters": "y", "providers_for": ["claude"], "providers_against": ["gemini"]},
    ], winner="claude", mode="synthesis_only")

    pats = load_disagreements(home=str(tmp_path))
    assert len(pats) == 1, "virtual synthesis_only council must be excluded (circular)"
    p = pats[0]
    assert p.providers_for == ["anthropic"] and set(p.providers_against) == {"google", "openai"}, \
        "dispatch slugs must fold to labs"
    assert p.chairman_winner == "anthropic"
    assert p.is_cross_provider


def test_retrieval_returns_cross_provider_only(tmp_path):
    _council(tmp_path, "council_x", [
        {"claim": "the router should key on lens basins not task type",
         "why_matters": "task_type is noisy at query time",
         "providers_for": ["claude"], "providers_against": ["gemini"]},
    ], winner="claude")
    # A same-lab-only "disagreement" is NOT cross-provider → must be filtered.
    _council(tmp_path, "council_same", [
        {"claim": "the router should key on lens basins not task type",
         "why_matters": "same topic, single lab",
         "providers_for": ["claude"], "providers_against": ["claude_ai"]},
    ], winner="claude")

    out = retrieve_recurring("should the router key on lens basins rather than task type",
                             home=str(tmp_path), top_k=5)
    assert out, "a topically-matched cross-provider disagreement should retrieve"
    assert all(len({*r["providers_for"], *r["providers_against"]}) >= 2 for r in out), \
        "retrieval must return cross-provider disagreements only"
    assert all("chairman_winner" in r and "councils_on_split" in r for r in out)


def test_retrieval_refuses_without_real_embedder(tmp_path, monkeypatch):
    """The 'remove tf-idf' gate on new code: retrieval must refuse loudly rather
    than compute on the SHA-1 stub in a production run."""
    from trinity_local import embeddings as emb
    _council(tmp_path, "council_y", [
        {"claim": "c", "why_matters": "w", "providers_for": ["claude"], "providers_against": ["gemini"]},
    ], winner="claude")
    monkeypatch.delenv("TRINITY_DISABLE_MLX", raising=False)
    monkeypatch.setattr(emb, "mlx_actually_loaded", lambda: False)
    with pytest.raises(emb.EmbedderNotReadyError):
        retrieve_recurring("anything", home=str(tmp_path))


def _synthetic_patterns(n, for_lab="anthropic", against_lab="google", winner="anthropic"):
    return [DisagreementPattern(
        claim_id=f"c#{i}", council_id=f"c{i}", at="2026-05-01T00:00:00+00:00",
        claim=f"claim {i}", why_matters="w",
        providers_for=[for_lab], providers_against=[against_lab], chairman_winner=winner,
    ) for i in range(n)]


def test_trustworthy_tally_clears_both_gates():
    pats = _synthetic_patterns(70)
    # 55 followed (anthropic wins), 15 contradicted → K3 = 55/70 = 0.786 (in band),
    # anthropic 55W/15L (CI excludes 0.5), resolved 70 >= 60 → trustworthy.
    res = {f"c#{i}": ("followed" if i < 55 else "contradicted") for i in range(70)}
    agg = aggregate_tally(pats, res)
    assert 0.55 <= agg["k3_chairman_agreement"] <= 0.90
    assert agg["k4_discriminates"] and agg["tally_trustworthy"]


def test_degenerate_tally_is_withheld():
    """All-followed with the chairman always on the winning side → K3 == 1.0:
    the resolver is just parroting the chairman, so the tally must be WITHHELD.
    Mutation target: drop the K3 upper bound → this reds."""
    pats = _synthetic_patterns(70)
    res = {f"c#{i}": "followed" for i in range(70)}
    agg = aggregate_tally(pats, res)
    assert agg["k3_chairman_agreement"] == 1.0
    assert not agg["k3_in_band"]
    assert not agg["tally_trustworthy"], "a chairman-parroting resolver must not be trustworthy"


def test_tally_keys_on_model_version_not_lab():
    """The tally must key per model x version so Opus 4.8's signal doesn't hide
    inside 'anthropic'. Same lab, two versions with opposite outcomes -> TWO
    records, never one folded lab row. Mutation target: revert the models_for
    fallback in aggregate_tally -> the two version rows collapse and this reds."""
    pats = []
    for i in range(30):
        pats.append(DisagreementPattern(
            claim_id=f"o8#{i}", council_id=f"c{i}", at="2026-05-01T00:00:00+00:00",
            claim=f"c{i}", why_matters="w", providers_for=["anthropic"],
            providers_against=["google"], chairman_winner="anthropic",
            models_for=["claude · opus · 4.8"], models_against=["google · pro · 3.1"]))
    for i in range(30):
        pats.append(DisagreementPattern(
            claim_id=f"o7#{i}", council_id=f"d{i}", at="2026-05-01T00:00:00+00:00",
            claim=f"d{i}", why_matters="w", providers_for=["anthropic"],
            providers_against=["google"], chairman_winner="google",
            models_for=["claude · opus · 4.7"], models_against=["google · pro · 3.1"]))
    res = {f"o8#{i}": "followed" for i in range(30)}
    res.update({f"o7#{i}": "contradicted" for i in range(30)})
    recs = aggregate_tally(pats, res)["records"]
    assert "claude · opus · 4.8" in recs and "claude · opus · 4.7" in recs, \
        "the tally must separate model versions"
    assert "anthropic" not in recs, "versions must NOT fold into a single lab row"
    assert recs["claude · opus · 4.8"]["win_rate"] == 1.0
    assert recs["claude · opus · 4.7"]["win_rate"] == 0.0


def test_effort_rolls_into_primary_with_gated_breakdown():
    """A stamped effort tallies into the model×version PRIMARY (no fragmentation);
    effort appears only in the SECONDARY breakdown, gated on clearing the floor.
    Mutation: revert the _model_version_and_effort split in aggregate_tally -> the
    primary fragments into per-effort rows and this reds."""
    pats = [DisagreementPattern(
        claim_id=f"e#{i}", council_id=f"c{i}", at="2026-05-01T00:00:00+00:00",
        claim=f"c{i}", why_matters="w", providers_for=["openai"],
        providers_against=["google"], chairman_winner="openai",
        models_for=["openai · flagship · 5.5 · xhigh"],
        models_against=["google · pro · 3.1 · high"]) for i in range(12)]
    agg = aggregate_tally(pats, {f"e#{i}": "followed" for i in range(12)})
    recs = agg["records"]
    assert "openai · flagship · 5.5" in recs, "effort must roll into the model×version primary"
    assert "openai · flagship · 5.5 · xhigh" not in recs, "effort must NOT be a primary row"
    assert recs["openai · flagship · 5.5"]["w"] == 12
    eb = agg["effort_breakdown"]
    assert eb.get("openai · flagship · 5.5", {}).get("xhigh", {}).get("w") == 12, \
        "the effort split must surface in the secondary breakdown"


def test_tally_falls_back_to_lab_when_model_absent():
    """A pattern with no captured model identity keeps its lab row rather than
    vanishing — no resolved disagreement is silently dropped."""
    pats = [DisagreementPattern(
        claim_id=f"x#{i}", council_id=f"c{i}", at="2026-05-01T00:00:00+00:00",
        claim=f"c{i}", why_matters="w", providers_for=["anthropic"],
        providers_against=["google"], chairman_winner="anthropic") for i in range(12)]
    recs = aggregate_tally(pats, {f"x#{i}": "followed" for i in range(12)})["records"]
    assert "anthropic" in recs and recs["anthropic"]["w"] == 12


def test_load_carries_effort_leg_when_member_stamped_it(tmp_path):
    """model×size×EFFORT: load_disagreements appends the effort leg to a side's
    model identity when the council recorded that member's effort, and drops to
    model×size when it didn't. Mutation: revert the effort branch in _ident_label
    -> the effort disappears from the label and this reds."""
    d = tmp_path / "council_outcomes"
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "council_run_id": "c1", "created_at": "2026-05-01T12:00:00+00:00",
        "metadata": {"task_text": "t"},
        "member_results": [
            {"provider": "codex", "model": "gpt-5.5", "metadata": {"effort": "xhigh"}},
            {"provider": "antigravity", "model": "Gemini 3.1 Pro"},  # no effort stamped
        ],
        "routing_label": {"winner": "codex", "disagreed_claims": [
            {"claim": "x", "why_matters": "y",
             "providers_for": ["codex"], "providers_against": ["gemini"]},
        ]},
    }
    (d / "council_c1.json").write_text(json.dumps(rec))
    pats = load_disagreements(home=str(tmp_path))
    assert len(pats) == 1
    assert "xhigh" in pats[0].models_for[0], f"effort leg missing: {pats[0].models_for}"
    # the member with no stamped effort stays at model×size (no trailing '?')
    assert "?" not in pats[0].models_against[0], pats[0].models_against


def test_reaggregate_from_resolutions_no_llm(tmp_path):
    """reaggregate_ledger rebuilds summary.json from the EXISTING resolutions.jsonl
    with no resolver/LLM/embedder — the granularity-refresh path."""
    _council(tmp_path, "council_1", [
        {"claim": "a", "why_matters": "w", "providers_for": ["claude"], "providers_against": ["gemini"]},
    ], winner="claude")
    d = _ledger_dir(str(tmp_path))
    d.mkdir(parents=True, exist_ok=True)
    (d / "resolutions.jsonl").write_text(
        json.dumps({"claim_id": "council_1#0", "resolution": "followed"}) + "\n")
    agg = reaggregate_ledger(str(tmp_path))
    assert agg["resolved"] == 1
    assert (d / "summary.json").exists()


def test_parse_resolution_is_shape_guarded():
    assert _parse_resolution('{"resolution": "followed", "evidence_quote": "x"}') == ("followed", "x")
    assert _parse_resolution('noise {"resolution":"contradicted"} tail')[0] == "contradicted"
    assert _parse_resolution("[1, 2, 3]") == ("unresolved", "")       # valid JSON, wrong type
    assert _parse_resolution("not json at all") == ("unresolved", "")  # unparseable
    assert _parse_resolution('{"resolution": "maybe"}') == ("unresolved", "")  # invalid label


def test_build_ledger_injectable_resolver_persists(tmp_path):
    """build_ledger orchestrates without any real LLM call when a resolver is
    injected — assemble → resolve → aggregate → persist. (The default resolver
    rides session sampling; here we prove the wiring + persistence.)"""
    _council(tmp_path, "council_1", [
        {"claim": "a", "why_matters": "w", "providers_for": ["claude"], "providers_against": ["gemini"]},
    ], winner="claude")
    _council(tmp_path, "council_2", [
        {"claim": "b", "why_matters": "w", "providers_for": ["codex"], "providers_against": ["claude"]},
    ], winner="codex")

    def stub_resolver(pattern, evidence, config=None):
        return "followed", "decisive phrase"

    agg = build_ledger(
        home=str(tmp_path), resolver=stub_resolver,
        embed_batch_fn=lambda texts: [[1.0] + [0.0] * 767 for _ in texts],
    )
    assert agg["resolved"] == 2, "both cross-provider disagreements resolved"
    led = tmp_path / "disagreement_ledger"
    assert (led / "resolutions.jsonl").exists() and (led / "summary.json").exists()
    rows = [json.loads(x) for x in (led / "resolutions.jsonl").read_text().splitlines()]
    assert all(r["resolution"] == "followed" for r in rows)
