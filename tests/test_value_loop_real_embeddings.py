"""The core value loop, proven on the REAL embedder: councils → consolidate →
picks → route, with semantic separation that only ModernBERT (not the TF-IDF
fallback) produces.

Trinity's whole value (#298 cortex-into-lens collapse) is: accumulated councils
are placed into lens basins by embedding their task_text, the chairman winner is
tallied per basin (`consolidate_via_lens_basins`), and a future query is routed to
that basin's winner (`place_query`). The unit tests (`test_lens_routing`,
`test_ask.AskRoutesViaLensBasin`) prove the LOOP LOGIC — but they inject a fake /
stub embed_fn ("two orthogonal synthetic basins so the embed stub controls
placement"). So they prove "GIVEN separable embeddings, routing is correct" — they
canNOT prove the REAL ModernBERT embedder actually separates distinct semantic
clusters well enough for the production gates (MATCH_FLOOR=0.36, MARGIN_FLOOR=0.02)
to route them. An embedder swap, a normalization regression, or a threshold drift
would keep every stub-embedding test green while real routing collapses into one
basin or clears no basin at all — the #277 stale/degenerate-space failure #298 was
meant to make impossible, invisible to the fake-embedding suite.

This is the measure-don't-assume guard ([[data_sampling_principle]], the #243
embedder-by-measurement discipline): seed two clearly-distinct domains (software
architecture vs runtime debugging), each with a decisive provider winner, build the
basin centroids from REAL seed text, run the REAL consolidate, and assert the loop
both (a) tallies the right winner into the right basin and (b) routes HELD-OUT
queries (not in the seed set — proving generalization, not string memorization) to
the right basin's winner.

Gated on `mlx_actually_loaded()` — skips on CI / any no-[mlx] env where the TF-IDF
fallback doesn't cluster semantically (the same gate test_semantic_filter uses).
It runs wherever the full suite runs with the [mlx] extras (the founder's box,
contributor dev machines) — the only place real semantic routing CAN be validated.
"""
from __future__ import annotations

import json

import pytest


def _seed_council(cid, task_text, winner, runner_up, created_at):
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )

    members = [
        CouncilMemberResult(provider=winner, model="m", output_text=f"answer from {winner}. " * 12),
        CouncilMemberResult(provider=runner_up, model="m", output_text=f"answer from {runner_up}. " * 12),
    ]
    label = CouncilRoutingLabel(
        winner=winner, runner_up=runner_up, confidence="high", task_type="x",
        agreed_claims=["a"], disagreed_claims=[],
    )
    save_council_outcome(CouncilOutcome(
        council_run_id=cid, bundle_id=cid, task_cluster_id="c",
        primary_provider=winner, primary_model="m", winner_provider=winner,
        winner_model="m", agreement_score=0.7, metadata={"task_text": task_text},
        member_results=members, synthesis_prompt="r", synthesis_output=f"{winner} wins",
        routing_label=label, created_at=created_at,
    ))


# Two clearly-distinct domains, each with a decisive winner. Held-out queries are
# semantically in-domain but share no salient tokens with the seeds (generalization).
_ARCH_SEEDS = [
    "How should I structure the service layer and its module boundaries?",
    "Design the component hierarchy for this dashboard architecture.",
    "What's the cleanest architecture for separating these concerns?",
    "Lay out the system design for the ingestion pipeline modules.",
]
_DEBUG_SEEDS = [
    "Why does this throw a null pointer at runtime and how do I fix it?",
    "Trace this stack and find the cause of the failing test.",
    "Debug the error in this function — the test crashes on edge input.",
    "This runtime exception is intermittent; help me find the root cause.",
]
_ARCH_HELDOUT = "Refactor the boundaries between these two services for cleaner separation"
_DEBUG_HELDOUT = "Figure out the reason this code blows up on that particular input"


def test_real_embedder_separates_domains_consolidate_and_route(tmp_path, monkeypatch):
    from trinity_local.embeddings import mlx_actually_loaded

    if not mlx_actually_loaded():
        pytest.skip("real semantic routing requires the [mlx] extras (TF-IDF doesn't cluster)")

    from trinity_local.embeddings import embed

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # Basin centroids from REAL seed text — the production embedding space.
    centroid_arch = embed("design the system architecture, module boundaries, and component structure")
    centroid_debug = embed("debug the runtime error, trace the stack, and fix the failing test")
    assert len(centroid_arch) == len(centroid_debug) >= 256, "embedder returned a degenerate vector"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(json.dumps({"basins": [
        {"id": "b00", "label": "Architecture", "top_terms": ["design", "architecture"],
         "centroid": centroid_arch, "size": 12, "representatives": [{"id": "rA", "snippet": "x"}]},
        {"id": "b01", "label": "Debugging", "top_terms": ["debug", "error"],
         "centroid": centroid_debug, "size": 10, "representatives": [{"id": "rB", "snippet": "y"}]},
    ]}), encoding="utf-8")

    # Decisive winners: architecture → claude, debugging → codex.
    i = 0
    for seeds, winner, runner in ((_ARCH_SEEDS, "claude", "codex"), (_DEBUG_SEEDS, "codex", "claude")):
        for task in seeds:
            _seed_council(f"council_vl{i:02d}", task, winner, runner, f"2026-06-0{(i % 8) + 1}T00:00:00+00:00")
            i += 1

    # The real consolidate: embed each council, place into the nearest basin, tally.
    from trinity_local.lens_routing import consolidate_via_lens_basins, place_query

    picks = consolidate_via_lens_basins()

    # 1. Each domain's councils land in their OWN basin with the decisive winner —
    #    NOT all collapsed into one basin (the degenerate-space failure mode).
    assert set(picks) == {"b00", "b01"}, (
        f"consolidate didn't separate the two domains into two basins: {picks}. "
        "The real embedder failed to cluster architecture apart from debugging "
        "(or a placement gate drifted) — the core value loop collapsed."
    )
    assert picks["b00"]["winner"] == "claude", f"architecture basin tallied the wrong winner: {picks['b00']}"
    assert picks["b01"]["winner"] == "codex", f"debugging basin tallied the wrong winner: {picks['b01']}"
    # Decisive, not a coin-flip — every council of a domain went to its winner.
    assert picks["b00"]["count"] == len(_ARCH_SEEDS) and picks["b01"]["count"] == len(_DEBUG_SEEDS), (
        f"some councils mis-placed across domains: {picks}"
    )

    # 2. HELD-OUT queries (not in the seed set) route to the right basin → winner.
    #    This proves the embedder GENERALIZES the separation, not that identical
    #    strings matched. This is what a future `ask` actually does.
    basins = json.loads((home / "memories" / "topics.json").read_text())["basins"]
    arch_basin = place_query(_ARCH_HELDOUT, basins, embed)
    debug_basin = place_query(_DEBUG_HELDOUT, basins, embed)
    assert arch_basin == "b00", (
        f"a held-out architecture query placed into {arch_basin!r}, not the "
        "architecture basin — real routing doesn't generalize at the prod thresholds"
    )
    assert debug_basin == "b01", (
        f"a held-out debugging query placed into {debug_basin!r}, not the debugging basin"
    )
    assert picks[arch_basin]["winner"] == "claude" and picks[debug_basin]["winner"] == "codex"
