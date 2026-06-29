"""The persistent council rail (build_recent_sidebar_html) — the ChatGPT/
claude.ai sidebar pattern the founder asked for (2026-06-01). Pins the link
shape + the brand-labeled winner so the rail stays consistent with the Elo
chart (no raw 'Chatgpt'/'Antigravity' harness slugs)."""
from __future__ import annotations

import re

from trinity_local.launchpad_data import build_recent_sidebar_html


def _winner_token_by_title(html: str) -> dict[str, str]:
    """Map each rendered rail row's lowercased data-title → the exact text inside
    its .rail-council-winner span. Lets a single multi-council render be
    value-asserted PER ROW (so a threshold/binding error on one row is caught
    even when a sibling row is correct)."""
    out: dict[str, str] = {}
    for m in re.finditer(r'<a [^>]*data-title="([^"]+)".*?</a>', html, re.S):
        block = m.group(0)
        win = re.search(r'rail-council-winner[^>]*>([^<]+)<', block)
        out[m.group(1)] = win.group(1) if win else ""
    return out


def _council(**kw) -> dict:
    base = {
        "council_id": "c1",
        "chain_root_id": "bundle_abc",
        "review_page_path": "/x/review_pages/council_abc.html",
        "title": "Should we ship the beta now?",
        "winner_provider": "chatgpt",
        "created_at": "2026-06-01T00:00:00+00:00",
    }
    base.update(kw)
    return base


def test_rail_row_links_to_review_page_with_thread_id():
    html = build_recent_sidebar_html([_council()])
    assert 'class="rail-council"' in html
    assert "/review_pages/council_abc.html?thread_id=bundle_abc" in html
    assert "Should we ship the beta now?" in html


def test_rail_rows_carry_lowercased_data_title_for_search():
    """The rail filter (founder 2026-06-01) matches the search query against the
    server-emitted data-title attr. Each row must carry it, lowercased, so the
    client-side substring search works without per-keystroke recompute."""
    html = build_recent_sidebar_html([_council(title="Launch on Hacker News?")])
    assert 'data-title="launch on hacker news?"' in html


def test_launchpad_render_includes_rail_search_box():
    """The full launchpad render must wire the rail-filter input + its JS so the
    sidebar search actually works (the rows alone aren't enough)."""
    from trinity_local.launchpad_page import render_launchpad_html
    html = render_launchpad_html()
    assert 'id="rail-filter"' in html
    assert "getElementById('rail-filter')" in html
    assert 'id="rail-no-match"' in html


def test_rail_winner_is_brand_not_raw_slug():
    """#275: the rail must show GPT/Gemini/Claude, never the fragmented harness
    slugs (chatgpt/antigravity/claude_ai) the founder flagged."""
    html = build_recent_sidebar_html([
        _council(council_id="a", chain_root_id="ra", winner_provider="chatgpt"),
        _council(council_id="b", chain_root_id="rb", winner_provider="antigravity"),
        _council(council_id="c", chain_root_id="rc", winner_provider="claude_ai"),
    ])
    # The winner brand renders inside the .rail-council-winner span (added with
    # the solo-council honesty fix — a 1-responder council shows "Solo" there
    # instead of a fake winner brand; these are real >=2-member contests, so the
    # brand stands). Assert the brand token, not the old `>GPT · ` byte-adjacency.
    assert ">GPT</span>" in html      # chatgpt → GPT
    assert ">Gemini</span>" in html   # antigravity → Gemini
    assert ">Claude</span>" in html   # claude_ai → Claude
    assert "Chatgpt" not in html and "Antigravity" not in html and "Claude_Ai" not in html


def test_rail_empty_state_when_no_councils():
    html = build_recent_sidebar_html([])
    assert "No councils yet" in html
    assert "rail-council" not in html


def test_rail_skips_councils_without_review_page():
    """A council with no review page can't be opened — it must not render a
    dead row (build_recent_sidebar_html's no-review-page guard)."""
    html = build_recent_sidebar_html([_council(review_page_path=None)])
    assert "rail-council" not in html


def test_rail_multi_round_chain_shows_round_count():
    """USEFULNESS (ux sweep): a multi-round refine/continue/auto-chain council
    collapses to ONE rail card whose thread_id link 'reveals every round on one
    scrollable page'. segment_count is computed + threaded all the way to the
    render — but the rail rendered a 3-round chain IDENTICALLY to a one-shot, so
    Trinity's signature iterate-to-convergence feature was invisible. The chain
    row must carry a visible round-count badge; a single council must NOT."""
    html = build_recent_sidebar_html([
        _council(council_id="chain", chain_root_id="rchain",
                 title="ORIGINAL question", segment_count=3),
    ])
    assert "rail-council-rounds" in html, (
        "a 3-round chain rendered with NO round-count badge — it looks identical "
        "to a single council, so the chain (refine/continue) feature is invisible "
        "in the rail"
    )
    assert "3 rounds" in html, (
        "the chain badge must name the round count (got no '3 rounds' text)"
    )


def test_rail_single_council_has_no_round_badge():
    """The round-count badge is a chain-only affordance — a one-shot council
    (segment_count 1, or missing) must NOT show it, or every council reads as a
    multi-round chain (the boy-who-cried-chain orphan-value failure)."""
    for kw in ({"segment_count": 1}, {}):  # explicit 1 AND missing both = one-shot
        html = build_recent_sidebar_html([_council(**kw)])
        assert "rail-council-rounds" not in html, (
            f"a single council ({kw or 'no segment_count'}) wrongly rendered a "
            f"round-count badge — only multi-round chains should: {html!r}"
        )
        assert "rounds" not in html


def test_rail_round_badge_excluded_from_search_data_title():
    """The rail-filter search matches the server-emitted data-title (lowercased
    title only). The round-count badge text must NOT leak into data-title, or a
    search for 'rounds' would spuriously match every chain card."""
    html = build_recent_sidebar_html([
        _council(council_id="chain", chain_root_id="rchain",
                 title="Cache strategy?", segment_count=4),
    ])
    # data-title is the lowercased TITLE only — never the badge copy.
    assert 'data-title="cache strategy?"' in html
    assert "rounds" not in 'data-title="cache strategy?"'


def test_rail_solo_marker_gates_on_distinct_provider_count_threshold():
    """USEFULNESS/USABILITY (ux sweep — derived-value SURFACE-BINDING class): the
    rail paints either an honest "Solo" marker OR the chairman's winner brand, and
    the ONLY thing deciding which is the `member_count <= 1` threshold
    (member_count = the latest round's DISTINCT-provider count, Iter 111). The
    threshold was value-tested NOWHERE at the rendered surface — every prior winner
    test used member_count=None (the legacy fall-through), so an off-by-one
    threshold (`< 1`, `<= 0`, `<= 2`) or a swapped field would paint a FAKE winner
    brand on a no-contest solo council (or hide a real winner) while every existing
    test stayed green. This is a DISCRIMINATING fixture: all four councils carry the
    SAME winner_provider ('chatgpt') and differ ONLY in member_count, so the painted
    token is driven solely by the threshold binding.

    Iter 269 STRENGTHENED this: member_count==0 is NOT solo — it's an ALL-FAILED
    council (every member failed → 0 distinct responders). The prior version folded
    n=0 into the n=1 "Solo" branch, so the rail painted "Solo" + the "Only ONE model
    answered" tooltip on a council where ZERO models answered — a flat lie (the
    #258 hand-editable-state class). n=0 now reads "Failed"."""
    html = build_recent_sidebar_html([
        # 1 distinct voice → no contest → honest "Solo", NOT the chairman's brand.
        _council(council_id="s1", chain_root_id="rsolo1", title="solo one voice",
                 winner_provider="chatgpt", member_count=1),
        # 0 voices → every member FAILED → "Failed", NOT "Solo" (zero answered).
        _council(council_id="s0", chain_root_id="rsolo0", title="all failed zero voices",
                 winner_provider="chatgpt", member_count=0),
        # 2 distinct voices → a real contest → the chairman's WINNER brand.
        _council(council_id="t2", chain_root_id="rcontest2", title="real contest two",
                 winner_provider="chatgpt", member_count=2),
        # legacy/imported outcome (no member_count) → can't prove solo → brand.
        _council(council_id="lg", chain_root_id="rlegacy", title="legacy no count",
                 winner_provider="chatgpt", member_count=None),
    ])
    tokens = _winner_token_by_title(html)
    # Precondition the assertions on all four rows actually rendering, so the
    # value checks below can't pass vacuously on a dropped row.
    assert set(tokens) == {
        "solo one voice", "all failed zero voices", "real contest two", "legacy no count"
    }, f"a rail row was dropped — value check would be vacuous: {tokens!r}"
    # The BITE: a 1-distinct-voice council MUST read "Solo" (no fake winner brand),
    # and a 2-distinct-voice council with the SAME winner MUST read the brand.
    assert tokens["solo one voice"] == "Solo", (
        "REGRESSION: a SOLO council (member_count=1, one distinct voice) painted a "
        f"fake winner brand {tokens['solo one voice']!r} in the rail instead of "
        "'Solo' — the no-contest overclaim the solo-honesty fix closed. The "
        "`member_count == 1` threshold binding is wrong."
    )
    # Iter 269 BITE: a 0-responder (all-failed) council MUST read "Failed", NOT
    # "Solo" — zero models answered, so the "Solo / one model answered" framing is
    # a flat lie. This is the discriminating n=0-vs-n=1 case.
    assert tokens["all failed zero voices"] == "Failed", (
        "FOUNDER SYMPTOM (Iter 269): a 0-responder ALL-FAILED council "
        f"(member_count=0) painted {tokens['all failed zero voices']!r} in the rail "
        "instead of 'Failed' — folding n=0 into the n=1 'Solo' branch makes the "
        "rail claim 'Only one model answered' on a council where ZERO answered."
    )
    assert tokens["real contest two"] == "GPT", (
        "REGRESSION: a REAL 2-distinct-voice contest (winner chatgpt) painted "
        f"{tokens['real contest two']!r} instead of the winner brand 'GPT' — the "
        "solo threshold wrongly swallowed a genuine contest's winner."
    )
    assert tokens["legacy no count"] == "GPT", (
        "REGRESSION: a legacy council with NO member_count must fall through to "
        f"the winner brand (can't prove solo), but painted {tokens['legacy no count']!r}."
    )
    # The "Solo / Only one model answered" tooltip must accompany ONLY the genuine
    # 1-responder solo row — NOT the 0-responder all-failed row (which must carry
    # the honest "Every model failed to respond" tooltip instead).
    assert html.count('Only one model') == 1, (
        "the 'Only one model answered' solo tooltip must render on EXACTLY the 1 "
        "genuine solo row (member_count==1), NOT the all-failed (member_count==0) "
        f"row: count={html.count('Only one model')}"
    )
    assert html.count('Every model failed to respond') == 1, (
        "the all-failed (member_count==0) row must carry the honest 'Every model "
        "failed to respond' tooltip, not the solo 'one model answered' one: count="
        f"{html.count('Every model failed to respond')}"
    )


def test_rail_reachable_below_breakpoint_as_drawer():
    """Regression (browser-found 2026-06-01; chat-UI redesign 2026-06-16): below the
    rail breakpoint the councils must stay REACHABLE — never display:none with no
    way to open them. The original fix made the rail flow in-page; the redesign
    makes it an off-canvas DRAWER (transform: translateX(-100%)) opened by the
    hamburger (body.rail-open) over a scrim — the claude.ai/chatgpt pattern. Same
    intent (reachable, not dropped), new mechanism."""
    from trinity_local.launchpad_page import render_launchpad_html

    html = render_launchpad_html()
    # The rail base rule must be an off-canvas drawer, not removed from the page.
    i = html.find(".council-rail {")
    assert i != -1, "council-rail base rule missing"
    base = html[i : html.find("}", i)]
    assert "display: none" not in base, "the rail must not be display:none (unreachable)"
    assert "translateX(-100%)" in base, "the rail must be an off-canvas drawer, not dropped"
    # And it must be openable: the hamburger toggle + the drawer-open rule both exist.
    assert "rail-toggle" in html, "the hamburger toggle that opens the drawer is missing"
    assert "body.rail-open .council-rail" in html, "the drawer-open (body.rail-open) rule is missing"


def test_comparison_charts_use_model_brand_not_raw_slug():
    """The eval + preference bar charts compare MODEL performance, so their legend
    labels must use the model trio (Claude/GPT/Gemini) via modelBrand() — like the
    Python _elo_chart_data sibling — not the raw-slug capitalize that rendered
    'Antigravity'/'Codex' (off the marketing trio; browser-found 2026-06-01)."""
    from trinity_local.launchpad_page import render_launchpad_html

    html = render_launchpad_html()
    assert "function modelBrand(" in html
    # both bar charts label datasets via modelBrand, none via raw capitalize
    assert html.count("label: modelBrand(provider)") == 2
    assert "label: provider.charAt(0).toUpperCase() + provider.slice(1)" not in html


def test_js_model_brand_mirrors_python_single_source():
    """modelBrand() in the template JS must stay in lockstep with the Python
    single-source _MODEL_BRAND_DISPLAY — drift here would split the brand the
    charts show from the brand the cards/Elo summary show."""
    from trinity_local.council_schema import _MODEL_BRAND_DISPLAY
    from trinity_local.launchpad_page import render_launchpad_html

    html = render_launchpad_html()
    for slug, brand in _MODEL_BRAND_DISPLAY.items():
        assert f"{slug}: '{brand}'" in html, f"JS modelBrand missing {slug}->{brand}"


def test_launchpad_grid_and_tables_shrink_at_every_width():
    """Horizontal-overflow regression (browser-measured 2026-06-01): the
    .launchpad-shell grid had no grid-template-columns, so its implicit column
    sized to the widest card's min-content (~982px) and a grid item's default
    `min-width: auto` kept it from shrinking — the launchpad scrolled sideways
    on a phone (~625px over) AND across the whole tablet/small-laptop range
    (769-1280px, below the rail-sidebar width). v1.7.182 scoped the fix to
    `max-width:768px`, which left that tablet dead zone overflowing (measured:
    114px @900, still over @1024). The fix is now UNCONDITIONAL — the shell
    column is minmax(0, 1fr) and the routing/cheat-sheet tables scroll inside
    their card at every width (a no-op when they fit on desktop, engaged the
    moment the card is narrower). Pin it so a future edit can't re-scope it to a
    breakpoint and re-open the dead zone — the HTTP browser_smoke only runs at
    desktop width, so nothing else guards the narrow-viewport behaviour."""
    from trinity_local.launchpad_page import render_launchpad_html

    html = render_launchpad_html()
    # minmax must live in the BASE .launchpad-shell rule (block = up to its
    # closing brace, so the comment length doesn't matter). If a future edit
    # moves it into a max-width media query, the base rule loses it and this
    # fails — which is the point: a breakpoint scope re-opens the 769-1280px
    # tablet/small-laptop overflow dead zone (v1.7.182's bug).
    shell = html.find(".launchpad-shell {")
    assert shell != -1, "launchpad-shell rule missing"
    base_rule = html[shell:html.find("}", shell)]
    assert "grid-template-columns: minmax(0, 1fr)" in base_rule, (
        "the BASE .launchpad-shell rule must use minmax(0, 1fr) so cards shrink "
        "at EVERY width — not gated behind a breakpoint (which re-opens the "
        "tablet/small-laptop sideways-scroll dead zone)"
    )
    # Wide tables scroll inside their card unconditionally.
    assert ".routing-table { display: block; overflow-x: auto; }" in html, (
        "routing-table must scroll inside its card (display:block + overflow-x:auto)"
    )
