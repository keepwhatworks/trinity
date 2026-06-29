"""Generators pass (the lens lift): domain-diverse selection + dual autonomous
critique. All deterministic — no LLM, no real corpus, no quota.

The live LLM behavior was validated by hand on the real corpus 2026-06-04
(diverse evidence beat recency by 4-8x on invariant coverage; the dual critique
recovered the subsumed/off-plane generators with no human hints). These guard
the mechanical contract: the geometry is domain-balanced, the critique prompt
stays GENERIC (a region hint would be a covert human-in-the-loop = the
ratification step the founder rejected), and the orchestration runs both passes.
"""
from __future__ import annotations

from trinity_local.me import generators as gen


# ── flag gate ──────────────────────────────────────────────────────────────


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("TRINITY_LENS_GENERATORS", raising=False)
    assert gen.generators_enabled() is False
    monkeypatch.setenv("TRINITY_LENS_GENERATORS", "1")
    assert gen.generators_enabled() is True
    monkeypatch.setenv("TRINITY_LENS_GENERATORS", "off")
    assert gen.generators_enabled() is False


# ── evidence selection: domain-balanced, neutral signal ──────────────────────


def _fake_embed(t: str):
    t = t.lower()
    if t.startswith("soft"):
        return [1.0, 0.0, 0.0]
    if t.startswith("mat"):
        return [0.0, 1.0, 0.0]
    if t.startswith("fin"):
        return [0.0, 0.0, 1.0]
    return [0.34, 0.33, 0.33]


_CENTROIDS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def test_select_evidence_is_domain_balanced():
    """Software dominates the pool AND owns the longest turns, so a naive
    length-sort would return software-only. The selector must round-robin
    across basins and surface the rare materials/finance domains anyway."""
    pairs = (
        [(f"soft turn {'x' * 300} {i}",) for i in range(20)]   # majority + longest
        + [(f"mat turn {'y' * 120} {i}",) for i in range(3)]   # rare, shorter
        + [(f"fin turn {'z' * 120} {i}",) for i in range(3)]
    )
    sel = gen.select_domain_diverse_evidence(
        n=6, pool_size=100, pairs=pairs, centroids=_CENTROIDS, embed_fn=_fake_embed
    )
    assert len(sel) == 6
    # naive length-top-6 would be ALL software; the selector must include the rare domains
    assert any(s.startswith("mat") for s in sel), "selector missed the materials basin"
    assert any(s.startswith("fin") for s in sel), "selector missed the finance basin"
    # and it must not be software-only
    assert sum(s.startswith("soft") for s in sel) < 6


def test_select_evidence_empty_inputs_safe():
    assert gen.select_domain_diverse_evidence(pairs=[], centroids=_CENTROIDS, embed_fn=_fake_embed) == []
    assert gen.select_domain_diverse_evidence(pairs=[("soft x" * 10,)], centroids=[], embed_fn=_fake_embed) == []


# ── the critique prompt stays GENERIC (no covert ratification) ────────────────


def test_critique_prompt_carries_both_autonomous_signals():
    p = gen.build_critique_prompt("G1 leverage; G2 frame", ["tension a", "tension b"], ["evidence one"]).lower()
    assert "contradiction" in p              # signal (a): contradiction-split
    assert "off-plane" in p                  # signal (b): off-plane gap
    assert "method" in p and "content" in p  # the completeness check


def test_critique_prompt_leaks_no_region_hint():
    """A region hint ('physical objects', 'durability', 'pairwise'…) is a covert
    human pointing the model at the answer — exactly the ratification step the
    founder rejected. The template must derive everything from the tensions +
    evidence, never name a domain or an expected generator."""
    p = gen.build_critique_prompt("G1 leverage", ["tension a"], ["evidence one"]).lower()
    for hint in (
        "physical object", "durability", "durable", "atoms over bits", "heavy mass",
        "permanence", "weight", "material", "pairwise", "kintsugi", "financial moat",
        "asset", "k-nn",
    ):
        assert hint not in p, f"critique prompt leaks a region hint: {hint!r}"


# ── orchestration: both passes run, critique result is the answer ─────────────


_J1 = ('```json\n{"generators":[{"name":"Leverage","tension":"system over instance",'
       '"projections":{"software":"a","materials":"b","finance":"c"},"task_tensions":[1,2]}]}\n```')
_J2 = ('reasoning...\n```json\n{"generators":[{"name":"Compression","tension":"minimal over exhaustive",'
       '"projections":{"software":"x","epistemology":"y","finance":"z"},"task_tensions":[5]}]}\n```')


def test_build_generators_runs_both_passes():
    calls = []

    def mock_dispatch(prompt: str) -> str:
        calls.append(prompt)
        return _J1 if len(calls) == 1 else _J2

    r = gen.build_generators(dispatch_fn=mock_dispatch, tensions=["t1", "t2"], evidence=["ev1", "ev2"])
    assert r["ok"] is True
    assert len(calls) == 2, "expected pass-1 then dual-critique"
    assert "PROJECTIONS" in calls[0]      # pass-1 lift prompt
    assert "CRITIQUE PASS" in calls[1]    # pass-2 dual critique
    # the post-critique (pass-2) parsed set is the answer
    assert len(r["generators"]) == 1 and r["generators"][0]["name"] == "Compression"
    assert "## Generators" in r["cards"] and "Compression" in r["cards"]
    assert r["evidence_turns"] == 2 and r["task_tensions"] == 2


# ── structured parse + render ────────────────────────────────────────────────


def test_parse_generators_fenced_bare_and_invalid():
    g = gen.parse_generators(_J1)
    assert g and g[0]["name"] == "Leverage" and g[0]["task_tensions"] == [1, 2]
    assert len(g[0]["projections"]) == 3
    # bare object (no fence)
    bare = '{"generators":[{"name":"M","tension":"P over Q","projections":{"a":"1"},"task_tensions":[]}]}'
    assert gen.parse_generators(bare)[0]["name"] == "M"
    # garbage / empty / missing-required → None
    assert gen.parse_generators("no json at all") is None
    assert gen.parse_generators('```json\n{"generators":[]}\n```') is None
    # malformed entries (no projections / no name) are dropped, valid ones kept
    half = '{"generators":[{"name":"OK","tension":"a over b","projections":{"d":"e"}},{"name":""}]}'
    parsed = gen.parse_generators(half)
    assert len(parsed) == 1 and parsed[0]["name"] == "OK"


def test_render_generators_cards():
    gens = [{
        "name": "Leverage",
        "tension": "system over instance",
        "projections": {"software": "the rule", "materials": "the module"},
        "task_tensions": [1, 2, 3],
    }]
    md = gen.render_generators_cards(gens)
    assert "## Generators (cross-domain invariants)" in md
    # No imperative → the name is the headline, tension the italic subtitle.
    assert "### 1. Leverage" in md and "*system over instance*" in md
    assert "- **software** — the rule" in md
    assert "Projects task-tensions: 1, 2, 3" in md


def test_render_generators_cards_imperative_headline():
    """When the chairman emits an imperative ('X, don't Y'), it becomes the
    headline (the user-voice compression) and the geometry's name + tension drops
    to the explanatory subtitle — easier to read, in the user's own form."""
    gens = [{
        "name": "Compound the owned substrate",
        "imperative": "Pull, don't push",
        "tension": "leverage the installed base over external dependence",
        "projections": {"finance": "QOF on owned OZ land"},
        "task_tensions": [5],
    }]
    md = gen.render_generators_cards(gens)
    assert "### 1. Pull, don't push" in md
    assert "*Compound the owned substrate — leverage the installed base over external dependence*" in md


# ── memory-viewer: the generators tab is OPTIONAL (shown only when built) ──────


def test_generators_tab_is_conditional(tmp_path, monkeypatch):
    """The advanced on-demand generators tier must NOT show an empty tab to users
    who never ran the verb — but appears once generators.md exists. Core memories
    always show."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local import memory_viewer as mv

    names = [f["name"] for f in mv._visible_files()]
    assert "generators.md" not in names, "empty generators tab leaked to a fresh home"
    assert "lens.md" in names and "core.md" in names  # core memories always show

    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "generators.md").write_text("## Generators\n### 1. X", encoding="utf-8")
    names2 = [f["name"] for f in mv._visible_files()]
    assert "generators.md" in names2, "generators tab missing after the file exists"
    # ?file= validation must accept it regardless (always in ALLOWED_FILES)
    assert any(f["name"] == "generators.md" for f in mv.ALLOWED_FILES)


# ── the MCP tool handler writes generators.md + returns the structured set ─────


def test_mcp_lens_generators_handler(tmp_path, monkeypatch):
    """The `lens_generators` MCP handler runs the pass off-thread, writes
    generators.md, and returns the structured generators. build_generators is
    mocked — this guards the handler wiring, not the LLM."""
    import asyncio
    import json

    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    import trinity_local.me.generators as gmod

    monkeypatch.setattr(gmod, "build_generators", lambda **k: {
        "ok": True, "evidence_turns": 5, "task_tensions": 3,
        "generators": [{"name": "X", "tension": "a over b", "projections": {"s": "1"}, "task_tensions": [1]}],
        "cards": "## Generators\n### 1. X\n",
    })
    from trinity_local.mcp_server import _lens_generators

    out = asyncio.run(_lens_generators({}))
    payload = json.loads(out[0]["text"])
    assert payload["ok"] is True and payload["count"] == 1
    assert payload["generators"][0]["name"] == "X"

    from trinity_local.state_paths import generators_path
    assert generators_path().exists()


def test_build_generators_guards_empty():
    r = gen.build_generators(dispatch_fn=lambda p: "x", tensions=[], evidence=["ev"])
    assert r["ok"] is False and r["reason"] == "no tensions"
    r = gen.build_generators(dispatch_fn=lambda p: "x", tensions=["t1"], evidence=[])
    assert r["ok"] is False and r["reason"] == "no evidence"


def test_build_generators_empty_pass1_degrades():
    r = gen.build_generators(dispatch_fn=lambda p: "", tensions=["t1"], evidence=["ev1"])
    assert r["ok"] is False and "pass-1" in r["reason"]


# ── cross-surface: the generators "lift" and degeneracy/lens-health must count
#    tensions out of the SAME lens.md the SAME way (one shared `↔` predicate) ──


def _seed_discriminating_lens(home):
    """A lens.md with 4 WELL-FORMED tensions (numbered + `↔`) plus 1 MALFORMED
    heading (numbered `### 5.` but NO `↔` separator — a hand edit / non-tension).
    The malformed line is the discriminator: the old generators parser
    `^### \\d+\\.\\s*(.+)$` counted it (5); the degeneracy `↔` parser never did
    (4). The two surfaces read the SAME file and disagreed by one."""
    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "lens.md").write_text(
        "# Lens\n\n## Lenses (paired tensions)\n\n"
        "### 1. concrete ↔ abstract\n- Pure-concrete fails as: **myopia**\n"
        "- Pure-abstract fails as: **vagueness**\n- Supported by 7 decisions\n\n"
        "### 2. terse ↔ verbose\n- Pure-terse fails as: **opacity**\n"
        "- Pure-verbose fails as: **bloat**\n- Supported by 5 decisions\n\n"
        "### 3. action ↔ description\n- Pure-action fails as: **recklessness**\n"
        "- Pure-description fails as: **inertia**\n- Supported by 4 decisions\n\n"
        "### 4. decisive ↔ hedging\n- Pure-decisive fails as: **overconfidence**\n"
        "- Pure-hedging fails as: **paralysis**\n- Supported by 3 decisions\n\n"
        "### 5. malformed heading with no arrow separator\n"
        "- Pure-x fails as: **y**\n- Supported by 1 decisions\n",
        encoding="utf-8",
    )


def test_generators_and_lens_health_count_tensions_the_same_way(tmp_path, monkeypatch):
    """CROSS-SURFACE MISCOUNT GUARD. Both the generators "lift" (_current_tensions,
    feeds the prompt's numbered tension list + the surfaced task_tensions count)
    and the degeneracy/lens-health structure check parse tensions out of lens.md.
    A TENSION is a `### N. pole_a ↔ pole_b` heading — the `↔` is what makes it one.
    Founder symptom: a malformed/hand-edited numbered heading WITHOUT a `↔` got
    counted as a 5th tension by the generators lift while lens-health saw only 4,
    so the same file read as two tension totals AND the chairman was asked to lift
    a generator from a non-tension line. Both must now count 4."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_discriminating_lens(tmp_path)

    # FILE TRUTH: 4 well-formed tensions (number + ↔); 5 numbered headings total.
    import re

    md = (tmp_path / "memories" / "lens.md").read_text(encoding="utf-8")
    wellformed = re.findall(r"^###\s+\d+\.\s+(.+?)\s+↔\s+(.+?)\s*$", md, re.M)
    numbered_any = re.findall(r"^### \d+\.\s*(.+)$", md, re.M)
    assert len(wellformed) == 4, "fixture must seed exactly 4 well-formed tensions"
    assert len(numbered_any) == 5, (
        "fixture must seed exactly 5 numbered headings (4 valid + 1 malformed) so "
        "the divergence has something to bite on"
    )

    # SURFACE B: the generators "lift" — _current_tensions must count the 4 real
    # tensions and EXCLUDE the malformed arrow-less heading (the bite).
    lift = gen._current_tensions()
    assert len(lift) == 4, (
        f"generators _current_tensions counted {len(lift)} tensions but only 4 are "
        f"well-formed — the arrow-less `### 5.` heading leaked into the lift "
        f"(prompt pollution + a tension miscount vs lens-health): {lift!r}"
    )
    assert not any("malformed heading" in t for t in lift), (
        "the malformed `### 5.` heading (no ↔) must NOT appear in the generators "
        f"tension list — it is not a tension: {lift!r}"
    )

    # SURFACE A: the degeneracy / lens-health structure check parses the SAME
    # lens.md through the SAME shared predicate → it sees the SAME 4 tensions
    # (it flags the 1 zero/low-evidence one but never the arrow-less heading).
    from trinity_local.me.pipeline import iter_lens_tensions

    health_tensions = iter_lens_tensions(md)
    assert len(health_tensions) == len(lift) == 4, (
        f"CROSS-SURFACE MISCOUNT: the generators lift counts {len(lift)} tensions "
        f"but the degeneracy/lens-health parser counts {len(health_tensions)} out "
        f"of the SAME lens.md — they must agree (both 4, the well-formed total)"
    )
