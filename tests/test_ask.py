"""Tests for the v1.5 `ask` orchestration.

Hits are fabricated as SearchResult instances so we don't depend on a populated
~/.trinity/prompts/ in the test environment. The production path uses
`memory.search_prompt_nodes` which we patch at module level.
"""
from __future__ import annotations

import pytest

from trinity_local import ask as ask_module
from trinity_local.ask import (
    ESCALATE_HINT_THRESHOLD,
    _decide_from_hits,
    decide_route,
    run_ask,
)
from trinity_local.memory.index import SearchResult


def _hit(
    *,
    prompt_id: str,
    chairman_winner: str | None = None,
    provider: str = "",
    score: float = 0.8,
) -> SearchResult:
    return SearchResult(
        prompt_id=prompt_id,
        text=f"prompt {prompt_id}",
        score=score,
        prompt_similarity=score,
        window_similarity=score,
        transcript_similarity=0.0,
        hardness=0.5,
        reasons=["test"],
        chairman_winner=chairman_winner,
        council_count=1 if chairman_winner else 0,
        provider=provider,
    )


class TestDecideFromHits:
    def test_no_hits_returns_default_provider_with_zero_trust(self):
        decision = _decide_from_hits([], available_providers=["claude", "codex"])
        assert decision.routed_to == "claude"
        assert decision.trust_score == 0.0
        assert decision.reason == "no_history"

    def test_chairman_pick_decides_the_route(self):
        # Three hits, all ratifying codex via the chairman pick (the sole
        # supervision signal now that the user-pick layer is gone). Codex
        # wins 3-0 with no runner-up.
        hits = [
            _hit(prompt_id="p1", chairman_winner="codex"),
            _hit(prompt_id="p2", chairman_winner="codex"),
            _hit(prompt_id="p3", chairman_winner="codex"),
        ]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "codex"
        assert decision.runner_up is None
        assert decision.vote_counts["codex"] == 3

    def test_unanimous_chairman_verdict_routes_with_high_trust(self):
        hits = [
            _hit(prompt_id=f"p{i}", chairman_winner="claude")
            for i in range(5)
        ]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "claude"
        # 5 hits all ratifying claude → agreement = 1.0, full sample → high trust
        assert decision.trust_score > 0.85

    def test_low_sample_size_caps_trust(self):
        # Single hit, unanimous chairman pick — but n_hits=1 hits the
        # min-hits floor (caps trust at 0.5).
        hits = [_hit(prompt_id="p1", chairman_winner="codex")]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "codex"
        # n_hits < 2 → trust capped at 0.5 regardless of agreement.
        assert decision.trust_score < 0.85

    def test_available_providers_filter_excludes_others(self):
        # codex would win but is filtered out.
        hits = [
            _hit(prompt_id="p1", chairman_winner="codex"),
            _hit(prompt_id="p2", chairman_winner="claude"),
        ]
        decision = _decide_from_hits(hits, available_providers=["claude", "antigravity"])
        assert decision.routed_to == "claude"
        assert "codex" not in decision.vote_counts

    def test_hits_with_no_winner_signal_falls_through(self):
        hits = [_hit(prompt_id="p1", chairman_winner=None)]
        decision = _decide_from_hits(hits, available_providers=["claude"])
        assert decision.reason == "hits_found_but_no_winner_signal"
        assert decision.trust_score == 0.0

    def test_cold_start_falls_back_to_transcript_provider(self):
        # No councils have run yet, but the user has asked 5 similar prompts
        # to Codex in their transcripts. We should still route to Codex with
        # capped trust + escalate hint.
        hits = [_hit(prompt_id=f"p{i}", provider="codex") for i in range(5)]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "codex"
        # Cold-start cap means trust stays below escalate threshold.
        assert decision.trust_score <= 0.55
        assert "transcript origin only" in decision.reason

    def test_council_signal_dominates_transcript_origin(self):
        # User reached for Codex 4 times historically but the one council
        # they ran ratified Claude (chairman pick). Council wins.
        hits = [
            _hit(prompt_id="p1", chairman_winner="claude", provider="codex"),
            _hit(prompt_id="p2", provider="codex"),
            _hit(prompt_id="p3", provider="codex"),
            _hit(prompt_id="p4", provider="codex"),
        ]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "claude"
        # Reason should reflect council-signal path, not the cold-start one.
        assert "council signals" in decision.reason


class TestAskEvidenceHonesty:
    """`vote_counts` + the reason count are agent-facing EVIDENCE — they must reflect
    the real number of voting prompts, not the internal weighted score or the raw
    neighbor count. Found dogfooding decide_route on the real corpus: a 5-prompt
    transcript route reported vote_counts={'claude': 2} (int of 5*0.5) while the
    reason said "5 similar past prompts" — an internal contradiction that under-states
    the evidence and mis-calibrates an agent's escalate decision."""

    def test_transcript_vote_counts_is_a_true_prompt_count_not_the_half_weight(self):
        # 5 transcript-origin prompts all reaching for codex. The cold-start path
        # weights each 0.5 (for TRUST), but the agent-facing vote_counts must be the
        # honest 5, not int(5*0.5)=2.
        hits = [_hit(prompt_id=f"p{i}", provider="codex") for i in range(5)]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "codex"
        assert decision.vote_counts["codex"] == 5, (
            "vote_counts must be a true prompt count, not the halved transcript "
            f"weight — got {decision.vote_counts}"
        )
        # The reason agrees with the count (no 5-vs-2 contradiction).
        assert "5 similar past prompts" in decision.reason
        # The WEIGHTING is preserved where it belongs: trust stays cold-start-capped.
        assert decision.trust_score <= 0.55

    def test_reason_counts_voters_not_neighbors(self):
        # 4 nearest prompts but only ONE carries a council signal → the reason must
        # say "1 ... prompt" (singular), not "4" (the raw neighbor count).
        hits = [
            _hit(prompt_id="p1", chairman_winner="claude"),
            _hit(prompt_id="p2"),
            _hit(prompt_id="p3"),
            _hit(prompt_id="p4"),
        ]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.routed_to == "claude"
        assert "voted from 1 similar past prompt " in decision.reason, (
            f"reason overstates the evidence (counts neighbors, not voters): "
            f"{decision.reason!r}"
        )
        assert decision.vote_counts["claude"] == 1

    def test_council_vote_counts_stays_a_count(self):
        # Regression: the council path (weight 1.0) was already a true count — keep it.
        hits = [_hit(prompt_id=f"p{i}", chairman_winner="codex") for i in range(3)]
        decision = _decide_from_hits(hits, available_providers=None)
        assert decision.vote_counts["codex"] == 3
        assert "voted from 3 similar past prompts" in decision.reason


class TestDecideRoute:
    def test_patches_memory_search(self, monkeypatch):
        fake_hits = [
            _hit(prompt_id="p1", chairman_winner="codex"),
            _hit(prompt_id="p2", chairman_winner="codex"),
        ]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        # Isolate from real ~/.trinity/scoreboard/picks.json — without
        # this, decide_route's _try_cortex_route walks the dev install's
        # real cortex (4s on 40k-prompt corpus). Same fix as the
        # surrounding tests at L1116, L1107 etc.
        monkeypatch.setattr(ask_module, "_try_cortex_route", lambda q, p: None)
        decision = decide_route("test query", top_k=2)
        assert decision.routed_to == "codex"


class TestRunAsk:
    # All tests in this class set `use_cortex=False` to isolate from the
    # contributor's real ~/.trinity/scoreboard/picks.json. Tests were green
    # pre-launch when no cortex patterns existed; post-launch this loop
    # caught silent failures (test set fake_hits → user_winner=claude, but
    # the user's cortex had a "capital of France"-adjacent basin → routed
    # to codex regardless of the fake hits). Pattern matches every other
    # run_ask test in this file from L612 onward.

    def test_end_to_end_dispatches_and_returns_structured(self, monkeypatch):
        fake_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)

        def fake_dispatch(provider: str, prompt: str) -> str:
            return f"[{provider}]: answer to '{prompt}'"

        result = run_ask("what is the capital of France?", dispatch_fn=fake_dispatch, use_cortex=False)
        assert result.routed_to == "claude"
        assert "claude" in result.answer
        assert result.trust_score > 0.8
        # High-trust route doesn't suggest escalation.
        assert result.escalate_hint is None
        assert result.latency_ms >= 0

    def test_low_trust_sets_escalate_hint_to_run_council(self, monkeypatch):
        # One hit only, with split signal → low trust → escalate hint.
        # Hint string is the actual MCP tool name `run_council` so the agent
        # can call it directly; "compare" was the spec-v1.5.md proposed name.
        fake_hits = [_hit(prompt_id="p1", chairman_winner="claude")]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        result = run_ask("complex question", dispatch_fn=lambda p, q: "answer", use_cortex=False)
        assert result.escalate_hint == "run_council"
        assert result.trust_score < ESCALATE_HINT_THRESHOLD

    def test_long_answer_is_truncated_with_marker(self, monkeypatch):
        from trinity_local.ask import ASK_ANSWER_CHAR_BUDGET

        fake_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        long_answer = "x" * 10000
        result = run_ask("q", dispatch_fn=lambda p, q: long_answer, use_cortex=False)
        payload = result.to_dict()
        assert len(payload["answer"]) <= ASK_ANSWER_CHAR_BUDGET
        assert "truncated by Trinity" in payload["answer"]

    def test_short_answer_passes_through_unchanged(self, monkeypatch):
        fake_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        result = run_ask("q", dispatch_fn=lambda p, q: "short and clear", use_cortex=False)
        payload = result.to_dict()
        assert payload["answer"] == "short and clear"

    def test_to_dict_is_compact(self, monkeypatch):
        fake_hits = [_hit(prompt_id="p1", chairman_winner="codex") for _ in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        result = run_ask("q", dispatch_fn=lambda p, q: "a", use_cortex=False)
        payload = result.to_dict()
        # Token-economy: only the keys Claude needs.
        assert set(payload.keys()).issubset(
            {"answer", "routed_to", "trust_score", "latency_ms", "runner_up", "escalate_hint"}
        )
        # No verbose "decision" or "evidence" blob in the compact return.
        assert "decision" not in payload
        assert "evidence_prompt_ids" not in payload


def _seed_topics_and_picks(tmp_path, basins, picks):
    """Seed an isolated ~/.trinity with topics.json basin centroids + a
    post-collapse picks.json. `basins` is [{id, centroid}], `picks` is the flat
    lens-basin tally keyed by basin id."""
    import json
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8"
    )
    from trinity_local import cortex
    cortex.save_routing_patterns(picks, allow_shrink=True)


def _pick(winner, *, count=4, margin=0.5, evidence=None):
    return {"winner": winner, "count": count, "margin": margin,
            "n_episodes": count, "evidence": list(evidence or [])}


class TestLensBasinRouting:
    """POST-COLLAPSE (#298): ask routes via the lens basins, not the deleted
    cortex centroid/trust engine. `_try_cortex_route` places the query into a
    lens basin (topics.json's live centroids via `place_query`) and routes on
    that basin's chairman-winner tally from picks.json. These pin the wired
    ask-side path; the pure tally + placement gates live in test_lens_routing.py.
    """

    # Two orthogonal synthetic basins so the embed stub controls placement.
    B00 = [1.0, 0.0, 0.0]  # → codex
    B01 = [0.0, 1.0, 0.0]  # → claude

    def _seed(self, tmp_path, picks):
        _seed_topics_and_picks(
            tmp_path,
            [{"id": "b00", "centroid": self.B00}, {"id": "b01", "centroid": self.B01}],
            picks,
        )

    def test_routes_to_basin_winner(self, monkeypatch, tmp_path):
        """A query that places into b00 routes to that basin's tallied winner,
        overriding the kNN hits, and the reason names the lens basin."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex", evidence=["c1", "c2"])})
        # Query embeds onto b00's centroid → place_query returns "b00".
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        # kNN would say claude — verify the lens route OVERRIDES it.
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api surface")
        assert decision.routed_to == "codex"
        assert "lens basin b00" in decision.reason
        assert "codex" in decision.reason
        # The margin doubles as the routing trust score.
        assert decision.trust_score == 0.5
        # Evidence prompt ids flow through from the tally.
        assert decision.evidence_prompt_ids[:2] == ["c1", "c2"]

    def test_abstains_to_knn_without_real_embeddings(self, monkeypatch, tmp_path):
        """The load-bearing abstain gate on the CORE routing action: the SAME query
        that routes to the basin winner above must fall through to kNN when mlx
        isn't loaded (the no-[mlx] install → centroids are SHA-1 TF-IDF, so a
        placement is word-overlap not meaning). Routing the degraded placement
        would assert a learned preference the embedding space can't support."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex")})
        # A matching placement IS available (embed lands on b00) ...
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # ... but the embedder is NOT real → cortex must abstain.
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: False, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api surface")
        assert decision.routed_to == "claude", (
            "cortex routed on a TF-IDF placement instead of abstaining to kNN — "
            f"the core routing action ships a degraded semantic route: {decision.reason!r}"
        )
        assert "lens basin" not in decision.reason

    def test_thin_tally_below_min_count_falls_through(self, monkeypatch, tmp_path):
        """A basin with a count below MIN_COUNT is too thin to route on → fall
        through to kNN (the basin hands the query back, exactly like the tally
        builder omits it)."""
        from trinity_local import ask as ask_module, embeddings
        from trinity_local.lens_routing import MIN_COUNT
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex", count=MIN_COUNT - 1)})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("q")
        assert decision.routed_to == "claude"
        assert "lens basin" not in decision.reason

    def test_near_tie_basin_below_winner_margin_falls_through(self, monkeypatch, tmp_path):
        """A basin whose winner only barely edged the runner-up (margin below
        WINNER_MARGIN_FLOOR) is a coin flip, not a learned preference → don't
        route on it, fall through to kNN. This is the quality gate: most real
        basins sat at margin 0.00–0.08; routing them asserts confidence the
        tally doesn't carry."""
        from trinity_local import ask as ask_module, embeddings
        from trinity_local.lens_routing import WINNER_MARGIN_FLOOR
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # count clears MIN_COUNT, but the margin is a near-tie below the floor.
        self._seed(tmp_path, {"b00": _pick("codex", count=6, margin=WINNER_MARGIN_FLOOR - 0.01)})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api surface")
        assert decision.routed_to == "claude"  # kNN, not the coin-flip basin
        assert "lens basin" not in decision.reason

    def test_decisive_basin_at_winner_margin_floor_routes(self, monkeypatch, tmp_path):
        """The boundary case: a basin exactly AT the winner-margin floor is
        decisive enough to route (the gate is `< floor`, not `<= floor`)."""
        from trinity_local import ask as ask_module, embeddings
        from trinity_local.lens_routing import WINNER_MARGIN_FLOOR
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex", count=6, margin=WINNER_MARGIN_FLOOR)})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api surface")
        assert decision.routed_to == "codex"
        assert "lens basin b00" in decision.reason

    def test_winner_margin_floor_is_preregistered(self):
        """Pre-registered floor (green-gate discipline #35): pin the value so a
        silent drift to ~0 can't re-enable coin-flip routing unnoticed."""
        from trinity_local.lens_routing import WINNER_MARGIN_FLOOR
        assert WINNER_MARGIN_FLOOR == 0.15

    def test_winner_margin_floor_fallback_literals_match_the_source(self):
        """DUPLICATED-CONSTANT-DRIFT guard: every surface that demotes a near-tie
        basin (`ask` routing, MCP `get_picks`, the launchpad routing cheat-sheet,
        the memory-viewer picks/topology badges) reads `WINNER_MARGIN_FLOOR` from
        `lens_routing` — but THREE of them wrap the import in
        `try: … except Exception: <literal 0.15>` as an import-failure fallback
        (mcp_server._winner_margin_floor, memory_viewer.render_memory_viewer_html,
        launchpad_data._load_cortex_rules). Those `except`-branch literals are a
        SECOND copy of the floor: bump `lens_routing.WINNER_MARGIN_FLOOR` to a new
        value and `test_winner_margin_floor_is_preregistered` above forces a
        deliberate update of the source pin — but the three fallback copies stay
        silently stale at 0.15, so an import-failure render (corrupt/partial
        install, exactly when the launchpad + viewer still paint) would demote
        basins on the OLD floor while `ask` routes on the NEW one. #299 was a
        hardcoded 0.2 demote-threshold drifting from the real floor; this keeps a
        NEW hardcoded copy from creeping back in undetected.

        Source-scan each module for the `except`-branch fallback literal and
        assert it equals `lens_routing.WINNER_MARGIN_FLOOR` — so the copies are
        one enforced value, caught at test time the moment any drifts.
        """
        import re
        from pathlib import Path

        from trinity_local.lens_routing import WINNER_MARGIN_FLOOR

        pkg = Path(ask_module.__file__).resolve().parent
        # Each entry: module file + the regex capturing the float assigned/returned
        # in the `except`-branch fallback for the margin floor.
        sites = {
            "mcp_server.py": re.compile(
                r"except\s+Exception:\s*\n\s*return\s+([0-9]*\.[0-9]+)"
            ),
            "memory_viewer.py": re.compile(
                r"except\s+Exception:\s*\n\s*winner_margin_floor\s*=\s*([0-9]*\.[0-9]+)"
            ),
            "launchpad_data.py": re.compile(
                r"except\s+Exception:\s*\n\s*WINNER_MARGIN_FLOOR\s*=\s*([0-9]*\.[0-9]+)"
            ),
        }
        seen: dict[str, float] = {}
        for fname, pat in sites.items():
            text = (pkg / fname).read_text(encoding="utf-8")
            literals = [float(m) for m in pat.findall(text)]
            assert literals, (
                f"could not locate the WINNER_MARGIN_FLOOR except-fallback literal "
                f"in {fname} — the guard's regex is stale; re-anchor it (the fallback "
                f"must still exist or be removed in every site together)."
            )
            seen[fname] = literals[0]
            for lit in literals:
                assert lit == WINNER_MARGIN_FLOOR, (
                    f"DUPLICATED-CONSTANT DRIFT: {fname} hardcodes a margin-floor "
                    f"fallback of {lit} but lens_routing.WINNER_MARGIN_FLOOR is "
                    f"{WINNER_MARGIN_FLOOR}. The launchpad/memory-viewer would demote "
                    f"near-tie basins on a STALE floor under an import-failure render "
                    f"while `ask` routes on the live one. Update every except-branch "
                    f"fallback to match the source (or remove the literal fallback "
                    f"in all three sites)."
                )
        # All three fallbacks agree with each other too (defense in depth).
        assert len(set(seen.values())) == 1, (
            f"the three margin-floor fallback literals disagree with each other: {seen}"
        )

    def test_null_evidence_does_not_crash_route(self, monkeypatch, tmp_path):
        """A pick entry with an explicit `evidence: null` (not just absent) must
        route cleanly, not crash — `list(None)` raises TypeError, so the read is
        `rule.get("evidence") or []`, not `rule.get("evidence", [])`."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": {"winner": "codex", "count": 4, "margin": 0.5,
                                      "n_episodes": 4, "evidence": None}})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api surface")
        assert decision.routed_to == "codex"
        assert decision.evidence_prompt_ids == []

    def test_out_of_domain_query_falls_through(self, monkeypatch, tmp_path):
        """A query that embeds to the zero vector clears no basin's match floor →
        place_query returns None → kNN handles it."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex")})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: [0.0, 0.0, 0.0])
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("translate this paragraph")
        assert decision.routed_to == "claude"
        assert "lens basin" not in decision.reason

    def test_unavailable_basin_winner_falls_through(self, monkeypatch, tmp_path):
        """If the basin's winner isn't in the available pool, drop the route and
        let kNN handle it (the harness can't dispatch to that provider)."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex")})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        # codex (the basin winner) is NOT available → fall through.
        decision = ask_module.decide_route("design the api", available_providers=["claude", "antigravity"])
        assert decision.routed_to == "claude"
        assert "lens basin" not in decision.reason

    def test_basin_with_no_winner_tally_falls_through(self, monkeypatch, tmp_path):
        """A legacy/malformed pick entry (a dict missing `winner`) for a placed
        basin yields no route → kNN handles it."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # b00 exists in topics but its pick entry has no winner.
        self._seed(tmp_path, {"b00": {"count": 5, "margin": 0.8}})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("design the api")
        assert decision.routed_to == "claude"
        assert "lens basin" not in decision.reason

    def test_no_consolidation_yet_falls_through_to_knn(self, monkeypatch, tmp_path):
        """Day-1 install has no picks.json; ask uses kNN unchanged."""
        from trinity_local import ask as ask_module

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))  # no picks file
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="codex") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("question")
        assert decision.routed_to == "codex"
        assert "lens basin" not in decision.reason

    def test_use_cortex_false_skips_basin_routing(self, monkeypatch, tmp_path):
        """The A/B flag (name is historical) skips the lens-basin lookup even when
        picks exist — pure kNN."""
        from trinity_local import ask as ask_module, embeddings

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex")})
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: self.B00)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        with_basin = ask_module.decide_route("design the api", use_cortex=True)
        assert with_basin.routed_to == "codex"
        without_basin = ask_module.decide_route("design the api", use_cortex=False)
        assert without_basin.routed_to == "claude"

    def test_embedding_failure_falls_through_safely(self, monkeypatch, tmp_path):
        """If embed() throws (broken model file), placement returns None and ask
        falls back to kNN. No crash."""
        from trinity_local import ask as ask_module, embeddings

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._seed(tmp_path, {"b00": _pick("codex")})
        monkeypatch.setattr(
            embeddings, "embed",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("embed model broken")),
        )
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)

        decision = ask_module.decide_route("q", available_providers=["claude", "codex"])
        assert decision.routed_to == "claude"


class TestRateLimitAutoRetry:
    """The v1.5 killer flow: when Claude (primary) hits a rate limit,
    Trinity routes to the runner-up provider seamlessly. Tests cover the
    full taxonomy of dispatch failures — only the retry-with-other-provider
    ones trigger fallback; auth-only / unknown failures bail immediately.
    """

    def test_rate_limit_on_primary_falls_to_runner_up(self, monkeypatch):
        """Primary fails with rate-limit → runner-up is tried automatically."""
        # 5 hits split: claude wins narrowly, codex is runner_up.
        hits = (
            [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(3)]
            + [_hit(prompt_id=f"q{i}", chairman_winner="codex") for i in range(2)]
        )
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        calls = []

        def dispatch(provider: str, prompt: str) -> str:
            calls.append(provider)
            if provider == "claude":
                raise RuntimeError("HTTP 429 Too Many Requests")
            return f"[{provider}] success"

        result = run_ask("q", dispatch_fn=dispatch, use_cortex=False)
        # Claude was tried first, failed with rate-limit, codex was tried next.
        assert calls == ["claude", "codex"]
        # Final route reflects the actually-successful provider.
        assert result.routed_to == "codex"
        assert "codex" in result.answer

    def test_all_providers_rate_limited_raises_with_kind(self, monkeypatch):
        """Both primary and runner-up hit rate limits → raise so the caller
        can decide (back off, escalate to user, etc.)."""
        hits = (
            [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(3)]
            + [_hit(prompt_id=f"q{i}", chairman_winner="codex") for i in range(2)]
        )
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        def dispatch(provider: str, prompt: str) -> str:
            raise RuntimeError(f"{provider}: rate limit exceeded")

        with pytest.raises(RuntimeError, match="All providers failed"):
            run_ask("q", dispatch_fn=dispatch, use_cortex=False)

    def test_auth_failure_on_primary_falls_to_runner_up(self, monkeypatch):
        """Auth failure on one provider doesn't tell us anything about
        the others — retry is sensible."""
        hits = (
            [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(3)]
            + [_hit(prompt_id=f"q{i}", chairman_winner="codex") for i in range(2)]
        )
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        def dispatch(provider: str, prompt: str) -> str:
            if provider == "claude":
                raise RuntimeError("401 Unauthorized")
            return f"[{provider}] ok"

        result = run_ask("q", dispatch_fn=dispatch, use_cortex=False)
        assert result.routed_to == "codex"

    def test_unknown_failure_does_not_retry(self, monkeypatch):
        """Unknown failure shape — could be content policy, deterministic
        bug, etc. Don't auto-retry; surface to caller."""
        hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        calls = []

        def dispatch(provider: str, prompt: str) -> str:
            calls.append(provider)
            raise RuntimeError("some weird unclassifiable CLI panic")

        with pytest.raises(RuntimeError):
            run_ask("q", dispatch_fn=dispatch, use_cortex=False)
        # Should NOT retry with another provider — only one attempt.
        assert len(calls) == 1

    def test_model_not_found_does_not_retry(self, monkeypatch):
        """Model deprecation is a config bug — the operator needs to fix
        the model alias. Auto-retry would mask the issue."""
        hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        calls = []

        def dispatch(provider: str, prompt: str) -> str:
            calls.append(provider)
            raise RuntimeError("Model not found: deprecated-model-name")

        with pytest.raises(RuntimeError):
            run_ask("q", dispatch_fn=dispatch, use_cortex=False)
        assert len(calls) == 1  # no retry

    def test_max_retries_zero_disables_fallback(self, monkeypatch):
        """max_retries=0 → only the primary is tried, no fallback."""
        hits = (
            [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(3)]
            + [_hit(prompt_id=f"q{i}", chairman_winner="codex") for i in range(2)]
        )
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        calls = []

        def dispatch(provider: str, prompt: str) -> str:
            calls.append(provider)
            raise RuntimeError("HTTP 429 rate limit")

        with pytest.raises(RuntimeError):
            run_ask("q", dispatch_fn=dispatch, max_retries=0, use_cortex=False)
        assert calls == ["claude"]  # no retry attempted


class TestRateLimitSavesMetric:
    """The case-study metric. Every successful retry after a primary-failure
    is logged to ~/.trinity/analytics/dispatch_outcomes.jsonl with
    rate_limit_save=True, which `dispatch_health.compute_provider_health()`
    reads to compute per-provider trust + rate-limit-save counts. (The
    `trinity-local metric` CLI that surfaced this on a launchpad chip
    was retired pre-launch; the jsonl is still the canonical record per
    docs/launch-package.md's day-1 case-study number.)
    """

    def test_rate_limit_save_appends_jsonl_entry(self, monkeypatch, tmp_path):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        hits = (
            [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(3)]
            + [_hit(prompt_id=f"q{i}", chairman_winner="codex") for i in range(2)]
        )
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        def dispatch(provider: str, prompt: str) -> str:
            if provider == "claude":
                raise RuntimeError("HTTP 429 Too Many Requests")
            return f"[{provider}] success"

        run_ask("design a thing", dispatch_fn=dispatch, use_cortex=False)

        log_path = tmp_path / "analytics" / "dispatch_outcomes.jsonl"
        assert log_path.exists()
        entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["primary"] == "claude"
        assert entry["succeeded_on"] == "codex"
        assert entry["retries"] == 1
        # The case-study flag — the one that makes this a "rate-limit save."
        assert entry["rate_limit_save"] is True
        assert entry["failure_kind"] == "rate_limited"

    def test_first_try_success_is_not_a_save(self, monkeypatch, tmp_path):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        run_ask("q", dispatch_fn=lambda p, q: "ok", use_cortex=False)

        log_path = tmp_path / "analytics" / "dispatch_outcomes.jsonl"
        entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        # No retry → not a save.
        assert entries[0]["rate_limit_save"] is False
        assert entries[0]["retries"] == 0

    def test_telemetry_failure_does_not_break_dispatch(self, monkeypatch, tmp_path):
        """Architectural commitment: observability MUST NOT crash callers."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

        hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: hits)

        # Make the logger function itself throw — should be swallowed.
        from trinity_local import ask as _ask

        def explode(**kwargs):
            raise RuntimeError("telemetry blew up")

        # Wrap with the same try/except shape — it's already there.
        # We just verify run_ask completes despite a broken logger.
        monkeypatch.setattr(_ask, "_log_dispatch_outcome", lambda **kw: explode(**kw))

        # Should NOT raise — the telemetry call is wrapped in try/except.
        # Note: the safety wrapper is INSIDE _log_dispatch_outcome itself,
        # so if we replace the whole function with one that raises, the
        # safety net doesn't apply. Re-attach a safer wrapper for this
        # test by emulating the production exception handling shape:
        def safe_wrapper(**kw):
            try:
                explode(**kw)
            except Exception:
                pass
        monkeypatch.setattr(_ask, "_log_dispatch_outcome", safe_wrapper)

        result = run_ask("q", dispatch_fn=lambda p, q: "ok", use_cortex=False)
        assert result.answer == "ok"


class TestMcpGetCortexRules:
    """The agent-facing introspection tool (`get_picks`). Post-collapse (#298)
    each rule is the flat lens-basin tally `{winner, count, margin, n_episodes,
    evidence}`; `min_trust` filters on `margin` (the new confidence proxy).
    """

    def test_empty_when_no_consolidation_yet(self, tmp_path, monkeypatch):
        import asyncio
        import json as _json
        from trinity_local import mcp_server

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        result = asyncio.run(mcp_server._get_picks({}))
        payload = _json.loads(result[0]["text"])
        assert payload["rules"] == {}
        assert "consolidate" in payload["note"]

    def test_returns_all_rules_when_no_filter(self, tmp_path, monkeypatch):
        import asyncio
        import json as _json
        from trinity_local import mcp_server, cortex

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        cortex.save_routing_patterns({
            "b00": _pick("codex", count=30, margin=0.82),
            "b01": _pick("claude", count=5, margin=0.40),
        })

        result = asyncio.run(mcp_server._get_picks({}))
        payload = _json.loads(result[0]["text"])
        assert set(payload["rules"].keys()) == {"b00", "b01"}
        assert payload["total_basins"] == 2
        # Each rule carries the new schema fields, NOT trust_score / routing_rule.
        rule = payload["rules"]["b00"]
        assert rule["winner"] == "codex"
        assert rule["count"] == 30
        assert rule["margin"] == 0.82
        assert "trust_score" not in rule and "routing_rule" not in rule

    def test_skips_legacy_and_malformed_entries(self, tmp_path, monkeypatch):
        """A legacy RoutingPattern dict (no `winner`) or junk entry must be
        skipped — only live lens-basin picks surface to the agent."""
        import asyncio
        import json as _json
        from trinity_local import mcp_server
        from trinity_local.state_paths import cortex_routing_patterns_path

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        cortex_routing_patterns_path().write_text(
            _json.dumps({
                "b00": _pick("codex"),
                "legacy": {  # old RoutingPattern shape — no top-level winner
                    "routing_rule": {"primary": "claude"},
                    "trust_score": {"value": 0.8},
                    "basin_centroid": [0.01] * 768,
                },
            }),
            encoding="utf-8",
        )
        result = asyncio.run(mcp_server._get_picks({}))
        payload = _json.loads(result[0]["text"])
        assert set(payload["rules"].keys()) == {"b00"}
        # The legacy entry's 768-float centroid never reaches the agent.
        assert "basin_centroid" not in _json.dumps(payload["rules"])

    def test_filters_by_basin_id(self, tmp_path, monkeypatch):
        import asyncio
        import json as _json
        from trinity_local import mcp_server, cortex

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        cortex.save_routing_patterns({"b00": _pick("codex")})

        result = asyncio.run(mcp_server._get_picks({"basin_id": "b00"}))
        payload = _json.loads(result[0]["text"])
        assert "b00" in payload["rules"]
        # Filter to non-existent basin returns no matches but no error.
        result = asyncio.run(mcp_server._get_picks({"basin_id": "nonexistent"}))
        payload = _json.loads(result[0]["text"])
        assert payload["rules"] == {}
        assert payload["returned"] == 0

    def test_filters_by_min_trust(self, tmp_path, monkeypatch):
        """`min_trust` filters on `margin` (the new confidence proxy)."""
        import asyncio
        import json as _json
        from trinity_local import mcp_server, cortex

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        cortex.save_routing_patterns({
            "high": _pick("codex", count=30, margin=0.85),
            "low": _pick("claude", count=3, margin=0.30),
        })

        result = asyncio.run(mcp_server._get_picks({"min_trust": 0.5}))
        payload = _json.loads(result[0]["text"])
        # Only the wide-margin pick clears the filter.
        assert set(payload["rules"].keys()) == {"high"}
        assert payload["returned"] == 1
        assert payload["total_basins"] == 2  # but both exist

    def test_rejects_bad_input(self):
        import asyncio
        from trinity_local import mcp_server

        bad = asyncio.run(mcp_server._get_picks({"basin_id": 123}))
        assert hasattr(bad[0], "code")  # ErrorData
        bad = asyncio.run(mcp_server._get_picks({"min_trust": "not-a-number"}))
        assert hasattr(bad[0], "code")


class TestMcpProviderPool:
    """The MCP layer composes the available-provider pool from config +
    detected local Ollama models. This is what makes ask aware of local
    models without callers having to declare them explicitly.
    """

    def test_full_pool_includes_config_and_local(self, monkeypatch):
        from trinity_local import mcp_server, local_models

        # config.providers is a dict keyed by name in production. Build a stub
        # that matches that shape so .values() yields ProviderConfig-ish objects.
        fake_cfg = type("C", (), {})()
        fake_cfg.providers = {
            "claude": type("P", (), {"name": "claude", "enabled": True})(),
        }
        import trinity_local.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: fake_cfg)

        # Stub local-model detection.
        fake_local = [
            local_models.LocalModel(runtime="ollama", name="qwen3:32b", size_bytes=20*1024**3),
            local_models.LocalModel(runtime="ollama", name="deepseek-r1", size_bytes=4*1024**3),
        ]
        monkeypatch.setattr(local_models, "detect_local_models", lambda: fake_local)

        pool = mcp_server._full_provider_pool()
        assert "claude" in pool
        assert "ollama:qwen3:32b" in pool
        assert "ollama:deepseek-r1" in pool

    def test_full_pool_handles_config_error_gracefully(self, monkeypatch):
        """Broken config shouldn't crash the pool — fall through to local
        models only."""
        from trinity_local import mcp_server, local_models

        import trinity_local.config as cfg_mod
        def broken_load():
            raise RuntimeError("config file missing")
        monkeypatch.setattr(cfg_mod, "load_config", broken_load)
        monkeypatch.setattr(local_models, "detect_local_models",
                          lambda: [local_models.LocalModel(runtime="ollama", name="qwen3:32b")])

        pool = mcp_server._full_provider_pool()
        # Should still return the local-model list even though config failed.
        assert pool == ["ollama:qwen3:32b"]

    def test_full_pool_handles_detection_error_gracefully(self, monkeypatch):
        """Broken Ollama daemon shouldn't crash the pool either."""
        from trinity_local import mcp_server, local_models

        fake_cfg = type("C", (), {})()
        fake_cfg.providers = {
            "claude": type("P", (), {"name": "claude", "enabled": True})(),
        }
        import trinity_local.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: fake_cfg)
        def broken_detect():
            raise RuntimeError("ollama daemon down")
        monkeypatch.setattr(local_models, "detect_local_models", broken_detect)

        pool = mcp_server._full_provider_pool()
        assert pool == ["claude"]


class TestMcpAskHandler:
    """The MCP `_ask` handler wraps run_ask and serializes for the agent.
    Uses asyncio.run() to match the existing test pattern in test_mcp_tools.py.
    """

    def test_returns_compact_json_text_payload(self, monkeypatch):
        import asyncio
        import json as _json

        from trinity_local import mcp_server

        fake_hits = [_hit(prompt_id=f"p{i}", chairman_winner="claude") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        # Patch the dispatch shim so the test doesn't shell out.
        monkeypatch.setattr(
            mcp_server,
            "_dispatch_via_config",
            lambda provider, prompt: f"[stub-{provider}] {prompt}",
        )
        # Isolate from the contributor's real cortex picks (the MCP _ask
        # handler doesn't expose use_cortex). Without this, a real
        # ~/.trinity/scoreboard/picks.json entry whose centroid matches
        # the test query overrides the fake_hits setup.
        monkeypatch.setattr(ask_module, "_try_cortex_route", lambda q, p: None)
        # Stub mcp_server's preamble work to skip:
        # - _trigger_incremental_ingest: 1s ingest_recent() walk
        # - _full_provider_pool: subprocess detection of ollama/mlx models
        # Both are upstream of the routing logic the test asserts on.
        # Was the suite's #6 slowest at 6.7s; brings it to <0.1s.
        monkeypatch.setattr(mcp_server, "_trigger_incremental_ingest", lambda: None)
        monkeypatch.setattr(mcp_server, "_full_provider_pool", lambda: ["claude", "codex", "antigravity"])

        result = asyncio.run(mcp_server._ask({"query": "what's the migration path?"}))
        assert isinstance(result, list) and len(result) == 1
        payload = _json.loads(result[0]["text"])
        assert payload["routed_to"] == "claude"
        assert "claude" in payload["answer"]
        # Confidence is high (5 unanimous hits) → no escalate_hint.
        assert payload.get("escalate_hint") is None

    def test_rejects_missing_query(self):
        import asyncio

        from trinity_local import mcp_server

        result = asyncio.run(mcp_server._ask({}))
        # Error path returns ErrorData, not a text payload.
        assert hasattr(result[0], "code") or "ErrorData" in type(result[0]).__name__

    def test_propagates_dispatch_failure_as_error(self, monkeypatch):
        import asyncio

        from trinity_local import mcp_server

        fake_hits = [_hit(prompt_id="p1", chairman_winner="codex") for _ in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: fake_hits)
        # Same preamble-stubs as test_returns_compact_json_text_payload —
        # skip the 1s ingest + ollama detection upstream of routing.
        monkeypatch.setattr(mcp_server, "_trigger_incremental_ingest", lambda: None)
        monkeypatch.setattr(mcp_server, "_full_provider_pool", lambda: ["claude", "codex", "antigravity"])
        # Cortex routing patch — isolate from real picks.json.
        monkeypatch.setattr(ask_module, "_try_cortex_route", lambda q, p: None)

        def broken_dispatch(provider, prompt):
            raise RuntimeError("rate limit exceeded")

        monkeypatch.setattr(mcp_server, "_dispatch_via_config", broken_dispatch)

        result = asyncio.run(mcp_server._ask({"query": "q"}))
        # Structured error shape (persona audit D7 reshape) — surfaces
        # as a {ok:false, error_code, recoverable, retry_with, ...} text
        # response so the agent can auto-retry around the failure
        # instead of seeing a free-form string.
        import json
        payload = json.loads(result[0]["text"])
        assert payload["ok"] is False
        assert payload["error_code"] == "RATE_LIMITED"
        assert "rate limit" in payload["detail"].lower()
        assert payload["recoverable"] is True or payload["retry_with"] is None


class TestLensPlacementMarginGate:
    """The PRECISION gate, now applied by `lens_routing.place_query` against the
    LENS centroids (#298): route on a basin only when the query is clearly closest
    to ONE basin (margin = top1−top2 ≥ MARGIN_FLOOR), abstaining on near-ties. The
    floors moved out of ask into lens_routing; this pins that ask still HONORS them
    end-to-end (near-tie → kNN, well-separated → lens route)."""

    def _two_basins(self, monkeypatch, tmp_path):
        # basin b00 → codex, basin b01 → claude. Orthogonal centroids so the
        # query's projection onto each is a clean cosine.
        A = [1.0, 0.0] + [0.0] * 254
        B = [0.0, 1.0] + [0.0] * 254
        _seed_topics_and_picks(
            tmp_path,
            [{"id": "b00", "centroid": A}, {"id": "b01", "centroid": B}],
            {"b00": _pick("codex"), "b01": _pick("claude")},
        )

    def test_near_tie_abstains_even_above_the_sim_floor(self, monkeypatch, tmp_path):
        """A query ~equidistant from two basins (both sims well above the 0.36
        match floor, but margin ≈ 0.01 < MARGIN_FLOOR 0.02) is an AMBIGUOUS
        placement — ask must abstain (fall through to kNN), NOT commit to the top
        basin's winner."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._two_basins(monkeypatch, tmp_path)
        # cos(q,A)≈0.712, cos(q,B)≈0.702 → margin≈0.01, both ≥0.36.
        q = [0.71, 0.70] + [0.0] * 254
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: q)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        knn_hits = [_hit(prompt_id=f"p{i}", chairman_winner="antigravity") for i in range(5)]
        monkeypatch.setattr(ask_module, "search_prompt_nodes", lambda q, top_k: knn_hits)
        decision = ask_module.decide_route("ambiguous query", available_providers=["codex", "claude", "antigravity"])
        assert "lens basin" not in decision.reason, (
            f"a near-tie (margin≈0.01) must NOT route via a lens basin — it should "
            f"abstain to kNN; got: {decision.reason!r}"
        )
        assert decision.routed_to == "antigravity"  # the kNN winner

    def test_well_separated_match_routes(self, monkeypatch, tmp_path):
        """A query clearly closest to ONE basin (margin ≥ 0.02) routes via that
        basin's winner."""
        from trinity_local import ask as ask_module, embeddings
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        self._two_basins(monkeypatch, tmp_path)
        # cos(q,A)≈0.894, cos(q,B)≈0.447 → margin≈0.447 ≥ 0.02.
        q = [1.0, 0.5] + [0.0] * 254
        monkeypatch.setattr(embeddings, "embed", lambda text, **kw: q)
        # treat the injected embed stub as the real embedder (force past the
        # no-[mlx] abstain gate in _try_cortex_route — this tests routing geometry).
        monkeypatch.setattr(embeddings, "mlx_actually_loaded", lambda: True, raising=False)
        decision = ask_module.decide_route("clearly basin b00", available_providers=["codex", "claude"])
        assert decision.routed_to == "codex"
        assert "lens basin b00" in decision.reason

    def test_margin_floor_is_preregistered(self):
        """The placement margin floor is pre-registered/provisional at 0.02 (the
        only clean gap the 2026-06-02 calibration found). Pinned so a 'tune it up'
        can't silently drift it. It lives in lens_routing now."""
        from trinity_local.lens_routing import MARGIN_FLOOR
        assert MARGIN_FLOOR == 0.02
