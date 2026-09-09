"""Basin top_terms must work in every script the corpus actually contains.

Found 2026-09-08: basin b42 in the real corpus held one Thai prompt
("โปรดติดตามต่อไป") and had EMPTY top_terms and an empty label, so the
topology viewer would render a bare basin id. The cause was a tokenizer of
`[a-zA-Z][a-zA-Z\\-_]{2,}` — ASCII-Latin only. 691 of 41,512 prompt nodes
(1.7%) scored zero terms under it. A product that reads the user's own
transcripts cannot silently drop a whole script family from its discovery
surface.
"""
from __future__ import annotations

import pytest

from trinity_local.me.basins import _TERM_RX, _top_terms_for_cluster

FILLER = "unrelated english filler words about other subjects entirely"


class TestEveryScriptScores:
    @pytest.mark.parametrize("script,text", [
        ("thai", "โปรดติดตามต่อไป"),
        ("japanese", "機械学習のモデル"),
        ("korean", "기계 학습 모델"),
        ("chinese", "机器学习模型"),
        ("cyrillic", "проверка модели"),
        ("arabic", "نموذج التعلم"),
        ("hebrew", "מודל למידה"),
        ("hindi", "मशीन लर्निंग"),
        ("greek", "μοντέλο μάθησης"),
    ])
    def test_a_basin_in_this_script_is_labelable(self, script, text):
        terms = _top_terms_for_cluster([text], [text, FILLER])
        assert terms, f"{script} basin would render as a bare id — the b42 bug"

    def test_combining_marks_do_not_shatter_a_word(self):
        """Thai vowels and tone marks are category Mn. `\\w` excludes them, so a
        first fix returned ['ดตามต', 'อไป', 'โปรดต'] — fragments, not a word."""
        terms = _TERM_RX.findall("โปรดติดตามต่อไป")
        assert terms == ["โปรดติดตามต่อไป"], f"word shattered into {terms}"

    def test_two_character_words_score_in_scripts_that_have_them(self):
        """Korean and CJK words are routinely two characters; a single 3-char
        floor is a Latin convention that silences them."""
        assert _TERM_RX.findall("기계 모델") == ["기계", "모델"]


class TestLatinIsUnchanged:
    """The floor for ASCII stays at three characters, so existing basins keep
    the terms they had. Only the non-ASCII floor is lower."""

    def test_short_ascii_words_are_still_excluded(self):
        assert _TERM_RX.findall("of to ai ml the and council") == ["the", "and", "council"]

    def test_digits_and_punctuation_are_not_terms(self):
        assert _TERM_RX.findall("123 4.5 --- ___ 2026") == []

    def test_accented_latin_is_no_longer_truncated(self):
        """The ASCII class cut `café` to `caf`; a Unicode letter class keeps it."""
        assert _TERM_RX.findall("café résumé naïve") == ["café", "résumé", "naïve"]

    def test_ordinary_latin_terms_are_unaffected(self):
        # The global corpus must be substantially larger than the cluster, or
        # the TF-IDF residual (cluster_freq - global_freq) is ~0 for the
        # cluster's own words and NOTHING scores. That is the function working,
        # not the tokenizer failing — an earlier version of this test used a
        # three-word corpus and asserted a result the maths cannot produce.
        assert _top_terms_for_cluster(
            ["council verify kernel"], ["council verify kernel", FILLER]
        ) == ["council", "kernel", "verify"]


class TestItStillRefusesNothing:
    def test_empty_text_yields_no_terms(self):
        assert _top_terms_for_cluster([""], ["x"]) == []

    def test_digits_only_yields_no_terms(self):
        assert _top_terms_for_cluster(["123 456"], ["123 456", FILLER]) == []
