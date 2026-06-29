"""Tests for council review HTML rendering."""
from __future__ import annotations

from trinity_local.council_review import (
    render_live_council_page,
    render_unified_council_page,
    write_live_council_page,
)
from trinity_local.council_schema import CouncilMemberResult, CouncilOutcome, PromptBundle


class TestCouncilReviewMarkdown:
    def test_renders_markdown_blocks(self):
        bundle = PromptBundle(
            bundle_id="bundle_123",
            task_cluster_id="cluster_123",
            task_text="# Launch task\n\n- compare answers\n- pick a winner",
            goal="## Goal\nChoose the **strongest** answer.",
            comparison_instructions="Prefer answers with `specificity` and [clarity](https://example.com).",
            context_excerpt="```text\nmarket context\n```",
            created_at="2026-04-28T12:00:00+00:00",
        )
        outcome = CouncilOutcome(
            council_run_id="council_123",
            bundle_id=bundle.bundle_id,
            task_cluster_id=bundle.task_cluster_id,
            primary_provider="claude",
            winner_provider="antigravity",
            member_results=[
                CouncilMemberResult(
                    provider="antigravity",
                    model="gemini-pro",
                    output_text="## Best take\n\n- fast\n- social\n\n```py\nprint('hello')\n```",
                )
            ],
            synthesis_output="# The Strongest Answer\n\nUse the **Gemini** version.",
            synthesis_prompt="## Prompt\n\nReview all council answers.",
            created_at="2026-04-28T12:05:00+00:00",
        )

        html = render_unified_council_page(bundle, outcome)

        # Synthesis output is markdown-rendered. The content heading keeps its
        # VISIBLE <h1> tag (so its font-size is unchanged) but carries a demoted
        # aria-level so a screen reader never hears a content "# Heading" as a
        # page-level <h1> competing with the council-question <h1> (WCAG 1.3.1 /
        # 2.4.6 — fixed Iter 238; render_markdown heading_offset=2).
        assert '<h1 aria-level="3">The Strongest Answer</h1>' in html
        assert "<strong>Gemini</strong>" in html
        # Member output is markdown-rendered (## → <h2 aria-level="4">).
        assert '<h2 aria-level="4">Best take</h2>' in html
        assert '<pre class="md-code-block"><code>print(&#x27;hello&#x27;)</code></pre>' in html
        # Page structure
        assert "← Launchpad" in html
        assert "Comparative Analysis" in html
        assert "Full Responses" in html

    def test_member_labels_fold_web_era_capture_slugs(self):
        """Browser-found 2026-06-01: the review page's normalizeProviderSlug only
        mapped gemini→antigravity, so formatProviderLabel('chatgpt')→'Chatgpt' and
        ('claude_ai')→'Claude Ai' rendered on member cards / winner / runner-up.
        514 of the founder's councils carry chatgpt/claude_ai member slugs, so
        nearly every review page leaked a raw web-era label. The normalizer must
        fold all three capture slugs to the harness trio."""
        html = render_live_council_page()
        assert "gemini: 'antigravity'" in html
        assert "chatgpt: 'codex'" in html
        assert "claude_ai: 'claude'" in html
        assert "slug === 'gemini' ? 'antigravity' : slug" not in html

    def test_live_page_hides_claim_sections_when_arrays_empty(self):
        """The product's lead surface (disagreed_claims) must degrade gracefully:
        36% of real councils have empty disagreed_claims and 30% empty agreed
        (live 2026-06-01: e.g. council_6d5400da0f196d87 — 5 agreed, 0 disagreed;
        its page correctly HIDES the Disagreed section). The sections must gate on
        `.length`, NOT bare array truthiness — an empty array is truthy in JS, so
        `v-if="...disagreed_claims"` (no .length) would render a broken empty
        "Disagreed claims" header on a THIRD of all councils."""
        import re

        html = render_live_council_page()
        assert "Agreed claims" in html and "Disagreed claims" in html
        # Each section's v-if must include the `.length` non-empty check so an
        # empty [] hides it. The leading `.` disambiguates agreed vs disagreed.
        assert re.search(r"\.agreed_claims\.length", html), (
            "Agreed claims section must gate on .length so an empty [] hides it"
        )
        assert re.search(r"\.disagreed_claims\.length", html), (
            "Disagreed claims section must gate on .length so an empty [] hides it "
            "— 36% of real councils have empty disagreed_claims; without .length "
            "they would render a broken empty header on the product's lead surface"
        )

    def test_live_page_discloses_failed_members(self):
        """Honest-degradation (the #238 lineage). council_runner EXCLUDES failed
        providers from member_results (only successes get a CouncilMemberResult) but
        records the casualties in metadata.failed_members — so a 2-of-3 council
        rendered identically to a 2-model council, silently hiding that a provider
        was attempted and failed. Measured 2026-06-02: 14 of 562 real councils
        (2.5%) carry a non-empty failed_members. The walkover already says "Sole
        entrant" and the eval card discloses excluded_runs; this extends the same
        honesty to the council page. The disclosure must (a) read from
        metadata.failed_members, (b) gate on a >0 count so a clean council shows
        NOTHING (browser-verified: 0-failed painkiller has no note; 1-failed shows
        singular, 3-failed shows plural)."""
        import re

        html = render_live_council_page()
        # The helper that surfaces the count from metadata.failed_members.
        assert "failedMembersFor(seg)" in html, (
            "the failed-members disclosure helper was dropped"
        )
        assert re.search(r"failed_members", html), (
            "failedMembersFor must read from runState.metadata.failed_members"
        )
        # The disclosure must be COUNT-GATED (>0) so a clean council (the 97.5%
        # case) renders no note — a bare-truthy gate would be a measure-0 risk but
        # the count-compare is the contract. (The over-N branch ALSO ANDs on
        # respondedMembersFor(seg) > 0 since 2026-06-21 so a TOTAL failure doesn't
        # claim a phantom "synthesis over the 0 that responded" — the count gate
        # must survive that additional clause, hence the looser match here. The
        # over-0 honest branch is guarded by test_total_failure_… in the browser
        # suite.)
        assert re.search(r'v-if="failedMembersFor\(seg\) > 0', html), (
            "the disclosure must gate on failedMembersFor(seg) > 0 so a council "
            "with no failures shows nothing"
        )
        assert "attempted but failed" in html and "excluded" in html, (
            "the honest disclosure copy is missing from the Full Responses section"
        )

    def test_unified_page_discloses_failed_members(self):
        """Static-page sibling of the live-page disclosure above (#238). The
        PERSISTENT, shareable review page (render_unified_council_page) — the
        'review-link companion' a teammate opens — ALSO excludes failed providers
        from member_results while the runner records them in
        metadata.failed_members. Without this note a 2-of-3 council where a premium
        provider (e.g. GPT-5.5 rate-limited) was attempted reads identically to a
        deliberate 2-model council on the shared artifact, hiding the casualty.
        The live page disclosed it; the static page didn't (verified on real data:
        the founder has councils with failed_members=['gemini']). Count-only (no
        slug — the #275 call), >0-gated, singular/plural, SHAPE-guarded."""
        import re

        def _mk(metadata, members=None):
            bundle = PromptBundle(
                bundle_id="b_fm", task_cluster_id="c_fm", task_text="Q?", goal="g",
                comparison_instructions="cmp", created_at="2026-06-07T00:00:00+00:00",
            )
            if members is None:
                members = [
                    CouncilMemberResult(provider="claude", model="m", output_text="A"),
                    CouncilMemberResult(provider="codex", model="m", output_text="B"),
                ]
            return render_unified_council_page(bundle, CouncilOutcome(
                council_run_id="r_fm", bundle_id="b_fm", task_cluster_id="c_fm",
                primary_provider="claude", primary_model="claude-opus-4-8",
                member_results=members,
                synthesis_output="# S", created_at="2026-06-07T00:05:00+00:00",
                metadata=metadata,
            ))

        # 1 failure → singular, names the RESPONDED count, no provider slug.
        one = _mk({"failed_members": ["gemini"]})
        assert "1 provider attempted but failed and was excluded" in one
        assert "over the 2 that responded" in one
        note = re.search(r"⚠.*?responded\.", one, re.DOTALL)
        assert note and "gemini" not in note.group(0).lower(), (
            "the disclosure must be count-only (no provider slug — the #275 call)"
        )
        # 2 failures → plural.
        assert "2 providers attempted but failed and were excluded" in _mk(
            {"failed_members": ["gemini", "codex"]}
        )
        # A clean council (the ~97.5% case) shows NOTHING — mutation-catcher:
        # drop failed_disclosure_html and the 1-/2-failure asserts above red.
        assert "attempted but failed" not in _mk({"failed_members": []})
        assert "attempted but failed" not in _mk({})
        # Shape guard (guard_shape_not_just_parse): a non-list metadata value must
        # not crash AND must render no note.
        assert "attempted but failed" not in _mk({"failed_members": "garbage"})
        # TOTAL FAILURE (0 responders, all members in failed_members): the over-N
        # copy is degenerate — "this synthesis is over the 0 that responded" claims
        # a synthesis exists AND that nobody answered it. Found 2026-06-21 driving
        # the live page in the UX sweep; the static review page shared the class.
        # Mutation: drop the `_responded_count > 0` gate → the over-0 contradiction
        # comes back and this assert goes red.
        total_fail = _mk({"failed_members": ["claude", "codex"]}, members=[])
        assert "over the 0 that responded" not in total_fail, (
            "an all-members-failed review page rendered the self-contradicting "
            "'this synthesis is over the 0 that responded' — there is no synthesis "
            "when 0 members responded; the over-N copy must gate on responders > 0"
        )
        assert "no synthesis to show" in total_fail, (
            "the all-failed review page must honestly say 'there's no synthesis to "
            "show' rather than silently dropping the disclosure"
        )

    def test_unified_page_uses_model_brand_not_harness_name(self):
        """The STATIC review page (render_unified_council_page) is the persistent,
        SHAREABLE "review-link companion". #275 flipped every council surface to
        the MODEL BRAND (Claude / GPT / Gemini), but that sweep only touched the
        JS formatProviderLabel (live page + launchpad); THIS page renders provider
        labels SERVER-SIDE via provider.title(), so it kept leaking the old harness
        names (Codex / Antigravity) on the most-shared artifact while every other
        surface showed GPT / Gemini. Fixed via provider_model_brand() at the member
        headers, winner/runner-up, chairman attribution, and the score rows. Local
        slugs still fall through to their honest title-cased identity (no fabricated
        frontier brand). Mutation: revert any site to .title() → 'Codex'/'Antigravity'
        leaks back → reds."""
        import re

        from trinity_local.council_schema import CouncilRoutingLabel

        def _mk(members, winner, runner, scores, chair):
            bundle = PromptBundle(
                bundle_id="b_br", task_cluster_id="c_br", task_text="Q?", goal="g",
                comparison_instructions="cmp", created_at="2026-06-07T00:00:00+00:00",
            )
            return render_unified_council_page(bundle, CouncilOutcome(
                council_run_id="r_br", bundle_id="b_br", task_cluster_id="c_br",
                primary_provider=chair, primary_model="gpt-5.5", winner_provider=winner,
                member_results=[CouncilMemberResult(provider=p, model="m", output_text="x") for p in members],
                synthesis_output="# S", created_at="2026-06-07T00:05:00+00:00",
                routing_label=CouncilRoutingLabel(
                    winner=winner, runner_up=runner, confidence="high", task_type="design",
                    agreed_claims=["x"], disagreed_claims=[],
                    provider_scores={p: {"overall": 7.0} for p in scores},
                ),
            ))

        html = _mk(["claude", "codex", "antigravity"], "codex", "antigravity",
                   ["claude", "codex", "antigravity"], "codex")
        # Member headers, chairman, and score rows all read as the MODEL BRAND.
        assert re.findall(r"<h3>([^<]+)</h3>", html) == ["Claude", "GPT", "Gemini"], (
            "member headers must brand codex→GPT / antigravity→Gemini, not the harness names"
        )
        assert re.findall(r"Chaired by <strong>([^<]+)</strong>", html) == ["GPT"]
        assert re.findall(r"<tr><td>([^<]+)</td><td>[0-9]", html) == ["Claude", "GPT", "Gemini"]
        assert "<strong>GPT</strong>" in html, "winner line must brand the winner GPT"
        assert "runner-up: Gemini" in html, "runner-up must brand antigravity→Gemini"
        # The old harness names must not appear in any LABELED surface.
        labeled = re.findall(r"<h3>[^<]*</h3>|<strong>[^<]*</strong>|<td>[^<]*</td>", html)
        assert not any("Codex" in s or "Antigravity" in s for s in labeled), (
            f"a harness name leaked into a provider label: {[s for s in labeled if 'Codex' in s or 'Antigravity' in s]}"
        )
        # Local model stays honest — falls through to its own identity, no frontier brand.
        local = _mk(["claude", "qwen35"], "claude", "qwen35", ["claude", "qwen35"], "claude")
        assert re.findall(r"<h3>([^<]+)</h3>", local) == ["Claude", "Qwen35"], (
            "a local slug must render as its own title-cased identity, never a frontier brand"
        )

    def test_unified_page_renders_structured_agreed_disagreed_claims(self):
        """The council painkiller's core value is "see exactly where the models
        AGREED vs DISAGREED". The LIVE page rendered the structured breakdown, but
        the PERSISTENT, shareable review page (render_unified_council_page) only
        had the synthesis prose — so the scannable disagreement didn't survive on
        the artifact that travels. Back-ported 2026-06-07 (founder-greenlit). The
        disagreement providers brand consistently (codex→GPT), why_matters shows,
        empty arrays render nothing, malformed entries are skipped (shape guard),
        and all corpus text is escaped (the page's XSS surface)."""
        from trinity_local.council_schema import CouncilRoutingLabel

        def _mk(agreed, disagreed):
            bundle = PromptBundle(
                bundle_id="b_cl", task_cluster_id="c_cl", task_text="Q?", goal="g",
                comparison_instructions="cmp", created_at="2026-06-07T00:00:00+00:00",
            )
            return render_unified_council_page(bundle, CouncilOutcome(
                council_run_id="r_cl", bundle_id="b_cl", task_cluster_id="c_cl",
                primary_provider="claude", primary_model="m", winner_provider="claude",
                # TWO members — a real contest. A SOLO (1-responder) council
                # now SUPPRESSES the agreed/disagreed competition framing (the
                # share-card-twin solo fix), so testing the consensus RENDER
                # logic requires >=2 members; otherwise this asserted the very
                # overclaim the fix removes.
                member_results=[
                    CouncilMemberResult(provider="claude", model="m", output_text="A"),
                    CouncilMemberResult(provider="codex", model="m", output_text="B"),
                ],
                synthesis_output="# S", created_at="2026-06-07T00:05:00+00:00",
                routing_label=CouncilRoutingLabel(
                    winner="claude", runner_up="codex", confidence="high", task_type="design",
                    agreed_claims=agreed, disagreed_claims=disagreed,
                ),
            ))

        html = _mk(
            ["both cache the provider slug"],
            [{"claim": "per-call vs in-process caching", "providers_for": ["claude"],
              "providers_against": ["codex"], "why_matters": "per-call leaks across tenants"}],
        )
        assert "Where they agreed" in html and "both cache the provider slug" in html
        assert "Where they disagreed" in html and "per-call vs in-process caching" in html
        # Disagreement providers brand consistently (codex→GPT, no raw slug).
        assert "for: Claude" in html and "against: GPT" in html, html[html.index("Where they disagreed"):][:400]
        assert "against: codex" not in html.lower()
        assert "per-call leaks across tenants" in html  # why_matters
        # Empty arrays → no sections at all.
        empty = _mk([], [])
        assert "Where they agreed" not in empty and "Where they disagreed" not in empty
        # Shape guard: non-list / non-dict / missing-claim entries skipped, no crash.
        messy = _mk("garbage", ["notadict", {"noclaim": 1}, {"claim": "   "}, {"claim": "real one"}])
        assert "Where they disagreed" in messy and "real one" in messy
        # XSS: corpus text is escaped (no raw dangerous tag survives).
        xss = _mk(["<img src=x onerror=alert(1)>"], [{"claim": "<script>bad</script>"}])
        assert "<img src=x" not in xss and "<script>bad" not in xss
        assert "&lt;img" in xss and "&lt;script" in xss

    def test_unified_page_suppresses_contest_framing_for_all_same_provider(self):
        """The DEGENERATE same-provider case (Iter 111): every member is the SAME
        provider (claude·claude·claude) — 3 responders but ONE distinct voice, so
        the chairman's winner/runner_up/agreed all key on that single slug. The
        PERSISTENT, shareable review page would render '<strong>Claude</strong> ·
        runner-up: Claude' (the winner is its own runner-up!) + 'Where they
        agreed' + 'Where they disagreed' — a fabricated contest between identical
        voices (the same #35 overclaim the 1-responder solo branch suppresses;
        the same-provider roster was its unfixed sibling). The `_solo` gate must
        count DISTINCT provider slugs, not raw member_results, so this collapses
        to the honest 'One model — no council.' framing with NO winner/agreed/
        disagreed blocks."""
        from trinity_local.council_schema import CouncilRoutingLabel

        bundle = PromptBundle(
            bundle_id="b_same", task_cluster_id="c_same", task_text="Q?", goal="g",
            created_at="2026-06-18T00:00:00+00:00",
        )
        html = render_unified_council_page(bundle, CouncilOutcome(
            council_run_id="r_same", bundle_id="b_same", task_cluster_id="c_same",
            primary_provider="claude", primary_model="m", winner_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", model="m", output_text="A"),
                CouncilMemberResult(provider="claude", model="m", output_text="B"),
                CouncilMemberResult(provider="claude", model="m", output_text="C"),
            ],
            synthesis_output="# S", created_at="2026-06-18T00:05:00+00:00",
            routing_label=CouncilRoutingLabel(
                winner="claude", runner_up="claude", confidence="high", task_type="code",
                agreed_claims=["Use a hash map", "Validate at the boundary"],
                disagreed_claims=[{"claim": "Whether to cache", "why_matters": "invalidation cost"}],
            ),
        ))
        # Honest collapse: the solo framing renders, the contest framing does NOT.
        assert "One model — no council." in html, (
            "REGRESSION: an all-same-provider review page did NOT collapse to the "
            "honest 'One model — no council.' framing (Iter 111)."
        )
        assert "Where they agreed" not in html and "Where they disagreed" not in html, (
            "REGRESSION: an all-same-provider review page rendered the AGREED/"
            "DISAGREED contest blocks — three identical voices are not a contest; "
            "the chairman's winner is its own runner-up (#35 green-while-degenerate)."
        )
        # The fabricated 'runner-up: Claude' winner line must be gone (it's inside
        # the suppressed routing-winner block, replaced by the solo line).
        assert "runner-up:" not in html, (
            "REGRESSION: an all-same-provider review page rendered 'runner-up: "
            "Claude' — the winner cannot be its own runner-up."
        )

    def test_unified_page_surfaces_lens_pick_badge_from_chairman(self):
        """Phase 3d (2026-05-22): the rating click-to-pick UI is retired.
        Replacement affordance is a "Lens pick" badge sourced from the
        chairman's routing_label.winner — chairman synthesis is
        conditioned on the user's lens, so the chairman's pick IS the
        supervision signal. No click-handler, no rate-council shortcut,
        no metadata.user_verdict hydration."""
        from trinity_local.council_schema import CouncilRoutingLabel

        bundle = PromptBundle(
            bundle_id="bundle_123",
            task_cluster_id="cluster_123",
            task_text="Why is the sky blue?",
            goal="Choose the strongest answer.",
            comparison_instructions="Prefer the strongest answer for the user.",
            created_at="2026-04-28T12:00:00+00:00",
        )
        outcome = CouncilOutcome(
            council_run_id="council_123",
            bundle_id=bundle.bundle_id,
            task_cluster_id=bundle.task_cluster_id,
            primary_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", model="claude", output_text="Claude answer"),
                CouncilMemberResult(provider="antigravity", model="antigravity", output_text="Gemini answer"),
            ],
            synthesis_output="# Compare\n\nPick the clearest answer.",
            routing_label=CouncilRoutingLabel(winner="antigravity"),
            created_at="2026-04-28T12:05:00+00:00",
        )

        html = render_unified_council_page(bundle, outcome)

        # New affordance: Lens-pick badge keyed off the chairman's winner.
        assert "Lens pick</span>" in html
        assert "lensPickProvider" in html
        # JSON in the page_data block uses compact separators.
        assert '"lensPickProvider":"antigravity"' in html
        # Retired affordances: no click-to-rate, no Preferred badge, no
        # initialSelection hydration, no rate_council shortcut.
        assert "Preferred</span>" not in html
        assert "chooseAnswer(" not in html
        assert "chooseMember(" not in html
        assert "selectedLabel" not in html
        assert "selectedProvider" not in html
        assert "initialSelection" not in html
        assert "user_verdict" not in html
        assert "user_winner" not in html
        # Layout sanity: page renders launchpad nav + comparison body.
        assert "← Launchpad" in html
        assert "confirm your preference in the floating bar" not in html
        assert "floating-actions" not in html
        assert "signal_page" not in html

    def test_unified_page_omits_lens_pick_when_routing_label_missing(self):
        """When the chairman's Routing JSON failed to parse (or the
        outcome predates structured chairman output), the routing_label
        is None and the badge should not render."""
        bundle = PromptBundle(
            bundle_id="bundle_456",
            task_cluster_id="cluster_456",
            task_text="Pick something.",
            goal="Choose the strongest answer.",
            comparison_instructions="Prefer the strongest answer for the user.",
            created_at="2026-04-28T12:00:00+00:00",
        )
        outcome = CouncilOutcome(
            council_run_id="council_456",
            bundle_id=bundle.bundle_id,
            task_cluster_id=bundle.task_cluster_id,
            primary_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", model="claude", output_text="A"),
                CouncilMemberResult(provider="antigravity", model="antigravity", output_text="B"),
            ],
            synthesis_output="No structured pick.",
            created_at="2026-04-28T12:05:00+00:00",
        )

        html = render_unified_council_page(bundle, outcome)

        # routing_label is None → lensPickProvider is empty string →
        # Vue's v-if="lensPickProvider === '<provider>'" never matches.
        # JSON in page_data block uses compact separators.
        assert '"lensPickProvider":""' in html

    def test_unified_page_uses_three_column_layout_for_three_members(self):
        bundle = PromptBundle(
            bundle_id="bundle_123",
            task_cluster_id="cluster_123",
            task_text="Why is the sky blue?",
            goal="Choose the strongest answer.",
            comparison_instructions="Prefer the strongest answer for the user.",
            created_at="2026-04-28T12:00:00+00:00",
        )
        outcome = CouncilOutcome(
            council_run_id="council_123",
            bundle_id=bundle.bundle_id,
            task_cluster_id=bundle.task_cluster_id,
            primary_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", model="claude", output_text="Claude answer"),
                CouncilMemberResult(provider="antigravity", model="antigravity", output_text="Gemini answer"),
                CouncilMemberResult(provider="codex", model="codex", output_text="Codex answer"),
            ],
            synthesis_output="# Compare\n\nPick the clearest answer.",
            created_at="2026-04-28T12:05:00+00:00",
        )

        html = render_unified_council_page(bundle, outcome)

        assert 'class="answers-grid answers-grid-three"' in html

    def test_unified_page_two_member_uses_base_grid_not_three(self):
        """The DOMINANT council shape is 2 members (79% of the real corpus —
        most councils dispatch a pair, not the full trio), yet only the 3-member
        case gets the `answers-grid-three` fixed-3-column class. A 2-member
        council must use the BASE `answers-grid` (auto-fit) so its two responses
        sit side by side — the side-by-side comparison IS the painkiller. Pin
        that the exactly-3 special-case never wrongly captures the 2-member shape.
        The actual side-by-side LAYOUT is guarded in the browser by
        test_council_review_layout_browser.py."""
        bundle = PromptBundle(
            bundle_id="bundle_2m",
            task_cluster_id="cluster_2m",
            task_text="Diffusion vs transformers?",
            goal="Choose the strongest answer.",
            comparison_instructions="Prefer the strongest answer for the user.",
            created_at="2026-06-01T12:00:00+00:00",
        )
        outcome = CouncilOutcome(
            council_run_id="council_2m",
            bundle_id=bundle.bundle_id,
            task_cluster_id=bundle.task_cluster_id,
            primary_provider="claude",
            member_results=[
                CouncilMemberResult(provider="claude", model="claude", output_text="Claude answer"),
                CouncilMemberResult(provider="codex", model="codex", output_text="Codex answer"),
            ],
            synthesis_output="# Compare\n\nPick the clearest answer.",
            created_at="2026-06-01T12:05:00+00:00",
        )

        html = render_unified_council_page(bundle, outcome)

        # The grid ELEMENT carries exactly `class="answers-grid"` (the `.answers-
        # grid-three` *rule* is always defined in the <style> block, so we must
        # match the class attribute, not the bare substring).
        assert 'class="answers-grid"' in html
        assert 'class="answers-grid answers-grid-three"' not in html, (
            "the exactly-3 fixed-column class leaked onto a 2-member council"
        )

    def test_live_council_page_renders_stop_control(self, patch_trinity_home):
        html = render_live_council_page()

        assert "← Launchpad" in html
        assert "Stop council" in html
        assert "statusScriptBaseUrl" in html
        assert "window.addEventListener('pageshow'" in html
        assert "back_forward" in html
        assert "base.includes('?') ? `&t=${Date.now()}` : `?t=${Date.now()}`" in html
        assert "formatProviderLabel" in html
        assert "label: analysisLabel" in html
        assert "progressScriptBaseUrl" not in html
        # Stop button now dispatches via the Chrome extension instead
        # of the retired shortcuts:// path — assert the dispatcher
        # kind ('stop-council' per capture_host.ACTION_ALLOWLIST).
        assert "'stop-council'" in html
        assert "fallbackMembers" in html
        assert "members: params.fallbackMembers" in html
        assert "Object.keys(memberMap).length ? Object.keys(memberMap) : (seg.runState?.memberOrder || [])" in html
        # Threading UX: page renders stacked segments and supports ?thread_id= mode.
        assert "segments: []" in html
        assert "loadThreadScript" in html
        assert "_thread_" in html
        # Refinement directive is surfaced in the eyebrow row for any round
        # that has one. Source: outcome.metadata.user_refinement → rs.metadata.
        # Regression guard for bundle_42f8cea9c9e705e5 ("Stop copy-pasting
        # prompts. Own your context. Forge your core memories.") which had
        # its refinement directive vanish from the rendered thread.
        assert "seg.refinementText" in html
        assert "rs.metadata?.user_refinement" in html
        # Quote-into-refinement affordance (tick #60). Lets the user
        # cherry-pick fragments across member responses and stack them
        # into the refinement input, matching the user's hand-rolled
        # flow on bundle_42f8cea9c9e705e5 (took "Own your context" from
        # Gemini, merged with Claude's response, typed the merged line).
        # The button must have @click.stop so it doesn't trigger the
        # parent article's pick-winner click handler.
        assert "quoteMember(row.provider, row)" in html
        assert "quote-member-btn" in html
        assert "@click.stop=" in html
        # Phase 3d (2026-05-22): rating click-to-pick UI retired. The
        # "Lens pick" badge is sourced from the chairman's
        # routing_label.winner via lensPickProviderFor; no chooseMember
        # handler, no verifyPending / verifyFailed states, no
        # metadata.user_verdict hydration.
        assert "lensPickProviderFor" in html
        assert "routingLabelFor(seg)?.winner" in html
        assert "chooseMember" not in html
        assert "verifyPending" not in html
        assert "verifyFailed" not in html
        assert "Preferred</div>" not in html
        assert "user_verdict" not in html
        assert "user_winner" not in html
        assert "rate_council" not in html

        path = write_live_council_page()
        assert path.name == "live_council.html"
        assert path.exists()

    def test_live_page_lens_pick_badge_normalizes_both_slug_sides(self):
        """Trust-defect sweep (2026-06-09): the live council page's "Lens pick"
        badge gate compared the NORMALIZED chairman winner against the RAW member
        slug — `lensPickProviderFor(seg) === row.provider` resolved
        'claude' === 'claude_ai' → false, so the badge silently vanished on the
        41% of the founder's councils whose members carry web-capture slugs
        (claude_ai / gemini / chatgpt). The winner is normalized at the on-disk
        load boundary; the member rows are not. Fix: an isLensPick(seg,row) method
        that normalizes BOTH sides via normalizeProviderSlug.

        Mutation: revert the gate to a raw `=== row.provider` comparison, or drop
        normalizeProviderSlug from either operand, and this fails."""
        html = render_live_council_page()
        # The badge gate routes through the normalizing method, never a raw
        # winner===provider comparison.
        assert 'v-if="isLensPick(seg, row)"' in html
        assert "lensPickProviderFor(seg) === row.provider" not in html
        # isLensPick must normalize BOTH operands — the chairman winner AND the
        # raw member slug — or a web-capture council still loses its badge.
        assert "isLensPick(seg, row) {" in html
        assert "normalizeProviderSlug(String(this.lensPickProviderFor(seg))" in html
        assert "normalizeProviderSlug(String(row.provider" in html

    def test_thread_segment_round_number_is_manifest_authoritative(self, patch_trinity_home):
        """A thread segment's round number must come from the MANIFEST POSITION
        (1-based index in manifest order = chain order), not the per-entry
        `round_number` FIELD, which is unreliable.

        History: the 2026-06-02 fix seeded `roundNumber: entry.round_number || 1`
        from the manifest entry, assuming the FIELD was authoritative. Driving the
        founder's real 5-round thread on 2026-06-05 proved that assumption false —
        `update_thread_manifest` stamps `round_number == 1` for EVERY segment when
        the outcome carries none (and these don't), so the field is all-1 and the
        whole thread STILL mis-rendered as "Round 1 ×5". The manifest is
        authoritative for chain POSITION (the segments are built in manifest order),
        so the round number is the 1-based index. Fix: `Math.max(entry.round_number
        || 0, idx + 1)` — position is the floor; a higher round_number wins only on
        a gap (e.g. a deleted middle round). The real-behavior proof (all-1 manifest
        → Round 1..N) lives in test_council_chain_action_browser.py
        (mutation-verified); these substring checks are the fast-CI signal +
        browser_smoke Surface 9 confirms correctly-stamped threads still render 1..N.
        """
        html = render_live_council_page()
        # 1. makeSegment accepts a roundNumber param and uses it (not hardcoded 1).
        assert (
            "members=[], roundNumber=1}) {" in html
        ), "makeSegment must accept a roundNumber param (manifest round seed)"
        assert (
            "roundNumber: roundNumber || 1," in html
        ), "makeSegment must use the passed-in roundNumber, not a hardcoded 1"
        # 2. loadThread derives the round from the manifest POSITION (idx), with the
        #    entry.round_number FIELD only as an ahead-of-position override. The old
        #    field-trusting `entry.round_number || 1` form (which collapsed an all-1
        #    manifest to "Round 1 ×N") must be GONE.
        assert (
            "const roundNo = Math.max(entry.round_number || 0, idx + 1);" in html
        ), "loadThread must derive the round from the manifest POSITION (Math.max with idx+1)"
        assert (
            "roundNumber: roundNo," in html
        ), "loadThread's completed-segment branch must use the position-derived roundNo"
        assert (
            "roundNumber: entry.round_number || 1," not in html
        ), "the field-trusting `entry.round_number || 1` seed must be gone (all-1 manifest → Round-1×N)"
        # 3. The TWO segment-COMPLETION paths must FLOOR on the manifest-seeded
        #    position round number, never let the outcome/status
        #    metadata.round_number (the same all-1 degenerate field) collapse a
        #    correctly numbered Round 3 back to Round 1. Both mirror the build-path
        #    Math.max discipline. The earlier `field || current.roundNumber` form
        #    looked safe but still trusted the FIELD FIRST — on a real all-1 thread
        #    `1 || 3` resolved to 1, re-introducing the Round-1×N mis-render the
        #    instant each completed segment loaded (found 2026-06-18, the UX-sweep
        #    real-browser proof lives in
        #    test_live_council_chain_round_number_floor_browser.py).
        # 3a. _loadOutcomeIntoSegment (the ?thread_id= all-completed path).
        assert (
            "roundNumber: Math.max(rs.metadata?.round_number || 0, current.roundNumber || 1)," in html
        ), "the outcome-load completion path must FLOOR on the position-derived current.roundNumber"
        # 3b. the poll-completion branch (a live-streaming last round flips done).
        assert (
            "roundNumber: Math.max((status.metadata && status.metadata.round_number) || 0, ref.roundNumber || 1)," in html
        ), "the poll completion branch must FLOOR on the position-derived ref.roundNumber"
        # The two field-FIRST forms (`X || current.roundNumber || 1` /
        # `X.round_number || 1`) that let an all-1 thread collapse must be GONE.
        assert (
            "rs.metadata?.round_number || current.roundNumber || 1," not in html
        ), "the field-first `metadata.round_number || current.roundNumber` form must be gone (Round-1×N clobber)"
        assert (
            "(status.metadata && status.metadata.round_number) || 1," not in html
        ), "the blind poll-path `status.metadata.round_number || 1` round default must be gone (Round-1×N clobber)"

    def test_file_substrate_invariants_launchpad_and_council(self, patch_trinity_home):
        """Principle #2 — file:// is the production substrate (users open the page
        directly, not via a server). The status/outcome scripts load via <script>
        injection, so their base URLs MUST be relative: an absolute http://localhost
        base (or a leading '/') 404s under file://. The existing tests assert the KEY
        is present and the cache-buster expression exists, but NOT that the base URL
        VALUE is relative — a regression to an absolute base would break the
        documented substrate while passing every http-based test. Plus: Vue must be
        the IIFE build (ES `type=module` imports break on file://) and the runtime
        must carry the file:// cache-buster guard."""
        import re

        from trinity_local.launchpad_page import render_launchpad_html

        for label, html in (
            ("council", render_live_council_page()),
            ("launchpad", render_launchpad_html()),
        ):
            bases = re.findall(r'"(?:status|outcome)ScriptBaseUrl"\s*:\s*"([^"]*)"', html)
            assert bases, f"{label}: no *ScriptBaseUrl emitted"
            for b in bases:
                assert b.startswith(("./", "../")), (
                    f"{label}: base {b!r} is not relative — it 404s under file:// (principle #2)"
                )
                assert not b.startswith(("http://", "https://", "/")), (
                    f"{label}: absolute base {b!r} breaks the file:// substrate"
                )
            # file:// treats `?t=` as part of the literal filename → 404, so the
            # runtime must skip the cache-buster on file://.
            assert "location.protocol === 'file:'" in html, (
                f"{label}: missing file:// cache-buster guard"
            )
            # ES module imports silently break on file://; the IIFE build is the fix.
            assert "petite-vue.iife.js" in html and 'type="module"' not in html, (
                f"{label}: Vue must be the file://-safe IIFE build, not an ES module"
            )


# ---- CSS structural validity: <style> braces must balance ----
#
# render_unified_council_page shipped an unclosed `@media (max-width: 768px)`
# block (24 `{` opens vs 23 `}` closes — one missing `}`) that EVERY existing
# test was green for: none parsed the CSS. Browsers leniently auto-close at
# </style> so it rendered, but the malformed rule SWALLOWS any CSS added after
# it into the still-open media query, and the SERVED live shell
# (render_live_council_page) carries the same per-page <style> pattern with no
# guard. Pin brace balance on both council pages so this can't ship again.

def _style_blocks(html: str) -> list[str]:
    import re
    return re.findall(r"<style>(.*?)</style>", html, re.DOTALL)


def _css_braces_balanced(css: str) -> bool:
    depth = 0
    for ch in css:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False  # a `}` with no matching `{`
    return depth == 0


def _minimal_council_outcome():
    bundle = PromptBundle(
        bundle_id="b1", task_cluster_id="c1", task_text="pick one",
        goal="", comparison_instructions="", context_excerpt="",
        created_at="2026-06-01T00:00:00+00:00",
    )
    outcome = CouncilOutcome(
        council_run_id="council_1", bundle_id="b1", task_cluster_id="c1",
        primary_provider="claude", winner_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", model="opus", output_text="ok")
        ],
        synthesis_output="done", synthesis_prompt="p",
        created_at="2026-06-01T00:01:00+00:00",
    )
    return bundle, outcome


class TestCouncilPageCssIsWellFormed:
    def test_unified_page_style_braces_balanced(self):
        bundle, outcome = _minimal_council_outcome()
        blocks = _style_blocks(render_unified_council_page(bundle, outcome))
        assert blocks, "no <style> block found in the unified council page"
        for css in blocks:
            assert _css_braces_balanced(css), (
                "render_unified_council_page emitted unbalanced CSS braces — an "
                "unclosed rule/@media swallows everything declared after it"
            )

    def test_winner_reveal_present(self):
        """The winner-reveal delight: when a council lands, the chairman's pick
        glows in and a verdict banner restates the product promise. Guard the
        three load-bearing pieces so a future edit can't silently drop them."""
        html = render_live_council_page()
        assert "the answer you'd have picked" in html, (
            "live council page lost the winner verdict line"
        )
        assert "winner-verdict" in html and "@keyframes winner-glow" in html, (
            "live council page lost the winner-reveal CSS"
        )
        assert "'winner-reveal'" in html, (
            "winning member card lost its winner-reveal class binding"
        )

    def test_live_shell_style_braces_balanced(self):
        # The SERVED page (live_council.html). A future edit imbalancing its
        # <style> would malform the CSS users actually see.
        blocks = _style_blocks(render_live_council_page())
        assert blocks, "no <style> block found in the live council shell"
        for css in blocks:
            assert _css_braces_balanced(css), (
                "render_live_council_page (the SERVED council page) emitted "
                "unbalanced CSS braces"
            )
