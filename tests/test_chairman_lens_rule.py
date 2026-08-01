"""The two-stage winner rule (council_33f3f375c82f03b1, 2026-07-05).

Ablation evidence: the latent core.md read moved only 1/12 chairman picks
(noise floor 0/6). All three council members agreed on explicit
tension-scoring inside a quality gate. These tests pin the prompt contract:
named tensions present, the two-stage rule present, the quality gate stated
FIRST (taste never overrides a clearly better answer), and the compact-block
budget (never the 25KB lens.md)."""
from __future__ import annotations


def _bundle_and_members():
    from trinity_local.council_schema import CouncilMemberResult, PromptBundle
    bundle = PromptBundle(
        bundle_id="b1", task_cluster_id="c1",
        task_text="Choose a retention strategy.",
        goal="Find the strongest answer.",
        comparison_instructions="Prefer the strongest answer.",
        created_at="2026-07-05T00:00:00+00:00",
    )
    members = [
        CouncilMemberResult(provider="claude", model="claude-opus-4-8",
                            output_text="Answer one. " * 30),
        CouncilMemberResult(provider="codex", model="gpt-5.5",
                            output_text="Answer two. " * 30),
    ]
    return bundle, members


def _seed_lens(home, n_tensions=5):
    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    body = "# Lens\n\n" + "\n\n".join(
        f"### {i}. pole-{i}a ↔ pole-{i}b\n\nEvidence for tension {i}."
        for i in range(1, n_tensions + 1)
    )
    (mem / "lens.md").write_text(body, encoding="utf-8")
    (home / "core.md").write_text("You prefer executable artifacts.", encoding="utf-8")


def test_prompt_carries_tensions_and_two_stage_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_lens(tmp_path)
    from trinity_local.council_runtime import render_primary_council_prompt
    bundle, members = _bundle_and_members()
    prompt = render_primary_council_prompt(bundle, members)
    # named tensions present, by number
    assert "pole-1a ↔ pole-1b" in prompt and "pole-5a ↔ pole-5b" in prompt
    # the two-stage rule, quality gate FIRST
    assert "WINNER RULE" in prompt
    q = prompt.index("Stage 1 (quality gate)")
    tste = prompt.index("Stage 2 (taste decides close calls)")
    assert q < tste, "quality gate must come before the taste stage"
    assert "taste never" in prompt  # never overrides a clearly better answer
    assert "## Lens fit" in prompt  # citation section mandated
    # compact budget: the tensions+rule block must stay small (never 25KB lens.md)
    assert "Evidence for tension" not in prompt, "full lens.md body leaked into the prompt"


def test_prompt_survives_missing_lens(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.council_runtime import render_primary_council_prompt
    bundle, members = _bundle_and_members()
    prompt = render_primary_council_prompt(bundle, members)
    assert "WINNER RULE" not in prompt  # no tensions → no rule block (no empty scaffold)
    assert "Council member outputs" in prompt


def test_synthesis_is_prosecutorial_unconditionally(tmp_path, monkeypatch):
    """Slice (b): the chairman ADJUDICATES disagreements against evidence
    (Gauntlet — the multi-agent gain traces to adversarial synthesis, not
    summary). The mandate is unconditional: present with OR without a lens.
    Six markers across the four edit sites so a partial revert reds."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))  # no lens seeded on purpose
    from trinity_local.council_runtime import render_primary_council_prompt
    bundle, members = _bundle_and_members()
    prompt = render_primary_council_prompt(bundle, members)
    # the intro mandate: prosecutor, not summarizer; survive-the-evidence framing
    assert "PROSECUTOR, not a summarizer" in prompt
    assert "which side SURVIVES" in prompt
    # the PART 1 adjudication section
    assert "## Contested" in prompt
    # the structured resolution field + its emission rule
    assert '"resolution"' in prompt
    assert "the RESOLUTION" in prompt
    # an unresolved split is itself a verdict (never paper over the undecidable)
    assert "unresolved" in prompt.lower()


def test_resolution_survives_parse_into_the_stored_outcome():
    """Round-trip guard. The prompt asking for `resolution` is only half the
    wire — `_normalize_routing_dict` REBUILDS each disagreed claim from an
    explicit key list, so a field it doesn't name is silently dropped.

    That is what happened on the first real prosecutorial council
    (council_8be9509b3d4036ea, 2026-07-24): the chairman emitted resolution,
    the markdown rendered it, and the stored outcome had only
    claim/providers_for/providers_against/why_matters. Asserting the PROMPT
    contains the field (the test above) cannot catch that — only parsing can."""
    from trinity_local.council_runtime import parse_routing_label

    synthesis = """## Winner
- Codex.

```routing-json
{
  "winner": "codex",
  "confidence": "high",
  "agreed_claims": ["a"],
  "disagreed_claims": [
    {"claim": "A local gate is un-bypassable",
     "providers_for": ["antigravity"],
     "providers_against": ["codex"],
     "resolution": "codex survives: mandatory and recorded is not un-bypassable",
     "why_matters": "the decision turns on proof quality"}
  ]
}
```"""
    label, err = parse_routing_label(synthesis)
    assert label is not None, f"routing label failed to parse: {err}"
    claim = label.disagreed_claims[0]
    assert claim["resolution"] == (
        "codex survives: mandatory and recorded is not un-bypassable"
    ), f"resolution dropped on parse — stored keys: {sorted(claim)}"
    # the sibling fields must still round-trip (no regression from the addition)
    assert claim["why_matters"] == "the decision turns on proof quality"
    assert claim["providers_for"] == ["antigravity"]


class TestCombineSurvivors:
    """The combine-over-survivors section (founder call 2026-07-24). Ships DORMANT
    behind TRINITY_COMBINE_SURVIVORS pending a two-stage falsifier: addressability
    first (does the merge even differ from the winner?), preference second (blind
    A/B judged by the FOUNDER — there is no valid model judge, the judged tier
    measured 57% against its own 70% floor)."""

    def _prompt(self, tmp_path, monkeypatch, on: bool):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        if on:
            monkeypatch.setenv("TRINITY_COMBINE_SURVIVORS", "1")
        else:
            monkeypatch.delenv("TRINITY_COMBINE_SURVIVORS", raising=False)
        from trinity_local.council_runtime import render_primary_council_prompt
        bundle, members = _bundle_and_members()
        return render_primary_council_prompt(bundle, members)

    def test_dormant_by_default(self, tmp_path, monkeypatch):
        assert "## Combined" not in self._prompt(tmp_path, monkeypatch, on=False)

    def test_opt_in_adds_the_section_in_the_right_place(self, tmp_path, monkeypatch):
        p = self._prompt(tmp_path, monkeypatch, on=True)
        assert "## Combined" in p
        # must come AFTER Contested (it merges what that section left surviving)
        # and BEFORE Key Tradeoffs (which is the section allowed to be dropped).
        assert p.index("## Contested") < p.index("## Combined") < p.index("## Key Tradeoffs")

    def test_merges_evidence_never_reproduces_a_voice(self, tmp_path, monkeypatch):
        """The dead claim guard. Lens-conditioned generation measured NULL (16/30,
        p=0.43) and chairman-transmission measured null; this section must merge
        SURVIVING CLAIMS on the evidence and must never ask for the user's voice."""
        p = self._prompt(tmp_path, monkeypatch, on=True)
        combined = p[p.index("## Combined"):p.index("## Key Tradeoffs")]
        assert "SURVIVING" in combined or "survivors" in combined
        assert "Do NOT rewrite in anyone's voice" in combined
        assert "consensus mush" in combined  # no smoothing into agreement
        # never resurrect the measured-null framing
        low = combined.lower()
        for dead in ("in your voice", "as the user would write", "the user's voice"):
            assert dead not in low, f"dead claim resurrected in the combine block: {dead!r}"

    def test_combine_output_is_machine_parsable_through_all_four_layers(self, tmp_path, monkeypatch):
        """The merge is only useful if a MACHINE can read it — an agent, the MCP
        surface, an eval. Prose in PART 1 reaches none of them.

        Four layers must agree or the field silently vanishes: (1) the prompt's
        JSON template asks for it, (2) `_normalize_routing_dict` keeps it — it
        rebuilds from a key whitelist, (3) `CouncilRoutingLabel` declares it —
        `from_dict` filters to dataclass fields, (4) `to_dict` emits it — an
        explicit payload list. `resolution` shipped with only (1) and lost (2),
        and this feature initially passed (1)(2)(3) while (4) silently dropped it.
        A test that asserts only the prompt cannot catch any of that, so this one
        round-trips real JSON all the way to the disk payload and back."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_COMBINE_SURVIVORS", "1")
        import json
        from trinity_local.council_runtime import parse_routing_label, render_primary_council_prompt
        from trinity_local.council_schema import CouncilRoutingLabel

        # (1) the prompt asks for the structured fields, not just the prose section
        bundle, members = _bundle_and_members()
        prompt = render_primary_council_prompt(bundle, members)
        assert '"combined_answer"' in prompt and '"grafts"' in prompt

        synth = (
            '```routing-json\n'
            '{"winner":"claude","confidence":"high","agreed_claims":["a"],'
            '"disagreed_claims":[{"claim":"c","providers_for":["claude"]}],'
            '"combined_answer":"Edge cache, invalidate on write (from GPT).",'
            '"grafts":[{"claim":"invalidate on write","from":"chatgpt","basis":"evidence"},'
            '{"claim":"name it plainly","from":"gemini","basis":"lens",'
            '"tension":"executable artifact"}]}\n```'
        )
        # (2)+(3) normalizer keeps it and the dataclass accepts it
        label, err = parse_routing_label(synth)
        assert label is not None, f"routing label failed to parse: {err}"
        assert label.combined_answer.startswith("Edge cache"), "combined_answer dropped on parse"
        assert len(label.grafts) == 2, f"grafts dropped on parse: {label.grafts}"
        # provider provenance is canonicalized like every other slug boundary
        assert label.grafts[0]["from"] == "codex"
        assert label.grafts[1]["from"] == "antigravity"
        # basis is the built-in instrument: it makes lens-tie-breaks countable
        assert label.grafts[0]["basis"] == "evidence"
        assert label.grafts[1]["basis"] == "lens"
        assert label.grafts[1]["tension"] == "executable artifact"

        # (4) it reaches the disk payload, and survives a full disk round trip
        payload = label.to_dict()
        assert "combined_answer" in payload, "combined_answer never reached to_dict"
        assert "grafts" in payload, "grafts never reached to_dict"
        back = CouncilRoutingLabel.from_dict(json.loads(json.dumps(payload)))
        assert back.combined_answer == label.combined_answer
        assert back.grafts == label.grafts

    def test_no_merge_emits_no_empty_scaffold(self, tmp_path, monkeypatch):
        """A council with nothing to merge must not store blank keys — an empty
        combined_answer would imply a merge happened and score as one."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.council_runtime import parse_routing_label
        label, _ = parse_routing_label(
            '```routing-json\n{"winner":"claude","combined_answer":"   ","grafts":[]}\n```'
        )
        assert label is not None
        assert label.combined_answer == "" and label.grafts == []
        payload = label.to_dict()
        assert "combined_answer" not in payload and "grafts" not in payload

    def test_empty_merge_is_refused(self, tmp_path, monkeypatch):
        """No empty scaffold: when the winner already subsumes every survivor the
        section must be skipped, not emitted blank."""
        p = self._prompt(tmp_path, monkeypatch, on=True)
        assert "Skip this section entirely" in p


def test_resolution_is_omitted_when_the_chairman_does_not_emit_one():
    """No empty scaffold: a chairman that skips resolution must not leave a
    blank key implying an adjudication that never happened."""
    from trinity_local.council_runtime import parse_routing_label

    synthesis = """```routing-json
{"winner": "claude", "disagreed_claims": [
  {"claim": "x", "providers_for": ["claude"], "resolution": "   "}]}
```"""
    label, _ = parse_routing_label(synthesis)
    assert label is not None
    assert "resolution" not in label.disagreed_claims[0]


def test_tension_cap_six(tmp_path, monkeypatch):
    """Token budget: at most 6 tensions render even if the lens holds more."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_lens(tmp_path, n_tensions=9)
    from trinity_local.council_runtime import render_primary_council_prompt
    bundle, members = _bundle_and_members()
    prompt = render_primary_council_prompt(bundle, members)
    assert "pole-6a ↔ pole-6b" in prompt
    assert "pole-7a ↔ pole-7b" not in prompt


class TestMemberLensConditioning:
    """Move B (council_7e031d6e431bcceb, 2026-07-05): the lens conditions
    GENERATION — member prompts carry the tensions as constraints, with the
    correctness guard, a kill switch, and the same compact budget as the
    chairman block. Closes the 2026-05-16 digital-twin design hole."""

    def test_member_prompt_carries_generation_constraints_when_opted_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_LENS_MEMBERS", "1")
        _seed_lens(tmp_path)
        from trinity_local.council_runtime import render_member_prompt
        bundle, _ = _bundle_and_members()
        prompt = render_member_prompt(bundle)
        assert "prefer pole-1a over pole-1b" in prompt  # lean direction: pole_a wins
        assert "FIRST pole" in prompt
        assert "Never sacrifice correctness" in prompt  # the generation-side quality guard
        assert "Evidence for tension" not in prompt      # compact block, not raw lens.md

    def test_default_is_dormant(self, tmp_path, monkeypatch):
        """Measured null (n=30, p=0.43) → the generation block defaults OFF;
        it must not render without explicit opt-in."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.delenv("TRINITY_LENS_MEMBERS", raising=False)
        _seed_lens(tmp_path)
        from trinity_local.council_runtime import render_member_prompt
        bundle, _ = _bundle_and_members()
        prompt = render_member_prompt(bundle)
        assert "prefer pole-1a" not in prompt and "FIRST pole" not in prompt

    def test_no_lens_keeps_prompt_clean(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.delenv("TRINITY_LENS_MEMBERS", raising=False)
        from trinity_local.council_runtime import render_member_prompt
        bundle, _ = _bundle_and_members()
        prompt = render_member_prompt(bundle)
        assert "FIRST pole" not in prompt
        assert "Task:" in prompt

    def test_tension_cap_six_members(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setenv("TRINITY_LENS_MEMBERS", "1")
        _seed_lens(tmp_path, n_tensions=9)
        from trinity_local.council_runtime import render_member_prompt
        bundle, _ = _bundle_and_members()
        prompt = render_member_prompt(bundle)
        assert "prefer pole-6a over pole-6b" in prompt
        assert "pole-7a" not in prompt
