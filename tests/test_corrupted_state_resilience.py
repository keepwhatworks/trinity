"""A corrupted-but-parseable state file must not crash the launchpad/status.

Found 2026-06-01 by corrupting an isolated ~/.trinity one file at a time and
running `portal-html` + `status`: many readers guard the JSON *parse*
(JSONDecodeError) but not the resulting *shape*. A state file that is valid JSON
of the WRONG type (a list/str where a dict is expected, or a sub-field of the
wrong type) slips past the parse-guard and then crashes downstream on
`.get`/`.items`/iteration (AttributeError) — and a non-UTF8 memory file crashes
on `read_text`. One corrupted file then nukes the ENTIRE launchpad build, leaving
the user with no launchpad at all. Per claude.md "analytics never crash" +
graceful-degradation (#238): drop the bad file's data, render the rest.

These pin every empirically-confirmed crash site. Realistic triggers: an
interrupted/concurrent write that leaves a valid-but-wrong-shape file, a clobber
that writes [] instead of {}, a schema-migration bug, or disk corruption.
"""
from __future__ import annotations

import json

import pytest


def _populate(home, *, topics=None, picks=None, routing=None, outcome=None,
              core=b"# Core\n", lens=b"# Lens\n", vocab=b"# Vocab\n"):
    (home / "memories").mkdir(parents=True, exist_ok=True)
    (home / "scoreboard").mkdir(parents=True, exist_ok=True)
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (home / "core.md").write_bytes(core)
    (home / "memories" / "lens.md").write_bytes(lens)
    (home / "memories" / "vocabulary.md").write_bytes(vocab)
    (home / "memories" / "topics.json").write_text(
        topics if topics is not None
        else json.dumps({"basins": [{"id": "b00", "label": "x", "size": 5}]}),
        encoding="utf-8")
    (home / "scoreboard" / "picks.json").write_text(
        picks if picks is not None
        else json.dumps({"rules": {"b00": {"provider": "claude"}}}),
        encoding="utf-8")
    (home / "scoreboard" / "routing.json").write_text(
        routing if routing is not None else json.dumps({}), encoding="utf-8")
    (home / "council_outcomes" / "council_aaa111.json").write_text(
        outcome if outcome is not None
        else json.dumps({"council_run_id": "council_aaa111", "winner_provider": "claude"}),
        encoding="utf-8")


def _build(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    from trinity_local.launchpad_data import build_page_data
    data = build_page_data(
        live_review_path=Path("review_pages/live_council.html"),
        recent_councils=[],
    )
    # The result must still be strict-JSON-serializable (no NaN, no crash).
    json.dumps(data, allow_nan=False)
    return data


# Each param: (label, kwargs to _populate that corrupt ONE file)
_CORRUPTIONS = [
    ("topics_wrong_type", {"topics": "[1,2,3]"}),
    ("topics_basins_string", {"topics": '{"basins":"oops"}'}),
    # `basins` IS a list (so it passes the `isinstance(basins, list)` guard) but
    # its ENTRIES are non-dicts — a truncated/clobbered write. Every consumer
    # does `b.get(...)` per entry, so this crashed the WHOLE launchpad render
    # (AttributeError: 'str' object has no attribute 'get') until _load_topics_
    # basins filtered to dict entries. The #298 topics-basin reader the #202
    # shape-guard sweep predated. The one valid dict here must still survive.
    ("topics_basins_nondict_entries", {"topics": '{"basins":["b00",123,null,{"id":"b00","centroid":[0.1]}]}'}),
    ("topics_truncated", {"topics": '{"basins":[{'}),
    ("picks_list", {"picks": "[1,2,3]"}),
    ("picks_string", {"picks": '"x"'}),
    # A STRUCTURALLY-VALID picks.json (top-level dict keyed by basin, each value a
    # dict WITH a `winner` so the no-winner skip does NOT fire) whose numeric field
    # is the WRONG TYPE: a string ("abc") `margin`, or a non-numeric `count`. picks.json
    # is hand-editable + has a documented migration chain (cortex/routing_patterns.json →
    # memories/picks.json → scoreboard/picks.json), so a half-migrated/mangled entry is
    # realistic. _load_cortex_rules did `float(p.get("margin"))` / `int(p.get("count"))`
    # — a bare ValueError that bubbled out of build_page_data and BLANKED THE WHOLE
    # launchpad render. The picks_list/picks_string cases above DON'T reach this: a
    # whole-file wrong-type makes load_routing_patterns return {} (no patterns to
    # iterate), so the per-value coercion is never exercised. (#304 wrong-type-VALUE
    # class — sibling of the by_rejection_type / NaN-eval-score launchpad-blank fixes.)
    ("picks_margin_nonnumeric_str",
     {"picks": '{"b00": {"winner": "claude", "margin": "abc", "count": 5, "n_episodes": 5, "evidence": []}}'}),
    ("picks_count_nonnumeric_str",
     {"picks": '{"b00": {"winner": "claude", "margin": 0.4, "count": "five", "n_episodes": 5, "evidence": []}}'}),
    ("picks_margin_nan",
     {"picks": '{"b00": {"winner": "claude", "margin": NaN, "count": 5, "n_episodes": 5, "evidence": []}}'}),
    ("routing_list", {"routing": "[1,2]"}),
    ("outcome_list", {"outcome": "[1,2]"}),
    ("outcome_string", {"outcome": '"oops"'}),
    ("outcome_truncated", {"outcome": "{trunc"}),
    ("core_binary", {"core": b"\xff\xfe\x00garbage"}),
    ("lens_binary", {"lens": b"\xff\xfegarbage"}),
    ("vocab_binary", {"vocab": b"\xff\xfegarbage"}),
]


@pytest.mark.parametrize("label,kwargs", _CORRUPTIONS, ids=[c[0] for c in _CORRUPTIONS])
def test_build_page_data_survives_corrupt_state(tmp_path, monkeypatch, label, kwargs):
    """build_page_data must not raise on any single corrupted state file —
    it underpins `portal-html`, which every user runs."""
    _populate(tmp_path, **kwargs)
    data = _build(tmp_path, monkeypatch)  # raises if the launchpad build crashes
    assert isinstance(data, dict), f"build_page_data degraded wrong for {label}"


def test_load_cortex_rules_coerces_wrong_type_numeric_fields(tmp_path, monkeypatch):
    """A picks.json basin with a `winner` (so it's NOT skipped) but a wrong-type
    `margin`/`count` must still RENDER as a real numeric pick — not crash the whole
    launchpad and not get silently dropped. The param sweep above proves no crash;
    this pins the OBSERVABLE result of the fix so a future revert that re-introduces
    the bare float()/int() (founder symptom: 'a single corrupt picks.json pick
    blanked the ENTIRE launchpad with `could not convert string to float`') reds
    HERE, on the exact code path the fix changed (_load_cortex_rules)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    (tmp_path / "scoreboard").mkdir(parents=True, exist_ok=True)
    # margin is the non-numeric string "abc"; count/n_episodes are numeric-STRINGS
    # ("7") that the coercer should RECOVER (a half-migrated entry), not zero out.
    (tmp_path / "scoreboard" / "picks.json").write_text(
        json.dumps({
            "b00": {"winner": "claude", "margin": "abc", "count": "7",
                    "n_episodes": "7", "evidence": ["bundle_x"]},
        }),
        encoding="utf-8",
    )
    from trinity_local.launchpad_data import _load_cortex_rules

    out = _load_cortex_rules()  # raised ValueError pre-fix
    assert out is not None, "the basin has a winner — it must not vanish"
    rules = out["rules"]
    assert len(rules) == 1, (
        "the corrupt-margin pick was DROPPED — a wrong-type field must degrade the "
        "field, not erase the whole pick"
    )
    pick = rules[0]
    # margin: non-numeric → finite default 0.0 (and renders a real 2dp string).
    assert isinstance(pick["margin"], float) and pick["margin"] == 0.0
    assert pick["margin_str"] == "0.00"
    # count/n_episodes: numeric-STRINGS are recovered to their real ints.
    assert pick["count"] == 7 and pick["n_episodes"] == 7
    # The whole payload must stay strict-JSON-serializable (no bare NaN leak that
    # would break the client's JSON.parse and never mount the launchpad).
    json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("label,kwargs", _CORRUPTIONS, ids=[c[0] for c in _CORRUPTIONS])
def test_consolidate_survives_corrupt_state(tmp_path, monkeypatch, label, kwargs):
    """`consolidate_via_lens_basins` (the `trinity-local consolidate` engine) must
    not crash on any corrupt state file. The build_page_data sweep above CAN'T
    cover this: build_page_data reads the ALREADY-consolidated picks.json — it
    never RUNS consolidate, which reads topics.json basins + council outcomes and
    iterates them (`b.get(...)` per basin, per council). The #304 sibling
    (2026-06-06) was exactly here: a corrupt topics.json crashed consolidate, but
    only with a REAL-CONTEST council present to drive the basin iteration — which
    the harness's default `winner_provider`-only council does NOT. So seed one
    with the real fields (winner / task_text / substantive_members / created_at),
    then run consolidate against each corruption. (Catches corrupt-topics AND
    corrupt-outcome crashes the build_page_data path can't reach.)"""
    _populate(tmp_path, **kwargs)
    # A real-contest council so compute_basin_routing actually iterates the basins.
    (tmp_path / "council_outcomes" / "council_real.json").write_text(
        json.dumps({
            "council_run_id": "council_real", "winner": "claude",
            "task_text": "refactor the auth module", "substantive_members": 2,
            "created_at": "2026-06-01T00:00:00+00:00",
            "metadata": {"task_text": "refactor the auth module"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    from trinity_local.lens_routing import consolidate_via_lens_basins
    routing = consolidate_via_lens_basins()  # raises if a corrupt file crashes it
    assert isinstance(routing, dict), f"consolidate degraded wrong for {label}"
    # Strict-serializable so a wrong-shape value can't poison picks.json downstream.
    json.dumps(routing, allow_nan=False)


def test_load_recent_councils_skips_wrong_type(tmp_path, monkeypatch):
    """The REAL portal path (launchpad_page.py) calls _load_recent_councils(limit=500)
    and passes the result into build_page_data — build_page_data(recent_councils=[])
    bypasses this reader, so it needs its own coverage. A list/str-typed outcome
    file must be skipped, not crash the launchpad build."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path)  # one valid outcome
    # add a corrupted (list-typed) outcome alongside the valid one
    (tmp_path / "council_outcomes" / "council_bad.json").write_text("[1,2]", encoding="utf-8")
    from trinity_local.launchpad_data import _load_recent_councils
    cards = _load_recent_councils(limit=500)  # raises AttributeError pre-fix
    assert isinstance(cards, list)
    # the valid outcome still surfaces; the corrupted one is silently dropped
    assert all(isinstance(c, dict) for c in cards)


@pytest.mark.parametrize("bad", ["[1,2,3]", '"x"', "42", "null"], ids=["list", "str", "int", "null"])
def test_load_recent_councils_survives_wrong_type_metadata(tmp_path, monkeypatch, bad):
    """An outcome whose ROOT is a dict (so it passes the root isinstance guard at
    the top of _load_recent_councils) but whose nested `metadata` is the WRONG type
    (a list/str/int from a half-migrated or hand-mangled outcome). The reader did
    `metadata = raw.get("metadata") or {}` — which KEEPS a truthy list — then
    `metadata.get("chain_root_id")`, raising AttributeError that bubbles out of
    build_page_data and 500s the WHOLE launchpad render (the rail builder is on the
    hot page-data path the served portal calls). The root-shape guard didn't cover
    the nested field — exactly the v1.7.202/#304 'guard the SHAPE not just the
    parse' class on a NESTED key. The valid sibling outcome must still surface."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path)  # one valid outcome with valid metadata
    # a dict-root outcome whose `metadata` is the wrong type — passes the root
    # guard, would crash on metadata.get(...) pre-fix.
    (tmp_path / "council_outcomes" / "council_badmeta.json").write_text(
        '{"council_run_id":"council_badmeta","created_at":"2026-06-10T00:00:00+00:00",'
        '"winner_provider":"claude","metadata":' + bad + ','
        '"routing_label":{"task_type":"coding","winner":"claude"}}',
        encoding="utf-8",
    )
    from trinity_local.launchpad_data import _load_recent_councils
    cards = _load_recent_councils(limit=500)  # raises AttributeError pre-fix
    assert isinstance(cards, list)
    assert all(isinstance(c, dict) for c in cards)
    # degrade-safe, not silent-total-loss: the council with the bad metadata still
    # surfaces (just without chain/round detail) ALONGSIDE the valid sibling — so
    # one mangled metadata field can't erase the user's whole recent-council rail.
    ids = {c.get("council_id") or c.get("chain_root_id") for c in cards}
    assert "council_badmeta" in ids, (
        "the wrong-type-metadata council vanished from the rail — coerce non-dict "
        "metadata to {}, don't drop the whole record"
    )


@pytest.mark.parametrize(
    "bad_round",
    ['"first"', "[1,2]", '{"r":1}', "null", "true", '"3.5"'],
    ids=["str", "list", "dict", "null", "bool", "floatstr"],
)
def test_load_recent_councils_survives_wrong_type_round_number(tmp_path, monkeypatch, bad_round):
    """Iter 258 JACKPOT (the Iter-257 corrupt-field-crash class on a DIFFERENT
    file/render): an outcome whose `metadata` IS a valid dict (so it passes the
    nested-metadata isinstance guard) but whose `metadata.round_number` is a
    non-numeric string ("first"), a list, a dict, or null. _load_recent_councils
    did a BARE `int(metadata.get("round_number") or 1)` — `int("first")` raises
    ValueError that bubbled out of the reader → _assemble_page_data →
    render_launchpad_html, BLANKING THE WHOLE LAUNCHPAD (portal-html exit 1, the
    served portal 500s) over one hand-edited council_outcomes/<id>.json field.
    Coerce via the shared _safe_number shape-guard. The valid sibling AND the
    corrupted council must both still surface in the rail (degrade to round 1,
    don't drop the record)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path)  # one valid outcome
    # Render-INDEPENDENT precondition: the seed genuinely carries a non-int
    # round_number inside an otherwise-valid dict metadata — so the bite is the
    # int() coercion, not a vacuous parse failure or a dropped record.
    (tmp_path / "council_outcomes" / "council_badround.json").write_text(
        '{"council_run_id":"council_badround","created_at":"2026-06-10T00:00:00+00:00",'
        '"winner_provider":"claude","metadata":{"chain_root_id":"council_badround",'
        '"round_number":' + bad_round + ',"task_text":"q"},'
        '"routing_label":{"task_type":"coding","winner":"claude"}}',
        encoding="utf-8",
    )
    seeded = json.loads(
        (tmp_path / "council_outcomes" / "council_badround.json").read_text()
    )
    assert not isinstance(seeded["metadata"]["round_number"], int) or isinstance(
        seeded["metadata"]["round_number"], bool
    ), "fixture is not the discriminating non-int round_number state"

    from trinity_local.launchpad_data import _load_recent_councils
    cards = _load_recent_councils(limit=500)  # raises ValueError pre-fix
    assert isinstance(cards, list)
    assert all(isinstance(c, dict) for c in cards)
    ids = {c.get("council_id") or c.get("chain_root_id") for c in cards}
    assert "council_badround" in ids, (
        "the wrong-type-round_number council vanished from the rail — coerce a "
        "garbled round_number to round 1, don't drop the whole record"
    )


def test_render_launchpad_html_survives_wrong_type_round_number(tmp_path, monkeypatch):
    """The END-TO-END proof for the Iter 258 jackpot: drive the FULL portal render
    (render_launchpad_html → _assemble_page_data → _load_recent_councils(limit=500)
    → build_page_data), which is the path the served portal + `portal-html` take —
    NOT the build_page_data(recent_councils=[]) shortcut the parametrized cases use.
    A bare int() on a hand-edited round_number crashed THIS path with ValueError,
    BLANKING the whole launchpad. The render must return real HTML."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _populate(tmp_path)
    (tmp_path / "council_outcomes" / "council_badround.json").write_text(
        '{"council_run_id":"council_badround","created_at":"2026-06-10T00:00:00+00:00",'
        '"winner_provider":"claude","metadata":{"chain_root_id":"council_badround",'
        '"round_number":"first","task_text":"q"},'
        '"routing_label":{"task_type":"coding","winner":"claude"}}',
        encoding="utf-8",
    )
    from trinity_local.launchpad_page import render_launchpad_html
    html = render_launchpad_html()  # raises ValueError pre-fix → blank launchpad
    assert isinstance(html, str) and len(html) > 1000
    assert "[object Object]" not in html


@pytest.mark.parametrize(
    "bad_count",
    ['"lots"', "null", '"3"', "[1]", '{"n":2}'],
    ids=["str", "null", "numstr", "list", "dict"],
)
def test_check_drift_survives_wrong_type_error_count(tmp_path, monkeypatch, bad_count):
    """Iter 258 SECOND jackpot (the same class on the drift ledger): a hand-edited
    outcomes.jsonl line whose `error_count` is a non-int. `_load_outcomes` passed
    it straight through (`raw.get("error_count", 0)`); `_score_outcome` then did
    `error_count > 2` — and `"lots" > 2` raises TypeError that bubbled out of
    check_drift and CRASHED `trinity-local status` (exit 1). Coerce error_count to
    a clean int at the load boundary. check_drift must return a list, not raise."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path).mkdir(parents=True, exist_ok=True)
    # A completed outcome whose error_count is the wrong type — reaches the
    # `error_count > 2` compare in _score_outcome (the timestamp is valid so the
    # record isn't skipped by the `ts <= 0` gate).
    (tmp_path / "outcomes.jsonl").write_text(
        '{"provider":"claude","task_type":"code","completed":true,'
        '"error_count":' + bad_count + ',"timestamp":"2026-06-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )
    # Render-INDEPENDENT precondition: the on-disk record genuinely carries a
    # non-int error_count, so the bite is the `>` compare, not a parse failure.
    rec = json.loads((tmp_path / "outcomes.jsonl").read_text().strip())
    assert not isinstance(rec["error_count"], int) or isinstance(rec["error_count"], bool), (
        "fixture is not the discriminating non-int error_count state"
    )
    from trinity_local.drift import check_drift
    alerts = check_drift()  # raises TypeError pre-fix → status exit 1
    assert isinstance(alerts, list)


@pytest.mark.parametrize("bad", ["[1,2,3]", '"x"', "42", "null"], ids=["list", "str", "int", "null"])
def test_eval_summary_survives_wrong_type_result_file(tmp_path, monkeypatch, bad):
    """The eval-leaderboard card (_eval_summary) walks evals/results/eval_*__model_*.json
    TWICE: the headline loop (isinstance-guarded) and a SECOND per-target leaderboard
    loop that did `data.get("target_provider")` with NO shape guard. A valid-JSON-
    but-wrong-type result file (a list/str/int/null root from a truncated or
    hand-mangled file) alongside VALID scored results parses fine, then crashes the
    second loop with AttributeError ('list' object has no attribute 'get') — which
    bubbles out of build_page_data and 500s the WHOLE launchpad. (The crash needs a
    VALID scored sibling present so the function reaches the second loop instead of
    returning the empty state early — that's why this test seeds two real results.)
    The valid leaderboard rows must still render; the bad file is skipped."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _populate(tmp_path)
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    # two VALID scored results (claude + codex) so the second leaderboard loop runs
    for slug, score in (("claude", 0.82), ("codex", 0.71)):
        (results / f"eval_set1__model_{slug}.json").write_text(json.dumps({
            "eval_id": "set1", "target_provider": slug, "target_model": f"{slug}-x",
            "aggregate_score": score, "items_completed": 12, "items_total": 12,
            "items_failed": 0, "completed_at": "2026-06-15T10:00:00+00:00",
            "by_rejection_type": {"REFRAME": {"mean_score": score, "count": 6}},
            "items": [{"judge_provider": "claude"}],
        }), encoding="utf-8")
    # the corrupt wrong-type result file (matches the eval_*__model_*.json glob)
    (results / "eval_set1__model_bad.json").write_text(bad, encoding="utf-8")

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()  # raises AttributeError pre-fix on the second loop
    assert isinstance(summary, dict)
    # the VALID results still produce the leaderboard — the bad file is dropped, not
    # the whole card: both real providers survive into the comparison rows.
    targets = {row.get("target") for row in summary.get("comparison", [])}
    assert {"claude", "codex"} <= targets, (
        "a wrong-type eval result file took down the WHOLE leaderboard — shape-guard "
        "the second per-target loop the way the headline loop already does"
    )


def test_write_portal_html_survives_corrupt_state(tmp_path, monkeypatch):
    """`portal-html` calls write_portal_html, which since v1.7.242 ALSO runs
    freeze_routing_to_disk() (re-reads council_outcomes/ at render time) before
    rendering. The existing param-test exercises build_page_data, NOT this fuller
    render path — so the freeze step's corrupt-outcome handling + the page write
    were unguarded. A single corrupt outcome must not crash the user's launchpad
    build; the launchpad still writes, and the frozen routing.json stays valid
    JSON (the memory viewer reads it)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    # A VALID outcome that carries a routing_label, so the freeze produces a real
    # by_task_type and OVERWRITES the corrupt routing.json (the realistic case:
    # the user has rated councils). _populate's default outcome has no
    # routing_label → empty table → freeze skips, which is also fine but doesn't
    # exercise the overwrite.
    good_outcome = json.dumps({
        "council_run_id": "council_good", "bundle_id": "b", "task_cluster_id": "c",
        "primary_provider": "claude", "winner_provider": "claude", "synthesis_output": "x",
        "routing_label": {"task_type": "t", "winner": "claude",
                          "provider_scores": {"claude": {"overall": 8.0}}},
        "metadata": {"task_type": "t"}, "created_at": "2026-06-01T00:00:00",
    })
    _populate(tmp_path, outcome=good_outcome)
    # corrupt outcomes the freeze's compute_personal_routing_table() must skip
    (tmp_path / "council_outcomes" / "council_list.json").write_text("[1,2]", encoding="utf-8")
    (tmp_path / "council_outcomes" / "council_trunc.json").write_text("{trunc", encoding="utf-8")
    (tmp_path / "council_outcomes" / "council_str.json").write_text('"oops"', encoding="utf-8")
    # a corrupt pre-existing routing.json (freeze overwrites it)
    (tmp_path / "scoreboard" / "routing.json").write_text("[1,2]", encoding="utf-8")

    from trinity_local.launchpad_page import write_portal_html

    write_portal_html()  # raises if the freeze or render crashes on corrupt state
    assert (tmp_path / "portal_pages" / "launchpad.html").exists(), (
        "write_portal_html did not produce a launchpad under corrupt state"
    )
    # The frozen routing.json the memory viewer reads must be valid JSON with the
    # good outcome's task_type — not the corrupt list we seeded (freeze recomputed
    # from the one good outcome, skipping the three corrupt ones).
    frozen = json.loads((tmp_path / "scoreboard" / "routing.json").read_text())
    assert isinstance(frozen, dict) and "t" in frozen.get("by_task_type", {})


def test_cortex_load_routing_patterns_wrong_type(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path, picks="[1,2,3]")
    from trinity_local import cortex
    assert cortex.load_routing_patterns() == {}  # wrong-type → no patterns, no crash


def test_telemetry_iter_council_payloads_skips_non_dict(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path, outcome="[1,2]")  # a list-typed outcome
    from trinity_local import telemetry
    payloads = telemetry._iter_council_payloads()
    assert all(isinstance(p, dict) for p in payloads)  # the list-typed one is dropped


@pytest.mark.parametrize(
    "label,corrupt_routing_label",
    [
        # routing_label.task_type as a wrong-type LIST/DICT — becomes a dict key
        # in aggregate_routing_table (by_task_real_contests[task_type],
        # by_task_scores.setdefault(task_type, ...)). CouncilRoutingLabel.from_dict
        # does NOT coerce task_type, so a hand-edited list/dict survives into the
        # aggregator raw → `TypeError: unhashable type`.
        ("task_type_list", {"task_type": ["design", "arch"], "winner": "claude"}),
        ("task_type_dict", {"task_type": {"k": "design"}, "winner": "claude"}),
        # routing_label.winner as a wrong-type LIST/DICT — becomes a dict key in
        # by_task_wins[task_type][winner]. from_dict runs winner through
        # normalize_provider_slug, which passes a non-str through UNCHANGED, so a
        # list/dict winner reaches the unhashable dict-key op raw.
        ("winner_list", {"task_type": "design", "winner": ["claude", "codex"]}),
        ("winner_dict", {"task_type": "design", "winner": {"k": "claude"}}),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_personal_routing_survives_wrong_type_label_keys(
    tmp_path, monkeypatch, label, corrupt_routing_label
):
    """compute_personal_routing_table() must not crash when ONE hand-edited
    council_outcomes/*.json carries a wrong-type `routing_label.task_type` or
    `routing_label.winner` (a list/dict), and the WHOLE routing cheat-sheet card
    must NOT blank — every other healthy council survives.

    The founder symptom: aggregate_routing_table uses both fields as dict keys
    (by_task_real_contests[task_type], by_task_wins[task_type][winner]); an
    unhashable list/dict key raised `TypeError: unhashable type: 'list'` out of
    aggregate_routing_table, and because _load_personal_routing_table wraps the
    whole call in `except Exception: return None`, ONE corrupt council blanked the
    ENTIRE routing table card (every healthy council lost) — the exact
    `overall`-coercion sibling symptom. This is NOT caught by the existing
    whole-file-wrong-type outcome cases: those are skipped by load_council_outcome
    at parse, never reaching the aggregator; a VALID outcome with a wrong-type
    INNER label field DOES reach it.
    """
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)

    def _member(provider):
        return {
            "provider": provider,
            "output_text": (
                "A thorough, complete answer that is comfortably over the "
                "fifty-character substantive floor and ends cleanly."
            ),
        }

    def _clean(cid, winner="claude"):
        return {
            "council_run_id": cid, "bundle_id": "b", "task_cluster_id": "c",
            "primary_provider": "codex", "winner_provider": winner,
            "member_results": [_member("claude"), _member("codex")],
            "routing_label": {
                "winner": winner, "confidence": "high", "task_type": "design",
                "provider_scores": {"claude": {"overall": 0.9}, "codex": {"overall": 0.6}},
                "agreed_claims": [], "disagreed_claims": [],
            },
            "metadata": {"task_type": "design"},
        }

    # 3 clean "design" councils so the task_type clears MIN_BEST_SAMPLES (=3) and
    # earns a `best_per_task_type` entry — the survival witness.
    for i in range(3):
        (tmp_path / "council_outcomes" / f"council_clean_{i:02d}.json").write_text(
            json.dumps(_clean(f"council_clean_{i:02d}")), encoding="utf-8")

    # The ONE corrupt council with a wrong-type inner label field.
    corrupt = _clean("council_corrupt")
    corrupt["routing_label"].update(corrupt_routing_label)
    (tmp_path / "council_outcomes" / "council_corrupt.json").write_text(
        json.dumps(corrupt), encoding="utf-8")

    from trinity_local.council_runtime import load_council_outcome
    from trinity_local.personal_routing import (
        compute_personal_routing_table,
        invalidate_cache,
    )

    # BITE PRECONDITION (render-independent): the corrupt inner field genuinely
    # survives load_council_outcome as the wrong type — so the guard bites the
    # coercion, not a vacuous drop. If load ever starts coercing this upstream,
    # this fails loudly rather than passing the survival check vacuously.
    loaded = load_council_outcome("council_corrupt")
    assert loaded is not None and loaded.routing_label is not None, (
        "precondition: the corrupt council must still LOAD with a routing_label "
        "(carrying the wrong-type field) — else the survival check is vacuous"
    )
    if "task_type" in label:
        corrupt_field, seeded_value = "task_type", loaded.routing_label.task_type
    else:
        corrupt_field, seeded_value = "winner", loaded.routing_label.winner
    assert not isinstance(seeded_value, str), (
        f"precondition: corrupt {corrupt_field} was coerced to str upstream "
        f"({label}) — guard would pass vacuously"
    )

    invalidate_cache()
    # The production cached path — NO try/except, so a raw unhashable-key
    # TypeError surfaces here (the founder crash).
    table = compute_personal_routing_table()

    # SURVIVAL: the whole card is NOT blanked — the 3 healthy design councils
    # still produce a best pick. (Pre-fix: aggregate_routing_table raised, the
    # cached path raised, and _load_personal_routing_table returned None — the
    # entire card gone.)
    assert "design" in table.get("by_task_type", {}), (
        f"routing card blanked by one corrupt {label}: healthy councils lost"
    )
    assert table.get("best_per_task_type", {}).get("design") == "claude", (
        f"corrupt {label} blanked the routing best-pick for the healthy task_type"
    )

    # The launchpad wrapper (which catches + returns None) must therefore NOT
    # return None — the card renders on the home surface.
    from trinity_local.launchpad_data import _load_personal_routing_table
    invalidate_cache()
    assert _load_personal_routing_table() is not None, (
        f"launchpad routing card returned None (blanked) under one corrupt {label}"
    )


def test_status_topics_summary_wrong_type_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    p = tmp_path / "topics.json"
    p.write_text("[1,2,3]", encoding="utf-8")
    from trinity_local.commands.status import _topics_summary
    assert _topics_summary(p) == ""  # honors the "never crashes" docstring promise


def test_memory_viewer_reads_non_utf8_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _populate(tmp_path, core=b"# Core \xff\xfe still readable")
    from trinity_local.memory_viewer import _read_memory_contents
    contents = _read_memory_contents()  # must not raise UnicodeDecodeError
    # core.md is present (garbled with U+FFFD), NOT hidden behind a None empty-state.
    core_text = contents.get("core.md")
    assert core_text is not None
    assert "Core" in core_text


@pytest.mark.parametrize("label,blob", [
    # non-UTF8 bytes — the v1.7.202 read_text crash class
    ("non_utf8", b"## Generators \xff\xfe\x00\n### 1. broken \xff card\n"),
    # 0-byte file — an interrupted/concurrent write; the conditional tab keys on
    # .exists(), so a present-but-empty file must render an empty body, not crash
    ("empty", b""),
    # a half-written card (truncated mid-render)
    ("truncated", b"## Generators (cross-domain invariants)\n\n### 1. "),
])
def test_render_memory_viewer_survives_corrupt_generators(tmp_path, monkeypatch, label, blob):
    """generators.md (the lens-lift tier, added 2026-06-05) is the NEWEST memory
    file the viewer reads — and the OPTIONAL tab keys on `resolver().exists()`, so
    a corrupt-but-present file is shown, then read, then rendered. The sibling test
    above stops at `_read_memory_contents` on core.md; this drives the FULL
    `render_memory_viewer_html()` (what `portal-html` actually writes) against a
    corrupted generators.md — covering BOTH the new file AND the full render path,
    neither of which was guarded. Empirically confirmed survivable 2026-06-05; this
    pins it so a future generators-specific reader can't reintroduce the
    one-bad-file-nukes-the-whole-viewer crash."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _populate(tmp_path)  # valid baseline for every OTHER tab
    (tmp_path / "memories" / "generators.md").write_bytes(blob)

    from trinity_local.memory_viewer import (
        _read_memory_contents, _visible_files, render_memory_viewer_html,
    )
    # the conditional tab shows because the file exists, regardless of content
    assert "generators.md" in [f["name"] for f in _visible_files()], (
        f"generators tab vanished for corrupt-but-present file ({label})"
    )
    _read_memory_contents()  # must not raise UnicodeDecodeError on the bad bytes
    html = render_memory_viewer_html()  # raises if the full render crashes
    assert "generators.md" in html and len(html) > 5000, (
        f"viewer render degraded wrong for corrupt generators.md ({label})"
    )
    # one corrupt file must NOT nuke the viewer — the other tabs still render
    assert "lens.md" in html and "core.md" in html, (
        f"a corrupt generators.md took down sibling tabs ({label})"
    )


# ---------------------------------------------------------------------------
# Memory viewer — the OTHER state files (topics.json / picks.json / routing.json)
# read at render time. Added Iter 65 to close the wrong-type class across every
# file the viewer reads, not just generators.md. The viewer reads ALL files as
# text and parses JSON only in client-side JS, so the Python render is expected
# to stay graceful on a wrong-type JSON file — these pin that CLEAN result so a
# future server-side reader for these files can't silently reintroduce a crash.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("corrupt_file", ["topics.json", "picks.json", "routing.json"])
@pytest.mark.parametrize("bad", ["[]", '"x"', "9", "null", "{trunc", ""])
def test_render_memory_viewer_survives_wrong_type_json(tmp_path, monkeypatch, corrupt_file, bad):
    """The memory viewer reads topics.json / picks.json / routing.json at render
    time. `_slim_topics_for_viewer` shape-guards topics.json (root + basins + per
    basin) and `_memory_health` is try/except-wrapped, so a valid-JSON-WRONG-TYPE
    file must NOT 500 the viewer. Pins the Iter-65 clean result across every
    wrong-type variant on each JSON file the viewer inlines."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _populate(tmp_path)  # valid baseline for every OTHER file
    if corrupt_file == "topics.json":
        target = tmp_path / "memories" / "topics.json"
    else:
        target = tmp_path / "scoreboard" / corrupt_file
    target.write_text(bad, encoding="utf-8")

    from trinity_local.memory_viewer import render_memory_viewer_html
    html = render_memory_viewer_html()  # raises if the full render crashes
    # The viewer still renders, with every sibling tab intact — one bad file
    # must not nuke the whole viewer.
    assert len(html) > 5000 and "lens.md" in html and "core.md" in html, (
        f"a wrong-type {corrupt_file} ({bad!r}) took down the memory viewer render"
    )


# ---------------------------------------------------------------------------
# Council review render + share card — the OTHER two render entry points
# (Iter 65). `load_council_outcome` shape-guards the ROOT (non-dict → clean
# ValueError) but historically left NESTED wrong-type fields raw on the
# dataclass, so the unified review page (render_unified_council_page) and the
# share card (collect_card_data_from_outcome) crashed with AttributeError /
# TypeError / ValueError — a 500 on the persistent, shareable council artifact.
# These pin every empirically-confirmed render crash. Realistic triggers:
# an interrupted/concurrent write, a clobber, a schema-migration bug, a
# hand-edited outcome, or disk corruption on a council_outcomes/*.json.
# ---------------------------------------------------------------------------

def _valid_outcome_dict():
    return {
        "council_run_id": "council_xyz",
        "bundle_id": "bun_xyz",
        "task_cluster_id": "tc1",
        "primary_provider": "claude",
        "primary_model": "opus",
        "winner_provider": "claude",
        "synthesis_output": "Use Postgres.",
        "agreement_score": 0.8,
        "member_results": [
            {"provider": "claude", "model": "opus", "output_text": "Postgres"},
            {"provider": "codex", "model": "gpt", "output_text": "MySQL"},
        ],
        "routing_label": {
            "winner": "claude", "confidence": "high",
            "agreed_claims": ["use sql"],
            "disagreed_claims": [
                {"claim": "which sql", "providers_for": ["claude"],
                 "why_matters": "perf"}
            ],
            "provider_scores": {"claude": {"overall": 0.9}, "codex": {"overall": 0.7}},
        },
        "chain_steps": [{"step_index": 0, "model_provider": "claude", "output_text": "s"}],
        "metadata": {"round_number": 1, "chain_root_id": "council_xyz",
                     "failed_members": []},
    }


def _seed_outcome(home, outcome_obj):
    """Write the outcome + its bundle into an isolated home."""
    (home / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (home / "prompt_bundles").mkdir(parents=True, exist_ok=True)
    (home / "prompt_bundles" / "bun_xyz.json").write_text(
        json.dumps({"bundle_id": "bun_xyz", "task_cluster_id": "tc1",
                    "task_text": "Best DB?", "goal": "pick",
                    "origin_provider": "claude"}),
        encoding="utf-8")
    (home / "council_outcomes" / "council_xyz.json").write_text(
        outcome_obj if isinstance(outcome_obj, str) else json.dumps(outcome_obj),
        encoding="utf-8")


def _mutate(field_path, value):
    """Deep-copy the valid outcome and set one nested field to a wrong type."""
    import copy
    o = copy.deepcopy(_valid_outcome_dict())
    cur = o
    *parents, last = field_path
    for p in parents:
        cur = cur[p]
    cur[last] = value
    return o


# (label, field_path, wrong-type value, symptom the un-fixed code raised)
_COUNCIL_WRONGTYPE_CASES = [
    ("routing_label=str", ["routing_label"], "oops",
     "AttributeError: 'str' object has no attribute 'winner'"),
    ("routing_label=int", ["routing_label"], 3,
     "AttributeError: 'int' object has no attribute 'winner'"),
    ("routing_label=list", ["routing_label"], [1, 2],
     "AttributeError: 'list' object has no attribute 'winner'"),
    ("provider_scores=str", ["routing_label", "provider_scores"], "x",
     "AttributeError: 'str' object has no attribute 'items'"),
    ("provider_scores=list", ["routing_label", "provider_scores"], [1],
     "AttributeError: 'list' object has no attribute 'items'"),
    # INNER per-provider corruption: provider_scores IS a dict (passes the items()
    # guard) but ONE provider's `overall` is a wrong type. The unified review render
    # did `f"{overall:.1f}"` on it — a string overall raised "Unknown format code
    # 'f' for object of type 'str'", a NaN/bool painted "nan"/"1.0". The sibling
    # reader of provider_scores the earlier shape-sweep (which only coerced the
    # WHOLE provider_scores to {}) left raw. (#304 inner-value class.)
    ("overall=str", ["routing_label", "provider_scores", "codex", "overall"], "abc",
     "ValueError: Unknown format code 'f' for object of type 'str' (f-string overall:.1f)"),
    ("overall=nan", ["routing_label", "provider_scores", "codex", "overall"], float("nan"),
     "router/review paints literal 'nan' as a provider's Overall score"),
    ("provider_scores_sub=str", ["routing_label", "provider_scores", "codex"], "notadict",
     "AttributeError: 'str' object has no attribute 'get' (scores.get('overall'))"),
    ("agreed_claims=str", ["routing_label", "agreed_claims"], "notalist",
     "share card iterates agreed_claims chars / render mis-renders"),
    ("agreed_claims=int", ["routing_label", "agreed_claims"], 9,
     "TypeError: 'int' object is not iterable"),
    ("disagreed_claims=int", ["routing_label", "disagreed_claims"], 3,
     "TypeError: 'int' object is not subscriptable (share card disagreed_claims[0])"),
    ("disagreed_claims=str", ["routing_label", "disagreed_claims"], "x",
     "share card subscripts disagreed_claims[0]"),
    ("metadata=str", ["metadata"], "oops",
     "AttributeError: 'str' object has no attribute 'get'"),
    ("metadata=int", ["metadata"], 3,
     "AttributeError: 'int' object has no attribute 'get'"),
    ("metadata=list", ["metadata"], [1, 2],
     "AttributeError: 'list' object has no attribute 'get'"),
    ("round_number=str", ["metadata", "round_number"], "abc",
     "ValueError: invalid literal for int() with base 10: 'abc'"),
    ("synthesis_output=int", ["synthesis_output"], 5,
     "TypeError: expected string or bytes-like object, got 'int'"),
    ("synthesis_output=list", ["synthesis_output"], [1, 2],
     "TypeError: re.sub on a non-str synthesis_output"),
    ("member_results=str", ["member_results"], "oops",
     "TypeError: CouncilMemberResult(** 'o') — iterating a str member_results"),
    ("member_results=int", ["member_results"], 7,
     "TypeError: 'int' object is not iterable"),
    ("member_results=[nondict]", ["member_results"], ["x", 5],
     "TypeError: CouncilMemberResult(** 'x')"),
    ("chain_steps=[wrongkeys]", ["chain_steps"], [{"bogus": 1}],
     "TypeError: CouncilChainStep missing required step_index/model_provider"),
]


@pytest.mark.parametrize("label,field_path,value,symptom", _COUNCIL_WRONGTYPE_CASES)
def test_render_unified_council_page_survives_wrong_type(
    tmp_path, monkeypatch, label, field_path, value, symptom
):
    """A corrupt council_outcomes/*.json with a NESTED wrong-type field must not
    crash the unified review render. `load_council_outcome` now coerces wrong-type
    nested fields (routing_label / metadata / provider_scores / claims /
    member_results / chain_steps) to safe shapes at the load boundary, and the
    render guards synthesis_output / round_number scalars. Symptom on the un-fixed
    code: {symptom}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _seed_outcome(tmp_path, _mutate(field_path, value))

    from trinity_local.council_runtime import load_council_outcome, load_prompt_bundle
    from trinity_local.council_review import (
        render_unified_council_page, write_unified_council_page,
    )
    outcome = load_council_outcome("council_xyz")  # must not raise on wrong-type nesting
    bundle = load_prompt_bundle(outcome.bundle_id)
    html = render_unified_council_page(bundle, outcome)  # raises if render crashes
    assert isinstance(html, str) and len(html) > 5000, (
        f"unified council render degraded wrong for {label} — expected the page "
        f"to still render (symptom on un-fixed code: {symptom})"
    )
    # A corrupt per-provider `overall` (NaN/Inf) must not paint the literal token
    # "nan"/"inf" into the routing-scores table on this PERSISTENT, shareable
    # artifact — the row must drop, not render junk. (Scoped to the score cell so a
    # legit word containing these letters elsewhere can't false-trip.)
    import re as _re
    for _cell in _re.findall(r'<td>([^<]*)</td>', html):
        _t = _cell.strip().lower()
        assert _t not in ("nan", "inf", "-inf", "+inf"), (
            f"council review painted a junk score cell {_cell!r} for {label} — "
            f"finite_float_or_none must skip a non-finite provider overall"
        )
    # The page still writes to disk (the artifact a review-link / share opens).
    path = write_unified_council_page(bundle, outcome)
    assert path.exists(), f"unified council page failed to write for {label}"


@pytest.mark.parametrize("label,field_path,value,symptom", _COUNCIL_WRONGTYPE_CASES)
def test_council_share_card_survives_wrong_type(
    tmp_path, monkeypatch, label, field_path, value, symptom
):
    """`trinity-local council-share` renders a PNG from the SAME outcome. The
    share-card builder (collect_card_data_from_outcome) subscripts
    `disagreed_claims[0]` and iterates `agreed_claims` unguarded, so a wrong-type
    claims field crashed council-share with '_ object is not subscriptable'. With
    the load-boundary coercion these degrade to an honest empty card. Symptom on
    the un-fixed code: {symptom}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    _seed_outcome(tmp_path, _mutate(field_path, value))

    from trinity_local.council_runtime import load_council_outcome
    from trinity_local.council_card import (
        collect_card_data_from_outcome, render_council_card,
    )
    outcome = load_council_outcome("council_xyz")
    card_data = collect_card_data_from_outcome(outcome)  # raises if claims wrong-type
    png = render_council_card(card_data)  # raises if the PNG render crashes
    assert isinstance(png, (bytes, bytearray)) and len(png) > 1000, (
        f"council share card degraded wrong for {label} — expected a valid PNG "
        f"(symptom on un-fixed code: {symptom})"
    )


_ME_CARD_WRONGTYPE_POLE = [
    # (label, pole_a value, pole_b value, symptom on the un-fixed code)
    ("pole_a=int", 123, "cleverness", "TypeError: 'int' object is not iterable"),
    ("pole_a=list", ["a", "b"], "cleverness",
     "wrap_text iterates list elements, ch.isspace() raises on a non-str element"),
    ("pole_b=int", "clarity", 7, "TypeError: 'int' object is not iterable"),
    ("pole_b=float", "clarity", 1.5, "TypeError: 'float' object is not iterable"),
    ("pole_a=dict", {"x": 1}, "cleverness",
     "wrap_text iterates dict keys, str ops raise on the key sequence"),
]


@pytest.mark.parametrize("label,pole_a,pole_b,symptom", _ME_CARD_WRONGTYPE_POLE)
def test_me_card_survives_wrong_type_pole(tmp_path, monkeypatch, label, pole_a, pole_b, symptom):
    """`trinity-local me-card` (and the launchpad "Save as PNG card" dispatch)
    renders the 1200x630 taste PNG from me/lenses.json. The PNG text-shapers
    (strip_unrenderable / wrap_text / fit_one_line) iterate the lens pole / failure
    char-by-char (`for ch in text`), but LensPair is a plain dataclass with NO
    __post_init__ type check, so a hand-edited / corrupt me/lenses.json row whose
    pole is a non-string (`LensPair(**row)` accepts it) flowed un-coerced through
    `collect_card_data` and crashed render_me_card with `'int' object is not
    iterable` (the #258 corrupt-state class on the PUBLICLY-shared taste card).
    `collect_card_data` now coerces every text field to str at the read boundary
    (mirrors council_card.collect_card_data_from_outcome). Symptom on the un-fixed
    code: {symptom}."""
    import json as _json
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    (tmp_path / "me").mkdir(parents=True, exist_ok=True)
    (tmp_path / "me" / "lenses.json").write_text(
        _json.dumps({"lenses": [{
            "pole_a": pole_a, "pole_b": pole_b,
            "failure_a": 999, "failure_b": None,
            "basins_spanned": ["b1", "b2"],
        }]}),
        encoding="utf-8",
    )

    from trinity_local.me.pair_mining import load_lenses
    from trinity_local.me_card import collect_card_data, render_me_card

    # BITE PRECONDITION (render-independent): the seed genuinely reaches
    # collect_card_data with a non-string pole INSIDE a valid LensPair (so the
    # bite is the str() coercion, not a vacuous drop/parse-fail). LensPair must
    # have actually been constructed with the wrong-type pole.
    pairs = load_lenses()
    assert pairs, f"seed for {label} produced no LensPair — fixture is vacuous"
    assert not isinstance(pairs[0].pole_a, str) or not isinstance(pairs[0].pole_b, str), (
        f"seed for {label} carries only str poles — would not exercise the coercion"
    )

    data = collect_card_data()  # raises 'object is not iterable' on the un-fixed code
    png = render_me_card(data)  # PNG text-shapers iterate the pole/failure
    assert isinstance(png, (bytes, bytearray)) and len(png) > 1000, (
        f"me-card render degraded wrong for {label} — expected a valid taste PNG "
        f"(symptom on un-fixed code: {symptom})"
    )


def test_memory_viewer_picks_margin_map_survives_nonfinite_margin(patch_trinity_home):
    """The viewer's own picks.json reader `_picks_margin_fmt_map` does
    `int(round(m, 3))` to build the margin-format map — a NaN/Inf margin (a
    poisoned/hand-edited picks.json) is a float that passed the isinstance check
    then raised `ValueError: cannot convert float NaN to integer`, crashing
    `render_memory_viewer_html` -> `write_portal_html` (the whole served portal).
    Sibling of `_load_cortex_rules._safe_number` on the launchpad path — both
    readers of the SAME corrupt file must degrade, not crash."""
    from trinity_local.state_paths import scoreboard_dir
    from trinity_local.memory_viewer import (
        _picks_margin_fmt_map,
        render_memory_viewer_html,
    )

    sdir = scoreboard_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    # NaN + Inf + non-numeric-str margins beside a valid 0.625 one.
    (sdir / "picks.json").write_text(
        '{"b00":{"winner":"claude","margin":NaN,"count":3,"n_episodes":3,"evidence":[]},'
        '"b01":{"winner":"codex","margin":"abc","count":2,"n_episodes":2,"evidence":[]},'
        '"b02":{"winner":"antigravity","margin":0.625,"count":4,"n_episodes":4,"evidence":[]}}',
        encoding="utf-8",
    )
    fmt = _picks_margin_fmt_map((sdir / "picks.json").read_text())
    # The valid margin is kept; the NaN/Inf/non-numeric ones are skipped (not crash).
    assert fmt == {"0.625": "0.62"}, f"REGRESSION: bad margin not skipped: {fmt!r}"
    # And the full render does not raise (the portal-write path).
    html = render_memory_viewer_html()
    assert "memory" in html.lower() and len(html) > 1000


# ---------------------------------------------------------------------------
# council_outcomes/*.json `provider_scores[provider].overall` — the SOURCE the
# routing scoreboard (routing.json + the launchpad cheat-sheet) is aggregated
# from. `personal_routing.aggregate_routing_table` did a bare `float(overall)`,
# so ONE council with a non-numeric overall ValueError'd; and since
# `_load_personal_routing_table` wraps the call in `except Exception: return
# None`, that single corrupt council BLANKED THE ENTIRE routing cheat-sheet card
# (every healthy council lost) AND silently skipped the routing.json freeze in
# write_portal_html. (#304 inner-value class — the aggregation SOURCE, not a
# display reader.)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad,sym",
    [("abc", "ValueError: could not convert string to float: 'abc'"),
     (float("nan"), "NaN poisons statistics.fmean → bare NaN in routing.json"),
     (True, "bool overall corrupts the per-provider mean")],
    ids=["overall_str", "overall_nan", "overall_bool"],
)
def test_aggregate_routing_table_survives_corrupt_overall(tmp_path, monkeypatch, bad, sym):
    """One corrupt `overall` must not crash `aggregate_routing_table` nor blank the
    whole routing card. Seeds a CLEAN council + a corrupt-overall council; asserts
    the aggregation survives, keeps the clean council's task_type, leaks NO NaN/Inf
    into any aggregated overall, AND the full launchpad payload still carries
    personalRoutingTable. Symptom on un-fixed code: {sym}."""
    import math
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    out = tmp_path / "council_outcomes"
    out.mkdir(parents=True, exist_ok=True)

    def _council(cid, tt, scores):
        return {
            "council_run_id": cid, "bundle_id": cid, "task_cluster_id": "c",
            "primary_provider": "claude", "winner_provider": "claude",
            "synthesis_output": "x", "created_at": "2026-06-01T00:00:00+00:00",
            "metadata": {"task_type": tt},
            "routing_label": {"task_type": tt, "winner": "claude",
                              "provider_scores": scores},
        }

    # CLEAN council — its task_type MUST survive the corrupt sibling.
    (out / "council_clean.json").write_text(json.dumps(
        _council("council_clean", "cleanwork",
                 {"claude": {"overall": 0.82}, "codex": {"overall": 0.61}})),
        encoding="utf-8")
    # CORRUPT-overall council (json.dumps allows NaN by default).
    (out / "council_bad.json").write_text(json.dumps(
        _council("council_bad", "badwork",
                 {"claude": {"overall": bad}, "codex": {"overall": 0.5}})),
        encoding="utf-8")

    from trinity_local import personal_routing
    personal_routing.invalidate_cache()
    recs = [json.loads(p.read_text()) for p in sorted(out.glob("council_*.json"))]
    table = personal_routing.aggregate_routing_table(iter(recs))  # raises pre-fix
    by_tt = table.get("by_task_type", {})
    assert "cleanwork" in by_tt, (
        f"the CLEAN council vanished when a sibling had a corrupt overall ({sym}) — "
        "the corrupt float must be skipped, not abort the whole aggregation"
    )
    for tt, provs in by_tt.items():
        for prov, sub in provs.items():
            ov = sub.get("overall")
            assert not (isinstance(ov, float) and not math.isfinite(ov)), (
                f"a non-finite overall leaked into routing.json[{tt}][{prov}]={ov!r}"
            )
    # FULL render path: the cheat-sheet card must NOT vanish (it returned None
    # pre-fix because the broad except swallowed the ValueError).
    personal_routing.invalidate_cache()
    from trinity_local.launchpad_data import _load_personal_routing_table
    assert _load_personal_routing_table() is not None, (
        "the corrupt-overall council blanked the WHOLE routing cheat-sheet card"
    )


@pytest.mark.parametrize(
    "by_rej,sym",
    [({"REFRAME": {"mean_score": 0.7, "count": "abc"},
       "REDIRECT": {"mean_score": 0.5, "count": 4}},
      "ValueError: invalid literal for int() with base 10: 'abc' (int(count))"),
     ({"REFRAME": {"mean_score": 0.7, "count": float("nan")},
       "REDIRECT": {"mean_score": 0.5, "count": 4}},
      "ValueError: cannot convert float NaN to integer (int(count))"),
     ({"REFRAME": {"mean_score": "abc", "count": 3},
       "REDIRECT": {"mean_score": 0.5, "count": 4}},
      "TypeError: '<' not supported between 'str' and 'float' (sorted by mean)")],
    ids=["count_str", "count_nan", "mean_str"],
)
def test_eval_summary_survives_corrupt_axis_numeric(tmp_path, monkeypatch, by_rej, sym):
    """The launchpad eval leaderboard `_eval_summary` reads each axis `count`
    (`int(...)`) and sorts by `mean` — a non-numeric/NaN count or mean in ONE
    corrupt eval result crashed it and BLANKED THE WHOLE launchpad. Drives the full
    launchpad payload with the corrupt seed. Symptom on un-fixed code: {sym}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "eval_set1__model_claude.json").write_text(json.dumps({
        "eval_id": "set1", "target_provider": "claude", "target_model": "opus",
        "aggregate_score": 0.7, "items_completed": 12, "items_total": 12,
        "items_failed": 0, "completed_at": "2026-06-15T10:00:00+00:00",
        "by_rejection_type": by_rej, "items": [{"judge_provider": "claude"}],
    }), encoding="utf-8")

    from trinity_local.launchpad_data import _eval_summary
    summary = _eval_summary()  # raises pre-fix
    assert isinstance(summary, dict) and summary.get("has_results"), (
        f"a corrupt eval axis numeric took down the eval card (symptom: {sym})"
    )
    # FULL render path: the whole launchpad must still build + serialize strict JSON.
    from pathlib import Path as _P
    from trinity_local.launchpad_data import build_page_data
    data = build_page_data(live_review_path=_P("review_pages/live_council.html"),
                           recent_councils=[])
    json.dumps(data, allow_nan=False)  # no bare NaN leaked into page_data


@pytest.mark.parametrize(
    "bad_field,bad_value,sym",
    [("items_completed", "abc", "ValueError: invalid literal for int() (int(items_completed))"),
     ("items_completed", float("nan"), "ValueError: cannot convert float NaN to integer"),
     ("items_failed", "boom", "ValueError on int(items_failed) in the exclusion-disclosure")],
    ids=["items_completed_str", "items_completed_nan", "items_failed_str"],
)
def test_eval_summary_survives_corrupt_items_counts(tmp_path, monkeypatch, bad_field, bad_value, sym):
    """`_eval_summary` does `int(payload.get("items_completed"))` (hero-confidence
    gate) and `int(r.get("items_failed"))` (exclusion disclosure) — a non-numeric
    or NaN count in ONE corrupt result crashed it and BLANKED THE WHOLE launchpad.
    Two scored providers so the comparison/exclusion path runs. Symptom: {sym}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    base = {
        "eval_id": "set1", "target_model": "opus", "aggregate_score": 0.7,
        "items_completed": 12, "items_total": 12, "items_failed": 0,
        "completed_at": "2026-06-15T10:00:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.7, "count": 6}},
        "items": [{"judge_provider": "claude"}],
    }
    bad = {**base, "target_provider": "claude", bad_field: bad_value}
    good = {**base, "target_provider": "codex", "aggregate_score": 0.6}
    (results / "eval_set1__model_claude.json").write_text(json.dumps(bad), encoding="utf-8")
    (results / "eval_set1__model_codex.json").write_text(json.dumps(good), encoding="utf-8")

    from trinity_local.launchpad_data import _eval_summary, build_page_data
    summary = _eval_summary()  # raises pre-fix
    assert isinstance(summary, dict) and summary.get("has_results")
    from pathlib import Path as _P
    data = build_page_data(live_review_path=_P("review_pages/live_council.html"),
                           recent_councils=[])
    json.dumps(data, allow_nan=False)


@pytest.mark.parametrize(
    "by_rej,agg,want_empty,sym",
    [({"REFRAME": {"mean_score": "abc", "count": 3}}, 0.7, False,
      "ValueError: could not convert string to float: 'abc' (float(mean_score))"),
     ({"REFRAME": {"mean_score": 0.7, "count": 3}}, float("nan"), True,
      "f-string painted 'Claude scored nan' on the PUBLIC eval share card")],
    ids=["axis_mean_str", "aggregate_nan"],
)
def test_eval_card_survives_corrupt_result(tmp_path, monkeypatch, by_rej, agg, want_empty, sym):
    """The PUBLIC eval-share PNG (`collect_card_data_from_result` →
    `render_eval_card`) crashed on a non-numeric axis mean and PAINTED literal
    "nan" when the aggregate was NaN (the empty-state gate only caught None).
    Symptom on un-fixed code: {sym}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    p = results / "eval_set1__model_claude.json"
    p.write_text(json.dumps({
        "eval_id": "set1", "target_provider": "claude", "target_model": "opus",
        "aggregate_score": agg, "items_completed": 12, "items_total": 12,
        "items_failed": 0, "completed_at": "2026-06-15T10:00:00+00:00",
        "by_rejection_type": by_rej, "items": [],
    }), encoding="utf-8")

    from trinity_local.evals.runner import load_run_result
    from trinity_local.eval_card import (
        collect_card_data_from_result, render_eval_card,
    )
    rr = load_run_result(p)
    cd = collect_card_data_from_result(rr)  # raises pre-fix on a non-numeric mean
    # A NaN aggregate is normalized to None so the gate falls to the empty state.
    if want_empty:
        assert cd.aggregate_score is None, (
            f"a NaN aggregate was NOT normalized to None — it would paint 'scored "
            f"nan' on the public card (symptom: {sym})"
        )
    png = render_eval_card(cd)  # raises pre-fix / paints junk on NaN
    assert isinstance(png, (bytes, bytearray)) and len(png) > 1000


def test_cli_leaderboard_rows_survive_corrupt_axis(tmp_path, monkeypatch):
    """`_collect_leaderboard_rows` (shared by `eval-show --compare` /
    `eval-share --compare`) did a bare `float(stats["mean_score"])` — one corrupt
    eval result crashed the command with a raw traceback. Also coerces a NaN
    aggregate to None so the leaderboard doesn't print/paint 'nan'."""
    import math
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    # corrupt-axis target + a NaN-aggregate target + a clean target
    (results / "eval_e1__model_claude.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": "claude", "target_model": "m",
        "aggregate_score": 0.8, "items_completed": 10, "items_total": 10,
        "items_failed": 0, "completed_at": "2026-06-15T10:00:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": "abc", "count": 3}}, "items": [],
    }), encoding="utf-8")
    (results / "eval_e1__model_codex.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": "codex", "target_model": "m",
        "aggregate_score": float("nan"), "items_completed": 10, "items_total": 10,
        "items_failed": 0, "completed_at": "2026-06-15T10:00:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.6, "count": 3}}, "items": [],
    }), encoding="utf-8")

    from trinity_local.commands.eval import _collect_leaderboard_rows
    rows, _ = _collect_leaderboard_rows("e1")  # raises pre-fix
    assert len(rows) == 2
    for r in rows:
        agg = r.get("aggregate_score")
        assert agg is None or (isinstance(agg, float) and math.isfinite(agg)), (
            f"a non-finite aggregate leaked into a leaderboard row ({agg!r}) — it "
            "would print/paint 'nan'"
        )


# --- eval SHARE-CARD wrong-type provider/model (the iter-455 me-card sibling) ---
# A provider slug / model name is text. A hand-edited / half-migrated eval result
# JSON whose `target_provider` or `target_model` is the WRONG TYPE flows RAW through
# load_run_result (raw.get("target_provider", "") / raw.get("target_model")) into the
# PUBLIC, journalist-screenshottable eval-share PNG.

_EVAL_CARD_WRONGTYPE = [
    # (label, target_provider, target_model, symptom on the un-fixed code)
    ("provider=int", 777, "m-v1",
     "_provider_display_name: provider_model_brand('')→ then 777.capitalize() "
     "AttributeError: 'int' object has no attribute 'capitalize'"),
    ("provider=list", ["claude"], "m-v1",
     "['claude'].capitalize() AttributeError on the headline provider name"),
    ("model=list", "claude", [1, 2, 3],
     "_strip_unrenderable iterates the model on the identity line: "
     "TypeError: 'list' object is not iterable"),
    ("model=int", "claude", 99,
     "for ch in 99 on the model identity line: 'int' object is not iterable"),
]


@pytest.mark.parametrize("label,provider,model,symptom", _EVAL_CARD_WRONGTYPE)
def test_eval_card_survives_wrong_type_provider_model(
    tmp_path, monkeypatch, label, provider, model, symptom,
):
    """The single-target eval-share PNG (`trinity-local eval-share`, the MCP
    eval-share artifact) renders from ~/.trinity/evals/results/*.json via
    load_run_result -> collect_card_data_from_result -> render_eval_card. The
    headline runs the provider slug through `_provider_display_name` (which falls
    back to `provider.capitalize()`) and the identity line iterates the model via
    `_strip_unrenderable` — both crash on a wrong-TYPE field that flows RAW from
    disk (the #258/#304 corrupt-state vein, the iter-455 me-card sibling). The
    provider is now str()-coerced at the display chokepoint shared by every card
    surface; the model is None-preservingly str()-coerced at the read boundary.
    Symptom on the un-fixed code: {symptom}."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    # A valid aggregate + by_axis so the card reaches the populated render path
    # (the empty-state gate `aggregate_score is None or not by_axis` would otherwise
    # short-circuit before the provider/model shapers — that's where the crash is).
    (results / "eval_e1__model_x.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": provider, "target_model": model,
        "aggregate_score": 0.82, "items_completed": 4, "items_total": 4,
        "items_failed": 0, "completed_at": "2026-06-23T00:00:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.8, "count": 2}},
        "items": [{"judge_provider": "claude"}],
    }), encoding="utf-8")

    from trinity_local.evals.runner import load_run_result
    from trinity_local.eval_card import (
        collect_card_data_from_result,
        render_eval_card,
    )

    res = load_run_result(results / "eval_e1__model_x.json")
    assert res is not None, f"seed for {label} failed to load — fixture is vacuous"
    # BITE PRECONDITION (render-independent): the wrong-type field genuinely
    # reaches the dataclass (load_run_result does NOT coerce target_provider, so
    # the bite is the display/read coercion, not a vacuous load-time drop).
    assert not isinstance(res.target_provider, str) or not isinstance(
        res.target_model, str
    ), f"seed for {label} carries only str fields — would not exercise the coercion"

    data = collect_card_data_from_result(res)
    # Must reach the populated path (else the crash site is never exercised).
    assert data.aggregate_score is not None and data.by_axis, (
        f"seed for {label} short-circuited to the empty state — crash site unreached"
    )
    png = render_eval_card(data)  # raises on the un-fixed code
    assert isinstance(png, (bytes, bytearray)) and len(png) > 1000, (
        f"eval-share card degraded wrong for {label} — expected a valid PNG "
        f"(symptom on un-fixed code: {symptom})"
    )


def test_eval_leaderboard_rows_survive_unhashable_provider(tmp_path, monkeypatch):
    """`_collect_leaderboard_rows` (shared by `eval-show --compare` /
    `eval-share --compare`) used `target_provider` directly as a DICT KEY
    (`if target in by_target` / `by_target[target] = ...`). A hand-edited /
    half-migrated eval result whose `target_provider` is a dict/list (UNHASHABLE)
    slipped past the `if not target` falsy gate (truthy) and past
    normalize_provider_slug (non-str passes through), then crashed the command
    with `TypeError: unhashable type: 'dict'`. The reader now drops a non-str
    provider slug at the boundary (a row with no usable provider identity is not
    leaderboard material) and the clean rows still render. The #258/#304
    corrupt-state vein on the compare-card READER (the shape-guard boundary)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
    results = tmp_path / "evals" / "results"
    results.mkdir(parents=True, exist_ok=True)
    # UNHASHABLE dict provider (the exact crash), a non-str int provider, AND a
    # clean claude row so the leaderboard is non-empty after the bad rows drop.
    (results / "eval_e1__model_dict.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": {"k": "v"}, "target_model": 99,
        "aggregate_score": 0.6, "items_completed": 4, "items_total": 4,
        "items_failed": 0, "completed_at": "2026-06-23T00:02:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.5, "count": 3}},
        "items": [{"judge_provider": "claude"}],
    }), encoding="utf-8")
    (results / "eval_e1__model_int.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": 777, "target_model": "m",
        "aggregate_score": 0.7, "items_completed": 4, "items_total": 4,
        "items_failed": 0, "completed_at": "2026-06-23T00:01:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.7, "count": 3}},
        "items": [{"judge_provider": "claude"}],
    }), encoding="utf-8")
    (results / "eval_e1__model_claude.json").write_text(json.dumps({
        "eval_id": "e1", "target_provider": "claude", "target_model": "claude-opus-4-8",
        "aggregate_score": 0.9, "items_completed": 4, "items_total": 4,
        "items_failed": 0, "completed_at": "2026-06-23T00:03:00+00:00",
        "by_rejection_type": {"REFRAME": {"mean_score": 0.9, "count": 4}},
        "items": [{"judge_provider": "codex"}],
    }), encoding="utf-8")

    from trinity_local.commands.eval import _collect_leaderboard_rows
    from trinity_local.eval_card import (
        CompareCardData,
        render_compare_card,
        render_compare_matrix_card,
    )

    rows, _ = _collect_leaderboard_rows("e1")  # raises 'unhashable type: dict' pre-fix
    # The dict/int provider rows are dropped; only the clean claude row survives.
    assert [r["target"] for r in rows] == ["claude"], (
        "a non-str provider slug leaked into a leaderboard row — it would crash "
        "the dict-key dedup ('unhashable type: dict') or render a junk provider name"
    )
    cd = CompareCardData(rows=rows, eval_id="e1", mixed_eval_sets=False)
    for name, fn in (("compare", render_compare_card), ("matrix", render_compare_matrix_card)):
        png = fn(cd)
        assert isinstance(png, (bytes, bytearray)) and len(png) > 1000, (
            f"{name} leaderboard card degraded wrong — expected a valid PNG"
        )
