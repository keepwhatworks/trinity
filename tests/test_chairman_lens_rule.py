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
