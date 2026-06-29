"""Unit tests for memory_viewer's HTML rendering surface.

The viewer renders client-side from inlined JSON, so most behavior is
covered by Surface 14a/14b/16/17 in the browser smoke. These unit
tests guard the *template strings* themselves — a renamed CLI command
or a dropped CSS class would otherwise only surface in the browser
gate, which can be slow to attribute.

Same shape as test_memory_health.py: per-feature class, each test names
the contract being defended.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _render():
    from trinity_local.memory_viewer import render_memory_viewer_html
    return render_memory_viewer_html()


class TestViewerRebuildChip:
    """Tick #27 — persistent rebuild chip in viewer header. Always-on
    counterpart to the staleness chip — fires per-file even when
    _memory_health() reports no issues. The CSS class + per-file
    CLI mapping are template strings that drift silently if
    suggestionFor() is edited without updating the chip wiring."""

    def test_rebuild_chip_class_defined(self, isolated_home):
        html = _render()
        # Both CSS rule and JS construction must reference the class.
        assert ".viewer-rebuild-chip" in html, ".viewer-rebuild-chip CSS missing"
        assert '"viewer-rebuild-chip"' in html, (
            "renderHeader doesn't construct a .viewer-rebuild-chip button"
        )

    def test_rebuild_chip_copy_matches_launchpad_chips(self, isolated_home):
        """Tick #79 — the memory viewer rebuild chip must use the same
        '↻ Rebuild' copy as the launchpad lens-rebuild (tick #76) and
        cortex-rebuild (tick #77) chips on a POPULATED memory. Principle #11:
        shared UI primitives stay consistent across surfaces. Catches a
        regression that would drift the populated-file chip back to bare
        'Rebuild'.

        UX sweep iter 73 made the label state-aware: a NOT-built-yet file says
        '↻ Build' (nothing to re-build — see the per-file unbuilt branch); only
        a populated file keeps '↻ Rebuild'. Both variants must be present, and
        the post-flash reset must restore the SAME label (the `buildLabel` var),
        not a hardcoded one that would drift to '↻ Rebuild' on a built file's
        second click."""
        html = _render()
        # Populated-file label still matches the launchpad chips.
        assert '"↻ Rebuild"' in html, (
            "viewer-rebuild-chip lost the unified '↻ Rebuild' copy for a "
            "populated memory"
        )
        # The unbuilt-file variant — 'Rebuild' is a lie when nothing is built.
        assert '"↻ Build"' in html, (
            "viewer-rebuild-chip missing the '↻ Build' variant — a cold-install "
            "file would falsely say 'Rebuild' (UX sweep iter 73)"
        )
        # Reset-after-flash restores the captured label (not a hardcoded string),
        # so the text doesn't drift on a second click in either state.
        assert "textContent = buildLabel" in html, (
            "the post-flash reset no longer restores the captured buildLabel — "
            "text drifts to a fixed label on the second click"
        )

    def test_rebuild_command_template_uses_suggestion_helper(self, isolated_home):
        html = _render()
        # The chip text is built as "trinity-local " + suggestionFor(file.name).
        # We verify the prefix template AND the helper exists with the
        # expected per-file mapping (one assertion per memory keeps the
        # guard granular).
        assert '"trinity-local " + suggestionFor(file.name)' in html, (
            "rebuild chip no longer threads suggestionFor() — per-file mapping broken"
        )
        # suggestionFor itself must keep the canonical CLI names. If a
        # CLI is renamed, both this guard and Surface 18 catch it.
        for marker in (
            '"lens.md" || name === "topics.json") return "lens"',
            '"picks.json") return "consolidate"',
            # core.md was previously suggested via `distill`; flipped to
            # `dream` 2026-05-18 (iter #11) when distill CLI was hidden
            # but the rebuild chip was still emitting a now-dead command.
            '"core.md") return "dream"',
            # routing.json (the per-task-type provider scoreboard) is frozen to
            # disk by `dream` (freeze_routing_to_disk, dream.py) — NOT by
            # `consolidate` (which writes picks.json + the cortex patterns). It's
            # a tempting but wrong "fix" to make routing.json mirror picks.json's
            # consolidate; this guard catches that. Verified live 2026-06-01 on a
            # cold-start home (memory viewer "Not built yet. Run trinity-local …").
            '"routing.json") return "dream"',
            # vocabulary.md is rebuilt by its own `vocabulary` verb, not dream.
            '"vocabulary.md") return "vocabulary"',
        ):
            assert marker in html, f"suggestionFor mapping drifted: {marker}"

    def test_chip_lives_in_header(self, isolated_home):
        html = _render()
        # Sanity check the chip is wired inside renderHeader (so it shows
        # for every file), not inside a single Reader. If it slips into
        # one Reader, the markdown views (lens.md, core.md) lose it.
        header_idx = html.find("function renderHeader(file, isBuilt)")
        chip_idx = html.find('"viewer-rebuild-chip"')
        assert header_idx > 0 and chip_idx > header_idx, (
            "viewer-rebuild-chip is not constructed inside renderHeader — "
            "markdown views won't render it"
        )

    def test_every_suggested_verb_is_a_live_registered_cli_command(self, isolated_home):
        # The sibling guard above pins the per-file MAPPING strings, but a
        # source-grep can't tell whether "consolidate"/"vocabulary"/etc. still
        # NAME a real CLI verb. The cold-start memory viewer ("Not built yet.
        # Run `trinity-local <verb>`") is a brand-new user's first instruction;
        # if a command is renamed/retired but suggestionFor keeps the old name,
        # that user runs a DEAD command and the mapping-string test stays green.
        # This guard closes that: every verb suggestionFor emits must resolve to
        # a live subparser. (consolidate + vocabulary aren't even in the
        # collapsed top-level help, so only the live parser confirms them.)
        import argparse
        import re

        from trinity_local.main import build_parser

        html = _render()
        # _render() returns the FORMATTED html (the source's `{{` is already
        # collapsed to `{`), so slice from the function name rather than brace-
        # match. The body is ~13 lines; 800 chars covers it without spilling
        # into the next function's returns.
        idx = html.find("function suggestionFor(name)")
        assert idx > 0, "suggestionFor() not found in rendered viewer — wiring changed"
        block = html[idx : idx + 800]
        emitted = set(re.findall(r'return "([a-z][a-z-]*)"', block))
        # The mapping covers core/lens/topics/picks/routing/vocabulary; the
        # fallback `return "dream"` is included. Guard against an empty parse.
        assert {"lens", "consolidate", "dream", "vocabulary"} <= emitted, (
            f"suggestionFor parsed too few verbs ({emitted}) — regex/format drift"
        )

        registered: set[str] = set()
        for action in build_parser()._actions:
            if isinstance(action, argparse._SubParsersAction):
                registered |= set(action.choices)
        dead = sorted(v for v in emitted if v not in registered)
        assert not dead, (
            f"cold-start memory viewer suggests dead CLI command(s): {dead}. "
            f"Rename in suggestionFor() (memory_viewer.py) or restore the verb."
        )


class TestTopicLaunchChip:
    """Tick #28 — launch-council chip on topic graph node detail panels.
    Closes the topology action arc per the forward arc bullet 'click
    a basin → launch a council on this topic'."""

    def test_launch_chip_class_defined(self, isolated_home):
        html = _render()
        assert ".topics-launch-chip" in html, ".topics-launch-chip CSS missing"
        assert '"topics-launch-chip"' in html, (
            "showDetail doesn't construct a .topics-launch-chip button"
        )

    def test_launch_chip_uses_council_launch_cli(self, isolated_home):
        html = _render()
        # If the CLI is renamed (council-launch → run_council or similar),
        # the chip silently copies a broken command.
        assert "'trinity-local council --task \"'" in html, (
            "launch chip no longer copies trinity-local council --task — template drift"
        )

    def test_launch_chip_escapes_shell_metas(self, isolated_home):
        html = _render()
        # The escape chain must handle backslash + dquote + backtick +
        # dollar — anything less can break a bash paste with user
        # prompts that contain code-fence or variable expansion.
        # We check the regex literals are present in the rendered JS.
        # Each replace produces JS source `/<x>/g` after Python escape.
        assert ".replace(/\\\\/g," in html, "missing backslash escape"
        assert '.replace(/"/g,' in html, "missing double-quote escape"
        assert ".replace(/`/g," in html, "missing backtick escape"
        assert ".replace(/\\$/g," in html, "missing dollar escape"


class TestRepReplayChip:
    """Tick #29 — per-representative replay chip. Finer-grained than the
    basin-level launch chip (uses any rep's headline as the seed, not
    just the closest-to-centroid). The escape logic is now shared via
    escapeBashArg — DRY guard ensures the basin chip and rep chip stay
    in sync as the escape rules evolve."""

    def test_replay_chip_class_defined(self, isolated_home):
        html = _render()
        assert ".topics-rep-replay" in html, ".topics-rep-replay CSS missing"
        assert '"topics-rep-replay"' in html, (
            "renderThreadRep doesn't construct a .topics-rep-replay button"
        )

    def test_replay_chip_stops_propagation(self, isolated_home):
        html = _render()
        # Without stopPropagation, clicking the chip would also toggle the
        # surrounding li's expand state — bad UX, especially on multi-turn
        # threads. Guard the wiring.
        assert "event.stopPropagation()" in html, (
            "rep replay chip click handler missing stopPropagation — "
            "expand toggle will fire when the user only meant to copy"
        )

    def test_replay_chip_uses_shared_escape_helper(self, isolated_home):
        html = _render()
        # The basin chip + rep chip both wrap the seed in escapeBashArg
        # so they share one source of truth for shell metacharacter
        # escaping. If a refactor breaks this, the chips can drift on
        # whether `$` gets escaped or not — silently breaking one path.
        assert "function escapeBashArg" in html, "shared escapeBashArg helper missing"
        assert "escapeBashArg(seedText)" in html, (
            "basin-level launch chip no longer threads escapeBashArg — DRY broken"
        )
        assert "escapeBashArg(replaySeed)" in html, (
            "per-rep replay chip no longer threads escapeBashArg"
        )


class TestTopicToPickCrossLink:
    """Tick #30 — topology → picks cross-link. POST-COLLAPSE (#298) picks.json is
    keyed by the lens basin id (b00..), the SAME id space topics.json uses, so the
    bridge is a plain identity match (a basin links to the pick of the same id).
    Closes the forward-arc cross-memory navigation gap 'see a basin, jump to its
    pick'."""

    def test_pick_xlink_class_defined(self, isolated_home):
        html = _render()
        assert ".topics-pick-xlink" in html, ".topics-pick-xlink CSS missing"
        assert '"topics-pick-xlink"' in html, (
            "showDetail doesn't construct a .topics-pick-xlink anchor"
        )

    def test_reverse_map_uses_identity_basin_id(self, isolated_home):
        """POST-COLLAPSE (#298): the picks→topology bridge is a plain identity
        match — picks are keyed by the lens basin id (b00..), the SAME id space
        topics.json uses, so a pick links to the topology basin of the same id.
        (Pre-collapse the bridge was a centroid cosine because picks were keyed by
        a task_type label and carried a separate basin_centroid; that centroid is
        gone.)"""
        html = _render()
        assert "basinToPickTask" in html, "basin→pick map missing"
        # The bridge must gate on a live pick (carries a winner), not a stale
        # centroid; and key on the basin id directly.
        assert "topologyIds.has(basinId)" in html, (
            "reverse map no longer does the identity (basin-id) match — the "
            "post-collapse picks key by lens basin id, so the bridge is identity"
        )
        assert "pick.winner" in html, (
            "bridge no longer gates on a live pick (pick.winner) — a legacy "
            "RoutingPattern entry must NOT bridge"
        )
        # The dead centroid path must be gone — a stale pick.basin_centroid read
        # would silently orphan every pick (the new picks carry no centroid).
        assert "pick.basin_centroid" not in html, (
            "matchBasinsToPicks still reads the deleted pick.basin_centroid"
        )

    def test_no_residual_centroid_threshold_in_bridge(self, isolated_home):
        """The cosine SIM_THRESHOLD gate was part of the deleted centroid bridge;
        the identity match needs no threshold. Guard that no stale threshold magic
        number lingers in the matchBasinsToPicks bridge."""
        html = _render()
        # The bridge function body is between its declaration and the next blank
        # function — assert it does NOT reference a similarity threshold.
        assert "const SIM_THRESHOLD =" not in html, (
            "a residual SIM_THRESHOLD assignment lingers — the identity bridge "
            "applies no cosine threshold"
        )

    def test_xlink_targets_picks_reader_with_task_param(self, isolated_home):
        html = _render()
        # Link must go to picks.json viewer with the ?task= deep-link
        # so the picks Reader scrolls to + highlights the right card.
        assert 'memory.html?file=picks.json&task=" + encodeURIComponent(pickTask)' in html, (
            "topic→pick xlink doesn't target the picks Reader's ?task= deep-link"
        )

    def test_detail_shows_routing_winner_inline(self, isolated_home):
        """Post-#298 a topology basin IS a routing unit, so the basin detail
        surfaces the basin's chairman-WINNER (+ margin) inline — not just a bare
        'Routing rule: <id> →' link — saving a click into the picks Reader.
        Uses providerBrand (#275, Iter 79: brands codex→GPT / antigravity→Gemini
        so the display matches the picks Reader's 'Use <brand>' AND the launchpad
        cheat-sheet). The rendered-DOM brand is guarded in
        test_topology_node_click_routing_winner_browser; this is the cheap
        source-presence sibling."""
        html = _render()
        # matchBasinsToPicks must expose winner+margin per basin.
        assert "basinIdToPick" in html, (
            "matchBasinsToPicks no longer exposes basinIdToPick — the topology "
            "detail can't show the routing winner inline"
        )
        # Refactor-robust: pin the two LOAD-BEARING substrings independently
        # rather than one contiguous concatenation. Iter 257's providerBrand
        # non-string coercion split the expression into a `pickBrand` intermediate
        # ("Routes to " + pickBrand, where pickBrand = providerBrand(pick.winner)),
        # so the old contiguous match drifted while the feature stayed intact. The
        # rendered-DOM brand is guarded in
        # test_topology_node_click_routing_winner_browser; this cheap source sibling
        # just pins (a) the inline "Routes to " label vs a regression to the opaque
        # "Routing rule: <id> →", and (b) the #275 providerBrand mapping on
        # pick.winner vs a raw slug.
        assert '"Routes to "' in html, (
            "topology basin detail no longer shows the routing winner inline "
            "(regressed to the opaque 'Routing rule: <basin_id> →' label)"
        )
        assert "providerBrand(pick.winner)" in html, (
            "topology basin detail lost the #275 brand mapping on the routing "
            "winner (raw slug instead of providerBrand(pick.winner))"
        )
        # The margin is rendered via fmtMargin (the server-pre-formatted Python
        # 2dp string), NOT JS toFixed(2) — toFixed rounds half-UP and disagrees
        # with the CLI/launchpad banker's round on an exact dyadic tie (0.625 →
        # "0.63" vs "0.62"). The rendered margin value is guarded across surfaces
        # in test_launchpad_picks_margin_rounding_matches_python_browser.
        assert "fmtMargin(pick.margin)" in html, (
            "winner margin (the confidence proxy) not shown inline in the detail"
        )
        assert "pick.margin.toFixed(2)" not in html, (
            "topology basin detail re-rounds the margin with JS toFixed(2) "
            "(round-half-up) — must use fmtMargin so it matches the CLI/launchpad "
            "(Python banker's) on an exact-tie margin like 0.625"
        )

    def test_malformed_picks_doesnt_crash_topology(self, isolated_home):
        # The try/except around JSON.parse is load-bearing — without it,
        # a corrupt picks.json would take down the topology view entirely.
        # Logic now lives in loadCrossMemoryMaps (shared by both Readers).
        html = _render()
        assert "function loadCrossMemoryMaps" in html, "shared cross-memory loader missing"
        assert 'JSON.parse(raw)' in html, "picks parse step missing in shared loader"
        assert "catch (_)" in html, "missing graceful-degradation try/catch around picks parse"


class TestPickBasinNodeStyling:
    """Tick #31 — visual marker on topology basin nodes that have
    crystallized into routing rules. Complements the in-panel chip
    from tick #30 so the user sees the routing-rule basins at a
    glance without having to click each node."""

    def test_pick_basin_class_defined(self, isolated_home):
        html = _render()
        # CSS rule with both .node.pick-basin and a hover variant — the
        # styling must layer on top of the existing .node base so the
        # circle fill (hsl gradient) still reads.
        assert ".topics-graph-svg .node.pick-basin" in html, (
            "pick-basin CSS rule missing — pick-bearing nodes won't visually differ"
        )
        assert ".topics-graph-svg .node.pick-basin:hover" in html, (
            "pick-basin :hover variant missing — hover state will fall back to base"
        )

    def test_node_class_keyed_on_pick_map(self, isolated_home):
        html = _render()
        # The node-class lookup must read from basinToPickTask (the
        # centroid-matched map built tick #30) — if it reads from
        # any other source (e.g. matching basin.id to pick-key
        # directly) the visual encoding will silently mis-mark nodes.
        assert "basinToPickTask.has(d.id)" in html, (
            "node class lookup doesn't query basinToPickTask — visual marker "
            "will drift from the in-panel chip"
        )

    def test_tooltip_surfaces_routing_rule(self, isolated_home):
        html = _render()
        # The native SVG <title> tooltip should surface the routing rule
        # on hover for pick-bearing basins — passive discovery without
        # having to click the node.
        assert "basinToPickTask.get(d.id)" in html, "tooltip doesn't read the pick map"
        assert "Routing rule:" in html, "tooltip text drift — missing routing-rule label"


class TestPicksToTopologyCrossLink:
    """Tick #32 — picks → topology cross-link. Completes the
    bidirectional bridge tick #30 opened. Each pick card now renders
    'View in topology →' targeting topics.html?basin=<id>, and the
    topology view auto-opens the matching basin via the ?basin=
    deep-link param."""

    def test_shared_centroid_match_helper_extracted(self, isolated_home):
        # The centroid match logic was duplicated potential — extracted
        # into matchBasinsToPicks so both Reader directions can't drift.
        html = _render()
        assert "function matchBasinsToPicks" in html, (
            "shared centroid-match helper missing — picks Reader + topology view "
            "will compute the match independently and drift"
        )
        # Returns BOTH directions so each Reader can grab the one it needs.
        assert "basinIdToTask" in html, "shared helper doesn't expose basinIdToTask"
        assert "taskToBasinId" in html, "shared helper doesn't expose taskToBasinId"

    def test_picks_reader_renders_topology_xlink(self, isolated_home):
        html = _render()
        # The xlink template targets topics.json with ?basin=<id>. If
        # the URL contract changes, the deep-link handler in topology
        # view stops opening the right basin.
        assert "View in topology" in html, "topology xlink label missing"
        assert 'memory.html?file=topics.json&basin=" + encodeURIComponent(topologyBasinId)' in html, (
            "topology xlink template drifted from ?basin= deep-link contract"
        )

    def test_topology_view_handles_basin_deep_link(self, isolated_home):
        html = _render()
        # The handler must read ?basin from URL and call showDetail on
        # the matching node. Without it, the picks Reader's xlink lands
        # on a graph with no panel open — confusing UX.
        assert 'params.get("basin")' in html, "topology view doesn't read ?basin= from URL"
        assert "nodes.find(n => n.id === focusBasin)" in html, (
            "topology view's basin deep-link doesn't lookup the matching node"
        )
        # The handler should also call highlightNeighborhood so the
        # selected basin's local neighborhood pops out — same UX as a
        # manual click on the node.
        assert "highlightNeighborhood(match.id)" in html, (
            "?basin= deep-link doesn't highlight neighborhood — selected "
            "basin will be visually indistinguishable from siblings"
        )


class TestRoutingToTopologyCrossLink:
    """Tick #33 shipped a routing → topology chip via the shared taskToBasinId
    map. POST-COLLAPSE (#298) that map keys by basin_id (b00..), NOT task_type,
    so the routing reader's `.get(<task_type>)` always missed — the chip never
    rendered (a phantom bridge). Removed; routing.json stands alone as the
    per-task-type track record. The valid basin bridges are picks↔topology +
    topics↔picks. Guard the phantom can't be re-added and silently dead-link."""

    def test_routing_topology_chip_removed(self, isolated_home):
        html = _render()
        assert '"routing-topology-chip"' not in html, (
            "renderRoutingReader re-constructed a routing-topology chip — "
            "post-#298 it's a phantom (basin_id-keyed map can't match a "
            "task_type), so it always renders a dead/absent link"
        )
        assert "topoBasinId" not in html, (
            "the phantom routing→basin lookup is back (topoBasinId from a "
            "basin_id-keyed map queried by task_type)"
        )


class TestChairmanBasinLabelFallback:
    """Tick #49 — viewer prefers `basin.label` (chairman-derived) over
    representative-headline truncation over top_terms. Older topics.json
    files written before the labeler stage have no .label and fall
    through the chain. Guards the fallback ordering so a future
    refactor can't silently change which signal wins."""

    def test_labelFor_prefers_chairman_label(self, isolated_home):
        html = _render()
        # Branch order matters: label first, then reps[0], then top_terms.
        assert "if (b.label)" in html, (
            "labelFor must check b.label FIRST so chairman semantics win "
            "over heuristic truncation of representative text"
        )

    def test_tooltipFor_prefers_chairman_label(self, isolated_home):
        html = _render()
        # Same guard for the SVG <title> tooltip.
        assert "tooltipFor" in html, "tooltipFor helper missing"
        # The label-first branch in tooltipFor is right above the legacy chain.
        idx = html.find("function tooltipFor")
        assert idx > 0
        nearby = html[idx:idx + 400]
        assert "b.label" in nearby, "tooltipFor doesn't read b.label"

    def test_basin_dataclass_carries_label_fields(self, isolated_home):
        """Round-trip: Basin → to_dict → JSON → load_basins → Basin
        must preserve the chairman fields (label / intent_type / language)."""
        from trinity_local.me.basins import Basin, save_basins, load_basins
        b = Basin(
            id="b00",
            size=10,
            top_terms=["one", "two"],
            centroid=[0.1, 0.2],
            prompt_ids=["p1"],
            thread_count=3,
            label="Brainstorming for short-form social media",
            intent_type="creative",
            language="en",
        )
        save_basins([b])
        loaded = load_basins()
        assert len(loaded) == 1
        assert loaded[0].label == "Brainstorming for short-form social media"
        assert loaded[0].intent_type == "creative"
        assert loaded[0].language == "en"


class TestStaleBasinBanner:
    """Tick #40 — ?basin=<id> deep-link gracefully handles a stale
    reference (basin no longer in topology, e.g. lens-build was
    re-run with different cluster count). Shows a warm-warning
    banner with a rebuild chip; without this, the link landed
    silently with no panel open and no feedback to the user."""

    def test_stale_basin_branch_present(self, isolated_home):
        html = _render()
        # The else-branch of the focusBasin handler must surface a
        # not-found banner. Guard the marker so a future refactor
        # doesn't silently drop the user-feedback path.
        assert 'not in the current topology' in html, (
            "stale-basin banner copy missing — ?basin= mismatches will "
            "land silently with no user feedback"
        )
        # The rebuild chip should copy the lens-build CLI when clicked.
        assert "trinity-local lens" in html, (
            "stale-basin banner doesn't surface the rebuild CLI chip"
        )

    def test_stale_basin_reuses_health_banner_classes(self, isolated_home):
        # Reuses the .viewer-health-banner + .viewer-health-cmd classes
        # so the stale notice looks identical to the picks Reader's
        # "not yet" banner. Same shape, same color, same affordances —
        # one CSS rule covers both surfaces.
        html = _render()
        # The handler constructs the banner using the same classes;
        # if either constructor drifts, the stale notice would render
        # unstyled.
        idx_handler = html.find("not in the current topology")
        assert idx_handler > 0, "stale-basin handler not present in JS"
        # Find the nearest preceding viewer-health-banner construction —
        # confirms the stale path uses the same DOM shape.
        nearby = html[max(0, idx_handler - 800):idx_handler]
        assert '"viewer-health-banner"' in nearby, (
            "stale-basin banner not built with .viewer-health-banner class — "
            "visual drift from the picks Reader's matching banner"
        )


class TestBasinHoverTitleHelper:
    """Tick #39 — JS-side basinHoverTitle helper mirrors the Python
    _topology_basin_labels + Vue basinHoverLabel. Renders 'Basin
    <id> — <terms>' when topics.json carries top_terms, otherwise
    falls back to 'Open basin <id> in the topology graph'. Used by
    the picks→topology xlink (tick #32) and routing→topology chip
    (tick #33) so all four launchpad/viewer chips agree on hover."""

    def test_helper_function_defined(self, isolated_home):
        html = _render()
        assert "function basinHoverTitle" in html, (
            "basinHoverTitle helper missing — viewer chips will fall back "
            "to opaque 'Open basin <id>' tooltips"
        )

    def test_basinLabels_attached_to_cross_memory_maps(self, isolated_home):
        html = _render()
        # loadCrossMemoryMaps must expose basinLabels alongside the
        # task↔basin maps so both Reader views get a consistent
        # source of truth.
        assert "maps.basinLabels = basinLabels" in html, (
            "loadCrossMemoryMaps doesn't attach basinLabels — viewer "
            "chips can't access the basin → top-terms map"
        )

    def test_picks_xlink_uses_basinHoverTitle(self, isolated_home):
        html = _render()
        assert "basinHoverTitle(topologyBasinId, basinLabels)" in html, (
            "picks card 'View in topology →' xlink no longer threads "
            "basinHoverTitle — hover text reverts to opaque"
        )

    def test_routing_reader_has_no_phantom_basin_bridge(self, isolated_home):
        """Post-#298 the routing reader's task_type→basin bridge is a PHANTOM:
        loadCrossMemoryMaps keys taskToBasinId by basin_id (b00..), so
        `.get(<task_type>)` always misses — the old routing→picks task link and
        routing→topology chip resolved to dead 'No pick' banners. Guard the
        phantom is gone (so it can't be re-added and silently dead-link again).
        The valid bridges are picks↔topology + topics↔picks (all basin-keyed)."""
        html = _render()
        assert "routingTaskToBasinId" not in html, (
            "routing reader re-introduced the phantom task_type→basin map "
            "(keyed by basin_id → can't match a task_type → dead cross-links)"
        )
        assert 'el("a", "routing-task-link"' not in html, (
            "routing row re-added a task→picks anchor — picks.json keys by "
            "basin_id now, so a raw task_type link lands on a dead banner"
        )


class TestPicksReaderCrossLinks:
    """Picks Reader cross-links post-#298. The picks→routing link was REMOVED:
    picks.json keys by basin_id (b00..), a basin spans many task_types, so a
    `routing.json&task=<basin_id>` link landed every card on a dead 'No routing
    data' banner. The valid cross-ref is the basin-keyed topology link."""

    def test_no_dead_picks_to_routing_xlink(self, isolated_home):
        html = _render()
        # The dead picks→routing cross-link must stay gone (regression guard).
        assert "View routing scores" not in html, (
            "picks card re-added the dead picks→routing link (basin_id used "
            "as a routing task_type → dead 'No routing data' banner)"
        )
        assert "memory.html?file=routing.json&task=" not in html, (
            "picks card re-added a routing.json&task=<basin_id> link — dead "
            "post-#298 (routing.json keys by task_type, not basin_id)"
        )
        # The valid, basin-keyed topology cross-link stays.
        assert "View in topology" in html, "picks card lost its topology link"
        assert "memory.html?file=topics.json&basin=" in html

    def test_pick_xlink_class_styled(self, isolated_home):
        html = _render()
        assert ".pick-xlink" in html, "pick-xlink CSS missing"


class TestMarkdownSanitizer:
    """XSS hardening — renderMarkdown adopts marked()'s output into the
    live DOM. Beyond stripping script/style/iframe/object/embed tags, the
    sanitizer must also drop on*= event-handler attributes and confine
    href/src to http/https/mailto so a hand-edited (or imported) memory
    file can't smuggle an onclick handler or a javascript:/data: URL."""

    def test_strips_event_handler_attributes(self, isolated_home):
        html = _render()
        # The sanitizer walks every element and removes on*= attributes.
        assert 'name.startsWith("on")' in html, (
            "renderMarkdown no longer strips on*= event-handler attributes "
            "— stored-XSS via onclick/onerror is reintroduced"
        )

    def test_restricts_href_and_src_schemes(self, isolated_home):
        html = _render()
        # href/src must be gated on an http/https/mailto allowlist, which
        # drops javascript: and data: URLs.
        assert "/^(https?:|mailto:)/i" in html, (
            "renderMarkdown lost the href/src scheme allowlist — "
            "javascript:/data: URLs are no longer stripped"
        )
        assert '(name === "href" || name === "src")' in html, (
            "renderMarkdown no longer gates href/src attributes by scheme"
        )


class TestInlineScriptInjection:
    """A memory file whose content contains "</script>" (common when notes
    discuss HTML/JS) must NOT break the page's inline <script> data block.

    Regression for the 2026-05-31 E2E find: the memory viewer threw
    "SyntaxError: Invalid or unexpected token" because a "</script>" in the
    user's lens closed the inline <script> tag mid-JSON. Fix escapes "<" so
    the browser can't see a closing tag inside the data.
    """

    def test_script_close_tag_in_memory_is_escaped(self, isolated_home):
        (isolated_home / "memories" / "lens.md").write_text(
            "tension: web\nexample: <script>alert(1)</script> inside </script> content\n",
            encoding="utf-8",
        )
        html = _render()
        assert "alert(1)</script>" not in html, (
            "memory content's </script> leaked unescaped into the page — it would "
            "close the inline <script> data block and break the viewer's JS"
        )
        assert "\\u003c/script>" in html, (
            "the < in the memory content's </script> must be escaped to \\u003c"
        )


def test_memory_viewer_is_responsive_below_rail_breakpoint():
    """Narrow-viewport overflow regression (browser-measured 2026-06-01): the
    memory viewer shipped with NO @media queries — a fixed `240px 1fr` nav/content
    grid where the content grid-item's default `min-width: auto` let it expand to
    its widest child's min-content (a long JSON line, the routing-table), so pages
    scrolled sideways on a phone (~718px over) AND the whole tablet/small-laptop
    range (240px nav + ~789px table doesn't fit until ~1093px). v1.7.183 scoped
    the fix to `max-width:768px`, leaving the tablet dead zone overflowing
    (measured: 193px @900, 69px @1024). The breakpoint is now 1179px (matching the
    launchpad rail-collapse): below it, stack the layout (grid → 1fr), let the
    content shrink (`min-width: 0` — only then does the inner pre.body's existing
    overflow-x:auto engage), and scroll the routing/markdown tables; ≥1180px keeps
    the 2-column. Pin it so a future edit can't re-scope it to 768 and re-open the
    dead zone — the HTTP browser_smoke only runs at desktop width."""
    html = _render()
    assert "@media (max-width: 1179px)" in html, (
        "memory viewer lost its narrow-viewport media query — pages overflow "
        "sideways on phones AND tablets/small laptops"
    )
    mq = html.find("@media (max-width: 1179px)")
    block = html[mq : mq + 400]
    assert ".layout { grid-template-columns: 1fr; }" in block, (
        "narrow .layout must stack to a single column (else the 240px nav squeezes "
        "the content until its min-content overflows the page)"
    )
    assert ".content { min-width: 0;" in block, (
        "narrow .content needs min-width: 0 so the grid item can shrink and the "
        "inner pre.body actually scrolls instead of widening the page"
    )
    assert ".routing-table { display: block; overflow-x: auto; }" in block, (
        "narrow routing-table must scroll inside its column, not widen the page"
    )


class TestTopicsPayloadSlim:
    """memory.html inlines topics.json verbatim into a client script. Measured
    2026-06-02 (real corpus): topics.json was ~2.2MB / 79% of a 2.8MB memory.html,
    and each basin's `prompt_ids` (thousands of opaque ids — read client-side ONLY
    for `.length`, a stale-topology check) was ~half of it. `_slim_topics_for_viewer`
    replaces the array with a `prompt_id_count` int before inlining; the on-disk
    file is untouched. Real-browser verified: 2.8MB→1.59MB, basins render, 0 console
    errors, the staleness check still fires off the count.
    """

    def _seed_topics(self, home, *, with_ids=True):
        import json
        basin = {
            "id": "b00", "size": 3, "thread_count": 2,
            "top_terms": ["alpha", "beta", "gamma"],
            "centroid": [0.1, 0.2, 0.3],  # bridge input — must survive
            "representatives": [{"text": "a representative thread"}],  # displayed — must survive
            "label": "Alpha basin", "intent_type": "", "language": "en",
        }
        if with_ids:
            basin["prompt_ids"] = ["id_aaaaaaaa", "id_bbbbbbbb", "id_cccccccc"]
        (home / "memories" / "topics.json").write_text(
            json.dumps({"basins": [basin]}), encoding="utf-8")

    def test_slim_replaces_prompt_ids_with_count(self):
        from trinity_local.memory_viewer import _slim_topics_for_viewer
        import json
        contents = {"topics.json": json.dumps({"basins": [
            {"id": "b00", "size": 3, "prompt_ids": ["x", "y", "z"],
             "centroid": [0.1, 0.2], "representatives": [{"text": "r"}],
             "top_terms": ["a"]},
        ]})}
        slim = _slim_topics_for_viewer(contents)
        b = json.loads(slim["topics.json"])["basins"][0]
        assert b["prompt_id_count"] == 3, "count must equal the original list length"
        assert "prompt_ids" not in b, "the id array must be dropped"
        # Bridge + display fields must survive untouched.
        assert b["centroid"] == [0.1, 0.2]
        assert b["representatives"] == [{"text": "r"}]
        assert b["top_terms"] == ["a"]
        # And it must actually be smaller.
        assert len(slim["topics.json"]) < len(contents["topics.json"])

    def test_slim_is_tolerant_of_bad_topics(self):
        from trinity_local.memory_viewer import _slim_topics_for_viewer
        # None / malformed / wrong-shape must pass through unchanged (the viewer
        # must render even with a corrupt topics.json — graceful degradation).
        for bad in (None, "{not json", '{"basins":"oops"}', '{"other":1}'):
            assert _slim_topics_for_viewer({"topics.json": bad}) == {"topics.json": bad}

    def test_rendered_html_drops_id_arrays_keeps_count(self, isolated_home):
        """End-to-end: the rendered memory.html must NOT carry the opaque
        prompt_ids, but MUST carry prompt_id_count. Mutation: remove the
        _slim_topics_for_viewer call → the id strings reappear in the HTML."""
        self._seed_topics(isolated_home, with_ids=True)
        html = _render()
        # The opaque ids must not be inlined anywhere in the page.
        assert "id_aaaaaaaa" not in html, "prompt_ids array leaked into memory.html"
        # The count summary must be present (the staleness check reads it).
        assert "prompt_id_count" in html
        # Displayed/bridge fields still inlined (inner JSON quotes are escaped
        # by the outer json.dumps, so match the bare key).
        assert "representative thread" in html
        assert "centroid" in html

    def test_staleness_check_reads_the_count(self, isolated_home):
        """The client stale-topology check must consult prompt_id_count (the
        slimmed form), not only a literal prompt_ids array — else the warning
        silently dies after the slim. Pin the JS path."""
        self._seed_topics(isolated_home, with_ids=True)
        html = _render()
        assert "b.prompt_id_count" in html, (
            "client must read prompt_id_count after the array is dropped"
        )


class TestColdStartMemoryViewer:
    """Cold-start guard for the memory viewer — the SECOND surface a new user
    explores ("see what Trinity learned about me"), right after the launchpad.
    The launchpad has a cold-start guard (test_frontend_flow.py::
    test_write_portal_html_cold_start_no_data), but the memory viewer — which
    renders entirely different surfaces (lens/topics/picks readers + the d3
    topology graph) off six memory files that DON'T EXIST on a fresh install —
    had none. Browser-verified clean 2026-06-02 on a real empty home (0 console
    errors, topology correctly skips 0 basins); this pins it at the render level.

    Per principle #2 (file:// is the substrate) + #15 (silence is the all-good
    state): the cold viewer must render without Python exceptions, inline `null`
    for each missing memory (not the string "undefined"/"[object Object]"), and
    leave the topics payload empty so the client topology graph gets 0 basins to
    crash on — NOT a half-built value.
    """

    def _cold_render(self, tmp_path, monkeypatch):
        # A TRULY empty home — not even a memories/ dir (the most pessimistic
        # first-run state). isolated_home creates memories/; this is barer.
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        from trinity_local.memory_viewer import render_memory_viewer_html
        return render_memory_viewer_html()  # raises if the cold render path throws

    def test_cold_render_has_no_unresolved_leaks(self, tmp_path, monkeypatch):
        html = self._cold_render(tmp_path, monkeypatch)
        # The viewer builds its DOM client-side via el() calls, so the static HTML
        # carries the JS SOURCE — which legitimately NAMES leak tokens like
        # "[object Object]" inside `//` comments documenting the providerBrand
        # non-string coercion guards (Iter 257). A comment that names the token it
        # PREVENTS is not a render leak; strip comments (HTML + JS block + JS line,
        # the latter only when `//` follows start-of-line/whitespace so a `https://`
        # URL survives) before the check, so the assertion bites a REAL unresolved
        # token in executable/rendered content — not a defensive comment.
        import re
        stripped = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
        stripped = re.sub(r"(^|\s)//[^\n]*", r"\1", stripped, flags=re.M)
        # The classic "render broke / data assumed to exist" signals.
        for token in ("{{ undefined }}", "[object Object]", ">undefined<", ": undefined"):
            assert token not in stripped, f"cold memory viewer leaked {token!r}"

    def test_cold_inlines_null_for_each_missing_memory(self, tmp_path, monkeypatch):
        """Each of the six memory files is absent on a fresh install — the inlined
        __TRINITY_MEMORIES__ payload must carry JSON null (the viewer's
        empty-state path), never a stringified 'undefined'/'None'."""
        html = self._cold_render(tmp_path, monkeypatch)
        assert "__TRINITY_MEMORIES__" in html, "the memory payload must be inlined"
        # Pull the inlined payload and confirm every file resolves to null/None.
        from trinity_local.memory_viewer import _read_memory_contents, _slim_topics_for_viewer
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        cold = _slim_topics_for_viewer(_read_memory_contents())
        assert cold, "cold contents dict should still enumerate the file slots"
        assert all(v is None for v in cold.values()), (
            f"cold memory files must read as None, got: "
            f"{ {k: type(v).__name__ for k, v in cold.items()} }"
        )
        # The Python string "None" must never be what gets inlined for a missing
        # file (that would render literally in the raw-JSON view).
        assert ">None<" not in html

    def test_cold_topology_has_zero_basins_to_render(self, tmp_path, monkeypatch):
        """With no topics.json, the client topology graph must receive an
        empty/absent basin set — 0 nodes for d3-force, which (browser-verified)
        renders no SVG rather than throwing. Guard the data contract that feeds
        it: the inlined topics payload is null on a cold home."""
        from trinity_local.memory_viewer import _read_memory_contents, _slim_topics_for_viewer
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        cold = _slim_topics_for_viewer(_read_memory_contents())
        assert cold.get("topics.json") is None, (
            "cold topics payload must be null so the d3 topology gets 0 basins; "
            "a non-null half-built payload risks a client-side force-layout crash"
        )

    def test_cold_render_keeps_the_file_nav(self, tmp_path, monkeypatch):
        """Structure must survive the empty state — the new user still needs the
        nav to see WHICH memories will fill in (lens / topics / vocabulary)."""
        html = self._cold_render(tmp_path, monkeypatch)
        for f in ("lens.md", "topics.json", "vocabulary.md", "core.md"):
            assert f in html, f"cold viewer dropped the {f} nav entry"

    def test_empty_basins_returns_before_the_d3_topology(self, tmp_path, monkeypatch):
        """A topics.json that EXISTS with `{"basins": []}` (lens-build ran but
        found 0 basins — a tiny corpus) is a DIFFERENT path than the cold
        missing-file case: the payload is non-null, so `renderTopicsReader`
        actually runs. It MUST early-return on `basins.length === 0` BEFORE the
        d3 force-simulation + zoom-to-fit (#294) — otherwise 0 nodes reach the
        fitToView() bounds math and the topology divides by zero / NaNs the
        transform. Browser-verified 2026-06-02 (empty-basins home → "No topics
        yet", svg absent, 0 runtime errors); the cold tests above only cover the
        NULL payload, so this guards the non-null empty-array path that the
        existing `if (basins.length === 0) return` gate protects.
        """
        from trinity_local.memory_viewer import render_memory_viewer_html
        html = render_memory_viewer_html()
        start = html.index("function renderTopicsReader")
        reader = html[start:start + 8000]
        gate = reader.find("if (basins.length === 0)")
        no_topics = reader.find("No topics yet")
        gate_return = reader.find("return;", gate)
        force_sim = reader.find("forceSimulation")
        assert gate != -1 and no_topics != -1, (
            "renderTopicsReader must gate 0-basins with a 'No topics yet' message"
        )
        assert gate_return != -1 and force_sim != -1
        assert gate_return < force_sim, (
            "the 0-basins early-return must come BEFORE forceSimulation — else a "
            "topics.json with empty basins reaches the d3 force/zoom-to-fit (#294) "
            "with 0 nodes and divides by zero"
        )

    def test_slim_topics_preserves_empty_basins_for_client_gate(self):
        """The empty-basins case must reach the client AS `{basins: []}` so
        renderTopicsReader's 0-basins gate fires. `_slim_topics_for_viewer`
        takes/returns the RAW JSON STRING (the shape `_read_memory_contents`
        actually produces — NOT a parsed dict); with 0 basins there's nothing
        to slim, so it passes the string through unchanged. Guards that a
        future slim change can't flatten empty-basins to null (would mask the
        gate) or corrupt it (would crash the client parse)."""
        import json
        from trinity_local.memory_viewer import _slim_topics_for_viewer
        slimmed = _slim_topics_for_viewer({"topics.json": json.dumps({"basins": []})})
        topics_raw = slimmed.get("topics.json")
        assert isinstance(topics_raw, str), (
            "slim must return the topics payload as a JSON string (the "
            "_read_memory_contents shape), not a parsed dict"
        )
        assert json.loads(topics_raw).get("basins") == [], (
            "empty-basins topics must reach the client as {basins: []} so the "
            "renderTopicsReader 0-basins gate fires"
        )
