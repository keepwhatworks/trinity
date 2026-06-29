"""#116 closer: empty-state CTA for cross-provider benchmark.

The eval-summary card only renders when has_results === true. Cold-install
users (and journalists) never see the "benchmark on YOUR corpus" wedge
until they've already run an eval. The empty-state card closes the gap
by surfacing the prompt + copy-chips even on a fresh install.

Two states the empty card has:
1. No eval set built yet → CTA points at `trinity-local eval-build`
2. Eval set built, no runs yet → CTAs point at `eval-run --target X`
   for each council provider (claude / codex / antigravity)
"""
from __future__ import annotations


def _render_with(eval_summary):
    from trinity_local.launchpad_template import render_launchpad_html
    return render_launchpad_html(
        page_data={"evalSummary": eval_summary},
    )


class TestEmptyStateRendersOnColdInstall:
    """When evalSummary.has_results is False, the empty-state card must
    appear with the cross-provider-benchmark framing."""

    def test_empty_state_card_renders_when_no_results(self):
        html = _render_with({
            "has_results": False,
            "eval_set_available": False,
        })
        assert "Cross-provider benchmark" in html  # eyebrow (no internal task # in rendered copy)
        assert "Score the 3 providers on YOUR corpus" in html
        # Wedge framing: global benchmarks vs YOUR corpus
        assert "Global benchmarks" in html
        assert "YOUR corpus" in html or "your own rejection" in html.lower()

    def test_populated_card_stays_gated_on_has_results(self):
        """Sanity: the populated card carries the proper v-if guard so
        Vue only mounts it when has_results === true. (We can't assert
        absence in the static HTML — both v-if branches are rendered to
        the template source; Vue's the runtime gate. We CAN assert the
        guard is wired correctly so a future refactor doesn't hoist
        the populated card out of its v-if.)"""
        html = _render_with({
            "has_results": False,
            "eval_set_available": False,
        })
        # The populated card's section opener must carry the has_results guard
        assert 'v-if="pageData.evalSummary && pageData.evalSummary.has_results"' in html, (
            "eval-summary-card v-if guard missing — would render even "
            "on cold installs without any benchmark data."
        )
        # And the empty-state card's guard must be the inverse
        assert 'v-if="pageData.evalSummary && !pageData.evalSummary.has_results"' in html, (
            "eval-empty-state-card v-if guard missing — would render "
            "alongside the populated card after the first benchmark run."
        )


class TestEvalBuildCtaWhenEvalSetMissing:
    """When eval_set_available is False, the user must build the set
    first — the CTA should point at eval-build, not eval-run."""

    def test_eval_build_chip_renders(self):
        html = _render_with({
            "has_results": False,
            "eval_set_available": False,
        })
        assert "trinity-local eval-build" in html
        # The Vue copy handler is wired
        assert "eval-build-empty" in html


class TestEvalRunCtaWhenEvalSetExists:
    """When eval_set_available is True but no runs yet, the CTA flips to
    eval-run with per-provider chips (claude / codex / antigravity)."""

    def test_three_provider_eval_run_chips_render(self):
        html = _render_with({
            "has_results": False,
            "eval_set_available": True,
        })
        assert "eval-run --target claude" in html
        assert "eval-run --target codex" in html
        assert "eval-run --target antigravity" in html
        # Per-chip copy-key invariant (avoids collision in the
        # copyText state machine — each chip needs a unique key)
        assert "eval-run-claude" in html
        assert "eval-run-codex" in html
        assert "eval-run-antigravity" in html

    def test_eval_build_branch_carries_inverse_guard(self):
        """When the eval set is already built, don't tell the user to
        build it again. Vue's the runtime gate, but we can assert the
        guard exists so a future refactor doesn't accidentally render
        the eval-build prompt alongside the eval-run chips. Post-2026-06-07
        the eval-build branch is the MIDDLE state (v-else-if) in the
        lens → eval-build → eval-run chain, so its guard is v-else-if."""
        html = _render_with({
            "has_results": False,
            "eval_set_available": True,
        })
        # The eval-build wrapper must carry the inverse guard (as v-else-if now —
        # it sits between the lens-lead State A and the eval-run State C).
        assert 'v-else-if="!pageData.evalSummary.eval_set_available"' in html, (
            "eval-build CTA missing its !eval_set_available guard — "
            "would render alongside the eval-run chips."
        )


class TestProviderSidePromptCta:
    """The eval-empty-state card surfaces a secondary path: if the user's
    rejections.jsonl is empty, ask each provider directly via
    `trinity-local eval-prompt | pbcopy` + `eval-import`. This pins
    those chips so a future cleanup doesn't quietly remove the
    provider-side loop discovery from the launchpad."""

    def test_eval_prompt_chip_renders_in_empty_state(self):
        html = _render_with({
            "has_results": False,
            "eval_set_available": False,
        })
        assert "trinity-local eval-prompt | pbcopy" in html
        assert "eval-prompt-copy" in html  # unique copyText key

    def test_eval_import_chip_renders_in_empty_state(self):
        html = _render_with({
            "has_results": False,
            "eval_set_available": False,
        })
        # The chip shows the canonical doc invocation: --provider <name>
        # in front of the JSON path. Asserting the prefix is enough —
        # tail filename is documentation, not contract.
        assert "trinity-local eval-import --provider claude" in html
        assert "eval-import-copy" in html


class TestProviderRegistryAlignment:
    """#116 surface must use the canonical 3 council providers. If
    CANONICAL_COUNCIL_PROVIDERS changes (e.g. a 4th provider joins),
    this test catches the drift before the launchpad ships stale chips."""

    def test_chips_match_canonical_council_providers(self):
        from trinity_local.registry import CANONICAL_COUNCIL_PROVIDERS

        html = _render_with({
            "has_results": False,
            "eval_set_available": True,
        })
        for provider in CANONICAL_COUNCIL_PROVIDERS:
            assert f"eval-run --target {provider}" in html, (
                f"Missing chip for council provider {provider!r}. "
                "CANONICAL_COUNCIL_PROVIDERS changed without updating "
                "the launchpad empty-state CTA. Add/remove chips in "
                "launchpad_template.py near eval-empty-state-card."
            )


class TestNoRetiredRejectionsFilenameInUserCopy:
    """#282 recurrence guard. `rejections.jsonl` was retired pre-launch (merged
    into the unified `preference_acts.jsonl` ledger), but the eval empty-state
    copy still told users to "build the eval set from your rejections.jsonl" and
    asked "Empty rejections.jsonl?" — directing real users at a file that no
    longer exists (the #233 doc-drift sweep missed the rendered launchpad). The
    fix uses concept-based copy ("your saved rejections" / "No rejections saved
    yet?"). Pin that the retired FILENAME can't creep back into the user-facing
    launchpad (the bare "rejections" CONCEPT is fine — only the `.jsonl` file is
    retired)."""

    def test_rendered_launchpad_has_no_retired_jsonl_filename(self):
        html = _render_with({"has_results": False, "eval_set_available": False})
        assert "rejections.jsonl" not in html, (
            "the retired rejections.jsonl filename leaked into the user-facing "
            "launchpad — the live store is preference_acts.jsonl; use concept copy"
        )

    def test_concept_based_empty_state_copy_present(self):
        html = _render_with({"has_results": False, "eval_set_available": False})
        # The "rejections" CONCEPT survives in the (post-2026-06-07) 3-state copy —
        # State B's eval-build step ("Build the eval set from your saved rejections")
        # and State A's lens lead ("it mines those rejections") — without the retired
        # rejections.jsonl filename.
        assert "Build the eval set from your saved rejections" in html
        assert "mines those rejections" in html
        # State A leads with `lens` (mines the rejections eval-build needs); the
        # eval-build command is still present for the rejections-exist state.
        assert "trinity-local lens" in html
        assert "trinity-local eval-build" in html
