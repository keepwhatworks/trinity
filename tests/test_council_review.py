"""Tests for council review HTML rendering."""
from __future__ import annotations

from trinity_local.council_review import (
    render_live_council_page,
    write_live_council_page,
)
from trinity_local.council_schema import CouncilMemberResult, CouncilOutcome, PromptBundle


class TestCouncilReviewMarkdown:
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
# A council page once shipped an unclosed `@media (max-width: 768px)` block
# (one missing `}`) that EVERY existing test was green for: none parsed the CSS.
# Browsers leniently auto-close at </style> so it rendered, but the malformed
# rule SWALLOWS any CSS added after it into the still-open media query. The
# SERVED live shell (render_live_council_page) carries the per-page <style>
# pattern; pin its brace balance so this can't ship again. (The static
# render_unified_council_page that originally carried the bug was removed in
# #311/#8; the live page is the only council-review surface that ships now.)

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
