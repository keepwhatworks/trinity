"""Stage 3 — pair mining via 3-member council + verifier contract.
Stage 4 — deterministic basin post-filter.

Three members propose pair candidates from `decisions.jsonl`. The
chairman applies the three tests as a JSON contract:
- TENSION: decisions in BOTH directions (A privileged sacrificing B,
  AND B privileged sacrificing A).
- DUAL EVIDENCE: regret/correction/cost on BOTH poles (any of the
  expanded valence values per council_c63fa273bdc2ed21).
- FAILURE-MODE LEGIBILITY: a named failure for pure-A and pure-B
  (paralysis / hedonism / theater / sterility / ...).

Verdict per pair: accepted | preserve_as_ordering | dropped.

Stage 4 applies a deterministic post-filter on top: any pair whose
tension evidence sits entirely in ONE basin gets demoted to
preserve_as_ordering. Council council_70eaf228d7753074 ratified this
as the load-bearing piece — without it, basin tags are dead code.

Output:
- `~/.trinity/me/lenses.json` — accepted pairs (4–8 expected)
- `~/.trinity/me/orderings.json` — preserve_as_ordering pairs
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..embeddings import cosine_similarity  # canonical cosine (unified — was a nested copy missing the dim guard)
from .basins import me_dir
from .decisions import Decision


VALID_HORIZONS = {"tactical", "strategic", "philosophical"}


@dataclass
class LensPair:
    pole_a: str
    pole_b: str
    failure_a: str
    failure_b: str
    tension_decisions: list[str] = field(default_factory=list)
    dual_evidence: dict[str, list[str]] = field(default_factory=dict)
    basins_spanned: list[str] = field(default_factory=list)
    verdict: str = "accepted"
    # #139 (#1 multi-resolution horizon): tactical = response-format /
    # turn-scale preferences ("be terse", "show code first"); strategic
    # = quarter-scale trajectory choices ("ship MVP over polish");
    # philosophical = year-scale identity / framing ("intelligence is
    # infrastructure not interface"). Lets council_runtime weight which
    # lens applies based on query horizon — without it, philosophical
    # lenses fire on tactical questions and drown the signal.
    horizon: str = "tactical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pole_a": self.pole_a,
            "pole_b": self.pole_b,
            "failure_a": self.failure_a,
            "failure_b": self.failure_b,
            "tension_decisions": self.tension_decisions,
            "dual_evidence": self.dual_evidence,
            "basins_spanned": self.basins_spanned,
            "verdict": self.verdict,
            "horizon": self.horizon,
        }


def lenses_path() -> Path:
    return me_dir() / "lenses.json"


def orderings_path() -> Path:
    return me_dir() / "orderings.json"


def render_pair_mining_prompt(decisions: list[Decision]) -> str:
    """Build the chairman + members prompt for Stage 3.

    Members propose candidate pairs; chairman applies the three tests
    and returns a JSON array of verdicts.
    """
    decision_lines = []
    for d in decisions[:120]:  # cap to keep prompt sane
        decision_lines.append(json.dumps({
            "id": d.id,
            "privileged": d.privileged,
            "sacrificed": d.sacrificed,
            "valence": d.valence,
            "basin": d.basin,
            "verbatim": d.verbatim,
        }))
    decisions_block = "\n".join(decision_lines)

    return f"""You are mining lens-pairs from a user's decisions.

A LENS is a tension between two value poles (A, B) where:
1. TENSION — decisions privilege A at B's cost AND vice versa (both directions).
2. DUAL EVIDENCE — regret/correction/cost shows up on BOTH poles
   (the user has stated downsides of over-indexing on each pole).
3. FAILURE-MODE LEGIBILITY — a named failure mode for pure-A AND pure-B
   (e.g., paralysis, hedonism, theater, sterility, performance, avoidance).

If only ONE direction has evidence, it's a virtue or an ordering, not a lens.
If failure-modes are unnameable on either pole, it's a virtue, not a lens.

CRITICAL: ABSTRACT THE POLES. Each pole must be a STRUCTURAL pattern that
recurs across multiple domains, not a literal phrase from one decision.

GENERATOR-OVER-GENERATED — when two candidate pole names BOTH pass the
cross-basin test, pick the one that DERIVES the other. The generator
beats the generated. The rule beats the instance produced by the rule.

Tests for "is A the generator of B?":
- Can you reach B by applying A to a domain? (Yes → A is the generator.)
- Does A explain WHY B is also true? (Yes → A is upstream.)
- Could A have produced B, but not the reverse? (Yes → emit A.)

Examples:
  candidates: "shipping velocity over polish"  vs  "executable artifact over description of one"
    → "executable artifact over description" GENERATES "shipping velocity over polish"
    (shipping fast IS one instance of preferring the executable form)
    → emit "executable artifact over description of one".

  candidates: "user ownership of data over lab pipeline"  vs  "source over derivative"
    → "source over derivative" GENERATES the data-ownership preference
    (owning your data IS choosing the source over the lab's derivative)
    → emit "source over derivative".

When two pass the cross-basin test, ask: "which one sounds like it could
have been emitted by the OTHER one's rule?" That direction names the
generator. Emit it; demote the other to a cross-link or drop it.

BAD pole names (too literal, single domain — REJECT these):
  ❌ "speed/momentum to close"           → too specific to a real-estate deal
  ❌ "lower buyer-agent fee"             → one transaction
  ❌ "frigate setup over usb mount"      → one home automation task
  ❌ "settle the prior determination"    → too narrow

GOOD pole names (structural, span 3+ domains):
  ✓ "infrastructure over interface"          (architecture, software, smart-home)
  ✓ "locked corpus over forward theory"      (genetics, geopolitics, tax, SEO)
  ✓ "temporal trajectory over present snapshot"  (materials, real-estate, tax)
  ✓ "full-stack control over component excellence" (manufacturing, kit business, travel)
  ✓ "generative grammar over selected instance"    (floor-plans, genetics, philosophy)
  ✓ "codified rule over aesthetic judgment"        (genetics, design, manifestos)

THE TEST: would two strangers reading this lens converge on the same answer
in a domain you've never discussed? If a pole only fits one topical area, it's
a preference, not a lens. Name the structural move that ties decisions across
DIFFERENT basins together.

Cross-basin requirement: your tension_decisions for any "accepted" pair must
draw from AT LEAST 2 distinct basin ids (b00..bNN in the decisions below).
Pairs whose evidence sits in one basin are topic preferences — emit them as
"preserve_as_ordering" instead of "accepted".

Propose 6–12 candidate pairs from the decisions below. For each pair, return
a verdict object — JSON array, one element per pair, schema below:

{{
  "pole_a": "<short noun phrase, the optimized-for axis>",
  "pole_b": "<short noun phrase, the traded-away axis>",
  "failure_a": "<one-word or short phrase named failure of pure-A>",
  "failure_b": "<one-word or short phrase named failure of pure-B>",
  "tension_decisions": ["<decision_id where A privileged>", "<decision_id where B privileged>", ...],
  "dual_evidence": {{
    "pole_a": ["<decision_id with regret/correction/cost on A>", ...],
    "pole_b": ["<decision_id with regret/correction/cost on B>", ...]
  }},
  "horizon": "tactical | strategic | philosophical",
  "verdict": "accepted | preserve_as_ordering | dropped"
}}

HORIZON GUIDE — what time-scale does this lens operate at?
- tactical: response-format / turn-scale preferences. ("be terse vs
  comprehensive", "code first vs explanation first", "JSON vs prose")
- strategic: quarter-scale trajectory / project-level direction.
  ("ship MVP over polish", "build leverage over manual ops",
  "specialize over generalize")
- philosophical: year-scale identity / framing / world-model.
  ("intelligence is infrastructure not interface", "patient capital
  over conventional venture", "generative grammar over selected
  instance")

If unsure: prefer "strategic". Don't claim "philosophical" unless
the lens reframes how the user sees a whole domain.

VERDICT GUIDE:
- accepted: passes all three tests
- preserve_as_ordering: stable preference (A always over B) but no
  dual evidence — useful as a lookup, not a lens
- dropped: neither tension nor dual evidence; a virtue or noise

Output: ONE JSON array. NO commentary outside the array. NO markdown fences.

DECISIONS:

{decisions_block}
"""


def parse_pair_mining_output(raw: str) -> list[LensPair]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    # Find the outermost JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []

    pairs: list[LensPair] = []
    for obj in items:
        if not isinstance(obj, dict):
            continue
        pole_a = (obj.get("pole_a") or "").strip()
        pole_b = (obj.get("pole_b") or "").strip()
        if not pole_a or not pole_b or pole_a == pole_b:
            continue
        verdict = (obj.get("verdict") or "accepted").strip().lower()
        if verdict not in {"accepted", "preserve_as_ordering", "dropped"}:
            verdict = "accepted"
        horizon = (obj.get("horizon") or "tactical").strip().lower()
        if horizon not in VALID_HORIZONS:
            # Chairman invented a label outside the enum (or pre-#139
            # data missing the field) — default to tactical, which is
            # the safe "always-applies" floor. Strategic/philosophical
            # would over-claim; tactical never wrongly suppresses.
            horizon = "tactical"
        tension = obj.get("tension_decisions") or []
        if not isinstance(tension, list):
            tension = []
        dual = obj.get("dual_evidence") or {}
        if not isinstance(dual, dict):
            dual = {}
        pairs.append(LensPair(
            pole_a=pole_a,
            pole_b=pole_b,
            failure_a=(obj.get("failure_a") or "").strip(),
            failure_b=(obj.get("failure_b") or "").strip(),
            tension_decisions=[str(t) for t in tension if isinstance(t, (str, int))],
            dual_evidence={
                "pole_a": [str(t) for t in (dual.get("pole_a") or []) if isinstance(t, (str, int))],
                "pole_b": [str(t) for t in (dual.get("pole_b") or []) if isinstance(t, (str, int))],
            },
            basins_spanned=[],  # filled by basin_post_filter
            verdict=verdict,
            horizon=horizon,
        ))
    return pairs


_MIN_BASINS_FOR_LENS = 2
"""Spec threshold: ≥2 domains supporting each lens.

De-biasing change (#267, 2026-05-30): lowered 3→2. The original ≥3 rule
was tuned for a software-only corpus where every basin was a dev sub-topic,
so 3-domain convergence was cheap. For a genuinely cross-domain user (real
estate + health + investing + software + …) a decision-style that recurs
across just 2 *unrelated* domains is already a lens, not a topic-bound
virtue — the ≥3 gate was demoting most of the user's non-software taste to
flat orderings (this user: 11 candidates → 1 accepted at ≥3). The
support-count + recency-decay registry guards keep the quality bar; the
basin gate only governs cross-domain generality.

Adapted from the external taste-terminal project's spec (no longer a
Trinity runtime dependency — see claude.md "What was deliberately
deleted" / `~/.taste/`). Trinity's basins are functionally equivalent
to taste-terminal's domains: k-means topological clusters over
PromptNode embeddings. Anything under 3 basins is a topic-bound
preference — keep as ordering, not lens. Ratified into Trinity's
lens pipeline by council `council_70eaf228d7753074` (Option C —
basins as verifier, not chairman input)."""


def _tension_probe_text(pair: LensPair) -> str:
    """Concat a tension's poles + failure modes into one probe text.
    Used by the T2 semantic filter to give the embedder a single
    surface representing the tension's full claim."""
    parts = [pair.pole_a, pair.pole_b]
    if pair.failure_a:
        parts.append(pair.failure_a)
    if pair.failure_b:
        parts.append(pair.failure_b)
    return " · ".join(p for p in parts if p)


_T2_LENS_GATE_THRESHOLD = 0.40
"""Cosine threshold for tension-vs-basin semantic membership.

Looser than the moves-gate T2 default (0.70) because tensions are
short, abstract, and cross-domain by construction — their semantic
overlap with a basin's centroid is necessarily lower than a
procedural pattern's. Empirically: tensions on a real lens score
~0.40-0.65 against the basins they actually belong to; junk
tensions score below 0.30."""


def _filter_basins_by_semantic_membership(
    pair: LensPair,
    basins: set[str],
    basin_centroids: dict[str, list[float]],
    *,
    threshold: float = _T2_LENS_GATE_THRESHOLD,
) -> set[str]:
    """T2 gate-over-lens: drop basins whose centroid is semantically
    distant from the tension probe text.

    This is the lens-build analogue of the moves-gate T2 (#181). The
    chairman LLM bridges declarative→procedural inside the council;
    here the embedding model bridges abstract-tension-vocabulary vs
    concrete-basin-content. T1 (lexical Jaccard) is the wrong primitive
    for that bridge — surface text differs by construction — so this
    function uses cosine only.

    Returns the subset of `basins` whose centroid passed the threshold.
    Basins with no centroid in the supplied map fall through unchanged
    (caller may not have loaded topics.json, or the basin id is novel).

    REQUIRES real (MLX) embeddings. Under the TF-IDF fallback the
    cosine collapses for the abstract-tension/concrete-pattern pair
    this filter exists to judge — empirically a *related* tension
    scores ~0.14 (well below threshold) because TF-IDF is lexical and
    the two texts share almost no tokens. Applying the threshold under
    TF-IDF would over-reject every tension and silently gut the lens
    (the same dormancy class as the retired moves T1 gate). So when
    MLX isn't loaded we skip the semantic filter entirely and let the
    count-only rule stand.
    """
    if not basin_centroids:
        return basins
    from ..embeddings import embed, mlx_actually_loaded
    if not mlx_actually_loaded():
        # TF-IDF fallback can't bridge abstract↔concrete vocab — the
        # cosine threshold would over-reject. Degrade to count-only.
        return basins
    probe = _tension_probe_text(pair)
    if not probe:
        return basins
    try:
        probe_emb = embed(probe)
    except Exception:
        # If the embedder fails (offline + no fallback), keep all
        # basins — semantic filtering is advisory, not load-bearing.
        return basins

    surviving: set[str] = set()
    for basin in basins:
        centroid = basin_centroids.get(basin)
        if centroid is None:
            # No centroid available — treat as pass-through (caller
            # didn't load topics, or this basin is new since topics
            # was last built).
            surviving.add(basin)
            continue
        if len(centroid) != len(probe_emb):
            # Dimension mismatch (embedding backend changed since
            # topics was built) — keep the basin to avoid silent
            # data loss; user re-runs dream to refresh topics.
            surviving.add(basin)
            continue
        if cosine_similarity(probe_emb, centroid) >= threshold:
            surviving.add(basin)
    return surviving


def basin_post_filter(
    pairs: list[LensPair],
    decisions: list[Decision],
    *,
    basin_centroids: dict[str, list[float]] | None = None,
) -> list[LensPair]:
    """Stage 4: drop tension evidence that doesn't span enough basins.

    Rule: minimum `_MIN_BASINS_FOR_LENS` (2, post-#267) domains supporting
    an entry. A tension below that sits in a single topic — a domain-local
    virtue, kept as an ordering with its domain noted, not promoted to a
    cross-domain lens. Ratified into Trinity by `council_70eaf228d7753074`;
    threshold lowered 3→2 for cross-domain users (#267).

    Verdicts:
    - accepted: ≥`_MIN_BASINS_FOR_LENS` basins (cross-domain lens)
    - preserve_as_ordering: 1 basin (domain-local taste — still surfaced)
    - dropped: 0 basins (chairman emitted IDs that don't anchor)

    When `basin_centroids` is provided (the lens-build pipeline loads
    them from topics.json), a second filter runs after the basin-count
    rule: each basin's centroid is checked for semantic membership
    against the pair's tension probe text via cosine. Basins that fail
    the threshold get dropped from `basins_spanned` BEFORE the count
    rule decides the verdict. Without this, a chairman that emits
    plausible-looking but semantically wrong basin IDs gets a free
    pass into the lens.
    """
    decision_basin = {d.id: d.basin for d in decisions}
    # Sentinel values chairmen emit when uncertain. Treat them as None
    # so they don't inflate basins_spanned past the spec threshold.
    _sentinels = {"?", "unknown", "none", "n/a"}
    filtered: list[LensPair] = []
    for pair in pairs:
        basins: set[str] = set()
        for d_id in pair.tension_decisions:
            b = decision_basin.get(d_id)
            if b and b.strip().lower() not in _sentinels:
                basins.add(b)
        # T2 semantic filter: drop basins whose centroid is far from
        # the tension's probe text. No-op when basin_centroids is None
        # (preserves backward compat for callers that don't pass it).
        if basin_centroids:
            basins = _filter_basins_by_semantic_membership(
                pair, basins, basin_centroids
            )
        pair.basins_spanned = sorted(basins)
        if pair.verdict != "accepted":
            filtered.append(pair)
            continue
        if len(pair.basins_spanned) >= _MIN_BASINS_FOR_LENS:
            filtered.append(pair)
        elif len(pair.basins_spanned) >= 1:
            pair.verdict = "preserve_as_ordering"
            filtered.append(pair)
        else:
            pair.verdict = "dropped"
            filtered.append(pair)
    return filtered


def split_by_verdict(pairs: list[LensPair]) -> tuple[list[LensPair], list[LensPair]]:
    """Split pairs into (accepted, orderings). Dropped pairs filtered out."""
    accepted = [p for p in pairs if p.verdict == "accepted"]
    orderings = [p for p in pairs if p.verdict == "preserve_as_ordering"]
    return accepted, orderings


def _guarded_pair_write(
    path: Path,
    key: str,
    pairs: list[LensPair],
    loader,
    *,
    allow_shrink: bool,
) -> None:
    """Atomic write of a {key: [pairs]} JSON file carrying the #194 clobber
    guard: refuse to overwrite a populated store with a cliff-drop (empty
    when >= _CLOBBER_MIN_EXISTING entries exist, or below
    _CLOBBER_MIN_FRACTION of the existing count). A degenerate Stage 3
    (chairman returned empty, parse failed) would otherwise wipe the live
    lens. The live file is preserved and the would-be result lands in a
    `.degenerate` sidecar."""
    from ..utils import atomic_write_text
    from .turn_pairs import (
        _CLOBBER_MIN_EXISTING,
        _CLOBBER_MIN_FRACTION,
        DegenerateExtractionError,
    )

    existing = len(loader())
    floor = max(1, int(existing * _CLOBBER_MIN_FRACTION))
    payload = json.dumps({key: [p.to_dict() for p in pairs]}, indent=2)
    if not allow_shrink and existing >= _CLOBBER_MIN_EXISTING and len(pairs) < floor:
        sidecar = path.parent / (path.name + ".degenerate")
        try:
            sidecar.write_text(payload, encoding="utf-8")
        except OSError:
            pass
        raise DegenerateExtractionError(
            f"Refusing to overwrite {existing} {key} with {len(pairs)} "
            f"(cliff-drop below {floor}). Live {key} preserved; degenerate "
            f"result written to {sidecar.name}. Pass allow_shrink=True only "
            f"if the corpus genuinely shrank."
        )
    atomic_write_text(path, payload)


def save_lenses(
    accepted: list[LensPair],
    orderings: list[LensPair],
    *,
    allow_shrink: bool = False,
) -> tuple[Path, Path]:
    """Persist accepted lenses + orderings atomically, each behind the #194
    clobber guard. `allow_shrink=True` bypasses the guard on both files for
    a genuine shrink. A guard raise on the lenses write preserves BOTH
    files (orderings is written second, after the lenses guard passes), so
    the live lens never goes degenerate from a transient empty Stage 3."""
    lp = lenses_path()
    op = orderings_path()
    _guarded_pair_write(lp, "lenses", accepted, load_lenses, allow_shrink=allow_shrink)
    _guarded_pair_write(op, "orderings", orderings, load_orderings, allow_shrink=allow_shrink)
    return lp, op


def _load_pairs(path: Path, key: str) -> list[LensPair]:
    """Robust reader for the lenses / orderings JSON.

    A corrupted file (invalid JSON, wrong top-level type, a non-list under
    ``key``, non-dict or malformed rows, non-UTF8) degrades to ``[]`` rather
    than crashing. These readers feed BOTH the launchpad page build
    (``_load_taste_lenses`` calls them outside any try/except) AND the #194
    clobber-guard's shrink check, so a single bad ~/.trinity file must not nuke
    the portal or block an otherwise-valid lens write. Guard the SHAPE, not
    just the parse (v1.7.202 class — see ``guard_shape_not_just_parse``)."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    rows = raw.get(key, [])
    if not isinstance(rows, list):
        return []
    out: list[LensPair] = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        try:
            out.append(LensPair(**p))
        except TypeError:
            # row has unexpected / missing keys — skip it, don't crash the reader
            continue
    return out


def load_lenses() -> list[LensPair]:
    return _load_pairs(lenses_path(), "lenses")


def load_orderings() -> list[LensPair]:
    return _load_pairs(orderings_path(), "orderings")
