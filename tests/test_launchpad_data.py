

class TestTimelineForLaunchpad:
    """#252 'Your timeline' — life-chapters surfaced chronologically, dev/
    agent-ops chapters filtered, thin chapters dropped."""

    def _chapter(self, label, start, end, prompts):
        from types import SimpleNamespace
        return SimpleNamespace(
            label=label, start_month=start, end_month=end,
            months=1, total_prompts=prompts,
        )

    def test_filters_dev_sorts_chronologically(self, monkeypatch):
        import trinity_local.launchpad_data as ld
        chapters = [
            self._chapter("loop, run", "2026-05", "2026-05", 1363),   # dev → drop
            self._chapter("home, smart", "2025-07", "2025-07", 420),
            self._chapter("property, lots", "2025-05", "2026-03", 771),
            self._chapter("noise", "2024-01", "2024-01", 30),          # thin → drop
        ]
        monkeypatch.setattr(ld, "detect_chapters", lambda: chapters, raising=False)
        import trinity_local.me.chapters as ch
        monkeypatch.setattr(ch, "detect_chapters", lambda: chapters)

        rows = ld._timeline_for_launchpad()
        labels = [r["label"] for r in rows]
        assert "Loop, Run" not in labels      # dev chapter filtered
        assert all("noise" != r["label"].lower() for r in rows)  # thin dropped
        assert labels == ["Property, Lots", "Home, Smart"]  # chronological by start
        # range collapses single-month chapters.
        home = next(r for r in rows if r["label"] == "Home, Smart")
        assert home["range"] == "2025-07"
        prop = next(r for r in rows if r["label"] == "Property, Lots")
        assert prop["range"] == "2025-05 → 2026-03"

    def test_empty_on_no_chapters(self, monkeypatch):
        import trinity_local.launchpad_data as ld
        import trinity_local.me.chapters as ch
        monkeypatch.setattr(ch, "detect_chapters", lambda: [])
        assert ld._timeline_for_launchpad() == []


def test_timeline_card_binding_in_template():
    """#252 — the launchpad template carries the timeline card binding so the
    page-data field actually renders (browser-verified; this pins it)."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    assert "pageData.timeline" in html
    assert "Your timeline" in html


def test_timeline_copy_matches_chronological_display():
    """The card DISPLAYS arcs chronologically — `_timeline_for_launchpad` sorts
    by start ascending (guarded in TestTimelineForLaunchpad::
    test_filters_dev_sorts_chronologically; the substance-sort only SELECTS the
    top-N). So the user-facing copy must NOT claim 'most-substantial first',
    which contradicts the oldest-first order the user actually sees. Found
    2026-06-01 eyeballing the real launchpad: prompt counts ran 428, 416, 771,
    420, 659, 736 (NOT descending) under a 'most-substantial first' header."""
    from trinity_local.launchpad_template import render_launchpad_html
    html = render_launchpad_html(page_data={})
    assert "most-substantial first" not in html, (
        "timeline copy claims 'most-substantial first' but the card renders "
        "oldest-first — the description contradicts the displayed order"
    )
    # The copy must convey the chronological order it actually displays.
    assert "oldest first" in html or "chronological" in html


class TestColdStartPageData:
    """Fresh-install launchpad. build_page_data on an EMPTY ~/.trinity (0
    councils, no lens, no captures) must degrade gracefully: NO NaN/Infinity
    (which break JS JSON.parse for every first-run user) and the value-proof
    headline suppressed (no division-by-0-councils "NaN%"). Browser-verified
    2026-06-01 (cold launchpad mounts, 0 console errors, 0 {{ }} template leaks,
    cold-start CTA renders); this pins the DATA invariant so a future value-proof
    / wins change can't NaN the fresh-install launchpad in-gate."""

    def _empty_home_page_data(self, tmp_path, monkeypatch):
        from pathlib import Path
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.launchpad_data import build_page_data
        return build_page_data(
            live_review_path=Path("review_pages/live_council.html"),
            recent_councils=[],
        )

    def test_empty_home_page_data_is_strict_json_no_nan(self, tmp_path, monkeypatch):
        import json
        data = self._empty_home_page_data(tmp_path, monkeypatch)
        # allow_nan=False raises ValueError on any NaN/Infinity — exactly the
        # values JS JSON.parse chokes on. A wins% / 0-councils division lands here.
        json.dumps(data, allow_nan=False)

    def test_empty_home_suppresses_value_proof(self, tmp_path, monkeypatch):
        data = self._empty_home_page_data(tmp_path, monkeypatch)
        # The "winner differed from your default X%" headline (councilValue) is
        # v-if-gated; on a 0-council install it must be falsy so no "NaN%"/"0%"
        # misleading headline paints before the user has run anything.
        assert not data.get("councilValue"), (
            "value-proof (councilValue) must be suppressed on a 0-council install"
        )




def test_recent_council_links_target_the_always_present_live_page(tmp_path, monkeypatch):
    """Every rail/card council link must point at `live_council.html` — the SINGLE
    always-written review page — not a per-council `<id>.html` stub. Stubs are only
    written for runner-completed councils (65 of 562 on the real corpus), so if
    _load_recent_councils ever set review_page_path back to a per-council stub, the
    ~88% of councils with no stub would 404 on click. test_council_sidebar can't
    catch that — it feeds its OWN stub-shaped review_page_path. Verified 2026-06-05:
    the real launchpad's 305 rail links all resolve to live_council.html. This pins
    review_page_path to the always-present target at the source (_load_recent_councils)."""
    from pathlib import Path

    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_data import _load_recent_councils

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    save_council_outcome(
        CouncilOutcome(
            council_run_id="council_linkguard",
            bundle_id="bundle_linkguard",
            task_cluster_id="cluster_linkguard",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": "Which provider should answer this?"},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text="A full answer."),
                CouncilMemberResult(provider="codex", model="gpt", output_text="Another full answer."),
            ],
            synthesis_output="Synthesis.",
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at="2026-06-02T00:00:00+00:00",
        )
    )
    items = _load_recent_councils(limit=50)
    assert items, "seeded council did not surface in _load_recent_councils"
    for it in items:
        name = Path(str(it.get("review_page_path"))).name
        assert name == "live_council.html", (
            f"review_page_path points at {name!r}, not the always-present "
            "live_council.html — a per-council stub target would 404 for the ~88% "
            "of councils that have no <id>.html stub written"
        )


def test_recent_council_title_falls_back_to_outcome_task_text(tmp_path, monkeypatch):
    """The recent-councils rail reads each card title from the prompt_bundles/
    store — but ~100% of outcomes ALSO carry metadata.task_text, the canonical
    record. When the bundle can't be resolved (imported/legacy councils, a
    council whose bundle was never written, or a bundle pruned while the outcome
    ledger persists), the title must fall back to the outcome's own task_text,
    NOT degrade to the useless '[Council prompt unavailable]' placeholder.

    Found by driving the synthetic launchpad in a real browser: the rail read
    'unavailable' for EVERY seeded council because none had a matching bundle,
    even though each outcome carried its task_text. The sibling link-target test
    above seeds exactly this shape (bundle_id with no bundle file) and never
    noticed — it only asserts review_page_path. Mutation: drop the
    metadata.task_text fallback in _load_recent_councils -> title reverts to the
    placeholder -> reds."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_data import _load_recent_councils

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    task = "Should the cache key include the provider slug?"
    save_council_outcome(
        CouncilOutcome(
            council_run_id="council_titlefallback",
            bundle_id="bundle_never_written",  # no prompt_bundles/ entry for this id
            task_cluster_id="cluster_tf",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": task},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text="A full answer."),
                CouncilMemberResult(provider="codex", model="gpt", output_text="Another full answer."),
            ],
            synthesis_output="Synthesis.",
            routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
            created_at="2026-06-03T00:00:00+00:00",
        )
    )
    items = _load_recent_councils(limit=50)
    assert items, "seeded council did not surface in _load_recent_councils"
    title = (items[0].get("title") or "").lower()
    assert "unavailable" not in title, (
        f"recent-council title degraded to a placeholder despite the outcome "
        f"carrying metadata.task_text: {items[0].get('title')!r}"
    )
    # _truncate preserves the prefix, so the start of the task must survive.
    assert task[:20].lower() in title, (
        f"title did not fall back to the outcome's task_text: {items[0].get('title')!r}"
    )


def test_recent_council_root_title_is_earliest_round_not_globbed_last(tmp_path, monkeypatch):
    """A multi-round chain's recent-card title must be the ORIGINAL question (the
    EARLIEST round by created_at), authoritative over the unreliable round_number
    field. round_number is unreliable two ways: real councils OMIT it, so
    update_thread_manifest defaults every segment to 1 (then the old
    `round_number == 1` selection matched every round and the title became
    whichever globbed LAST — non-deterministic; a real 3-round chain's card showed
    a mid-round refinement instead of the original question, found driving a
    synthetic chain in a real browser); and even when present it needn't track
    recency. Fixed by selecting the root via the earliest (created_at,
    round_number).

    This seed makes round_number and created_at DELIBERATELY DISAGREE so the
    mutation reds deterministically (the all-absent real shape is
    glob-order-dependent and can't): the LATEST round is the only one stamped
    round_number==1, while the EARLIEST carries a higher number. The old code
    keys on `round_number == 1` and so picks the LATEST round's task; the fix
    keys on created_at and picks the earliest. Mutation: revert to
    `round_number == 1` selection -> the title becomes the latest round -> reds."""
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import (
        CouncilMemberResult,
        CouncilOutcome,
        CouncilRoutingLabel,
    )
    from trinity_local.launchpad_data import _load_recent_councils

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    root = "bundle_chain_roottitle"
    # (council_id, task, created_at, round_number). Only the LATEST round claims
    # round_number==1; the earliest carries a higher number → the buggy code
    # picks the latest deterministically, the fix picks the earliest.
    rounds = [
        ("council_ct_a", "ORIGINAL: cache per-call or in-process?", "2026-06-02T00:00:00+00:00", 2),
        ("council_ct_b", "refine: cross-tenant leakage?", "2026-06-02T00:01:00+00:00", 3),
        ("council_ct_c", "final: eviction policy?", "2026-06-02T00:02:00+00:00", 1),
    ]
    for cid, task, ts, rnum in rounds:
        save_council_outcome(
            CouncilOutcome(
                council_run_id=cid,
                bundle_id=root,
                task_cluster_id="cl",
                primary_provider="claude",
                winner_provider="claude",
                metadata={"task_text": task, "chain_root_id": root, "round_number": rnum},
                member_results=[
                    CouncilMemberResult(provider="claude", model="m", output_text="a full answer here."),
                ],
                synthesis_output="s",
                routing_label=CouncilRoutingLabel(winner="claude", confidence="high", task_type="design"),
                created_at=ts,
            )
        )
    items = _load_recent_councils(limit=10)
    assert len(items) == 1, f"one chain must collapse to ONE card, got {len(items)}"
    assert items[0].get("segment_count") == 3, "the card must aggregate all 3 rounds"
    title = items[0].get("title") or ""
    assert "ORIGINAL" in title, (
        f"recent-card title is not the chain's earliest (root) question — it "
        f"picked a non-root round (round_number won over created_at): {title!r}"
    )


def test_recent_council_latest_round_is_deterministic_on_a_created_at_tie(tmp_path, monkeypatch):
    """The rail meta line shows the LATEST round's winner + Solo verdict. That
    "latest" pick must be DETERMINISTIC — but created_at is second-resolution
    (now_iso() pins microsecond=0), so two rounds of a fast chain land in the
    SAME second. The old selector compared created_at ALONE (`created_at >=
    latest_created_at`), so on that tie the LAST-globbed round won — and glob
    order is filesystem-nondeterministic. Result: the rail's winner BRAND and its
    Solo/contest verdict (latest_member_count) flipped run-to-run for a same-
    second chain. The root-title selector already tie-breaks on round_number
    (sibling test above); the latest selector must mirror it.

    Seed ONE chain, TWO rounds at an IDENTICAL created_at, where the rounds
    disagree on BOTH the displayed values: the TRUE latest round (round_number=2)
    is a SOLO codex result (1 distinct provider); the earlier round is a real
    3-provider contest won by claude. The seed makes them genuinely differ so a
    flip is observable. Drive _load_recent_councils under BOTH possible glob
    orderings and assert the latest-round verdict is IDENTICAL across them AND
    equals the round_number=2 round (codex, member_count 1 → 'Solo').

    Mutation: revert the latest selector to `created_at >= latest_created_at`
    (drop the round_number tie-break) → the two glob orderings disagree (one
    yields codex/1, the other claude/3) → this guard reds with the exact founder
    symptom (a non-deterministic Solo verdict on the rail)."""
    import json

    import trinity_local.launchpad_data as ld
    from trinity_local.launchpad_data import _load_recent_councils
    from trinity_local.state_paths import council_outcomes_dir

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    outdir = council_outcomes_dir()
    outdir.mkdir(parents=True, exist_ok=True)
    tie = "2026-06-18T12:00:00+00:00"
    root = "bundle_chain_tieroot"

    def _outcome(cid, rnum, winner, members):
        return {
            "council_run_id": cid,
            "bundle_id": root,
            "task_cluster_id": "cl",
            "primary_provider": "claude",
            "created_at": tie,
            "winner_provider": winner,
            "metadata": {"chain_root_id": root, "round_number": rnum, "task_text": "Design the auth flow"},
            "routing_label": {"winner": winner, "confidence": "high", "task_type": "architecture",
                              "agreed_claims": [], "disagreed_claims": []},
            "member_results": [{"provider": p, "output_text": "x" * 250} for p in members],
        }

    # Earlier round: real 3-provider contest, winner claude.
    f_early = outdir / "council_round_one.json"
    f_early.write_text(json.dumps(_outcome("council_round_one", 1, "claude", ["claude", "codex", "antigravity"])))
    # TRUE latest round (round_number=2): SOLO codex (1 distinct provider).
    f_late = outdir / "council_round_two.json"
    f_late.write_text(json.dumps(_outcome("council_round_two", 2, "codex", ["codex"])))

    real_dir = ld.council_outcomes_dir

    class _OrderedDir:
        def __init__(self, ordered):
            self._ordered = ordered

        def glob(self, pattern):  # only council_*.json is globbed here
            return iter(self._ordered)

        def __truediv__(self, other):
            return outdir / other

        def exists(self):
            return True

    verdicts = {}
    for name, order in (("early_then_late", [f_early, f_late]), ("late_then_early", [f_late, f_early])):
        monkeypatch.setattr(ld, "council_outcomes_dir", lambda o=order: _OrderedDir(o))
        items = _load_recent_councils(limit=10)
        monkeypatch.setattr(ld, "council_outcomes_dir", real_dir)
        assert len(items) == 1, f"one chain must collapse to ONE card, got {len(items)} ({name})"
        it = items[0]
        verdicts[name] = (it.get("winner_provider"), it.get("member_count"))

    # Founder symptom: the rail's latest-round winner + Solo verdict must NOT
    # depend on filesystem glob order.
    assert verdicts["early_then_late"] == verdicts["late_then_early"], (
        "rail latest-round verdict FLIPS with glob order on a created_at tie — "
        f"the Solo verdict + winner brand is non-deterministic: {verdicts}"
    )
    # And it must be the TRUE latest round (round_number=2): codex, solo (1).
    assert verdicts["early_then_late"] == ("codex", 1), (
        "rail latest-round verdict did not resolve to the round_number=2 round "
        f"(expected codex + member_count 1 → 'Solo'): {verdicts['early_then_late']}"
    )


# ---------------------------------------------------------------------------
# Rail decision chip (2026-08-03, "the launchpad IS the recap"): a rail row
# showed WHO won but not whether there was a fight or what got settled — the
# decision signal stopped one click too deep. These tests value-assert at the
# SURFACE binding (build_recent_sidebar_html output), not just the loader
# primitives, per the loop-data-correctness rule: the recurring bug class is a
# value derived correctly at the primitive and dropped/mislabeled at the
# surface that ships it.
# ---------------------------------------------------------------------------


def _seed_chip_outcome(tmp_path, monkeypatch, *, council_id, routing_label,
                       task_text="Which provider should answer this?"):
    from trinity_local.council_runtime import save_council_outcome
    from trinity_local.council_schema import CouncilMemberResult, CouncilOutcome

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    save_council_outcome(
        CouncilOutcome(
            council_run_id=council_id,
            bundle_id=f"bundle_{council_id}",
            task_cluster_id=f"cluster_{council_id}",
            primary_provider="claude",
            winner_provider="claude",
            metadata={"task_text": task_text},
            member_results=[
                CouncilMemberResult(provider="claude", model="opus", output_text="A."),
                CouncilMemberResult(provider="codex", model="gpt", output_text="B."),
            ],
            synthesis_output="Synthesis.",
            routing_label=routing_label,
            created_at="2026-06-02T00:00:00+00:00",
        )
    )


def test_rail_decision_chip_renders_splits_and_settled(tmp_path, monkeypatch):
    """3 splits, 2 with a survives-resolution + 1 'unresolved' -> the literal
    chip text '3 splits · 2 settled' must appear in the RAIL HTML (an
    'unresolved' resolution is a verdict of non-settlement, never counted)."""
    from trinity_local.council_schema import CouncilRoutingLabel
    from trinity_local.launchpad_data import (
        _load_recent_councils,
        build_recent_sidebar_html,
    )

    label = CouncilRoutingLabel(
        winner="claude",
        confidence="high",
        task_type="design",
        disagreed_claims=[
            {"claim": "A", "providers_for": ["claude"], "providers_against": ["codex"],
             "resolution": "claude survives: the evidence holds."},
            {"claim": "B", "providers_for": ["codex"], "providers_against": ["claude"],
             "resolution": "codex survives on cost ordering."},
            {"claim": "C", "providers_for": ["claude"], "providers_against": ["codex"],
             "resolution": "unresolved — the evidence cannot decide."},
        ],
    )
    _seed_chip_outcome(tmp_path, monkeypatch, council_id="council_chip3", routing_label=label)
    items = _load_recent_councils(limit=10)
    assert items and items[0]["n_splits"] == 3 and items[0]["n_settled"] == 2, (
        f"loader primitives wrong: {items[0] if items else 'no rows'}"
    )
    html = build_recent_sidebar_html(items)
    assert ">3 splits · 2 settled<" in html, (
        "the decision chip's literal text is missing from the rail HTML — the "
        "loader counted splits but the surface binding dropped them:\n" + html[:400]
    )


def test_rail_decision_chip_unanimous_when_label_has_no_splits(tmp_path, monkeypatch):
    """A routing label with NO disagreed_claims key (to_dict() filters empty
    containers out of the JSON) is a UNANIMOUS verdict — the chip must say so,
    never render empty, and never claim a split happened."""
    from trinity_local.council_schema import CouncilRoutingLabel
    from trinity_local.launchpad_data import (
        _load_recent_councils,
        build_recent_sidebar_html,
    )

    label = CouncilRoutingLabel(winner="claude", confidence="high", task_type="design")
    _seed_chip_outcome(tmp_path, monkeypatch, council_id="council_chipu", routing_label=label)
    items = _load_recent_councils(limit=10)
    assert items and items[0]["n_splits"] == 0, f"expected n_splits=0: {items[0] if items else 'no rows'}"
    html = build_recent_sidebar_html(items)
    assert ">unanimous<" in html, "zero-split council must render the 'unanimous' chip"
    assert "split" not in html.split("rail-council-splits", 1)[-1].split("</span>")[0], (
        "a unanimous council must not render a splits count"
    )


def test_rail_row_chipless_when_no_routing_label(tmp_path, monkeypatch):
    """No routing label at all (a legacy/imported outcome on disk — the runner
    itself refuses to SAVE one, so raw-write the file) -> the row still renders
    but carries NO decision chip: claiming 'unanimous' for a verdict nobody
    emitted is the green-over-degenerate shape (#35)."""
    import json

    from trinity_local.launchpad_data import (
        _load_recent_councils,
        build_recent_sidebar_html,
        council_outcomes_dir,
    )

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    out = council_outcomes_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "council_chipn.json").write_text(json.dumps({
        "council_run_id": "council_chipn",
        "bundle_id": "bundle_chipn",
        "created_at": "2026-06-02T00:00:00+00:00",
        "winner_provider": "claude",
        "metadata": {"task_text": "Legacy council with no label?"},
        "member_results": [
            {"provider": "claude", "model": "opus", "output_text": "A."},
            {"provider": "codex", "model": "gpt", "output_text": "B."},
        ],
        "synthesis_output": "S.",
    }), encoding="utf-8")
    items = _load_recent_councils(limit=10)
    assert items and items[0]["n_splits"] is None, (
        f"missing label must yield n_splits=None (unknown), not a number: {items[0] if items else 'no rows'}"
    )
    html = build_recent_sidebar_html(items)
    assert "rail-council-splits" not in html, (
        "a council with no routing label must render CHIPLESS — neither "
        "'unanimous' nor a split count can be claimed"
    )
    assert "rail-council-title" in html, "the row itself must still render"


def test_rail_row_survives_corrupt_disagreed_claims(tmp_path, monkeypatch):
    """disagreed_claims as a non-list (hand-mangled outcome, the #258
    hand-editable-state class) must degrade to a chipless row — never crash
    the rail and never invent a count."""
    import json

    from trinity_local.launchpad_data import (
        _load_recent_councils,
        build_recent_sidebar_html,
        council_outcomes_dir,
    )

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    out = council_outcomes_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "council_chipc.json").write_text(json.dumps({
        "council_run_id": "council_chipc",
        "bundle_id": "bundle_chipc",
        "created_at": "2026-06-02T00:00:00+00:00",
        "winner_provider": "claude",
        "metadata": {"task_text": "Corrupt label council?"},
        "member_results": [
            {"provider": "claude", "model": "opus", "output_text": "A."},
            {"provider": "codex", "model": "gpt", "output_text": "B."},
        ],
        "synthesis_output": "S.",
        "routing_label": {"winner": "claude", "disagreed_claims": "not-a-list"},
    }), encoding="utf-8")
    items = _load_recent_councils(limit=10)
    assert items and items[0]["n_splits"] is None, (
        f"corrupt disagreed_claims must yield None, not a fabricated count: {items[0] if items else 'no rows'}"
    )
    html = build_recent_sidebar_html(items)
    assert "rail-council-splits" not in html and "rail-council-title" in html


def test_rail_title_is_first_line_only(tmp_path, monkeypatch):
    """Council prompts are multi-paragraph briefs; the one-line rail row must
    show the FIRST line only (measured 2026-08-03: second paragraphs leaked
    into rows as '...IN WHAT SHAPE?\\n\\nTH')."""
    from trinity_local.council_schema import CouncilRoutingLabel
    from trinity_local.launchpad_data import _load_recent_councils

    label = CouncilRoutingLabel(winner="claude", confidence="high", task_type="design")
    _seed_chip_outcome(
        tmp_path, monkeypatch, council_id="council_chipt", routing_label=label,
        task_text="SHOULD THE TIER SHIP, AND IN WHAT SHAPE?\n\nTHE CONTEXT: a wall of detail.",
    )
    items = _load_recent_councils(limit=10)
    assert items, "seeded council did not surface"
    title = str(items[0]["title"])
    assert title.startswith("SHOULD THE TIER SHIP"), f"unexpected title: {title!r}"
    assert "THE CONTEXT" not in title and "\n" not in title, (
        f"second paragraph leaked into the rail title: {title!r}"
    )


def test_chip_distinguishes_absent_key_from_empty_list(tmp_path, monkeypatch):
    """An ABSENT disagreed_claims key is UNKNOWN; an EMPTY list is unanimous.

    CouncilRoutingLabel.to_dict() emits `disagreed_claims: []` for a unanimous
    council — it does NOT omit the key (an earlier comment in launchpad_data
    claimed the opposite). So defaulting a missing key to [] conflates "the
    chairman found no disputes" with "this label never carried the field",
    stamping `unanimous` on legacy/half-written outcomes that rendered no verdict.
    Two raw-written outcomes, identical but for the key's presence, must chip
    differently."""
    import json

    from trinity_local.launchpad_data import (
        _load_recent_councils,
        build_recent_sidebar_html,
        council_outcomes_dir,
    )

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    out = council_outcomes_dir()
    out.mkdir(parents=True, exist_ok=True)

    def _write(cid, label):
        (out / f"{cid}.json").write_text(json.dumps({
            "council_run_id": cid, "bundle_id": f"b_{cid}",
            "created_at": "2026-06-02T00:00:00+00:00", "winner_provider": "claude",
            "metadata": {"task_text": f"question for {cid}?"},
            "member_results": [
                {"provider": "claude", "model": "opus", "output_text": "A."},
                {"provider": "codex", "model": "gpt", "output_text": "B."},
            ],
            "synthesis_output": "S.", "routing_label": label,
        }), encoding="utf-8")

    _write("council_empty", {"winner": "claude", "disagreed_claims": []})
    _write("council_absent", {"winner": "claude"})

    by_id = {str(i["council_id"]): i for i in _load_recent_councils(limit=10)}
    assert by_id["council_empty"]["n_splits"] == 0, "empty list must read as unanimous (0)"
    assert by_id["council_absent"]["n_splits"] is None, (
        "an ABSENT disagreed_claims key must read as UNKNOWN (None), not as unanimous — "
        "otherwise a legacy label is credited with a verdict it never emitted"
    )
    html = build_recent_sidebar_html(list(by_id.values()))
    assert ">unanimous<" in html, "the genuinely-unanimous council lost its chip"
    assert html.count(">unanimous<") == 1, (
        "both councils rendered 'unanimous' — the absent-key case is being conflated "
        "with the empty-list case"
    )
