"""Real-corpus structural invariants — guard rails on the two things
Trinity has to do well to be worth running:

1. **Discover who I am.** The topology + depth signal has to surface
   the threads that show the user's taste — not a 24%-of-corpus junk
   drawer that buries them, not a NaN-poisoned score, not top_terms
   so generic they label nothing.

2. **Direct agents like me.** The same signals feed cortex → picks →
   the chairman that other harnesses pull via `get_persona`. If the
   topology is degenerate here, every downstream routing decision is
   degenerate too.

Bridges the gap between unit-test fixtures (too small to show the
b00-style junk-drawer failure) and the browser smoke gate (UI-only).
These tests run against the real `~/.trinity/memories/topics.json`
on the dev install. When that file doesn't exist (fresh CI, fresh
contributor checkout), tests skip cleanly.

The discipline:
- Structural invariants only — no specific basin IDs, no specific
  top_terms, no rank-order assertions. Data evolves; tests stay stable.
- Range/threshold assertions catch *structural* violations
  (junk-drawer basins, empty top_terms, broken round-trips).
- `xfail` marks document known-failing-pending-fix bugs so the build
  stays green AND the bug is tracked through to remediation.

Birthed from the tick #54 diagnostic: b00 held 24% of the corpus
with top_terms `null, like, give` and semantically unrelated members
("install github mcp" + "drinking Ginseng tea" + Thai text + …).
Pure-geometry coherence checks catch this without semantic labels.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest


def _real_topics_path() -> Path:
    """Default real install path. Tests skip if it doesn't exist —
    keeps CI green on fresh checkouts."""
    return Path.home() / ".trinity" / "memories" / "topics.json"


@pytest.fixture
def real_topics():
    path = _real_topics_path()
    if not path.exists():
        pytest.skip(f"no real corpus at {path}; this test requires a seeded install")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        pytest.skip(f"real corpus unreadable: {exc}")
    basins = data.get("basins") or []
    if not basins:
        pytest.skip("real corpus has no basins yet — run trinity-local lens-build")
    return basins


@pytest.mark.real_corpus
class TestDiscoverySurfaceIntegrity:
    """Discover-who-I-am invariants on the topology output.

    The launchpad's memory viewer and the topic graph use `topics.json`
    as the user's map of "what I think about". If a basin has no
    top_terms, no representatives, or its prompt_ids count drifts
    from its declared size, the user can't see themselves in their
    own data — discovery has failed.
    """

    def test_every_basin_has_top_terms(self, real_topics):
        """top_terms is what the topology view renders as a basin
        label when there's no chairman label. Empty top_terms → the
        viewer shows just `b03` which is opaque. Caught when the
        TF-IDF residual returned no positive residuals at all."""
        empty = [b["id"] for b in real_topics if not b.get("top_terms")]
        assert not empty, (
            f"{len(empty)} basins have empty top_terms: {empty[:5]}. "
            f"Viewer will show only the basin id, no label."
        )

    def test_every_basin_has_at_least_one_representative(self, real_topics):
        """Without representatives, the basin detail panel renders
        with no thread snippets — there's nothing for the user to
        click into."""
        no_reps = [
            b["id"] for b in real_topics
            if not b.get("representatives")
        ]
        assert not no_reps, (
            f"{len(no_reps)} basins have no representatives: {no_reps[:5]}. "
            f"Detail panel will be empty."
        )

    def test_prompt_ids_count_matches_size(self, real_topics):
        """basin.prompt_ids should carry every turn in the basin.
        The pre-tick-#5 truncation bug capped this at 50 entries and
        broke basin_for_prompt() membership lookups for any prompt
        beyond #50. The fix is now load-bearing — pin it."""
        violations = []
        for b in real_topics:
            ids = b.get("prompt_ids") or []
            size = int(b.get("size", 0))
            # Tolerate ±0 deviation in the count — both fields are
            # canonical and should agree exactly. Diff means corruption.
            if len(ids) != size:
                violations.append((b["id"], size, len(ids)))
        assert not violations, (
            f"{len(violations)} basins have prompt_ids count mismatch "
            f"(first 5: {violations[:5]}). Indicates serialization regression."
        )

    def test_centroid_dimensions_consistent(self, real_topics):
        """All basin centroids must share the same dimensionality
        (the embedder's output). Mismatches indicate stale data from
        a model migration that didn't re-embed everything."""
        dims = {len(b.get("centroid") or []) for b in real_topics}
        # 0 = some basin has an empty centroid (acceptable for
        # degenerate single-vector basins); otherwise should be 1 dim.
        non_zero = {d for d in dims if d > 0}
        assert len(non_zero) <= 1, (
            f"basin centroids have mixed dimensions: {sorted(non_zero)}. "
            f"Indicates mid-migration corpus — re-embed via "
            f"trinity-local seed-from-taste-terminal."
        )


@pytest.mark.real_corpus
class TestNoJunkDrawerForWhoIAm:
    """Discover-who-I-am invariants on semantic coherence.

    A junk-drawer basin (one cluster absorbing 20%+ of the corpus with
    semantically unrelated members) is the failure mode where Trinity
    looks at the user and sees noise. Without these guards, the topology
    happily ships a 'you' that's structurally indistinguishable from
    'everyone else'. Geometry-only checks — no semantic labels."""

    def test_no_junk_drawer_basin(self, real_topics):
        """No basin should hold >20% of the corpus. A basin that
        large is almost certainly the k-means residual where short
        unrelated prompts landed because their thread-mean centroids
        sit near the global mean. The tick #54 real-corpus diagnostic
        spotted b00 around 24% on one corpus shape (top_terms
        `null, like, give`, semantically unrelated members like
        "install github mcp" + "drinking Ginseng tea" + Thai text).
        The threshold catches future regressions; if it ever trips,
        the fix is the k-LLMmeans chairman-in-loop wiring."""
        total = sum(int(b.get("size", 0)) for b in real_topics)
        for b in real_topics:
            share = b.get("size", 0) / total if total else 0
            assert share < 0.20, (
                f"basin {b['id']} holds {share:.1%} of corpus "
                f"(top_terms: {b.get('top_terms')}) — likely a junk drawer. "
                f"Run lens-build with k-LLMmeans hook to split."
            )

    def test_top_terms_arent_just_stopwords(self, real_topics):
        """Catches the b15-style 'urls, webpages, darius' failure
        where TF-IDF picked words too generic to identify the cluster.
        Heuristic-y but the bar is low — at least ONE top term per
        basin should be longer than 3 characters."""
        # b15 had "urls, webpages, darius" — "urls" is 4 chars but
        # the pattern caught in real data was top_terms like
        # "null, like, give" where ALL terms are ≤4 chars OR are
        # near-stopwords. Test the weaker invariant: ≥1 term length ≥5.
        violations = []
        for b in real_topics:
            terms = b.get("top_terms") or []
            if not terms:
                continue  # caught by test_every_basin_has_top_terms
            if not any(len(str(t)) >= 5 for t in terms):
                violations.append((b["id"], terms))
        # Allow up to 15% of basins to violate (real corpora have
        # legitimately short-word topics like "json", "url"). Failing
        # only when most basins are too short to be useful labels.
        threshold = max(2, len(real_topics) // 6)
        assert len(violations) <= threshold, (
            f"{len(violations)} of {len(real_topics)} basins have all "
            f"short top_terms (≤4 chars each). Above the {threshold}-basin "
            f"threshold — top_terms aren't discriminating. "
            f"First 5: {violations[:5]}"
        )


# TestDirectAgentsViaDepthSignal + TestDepthSignalNotDominatedByShortThreads
# deleted 2026-05-27 with the me/depth.py retirement (#187). The depth
# signal was never wired into a live consumer, so these invariant tests
# had nothing real to defend. See retired_names.py for the module record.


@pytest.mark.real_corpus
class TestDreamPipelineCrossProviderClusters:
    """Direct-agents-like-me invariants on the dream pipeline's entry.

    `cross_provider_pairs.find_cross_provider_clusters` is what `dream`
    feeds the chairman as "questions you asked across providers" —
    structurally wrong clusters here propagate into every virtual
    council the consolidation pass synthesizes. Unit tests cover
    synthetic shapes; this test covers the real-corpus integration:
    same matmul pipeline that tick #55 caught NaN propagation in for
    `depth_score`, but on a different similarity matrix.
    """

    def test_clusters_satisfy_cross_provider_invariant(self):
        """Every returned cluster has ≥ min_providers distinct providers.

        The function's whole purpose: bundle questions answered across
        ≥2 providers. A cluster of size 5 from one provider is useless
        for `dream`'s virtual-council synthesis. Regression catches a
        future refactor that drops the provider-diversity gate.
        """
        path = _real_topics_path()
        if not path.exists():
            pytest.skip("no real corpus")
        try:
            from trinity_local.memory.store import iter_prompt_nodes
            from trinity_local.cross_provider_pairs import find_cross_provider_clusters
        except Exception as exc:
            pytest.skip(f"module unavailable: {exc}")
        # Limit to first 2000 embedded nodes — full corpus + similarity
        # threshold default takes too long for a unit-suite test; 2000
        # is enough to exercise the matmul path on real shapes.
        embedded = [n for n in iter_prompt_nodes(limit=None) if getattr(n, "embedding", None)][:2000]
        if len(embedded) < 50:
            pytest.skip(f"only {len(embedded)} embedded nodes")
        clusters = find_cross_provider_clusters(
            embedded, similarity_threshold=0.85, min_providers=2,
        )
        if not clusters:
            pytest.skip("no cross-provider clusters discovered at threshold=0.85")
        violations = [
            (c.representative_prompt[:40], c.n_providers, sorted(c.providers))
            for c in clusters
            if c.n_providers < 2
        ]
        assert not violations, (
            f"{len(violations)} cluster(s) violated the cross-provider "
            f"invariant (min_providers=2 ignored). First 3: {violations[:3]}"
        )

    def test_cluster_coherence_is_finite(self):
        """No NaN/Inf in any cluster's coherence score.

        Same shape as the depth-signal NaN bug — matmul over embeddings
        can propagate non-finite values if a single stale row slips
        the write-boundary gate. Catches a regression of tick #58's
        sanitize-at-write discipline.
        """
        import math
        path = _real_topics_path()
        if not path.exists():
            pytest.skip("no real corpus")
        try:
            from trinity_local.memory.store import iter_prompt_nodes
            from trinity_local.cross_provider_pairs import find_cross_provider_clusters
        except Exception as exc:
            pytest.skip(f"module unavailable: {exc}")
        embedded = [n for n in iter_prompt_nodes(limit=None) if getattr(n, "embedding", None)][:2000]
        if len(embedded) < 50:
            pytest.skip(f"only {len(embedded)} embedded nodes")
        clusters = find_cross_provider_clusters(
            embedded, similarity_threshold=0.85, min_providers=2,
        )
        if not clusters:
            pytest.skip("no cross-provider clusters")
        bad = [(c.representative_prompt[:40], c.coherence)
               for c in clusters if not math.isfinite(c.coherence)]
        assert not bad, (
            f"{len(bad)} cluster(s) had non-finite coherence — NaN slipped "
            f"the write-boundary gate. First 3: {bad[:3]}"
        )


@pytest.mark.real_corpus
class TestVocabularyDistillationOnRealCorpus:
    """Direct-agents-like-me invariants on the vocabulary pipeline.

    `find_homonyms` is the third matmul-shaped pipeline (alongside
    `me/depth.thread_lid` and `cross_provider_pairs`). Same NaN-
    propagation failure mode shape — a single non-finite vector
    in the per-token context list could poison the silhouette score.
    Real-corpus check on the actual >100k embedded tokens proves the
    `is_finite_embedding` filter at the boundary is doing its job
    on the path users care about.
    """

    def test_homonym_scores_are_finite(self):
        """No NaN/Inf in any homonym bimodality score.

        Same shape as the depth-signal and cross-provider tests:
        verifies tick #58's sanitize-at-write discipline propagates
        through to the silhouette stage. Limited to first 2000
        embedded nodes — the homonym scan is per-token contexts,
        which fans out fast on the real corpus.
        """
        path = _real_topics_path()
        if not path.exists():
            pytest.skip("no real corpus")
        try:
            from trinity_local.memory.store import iter_prompt_nodes
            from trinity_local.vocabulary import (
                _gather_token_contexts, find_homonyms,
            )
        except Exception as exc:
            pytest.skip(f"vocabulary module unavailable: {exc}")
        embedded = [
            n for n in iter_prompt_nodes(limit=None)
            if getattr(n, "embedding", None)
        ][:2000]
        if len(embedded) < 50:
            pytest.skip(f"only {len(embedded)} embedded nodes")
        contexts = _gather_token_contexts(embedded, min_freq=3)
        if not contexts:
            pytest.skip("no tokens meet min_freq=3 on first 2000 nodes")
        results = find_homonyms(contexts, top_n=10)
        if not results:
            pytest.skip("no homonym candidates")
        bad = [(tok, score) for tok, score, _ in results
               if not math.isfinite(score)]
        assert not bad, (
            f"{len(bad)} homonym scores are non-finite: {bad[:3]}. "
            f"NaN slipped past the per-token-context filter."
        )

    def test_homonym_scores_in_unit_range(self):
        """Bimodality silhouette is bounded [0, 1] by construction
        (the helper clips negative silhouette to 0 and caps at 1).
        Real-corpus check catches a future change to the scoring
        function that breaks the bound."""
        path = _real_topics_path()
        if not path.exists():
            pytest.skip("no real corpus")
        try:
            from trinity_local.memory.store import iter_prompt_nodes
            from trinity_local.vocabulary import (
                _gather_token_contexts, find_homonyms,
            )
        except Exception as exc:
            pytest.skip(f"vocabulary module unavailable: {exc}")
        embedded = [
            n for n in iter_prompt_nodes(limit=None)
            if getattr(n, "embedding", None)
        ][:2000]
        if len(embedded) < 50:
            pytest.skip(f"only {len(embedded)} embedded nodes")
        contexts = _gather_token_contexts(embedded, min_freq=3)
        if not contexts:
            pytest.skip("no tokens meet min_freq=3")
        results = find_homonyms(contexts, top_n=10)
        if not results:
            pytest.skip("no homonym candidates")
        violations = [
            (tok, score) for tok, score, _ in results
            if not (0.0 <= score <= 1.0)
        ]
        assert not violations, (
            f"{len(violations)} homonym scores outside [0, 1]: "
            f"{violations[:3]}. _two_means_split_variance broke its bound."
        )


# TestDepthSignalNotDominatedByShortThreads deleted 2026-05-27 with
# the me/depth.py retirement (#187). See retired_names.py.


@pytest.mark.real_corpus
class TestPromptCorpusPurity:
    """#245: the indexed prompt corpus must be overwhelmingly user-authored.

    A 2026-05-12 bulk ingest leaked 24,708 non-user prompts (43% of the
    corpus) — mostly Trinity's own "You are extracting durable facts…"
    extractor scaffolding — which poisoned basins/lens/cold-open until a
    re-apply of the ingest filter purged them. `_is_user_facing_prompt`
    rejects them at the boundary now; this guard re-checks the LIVE corpus
    (not the downstream basins, which the junk-drawer guard covers) so a
    future bulk-ingest that bypasses the filter fails loudly instead of
    silently re-poisoning the lens. (Source-level check — principle #3.)
    """

    # The corpus is allowed a little harness noise (a stray dispatch verb the
    # filter doesn't yet pattern-match), but 5% is far below the 43% blowout.
    MAX_SCAFFOLDING_FRACTION = 0.05
    MIN_CORPUS_FOR_SIGNAL = 200

    def test_corpus_is_overwhelmingly_user_authored(self):
        try:
            from trinity_local.ingest import _is_user_facing_prompt
            from trinity_local.memory.store import iter_prompt_nodes_no_embedding
            from trinity_local.session_schema import SessionMessage
        except Exception as exc:  # pragma: no cover - import guard
            pytest.skip(f"module unavailable: {exc}")

        total = 0
        scaffolding = 0
        examples: list[str] = []
        for node in iter_prompt_nodes_no_embedding(limit=None):
            text = (getattr(node, "text", "") or "").strip()
            if not text:
                continue
            total += 1
            # Re-run the boundary filter against the stored prompt. A clean
            # corpus passes everything it indexed; pollution surfaces as the
            # filter now rejecting rows that were indexed before it tightened.
            if not _is_user_facing_prompt(SessionMessage(role="user", text=text)):
                scaffolding += 1
                if len(examples) < 3:
                    examples.append(text[:80])

        if total < self.MIN_CORPUS_FOR_SIGNAL:
            pytest.skip(f"corpus too small ({total} prompts) for a purity signal")

        fraction = scaffolding / total
        assert fraction < self.MAX_SCAFFOLDING_FRACTION, (
            f"{fraction:.0%} of the {total}-prompt corpus is non-user "
            f"scaffolding ({scaffolding} rows) — above the "
            f"{self.MAX_SCAFFOLDING_FRACTION:.0%} ceiling. A bulk ingest "
            f"bypassed _is_user_facing_prompt (#245). Examples: {examples}"
        )


@pytest.mark.real_corpus
class TestBasinScaffoldingConcentration:
    """#248: corpus-wide scaffolding can pass the 5% floor (#245) yet
    CONCENTRATE into a few near-pure scaffolding basins that then surface on
    the topic map as fake user topics. A 2026-05 audit found b19=94%, b22=90%
    scaffolding (AGENTS.md dumps / <environment_context> / Trinity's own
    extraction prompts) while the corpus was only 1.7% scaffolding overall.
    The corpus-wide guard is necessary but not sufficient — concentration
    must be guarded at the basin grain (per principle #3, catch it where it
    shows, not only at the aggregate)."""

    MAX_BASIN_SCAFFOLDING = 0.50
    MIN_BASIN_SIZE = 20  # tiny basins are noise; the topic-map risk is sizeable ones

    def test_no_basin_is_scaffolding_dominated(self):
        try:
            from trinity_local.ingest import _is_user_facing_prompt
            from trinity_local.me.basins import load_basins
            from trinity_local.memory.store import iter_prompt_nodes_no_embedding
            from trinity_local.session_schema import SessionMessage
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"module unavailable: {exc}")

        basins = load_basins()
        if not basins:
            pytest.skip("no basins built yet — run trinity-local lens")
        texts = {
            n.id: (getattr(n, "text", "") or "")
            for n in iter_prompt_nodes_no_embedding(limit=None)
        }
        offenders = []
        for b in basins:
            members = [texts.get(pid, "") for pid in (b.prompt_ids or []) if texts.get(pid)]
            if len(members) < self.MIN_BASIN_SIZE:
                continue
            scaffold = sum(
                1 for t in members
                if not _is_user_facing_prompt(SessionMessage(role="user", text=t.strip()))
            )
            frac = scaffold / len(members)
            if frac > self.MAX_BASIN_SCAFFOLDING:
                offenders.append((b.id, len(members), frac, (b.top_terms or [])[:4]))

        assert not offenders, (
            "scaffolding-dominated basin(s) on the topic map (#248): "
            + "; ".join(
                f"{bid} (n={n}) {frac:.0%} scaffolding terms={tt}"
                for bid, n, frac, tt in offenders
            )
            + ". Tighten _is_user_facing_prompt or re-purge the corpus."
        )
