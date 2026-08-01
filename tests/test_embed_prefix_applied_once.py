"""The embedding prefix must be applied EXACTLY once, in every backend.

THE BUG (found 2026-08-01, live corpus). `backend_mlx_native` — the real Apple
MLX path, i.e. the one that embedded this corpus — did a bare
`_DOC_PREFIX + t` with no already-prefixed check, while `backend_mlx` has always
guarded. Production callers pre-prefix:

    ingest_helpers.py:117   f"search_document: {turn.text}"   (CLI ingest)
    stale_pass.py:238       f"search_document: {node.text}"   (embed heal)
    me_builder.py:276       f"search_document: {t[:600]}"

so their text became `search_document: search_document: ...` and embedded ~0.974
away from the correctly-prefixed vector for the SAME string.

MEASURED BLAST RADIUS: 24.7% of 300 sampled live nodes were double-prefixed —
roughly 10,000 of 40,236 — split WITHIN providers (claude 50 double / 35 single)
because it tracks the code path, not the source. Web captures were 100% clean;
the CLI transcript path was not.

WHY NOTHING CAUGHT IT. Every consumer compares vectors to OTHER vectors from the
same store, so a uniformly shifted subpopulation still clusters, still ranks,
still returns plausible neighbours — a corrupted space that behaves normally
under every relative query. It surfaced only when something asked a question with
a KNOWN answer: a near-duplicate census reported LESS near-duplicate mass (35.9%
at cos>=0.995) than EXACT-duplicate mass (38.0%), which is arithmetically
impossible unless byte-identical text was embedding to different vectors.

That is the test below: identity. Not "does it cluster" but "does the same string
produce the same vector, however it is spelled on the way in".
"""
from __future__ import annotations

import math

import pytest


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / ((na * nb) or 1.0)


class TestPrefixHelperAppliesOnce:
    """Pure-function guard — no model load, so it runs in the DEFAULT shard.

    The vector-level test below needs the real embedder and is slow-marked; if
    this file's only coverage lived there, the guard would sit in the shard
    nobody runs, which is how the original bug survived.
    """

    def test_bare_text_gets_the_prefix(self):
        from trinity_local.embeddings.backend_mlx_native import _ensure_doc_prefix

        assert _ensure_doc_prefix("hello") == "search_document: hello"

    def test_already_prefixed_text_is_returned_UNCHANGED(self):
        """The actual defect: this used to return
        'search_document: search_document: hello'."""
        from trinity_local.embeddings.backend_mlx_native import _ensure_doc_prefix

        already = "search_document: hello"
        assert _ensure_doc_prefix(already) == already

    def test_every_nomic_task_prefix_is_recognised(self):
        """A guard that only knew `search_document:` would silently double-prefix
        a query vector, which is the same bug with a different label."""
        from trinity_local.embeddings.backend_mlx_native import (
            _NOMIC_PREFIXES,
            _ensure_doc_prefix,
        )

        for prefix in _NOMIC_PREFIXES:
            text = f"{prefix} some text"
            assert _ensure_doc_prefix(text) == text, f"{prefix} was double-prefixed"

    def test_the_two_backends_agree_on_what_prefixed_means(self):
        """The bug was an ASYMMETRY between backends, not a missing constant.
        Pin that they share the same prefix set, so fixing one and not the other
        cannot recur."""
        from trinity_local.embeddings.backend_mlx import NOMIC_PREFIXES
        from trinity_local.embeddings.backend_mlx_native import _NOMIC_PREFIXES

        assert set(_NOMIC_PREFIXES) == set(NOMIC_PREFIXES), (
            "the MLX-native and torch backends disagree on which prefixes count "
            "as already-applied — the same text will embed to two different "
            "vectors depending on which backend is live"
        )

    def test_a_lookalike_that_is_not_a_real_prefix_still_gets_prefixed(self):
        """Negative control: the guard must not swallow arbitrary text that
        merely mentions the word, or it would UNDER-prefix real documents."""
        from trinity_local.embeddings.backend_mlx_native import _ensure_doc_prefix

        text = "the search_document: prefix is applied by the backend"
        assert _ensure_doc_prefix(text).startswith("search_document: the search_")


@pytest.mark.slow
class TestIdenticalTextEmbedsIdentically:
    """The property the corpus actually violated, checked on REAL vectors."""

    def test_pre_prefixed_and_bare_text_produce_the_same_vector(self):
        from trinity_local.embeddings import embed, embedder_fingerprint

        if "modernbert" not in str(embedder_fingerprint()).lower():
            pytest.skip("needs the real embedder; the TF-IDF stub has no prefixes")

        text = "should we dedupe the corpus at ingest or at retrieval time?"
        bare = embed(text)
        pre = embed(f"search_document: {text}")
        sim = _cos(bare, pre)
        assert sim > 0.9999, (
            f"pre-prefixed text embedded {sim:.6f} away from the same bare text — "
            "the backend is double-prefixing again, which puts a subpopulation of "
            "the corpus in a shifted vector space (was 0.984 before the fix)"
        )

    def test_a_DIFFERENT_task_prefix_still_changes_the_vector(self):
        """Proves the guard is not just stripping prefixes wholesale. Nomic task
        prefixes are semantically real; collapsing them would be its own bug."""
        from trinity_local.embeddings import embed, embedder_fingerprint

        if "modernbert" not in str(embedder_fingerprint()).lower():
            pytest.skip("needs the real embedder")

        text = "should we dedupe the corpus at ingest or at retrieval time?"
        doc = embed(text)
        qry = embed(f"search_query: {text}")
        assert _cos(doc, qry) < 0.99, (
            "search_document and search_query embedded identically — the prefix "
            "guard is discarding the task prefix instead of honouring it"
        )
