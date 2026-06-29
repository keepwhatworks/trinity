"""Tests for me_lenses parser — the launchpad's pair-wise lens cards depend on it."""
from __future__ import annotations

from trinity_local.me_lenses import parse_taste_lenses


SAMPLE_ME = """# /me

## Recurring topics
- Some topic.

## Vocabulary the user uses
- "disaggregated prefill" — splitting prefill from decode — turn [28], [33].
- "context loader" — the program that prefills your data into the KV cache — [40].

## Implicit rejections (the moat)

### Skip the curated list, point at the source
Model frame: "you mean GPQA Diamond. Here's the consolidated picture from current benchmarks."
User substituted: "Measuring the performance of our models on real-world tasks | OpenAI"
Why this matters: trust the locked corpus, not the assistant's tabulation.

### Don't lecture math, audit it
Model frame: "49/51 — the paper is clean."
User substituted: "Section 3.7 still uses the stationary-mean formula."
Why this matters: surface-level pass-rate is not consistency.

## Cross-domain analogies
- AI prefill ↔ identity / cookies: prefill the user's data once.

## Abstract lenses
- Source over synthesis — link the primary doc instead of the curated table.
- Second-level impacts — first-order read is the table stakes, the move is downstream.
"""


class TestParseTasteLenses:
    def test_extracts_rejections_with_titles_and_pairs(self):
        lenses = parse_taste_lenses(SAMPLE_ME)
        assert len(lenses.rejections) == 2
        first = lenses.rejections[0]
        assert first.title == "Skip the curated list, point at the source"
        assert first.model_frame.startswith("you mean GPQA Diamond")
        assert first.user_substituted.startswith("Measuring the performance")
        assert first.why_matters == "trust the locked corpus, not the assistant's tabulation"

    def test_extracts_vocabulary_phrase_and_meaning(self):
        lenses = parse_taste_lenses(SAMPLE_ME)
        phrases = [v.phrase for v in lenses.vocabulary]
        assert "disaggregated prefill" in phrases
        assert "context loader" in phrases
        # Turn references are stripped from the meaning
        prefill = next(v for v in lenses.vocabulary if v.phrase == "disaggregated prefill")
        assert "turn" not in prefill.meaning.lower()
        assert "decode" in prefill.meaning

    def test_extracts_abstract_lenses(self):
        lenses = parse_taste_lenses(SAMPLE_ME)
        assert len(lenses.abstract_lenses) == 2
        statements = [l.statement for l in lenses.abstract_lenses]
        assert any("Source over synthesis" in s for s in statements)
        assert any("Second-level impacts" in s for s in statements)

    def test_share_text_is_principles_only_no_prompts(self):
        """The share text MUST NOT contain verbatim model/user prompts —
        those are private. Only the principle (title + why-it-matters)."""
        lenses = parse_taste_lenses(SAMPLE_ME)
        share = lenses.rejections[0].to_share_text()
        # The principle parts:
        assert "Skip the curated list" in share
        assert "trust the locked corpus" in share
        # Privacy contract — verbatim prompt content MUST NOT leak:
        assert "GPQA Diamond" not in share, "model_frame leaked into share text"
        assert "Measuring the performance" not in share, "user_substituted leaked into share text"
        # Old format markers are gone:
        assert "Model said:" not in share
        assert "What I substituted:" not in share

    def test_rejections_share_text_bundles_all_principles_no_prompts(self):
        """The 'Copy all' bundle must contain every rejection's title +
        why-it-matters, but never the verbatim model/user quotes."""
        lenses = parse_taste_lenses(SAMPLE_ME)
        bundle = lenses.to_dict()["rejections_share_text"]
        # Both rejection titles present:
        assert "Skip the curated list, point at the source" in bundle
        assert "Don't lecture math, audit it" in bundle
        # Both why-it-matters lines present:
        assert "trust the locked corpus" in bundle
        assert "surface-level pass-rate is not consistency" in bundle
        # Privacy contract:
        assert "GPQA Diamond" not in bundle
        assert "Measuring the performance" not in bundle
        assert "Section 3.7" not in bundle
        # Header signals what this is:
        assert "principles" in bundle.lower()

    def test_empty_me_returns_empty_lenses(self):
        lenses = parse_taste_lenses("")
        assert lenses.is_empty
        assert lenses.rejections == []
        assert lenses.vocabulary == []
        assert lenses.abstract_lenses == []

    def test_to_dict_includes_per_card_and_section_share_text(self):
        lenses = parse_taste_lenses(SAMPLE_ME)
        out = lenses.to_dict()
        # Each rejection card carries its own share_text for the launchpad copy button.
        assert "share_text" in out["rejections"][0]
        # Vocabulary + abstract-lenses each have a "copy all" payload.
        assert "Vocabulary I keep using" in out["vocabulary_share_text"]
        assert "abstract lenses" in out["abstract_lenses_share_text"].lower()

    def test_combined_share_text_is_one_social_block(self):
        # The launchpad's single "Copy for sharing" button binds to this:
        # one clean text block joining rejections + abstract lenses,
        # excluding pair-wise quotes (which are private prompt history).
        lenses = parse_taste_lenses(SAMPLE_ME)
        text = lenses.to_dict()["combined_share_text"]
        assert "What I redirect away from" in text
        assert "The lenses I think through" in text
        # Verbatim model/user quotes never leak into the share block.
        for r in lenses.rejections:
            assert r.model_frame not in text
            assert r.user_substituted not in text
        assert text.endswith("(via trinity-local)")

    def test_malformed_rejection_card_skipped_not_crashed(self):
        # Card missing one of the three labeled rows should be skipped, not poison.
        broken = SAMPLE_ME.replace("Why this matters: trust the locked corpus, not the assistant's tabulation.\n", "")
        lenses = parse_taste_lenses(broken)
        # The well-formed second rejection should still parse.
        titles = [r.title for r in lenses.rejections]
        assert "Don't lecture math, audit it" in titles

    def test_abstract_lens_horizon_round_trip(self):
        """#139: lens.md may suffix bullets with [horizon] tags. Parser
        extracts them; pre-#139 bullets without tags default to tactical."""
        md = """# /me

## Abstract lenses
- Source over synthesis [philosophical]
- Locked corpus over forward theory [strategic]
- Concrete examples beat prose explanations [tactical]
- Pre-#139 lens without tag
- Mixed case still works [Strategic]
- Invalid tag falls through [whimsical]
"""
        lenses = parse_taste_lenses(md)
        by_statement = {l.statement: l.horizon for l in lenses.abstract_lenses}
        assert by_statement["Source over synthesis"] == "philosophical"
        assert by_statement["Locked corpus over forward theory"] == "strategic"
        assert by_statement["Concrete examples beat prose explanations"] == "tactical"
        # Pre-#139 (no tag) defaults to tactical
        assert by_statement["Pre-#139 lens without tag"] == "tactical"
        # Case is normalized
        assert by_statement["Mixed case still works"] == "strategic"
        # Invalid bracket tag falls through to "no match" → defaults to tactical;
        # the bracket text remains in the statement since regex didn't capture it.
        # Statement = "Invalid tag falls through [whimsical]" with horizon=tactical.
        invalid_entry = next(
            (l for l in lenses.abstract_lenses if "Invalid tag" in l.statement),
            None,
        )
        assert invalid_entry is not None
        assert invalid_entry.horizon == "tactical"

    def test_abstract_lens_to_dict_carries_horizon(self):
        md = """# /me

## Abstract lenses
- Tagged principle [strategic]
"""
        lenses = parse_taste_lenses(md)
        d = lenses.abstract_lenses[0].to_dict()
        assert d == {"statement": "Tagged principle", "horizon": "strategic"}
