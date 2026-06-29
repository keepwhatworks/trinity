from __future__ import annotations

from trinity_local.council_runtime import parse_synthesis_sections


def test_parse_synthesis_sections_accepts_markdown_heading_variants():
    text = """## What Each Response Does Best
Response A is strongest on specificity.

## Key Tradeoffs
Response B is simpler but less complete.

## What Reviewers Found
Reviewers agreed that both answers were accurate.

## Decision Framework
Choose Response A if you value depth.
"""

    sections = parse_synthesis_sections(text)

    assert sections["best_answer"] == "Response A is strongest on specificity."
    assert sections["differences"] == "Response B is simpler but less complete."
    assert sections["agreement"] == "Reviewers agreed that both answers were accurate."
    assert sections["winner"] == "Choose Response A if you value depth."


class TestResolveWinner:
    """Pin down the winner-resolution priority. The bug we're fixing: chairman
    narrative often mentions losing providers in passing ("claude argued for X
    even though codex won") and the old text-scan grabbed the first match.

    Routing JSON is structured and explicit — trust it first."""

    def test_routing_json_wins_over_narrative_substring(self):
        """The reproducer: chairman picked Codex but the Winner section
        narrative names claude in the first sentence. Old code returned
        'claude'. New code returns 'codex' from the structured Routing JSON."""
        from types import SimpleNamespace
        from trinity_local.council_runner import _resolve_winner

        routing_label = SimpleNamespace(winner="Codex")
        winner_section = (
            "**Codex.** Same pick (A) but adds the load-bearing follow-on "
            "that claude argued against — chairman ruled in codex's favour."
        )
        result = _resolve_winner(
            routing_label=routing_label,
            winner_section=winner_section,
            sequence=["claude", "antigravity", "codex"],
        )
        assert result == "codex"

    def test_consensus_round_uses_resolve_winner_not_prose_scan(self):
        """The iter-2 council found that run_consensus_round STILL did its own
        prose-winner scan on `sections["winner"]` instead of going through
        `_resolve_winner`. With Routing JSON missing, that path silently
        picked the first provider mentioned in the prose. Pin the fix."""
        import inspect
        from trinity_local import council_runner

        src = inspect.getsource(council_runner.run_consensus_round)
        # The legacy prose-scan loop is gone:
        assert "if \"winner\" in sections:" not in src, (
            "run_consensus_round must use _resolve_winner, not scan sections['winner']"
        )
        # And the canonical resolver IS called:
        assert "_resolve_winner(" in src

    def test_no_winner_when_routing_label_missing(self):
        # The prose-section + A/B/C label fallbacks were removed. With Routing
        # JSON parse-success ≥85%, the fallbacks were silently masking parse
        # failures rather than fixing them — `winner_provider=None` is the
        # honest signal that the rater needs to fix it.
        from trinity_local.council_runner import _resolve_winner

        assert _resolve_winner(
            routing_label=None,
            winner_section="**Gemini.** Wins on terseness.",
            sequence=["claude", "antigravity", "codex"],
        ) is None
        assert _resolve_winner(
            routing_label=None,
            winner_section="A",
            sequence=["claude", "antigravity", "codex"],
            label_to_provider={"A": "antigravity"},
        ) is None


class TestChairmanPromptOrdering:
    """Pin down /me → task → members ordering in the chairman prompt.

    The council ran the meta-question 'should persona come BEFORE or AFTER
    member responses?' and converged unanimously on BEFORE: 'persona should
    function as the evaluation rubric, not a post-hoc adjustment. AFTER
    ordering causes the chairman to anchor on a generic best answer first.'
    Lock that ordering in so a future refactor doesn't accidentally invert.
    """

    def test_me_comes_before_task_and_members(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import render_primary_council_prompt
        from trinity_local.council_schema import CouncilMemberResult, PromptBundle

        # Force /me to exist for this test by writing a synthetic lens.
        # (Pre-rename path was ~/.trinity/me.md — file auto-migrates.)
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "me.md").write_text(
            "# /me\nUser profile: prefers terse answers.\n",
            encoding="utf-8",
        )

        bundle = PromptBundle(bundle_id="b", task_cluster_id="c", task_text="Pick a cache.")
        members = [CouncilMemberResult(provider="claude", model="opus", output_text="Redis.")]
        prompt = render_primary_council_prompt(bundle, members)

        me_pos = prompt.find("User profile")
        task_pos = prompt.find("Original task:")
        member_pos = prompt.find("Council member outputs:")

        assert me_pos > 0, "User profile section missing"
        assert task_pos > me_pos, "task must come AFTER /me"
        assert member_pos > task_pos, "members must come AFTER task"

    def test_chairman_prefers_core_md_over_lens_md(self, tmp_path, monkeypatch):
        """When ~/.trinity/core.md exists, the chairman context loader must
        use the distilled paragraph and NOT the full lens. core is the
        identity memory — one paragraph subsuming the five plural memories.
        Loading both wastes context; loading only lens defeats Phase 5."""
        from trinity_local.council_runtime import render_primary_council_prompt
        from trinity_local.council_schema import CouncilMemberResult, PromptBundle

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # Both files present — core.md should win.
        (tmp_path / "core.md").write_text(
            "You ship leverage over structural ownership.", encoding="utf-8",
        )
        (tmp_path / "memories").mkdir()
        (tmp_path / "memories" / "lens.md").write_text(
            "# Lens\nSHOULD NOT APPEAR — core.md takes precedence.\n",
            encoding="utf-8",
        )

        bundle = PromptBundle(bundle_id="b", task_cluster_id="c", task_text="Pick a cache.")
        members = [CouncilMemberResult(provider="claude", model="opus", output_text="Redis.")]
        prompt = render_primary_council_prompt(bundle, members)

        assert "You ship leverage over structural ownership." in prompt
        assert "SHOULD NOT APPEAR" not in prompt, (
            "When core.md exists, the full lens MUST NOT also be loaded — "
            "it duplicates context and defeats the distillation."
        )

    def test_chairman_falls_back_to_lens_when_core_missing(self, tmp_path, monkeypatch):
        """Cold install — no core.md distilled yet. Chairman must still load
        the lens so it has SOMETHING to personalize on."""
        from trinity_local.council_runtime import render_primary_council_prompt
        from trinity_local.council_schema import CouncilMemberResult, PromptBundle

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        # core.md absent, only lens.md present.
        (tmp_path / "memories").mkdir()
        (tmp_path / "memories" / "lens.md").write_text(
            "# Lens\nFallback context line.\n", encoding="utf-8",
        )

        bundle = PromptBundle(bundle_id="b", task_cluster_id="c", task_text="Pick a cache.")
        members = [CouncilMemberResult(provider="claude", model="opus", output_text="Redis.")]
        prompt = render_primary_council_prompt(bundle, members)

        assert "Fallback context line" in prompt
        assert "core.md" not in prompt or "not yet distilled" in prompt, (
            "When core.md is absent, prompt should still mention WHY "
            "the lens is being read directly."
        )


class TestHorizonHint:
    """#139: chairman context appends a horizon hint so lens-card weighting
    matches the query's resolution. Strategic/philosophical queries get an
    explicit hint; tactical (the default) gets no hint — keeps the prompt
    quiet on the common case."""

    def _bundle(self, task: str):
        from trinity_local.council_schema import CouncilMemberResult, PromptBundle

        return (
            PromptBundle(bundle_id="b", task_cluster_id="c", task_text=task),
            [CouncilMemberResult(provider="claude", model="opus", output_text="x")],
        )

    def test_strategic_query_gets_horizon_hint(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import render_primary_council_prompt

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        bundle, members = self._bundle("Should I bet on Antigravity this quarter?")
        prompt = render_primary_council_prompt(bundle, members)
        assert "Query horizon: strategic" in prompt
        assert "[strategic]" in prompt  # points chairman at the tag

    def test_philosophical_query_gets_horizon_hint(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import render_primary_council_prompt

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        bundle, members = self._bundle("What kind of company do I want to be building five years from now?")
        prompt = render_primary_council_prompt(bundle, members)
        assert "Query horizon: philosophical" in prompt
        assert "[philosophical]" in prompt

    def test_tactical_query_omits_hint(self, tmp_path, monkeypatch):
        """Tactical is the default — chairman doesn't need a hint to use
        local-shape lenses. Suppress the line to keep the prompt quiet on
        the 80% case."""
        from trinity_local.council_runtime import render_primary_council_prompt

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        bundle, members = self._bundle("How do I write a regex for email validation?")
        prompt = render_primary_council_prompt(bundle, members)
        assert "Query horizon:" not in prompt


class TestThreadManifest:
    def _outcome(self, council_id: str, *, root: str | None = None, parent: str | None = None, round_number: int = 1, started_at: str = ""):
        from trinity_local.council_schema import CouncilOutcome

        # Mirror real Trinity convention: bundle_id is the canonical chain
        # root identifier. For root councils, chain_root_id falls back to
        # bundle_id. For chain rounds, chain_root_id is the root's bundle_id.
        bundle_id = f"bundle_{council_id}"
        metadata = {"round_number": round_number}
        if root:
            # Translate the test's logical "root council_id" into its bundle_id
            metadata["chain_root_id"] = f"bundle_{root}"
        if parent:
            metadata["parent_council_id"] = parent
        if started_at:
            metadata["started_at"] = started_at
        return CouncilOutcome(
            council_run_id=council_id,
            bundle_id=bundle_id,
            task_cluster_id="c",
            primary_provider="claude",
            created_at=started_at or "2026-05-06T00:00:00",
            metadata=metadata,
        )

    def test_root_only_writes_single_segment(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import update_thread_manifest

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        path = update_thread_manifest(self._outcome("root1"))

        from trinity_local.council_runtime import _read_thread_manifest
        text = path.read_text()
        # Manifest is keyed off bundle_id (canonical chain root)
        assert "_thread_bundle_root1.js" in str(path)
        assert "bundle_root1" in text
        body = _read_thread_manifest(path)
        assert body["chain_root_id"] == "bundle_root1"
        assert len(body["segments"]) == 1
        assert body["segments"][0]["council_id"] == "root1"
        assert body["segments"][0]["round_number"] == 1

    def test_appends_chained_segments_in_order(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import update_thread_manifest

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        update_thread_manifest(self._outcome("root1", started_at="2026-05-06T00:00:00"))
        update_thread_manifest(self._outcome("c2", root="root1", parent="root1", round_number=2, started_at="2026-05-06T00:01:00"))
        path = update_thread_manifest(self._outcome("c3", root="root1", parent="c2", round_number=3, started_at="2026-05-06T00:02:00"))

        from trinity_local.council_runtime import _read_thread_manifest
        body = _read_thread_manifest(path)
        assert [s["council_id"] for s in body["segments"]] == ["root1", "c2", "c3"]
        assert body["segments"][2]["parent_council_id"] == "c2"

    def test_re_save_is_idempotent(self, tmp_path, monkeypatch):
        from trinity_local.council_runtime import update_thread_manifest

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        update_thread_manifest(self._outcome("root1"))
        update_thread_manifest(self._outcome("c2", root="root1", parent="root1", round_number=2))
        path = update_thread_manifest(self._outcome("c2", root="root1", parent="root1", round_number=2))

        from trinity_local.council_runtime import _read_thread_manifest
        body = _read_thread_manifest(path)
        # c2 appears once even after re-save
        ids = [s["council_id"] for s in body["segments"]]
        assert ids.count("c2") == 1
        assert ids == ["root1", "c2"]

    def test_consensus_rounds_sharing_bundle_id_all_appear(self, tmp_path, monkeypatch):
        """Regression for the lost-iterations bug.

        consensus_round rounds derive their bundle_id deterministically from
        (task_cluster_id, task_text, goal, origin_session) — so all rounds of
        a refinement chain share ONE bundle_id. The old dedup-by-bundle_id
        wiped the prior round each time update_thread_manifest ran, leaving
        only the latest round visible at `?thread_id=bundle_X`.

        This test pins the fix: rounds 1+2+3 of the same bundle MUST all
        appear in the manifest, ordered by round_number.
        """
        from trinity_local.council_runtime import update_thread_manifest, _read_thread_manifest
        from trinity_local.council_schema import CouncilOutcome

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        # All three rounds share bundle_id (deterministic hash).
        SHARED_BUNDLE = "bundle_42f8cea9c9e705e5"

        def _round(council_id: str, round_number: int, parent: str | None) -> CouncilOutcome:
            meta = {"chain_root_id": SHARED_BUNDLE, "round_number": round_number}
            if parent:
                meta["parent_council_id"] = parent
            return CouncilOutcome(
                council_run_id=council_id,
                bundle_id=SHARED_BUNDLE,
                task_cluster_id="copywriting_polish",
                primary_provider="claude",
                created_at=f"2026-05-12T14:3{round_number}:00",
                metadata=meta,
            )

        update_thread_manifest(_round("council_caf", round_number=1, parent=None))
        update_thread_manifest(_round("council_32b", round_number=2, parent="council_caf"))
        path = update_thread_manifest(_round("council_dd0", round_number=3, parent="council_32b"))

        # Manifest lives at the bundle-keyed filename (matches the URL the
        # launchpad emits for this bundle).
        assert path.name == f"_thread_{SHARED_BUNDLE}.js"

        body = _read_thread_manifest(path)
        ids = [s["council_id"] for s in body["segments"]]
        assert ids == ["council_caf", "council_32b", "council_dd0"], (
            f"All three rounds must survive in the manifest; got {ids}"
        )
        round_numbers = [s["round_number"] for s in body["segments"]]
        assert round_numbers == [1, 2, 3]

    def test_pending_to_finalized_only_replaces_same_round(self, tmp_path, monkeypatch):
        """When register_pending_round adds a placeholder for round 2 and
        update_thread_manifest later replaces it, prior rounds' completed
        entries must not be collateral damage."""
        from trinity_local.council_runtime import (
            update_thread_manifest, register_pending_round, _read_thread_manifest
        )
        from trinity_local.council_schema import CouncilOutcome

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        SHARED = "bundle_shared_xyz"

        def _round(cid: str, rn: int) -> CouncilOutcome:
            return CouncilOutcome(
                council_run_id=cid, bundle_id=SHARED, task_cluster_id="c",
                primary_provider="claude",
                created_at=f"2026-05-12T14:3{rn}:00",
                metadata={"chain_root_id": SHARED, "round_number": rn},
            )

        # Round 1 finalized.
        update_thread_manifest(_round("c1", 1))
        # Round 2 pending (no council_id yet).
        register_pending_round(
            chain_root_id=SHARED, bundle_id=SHARED, status_token="tok2",
            round_number=2, parent_council_id="c1",
        )
        # Round 2 finalizes — should REPLACE only the pending round-2 entry.
        path = update_thread_manifest(_round("c2", 2))

        body = _read_thread_manifest(path)
        ids = [s["council_id"] for s in body["segments"]]
        assert ids == ["c1", "c2"], (
            f"Pending→finalized handoff must only replace round 2's entry; got {ids}"
        )

    def test_pending_round_is_replaced_by_completed_outcome(self, tmp_path, monkeypatch):
        # When a chain round starts we register a pending segment (no
        # council_id yet, status_token set, running=true) so the thread
        # view picks it up mid-flight. When the round saves, the matching
        # pending entry must be replaced by the completed one — keyed off
        # bundle_id, since council_run_id is allocated only at finalize time.
        # All threads use bundle_id as the chain root identifier.
        from trinity_local.council_runtime import (
            register_pending_round,
            update_thread_manifest,
            _read_thread_manifest,
        )
        from trinity_local.council_schema import CouncilOutcome

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

        # Root council: chain_root_id == bundle_id == "bundle_root1"
        update_thread_manifest(self._outcome("root1", started_at="2026-05-06T00:00:00"))

        # Round 2 starts: pending entry under chain_root_id = root's bundle_id
        path = register_pending_round(
            chain_root_id="bundle_root1",
            bundle_id="bundle_round2",
            status_token="status_xyz",
            round_number=2,
            parent_council_id="root1",
            started_at="2026-05-06T00:01:00",
        )
        body = _read_thread_manifest(path)
        running = [s for s in body["segments"] if s.get("running")]
        assert len(running) == 1
        assert running[0]["status_token"] == "status_xyz"
        assert running[0]["bundle_id"] == "bundle_round2"
        assert running[0]["council_id"] is None

        # Round 2 completes: outcome saved, pending entry replaced
        completed = CouncilOutcome(
            council_run_id="real_round2_id",
            bundle_id="bundle_round2",
            task_cluster_id="c",
            primary_provider="claude",
            created_at="2026-05-06T00:02:00",
            metadata={
                "chain_root_id": "bundle_root1",
                "parent_council_id": "root1",
                "round_number": 2,
                "started_at": "2026-05-06T00:01:00",
            },
        )
        path = update_thread_manifest(completed)
        body = _read_thread_manifest(path)
        # No more pending — replaced by the completed entry
        assert all(not s.get("running") for s in body["segments"])
        ids = [s["council_id"] for s in body["segments"]]
        assert ids == ["root1", "real_round2_id"]



class TestLoadCouncilOutcomeNormalizesProviderSlugs:
    """`load_council_outcome` normalizes legacy "gemini" → "antigravity"
    across every provider-keyed field, so downstream consumers
    (personal_routing aggregator, chairman picker, launchpad
    rendering, audit dashboards) see canonical slugs end-to-end.

    Tick 96 covered routing_label.{winner, runner_up, provider_scores};
    tick 97 extends to primary_provider, winner_provider, and each
    member's provider. Together the two ticks cover every Python-side
    consumer of historical outcome JSON files."""

    def test_normalizes_primary_and_winner_provider(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.council_runtime import load_council_outcome
        from trinity_local.state_paths import council_outcomes_dir

        outcomes_dir = council_outcomes_dir()
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        path = outcomes_dir / "council_test_legacy.json"
        path.write_text(json.dumps({
            "council_run_id": "council_test_legacy",
            "bundle_id": "b1",
            "task_cluster_id": "c1",
            "primary_provider": "gemini",
            "winner_provider": "gemini",
            "member_results": [
                {"provider": "claude", "output_text": "..."},
                {"provider": "gemini", "output_text": "..."},
            ],
        }))

        outcome = load_council_outcome("council_test_legacy")
        assert outcome.primary_provider == "antigravity"
        assert outcome.winner_provider == "antigravity"
        member_providers = [m.provider for m in outcome.member_results]
        assert member_providers == ["claude", "antigravity"]

    def test_canonical_slug_passes_through_unchanged(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.council_runtime import load_council_outcome
        from trinity_local.state_paths import council_outcomes_dir

        outcomes_dir = council_outcomes_dir()
        outcomes_dir.mkdir(parents=True, exist_ok=True)
        path = outcomes_dir / "council_test_canonical.json"
        path.write_text(json.dumps({
            "council_run_id": "council_test_canonical",
            "bundle_id": "b1",
            "task_cluster_id": "c1",
            "primary_provider": "antigravity",
            "winner_provider": "claude",
            "member_results": [
                {"provider": "antigravity", "output_text": "..."},
            ],
        }))

        outcome = load_council_outcome("council_test_canonical")
        assert outcome.primary_provider == "antigravity"
        assert outcome.winner_provider == "claude"
        assert outcome.member_results[0].provider == "antigravity"


class TestChainStepNormalizesModelProvider:
    """`CouncilChainStep.from_dict` normalizes legacy "gemini" →
    "antigravity" in `model_provider`, so historical chain-mode
    councils render canonical slugs through the same downstream
    path that load_council_outcome already normalizes for the
    outcome- and member-level provider fields (tick 97)."""

    def test_gemini_model_provider_normalized(self):
        from trinity_local.council_schema import CouncilChainStep
        step = CouncilChainStep.from_dict({
            "step_index": 0,
            "model_provider": "gemini",
            "input_text": "x",
            "output_text": "y",
        })
        assert step.model_provider == "antigravity"

    def test_canonical_model_provider_unchanged(self):
        from trinity_local.council_schema import CouncilChainStep
        step = CouncilChainStep.from_dict({
            "step_index": 1,
            "model_provider": "claude",
            "input_text": "x",
            "output_text": "y",
        })
        assert step.model_provider == "claude"


class TestLoadPromptBundleNormalizesOriginProvider:
    """`load_prompt_bundle` normalizes legacy "gemini" → "antigravity"
    in the bundle's `origin_provider` field, so downstream consumers
    (task_runtime.py:28,38; council_runner.py:323's source_provider;
    handoff.py's source_providers list) see canonical slugs only."""

    def test_origin_provider_gemini_normalized(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.council_runtime import load_prompt_bundle
        from trinity_local.state_paths import prompt_bundles_dir

        bundles_dir = prompt_bundles_dir()
        bundles_dir.mkdir(parents=True, exist_ok=True)
        path = bundles_dir / "bundle_test_legacy.json"
        path.write_text(json.dumps({
            "bundle_id": "bundle_test_legacy",
            "task_cluster_id": "c1",
            "origin_provider": "gemini",
            "task_text": "?",
        }))

        bundle = load_prompt_bundle("bundle_test_legacy")
        assert bundle.origin_provider == "antigravity"

    def test_no_origin_provider_field_passes_through(self, tmp_path, monkeypatch):
        import json
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.council_runtime import load_prompt_bundle
        from trinity_local.state_paths import prompt_bundles_dir

        bundles_dir = prompt_bundles_dir()
        bundles_dir.mkdir(parents=True, exist_ok=True)
        path = bundles_dir / "bundle_test_no_origin.json"
        path.write_text(json.dumps({
            "bundle_id": "bundle_test_no_origin",
            "task_cluster_id": "c1",
            "task_text": "?",
        }))

        bundle = load_prompt_bundle("bundle_test_no_origin")
        assert bundle.origin_provider is None


class TestChainQuotaErrorHandling:
    """A rate-limited / token-exhausted member in CHAIN mode must NOT have its
    stderr quota error ("You've hit your usage limit") fed forward as that step's
    answer. The PARALLEL member path (_run_member) already gates on
    `returncode != 0 and not stdout.strip()`; chain mode must mirror it.

    Found 2026-06-06 during the codex-quota outage: parallel councils correctly
    EXCLUDED the quota'd member (the naming council ran 2-member), but the chain
    path took `result.stdout or result.stderr`, turning the quota error string
    into the chain step's output_text — which then poisons the next chain step,
    the chairman synthesis, AND the re-ingested corpus. This pins the fix."""

    def _config(self):
        from trinity_local.config import AppConfig, ProviderConfig

        def pc(name, model):
            return ProviderConfig(
                name=name, type="cli", enabled=True, label=name.title(),
                command=[name], args=[], task_types=set(), model=model,
            )

        return AppConfig(
            max_turns=1, notifications=False,
            providers={"claude": pc("claude", "claude-opus-4-8"),
                       "codex": pc("codex", "gpt-5.5")},
            task_preferences={},
        )

    def test_chain_drops_quota_failed_member_not_its_error_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local import council_runner as cr
        from trinity_local.providers import ProviderResult
        from trinity_local.council_schema import PromptBundle

        QUOTA_ERR = "ERROR: You've hit your usage limit. Upgrade to Pro to continue."
        CLAUDE_OUT = "Here is my real chain answer: use DuckDB for analytical scans."

        class _Fake:
            def __init__(self, name):
                self.name = name

            def run(self, prompt, cwd):
                if self.name == "codex":
                    # rate-limited: non-zero exit, empty stdout, error on stderr.
                    return ProviderResult(provider="codex", stdout="", stderr=QUOTA_ERR, returncode=1)
                return ProviderResult(provider=self.name, stdout=CLAUDE_OUT, stderr="", returncode=0)

        monkeypatch.setattr(cr, "make_provider", lambda cfg: _Fake(cfg.name))
        # Canned synthesis so no real chairman dispatches.
        monkeypatch.setattr(
            cr, "_synthesize_with_fallback",
            lambda *a, **k: (
                'Winner\nclaude\n```routing-json\n{"winner":"claude","confidence":"high"}\n```',
                {}, None, "claude", "claude-opus-4-8",
            ),
        )

        bundle = PromptBundle(
            bundle_id="b1", task_cluster_id="c1",
            origin_provider="claude", task_text="Pick a database.",
        )
        result = cr._run_chain(
            config=self._config(), bundle=bundle,
            sequence=["claude", "codex"], primary_provider="claude", cwd=tmp_path,
        )

        members = result.outcome.member_results
        joined = " ".join((m.output_text or "") for m in members)
        # The quota error string must appear in NO member output (the bug).
        assert "usage limit" not in joined, (
            f"the codex quota error leaked into a chain member output: {joined[:200]!r}"
        )
        # Only the real (claude) step survives as a member; the quota'd codex
        # step is dropped, not kept with its error as the 'answer'.
        provs = [m.provider for m in members]
        assert provs == ["claude"], f"chain kept the quota-failed codex step: {provs}"
        assert CLAUDE_OUT in joined, "the real claude step output went missing"
