"""End-to-end driver for the 5-stage lens-discovery pipeline (Stages 0–4).

Stage 0 — turn-pair gap extraction (caller fires it as ONE batch chairman call)
Stage 1 — basin topology (no LLM; numpy k-means)
Stage 2 — decisions (caller fires it via council member call)
Stage 3 — pair mining (caller fires it via 3-member council)
Stage 4 — basin post-filter (deterministic; saves lenses.json + orderings.json
          + renders to memories/lens.md).

The driver is split so the caller (me_builder.build_me_via_council)
controls the LLM dispatches — keeping our "no LLM outside councils"
architectural commitment intact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# THE canonical lens.md tension-heading shape. render_me_markdown() below emits
# exactly `### {i}. {pole_a} ↔ {pole_b}` per accepted pair, so a TENSION is a
# numbered heading whose two poles are bridged by the `↔` separator — that arrow
# is what MAKES it a tension. Every surface that COUNTS tensions out of lens.md
# (the generators "lift" in me/generators.py, the degeneracy/lens-health
# structure check in degeneracy.py) MUST parse through this ONE predicate so they
# can never disagree about how many tensions the lens holds. Whitespace-tolerant
# (`\s+`) so a hand-edited lens.md with stray spacing still parses; `(.+?)` poles
# are non-greedy + the `↔` is required, so a numbered heading WITHOUT an arrow
# (a malformed hand edit, or a non-tension `### N.` line) is NOT counted as a
# tension. Anti-rot: test_lens_tension_count_one_parser_browser renders a real
# LensPair and asserts this matches it; the clobber-guard heading in me_builder
# is kept in sync by test_lens_clobber_guard.py.
_TENSION_HEADING = re.compile(r"^###\s+\d+\.\s+(.+?)\s+↔\s+(.+?)\s*$", re.MULTILINE)


def iter_lens_tensions(md: str) -> list[tuple[str, str]]:
    """Parse the (pole_a, pole_b) of every well-formed tension out of a lens.md
    body. THE single source of truth for "what counts as a tension" — see
    `_TENSION_HEADING`. A numbered `###` heading missing the `↔` separator is a
    malformed/non-tension line and is excluded, so every counting surface agrees.
    """
    return _TENSION_HEADING.findall(md or "")

from .basins import (
    Basin,
    basin_for_prompt,
    compute_basins,
    save_basins,
)
from .decisions import (
    Decision,
    parse_decisions,
    render_extraction_prompt,
)
from .pair_mining import (
    LensPair,
    basin_post_filter,
    parse_pair_mining_output,
    render_pair_mining_prompt,
    save_lenses,
    split_by_verdict,
)
from .turn_pairs import (
    RejectionSignal,
    iter_turn_pairs,
    parse_rejections,
    render_extraction_prompt as render_turn_pair_prompt,
    validate_signals,
)


@dataclass
class PipelineState:
    """Snapshot of intermediate artifacts for inspection / dry-run."""
    basins: list[Basin]
    decisions: list[Decision]
    pairs_raw: list[LensPair]
    pairs_filtered: list[LensPair]
    accepted: list[LensPair]
    orderings: list[LensPair]


def stage1_basins(
    *, k: int | None = None, seed: int = 42, split_megas: bool | None = None,
) -> list[Basin]:
    """Cluster PromptNodes into basins. Pure numpy, no LLM.

    k=None → corpus-size-aware basin count (compute_basins.auto_k), so the
    topic map doesn't junk-drawer as history grows (#245).

    split_megas=None (default) defers to the TRINITY_SPLIT_MEGA_BASINS env knob
    (default off) — the combined mega-basin splitter refines the topology by
    splitting ONLY oversized + incoherent basins into coherent sub-basins
    (#308). Opt-in until the founder flips it; behaviour-preserving when off."""
    basins = compute_basins(k=k, seed=seed, split_megas=split_megas)
    save_basins(basins)
    return basins


def stage0_turn_pair_prompt(
    pairs: list[dict[str, Any]],
    basins: list[Basin],
) -> str:
    """Render the Option A single-batch chairman prompt for turn-pair gaps."""
    return render_turn_pair_prompt(pairs, basins)


def stage0_parse_and_validate(
    raw_output: str,
    basins: list[Basin],
    pair_index: dict[str, dict[str, Any]],
) -> tuple[list[RejectionSignal], list[dict]]:
    """Parse chairman output, then run deterministic validators.

    Returns (kept_signals, rejected_records). The `rejected` list carries
    `reason` fields so chairman drift is auditable across rebuilds. Pure —
    no disk write; rejections flow in-memory into the unified ledger save
    (legacy rejections.jsonl retired in #209)."""
    raw_signals = parse_rejections(raw_output, basins)
    kept, rejected = validate_signals(raw_signals, pair_index)
    return kept, rejected


def collect_turn_pairs(limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build turn pairs for the Stage 0 batch and an index keyed by prompt_id
    so the post-validators can look up assistant/user/next_user text.

    Samples the window RECENCY-BIASED but DIVERSE (#252), not the first `limit`.
    The extractor iterates the corpus chronologically, so a `limit` passed
    straight through froze Stage 0 on the OLDEST pairs and never reached recent
    taste — that's how a 2023 IDE-era "give me the whole file" workaround stayed
    the #1 lens tension for 14 months. But the pure recent tail over-corrects to
    a single burst month (a dev sprint fills all 200). So: walk most-recent
    first, cap per calendar month (≈limit/10) so no one month dominates, and
    backfill if the caps leave us short. Recency tracks CURRENT taste; durable
    tensions still recur across the recent months (re-confirmed) while phases
    that ended fall off and decay via the registry's recency fade."""
    all_pairs = list(iter_turn_pairs(limit=None))  # chronological
    if limit and len(all_pairs) > limit:
        from ..memory.store import iter_prompt_nodes_no_embedding
        from .chapters import prompt_time
        from .thread_signal import LOW_SIGNAL_FLOOR, compute_thread_signals
        pid2month = {}
        pid2tid = {}
        for n in iter_prompt_nodes_no_embedding(limit=None):
            nid = getattr(n, "id", None)
            pid2month[nid] = prompt_time(n)[:7]
            pid2tid[nid] = getattr(n, "transcript_id", "") or ""
        # SEED gate (#269): score each thread; a pair from a throwaway/test
        # thread (the monkey, "say hi", a 3000-turn agent grind) is below the
        # floor and skipped, so the lens learns from real, high-signal progress.
        signals = compute_thread_signals()

        def _sig(pair) -> float:
            return signals.get(pid2tid.get(pair[2], ""), 0.0)

        import collections as _c
        per_month_cap = max(1, limit // 10)
        seen_per_month: _c.Counter = _c.Counter()
        chosen: list = []
        chosen_ids: set = set()
        # Pass 1: recent-first, high-signal only, capped per month → diverse
        # recent spread of substantive threads.
        for pair in reversed(all_pairs):
            if _sig(pair) < LOW_SIGNAL_FLOOR:
                continue
            m = pid2month.get(pair[2], "?")
            if seen_per_month[m] >= per_month_cap:
                continue
            seen_per_month[m] += 1
            chosen.append(pair)
            chosen_ids.add(pair[2])
            if len(chosen) >= limit:
                break
        # Pass 2: still high-signal, but drop the per-month cap to backfill from
        # any month (a few high-signal months shouldn't starve the window).
        if len(chosen) < limit:
            for pair in reversed(all_pairs):
                if pair[2] in chosen_ids or _sig(pair) < LOW_SIGNAL_FLOOR:
                    continue
                chosen.append(pair)
                chosen_ids.add(pair[2])
                if len(chosen) >= limit:
                    break
        # Pass 3: thin-corpus fallback — if high-signal pairs alone can't fill
        # the window, backfill recent pairs uncapped (a brand-new install with
        # little history still builds *a* lens).
        if len(chosen) < limit:
            for pair in reversed(all_pairs):
                if pair[2] in chosen_ids:
                    continue
                chosen.append(pair)
                if len(chosen) >= limit:
                    break
        recent = list(reversed(chosen))  # back to chronological for the chairman
    else:
        recent = all_pairs
    pairs: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    for assistant, user, prompt_id, next_user in recent:
        pairs.append({
            "prompt_id": prompt_id,
            "assistant_text": assistant,
            "user_text": user,
        })
        index[prompt_id] = {
            "assistant_text": assistant,
            "user_text": user,
            "next_user_text": next_user,
        }
    return pairs, index


def stage2_extraction_prompt(samples: list[dict[str, Any]], basins: list[Basin]) -> str:
    """Render the chairman prompt for decision extraction.

    `samples` items: {prompt_id, text}. Basin tag attached automatically
    via prompt_id lookup.
    """
    enriched = []
    for s in samples:
        prompt_id = s.get("prompt_id")
        enriched.append({
            "prompt_id": prompt_id,
            "text": s.get("text") or "",
            "basin": basin_for_prompt(basins, prompt_id) if prompt_id else None,
        })
    return render_extraction_prompt(enriched, basins)


def stage2_parse(raw_output: str, basins: list[Basin]) -> list[Decision]:
    # Pure parse — no disk write. Decisions flow in-memory into the unified
    # ledger save (legacy decisions.jsonl retired in #209).
    return parse_decisions(raw_output, basins)


def stage3_pair_mining_prompt(decisions: list[Decision]) -> str:
    return render_pair_mining_prompt(decisions)


def stage3_parse(raw_output: str) -> list[LensPair]:
    return parse_pair_mining_output(raw_output)


def _load_basin_centroids() -> dict[str, list[float]]:
    """Read basin centroids from ~/.trinity/memories/topics.json so
    Stage 4's T2 semantic filter can score each tension against each
    basin's geometric center. Returns {} if topics.json is missing or
    malformed — basin_post_filter degrades gracefully (count-only)."""
    import json
    from .. import state_paths as _sp
    path = _sp.memories_dir() / "topics.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
    except (OSError, ValueError):
        return {}
    out: dict[str, list[float]] = {}
    for basin in data.get("basins", []):
        bid = basin.get("id")
        centroid = basin.get("centroid")
        if isinstance(bid, str) and isinstance(centroid, list):
            out[bid] = centroid
    return out


def stage4_post_filter(pairs: list[LensPair], decisions: list[Decision]) -> tuple[list[LensPair], list[LensPair]]:
    """Apply basin post-filter (count + semantic), then split by verdict."""
    basin_centroids = _load_basin_centroids()
    filtered = basin_post_filter(pairs, decisions, basin_centroids=basin_centroids)
    accepted, orderings = split_by_verdict(filtered)
    save_lenses(accepted, orderings)
    return accepted, orderings


# stage4b_surface_conflicts (#141) was RETIRED 2026-06-05 — see retired_names.py.
# Its literal same-axis-opposite-pole detector produced an empty conflicts.json on
# the real corpus (surface-form logic on a semantic problem) and is superseded by the
# generators pass's semantic contradiction-split self-critique (the cross-topic layer).


def render_me_markdown(
    accepted: list[LensPair],
    orderings: list[LensPair],
    rejections: list[RejectionSignal] | None = None,
    tension_support: dict[tuple[str, str], dict[str, Any]] | None = None,
    preference_acts: list | None = None,
    trajectories: list | None = None,
) -> str:
    """Render lens artifacts as the lens-document markdown (written by
    the caller to ~/.trinity/memories/lens.md — function name retained
    for back-compat with the pre-task-#91 me.md path) so the chairman
    context loader picks them up. This replaces the old single-virtue-
    list shape with paired tensions.

    `tension_support` (#198) carries the accumulation signal from the
    lens registry, keyed by (pole_a, pole_b): `support_count`,
    `first_seen`, `last_confirmed`. When present, each tension renders
    how much evidence backs it and how long it has persisted — the
    durability the chairman should weight by. Tensions backed by fewer
    than `LOW_CONFIDENCE_BELOW` decisions are flagged so a thin signal
    isn't stated as if it were settled. Absent (e.g. registry layer
    skipped), tensions render without the line — graceful degradation.

    Rejections (Stage 0 turn-pair gaps) get a section too — they're
    behavioral evidence the chairman should see when scoring future
    council members against the user's actual choices.
    """
    from .lens_registry import LOW_CONFIDENCE_BELOW

    support = tension_support or {}
    lines: list[str] = ["# Lens", "", "## Lenses (paired tensions)", ""]
    if not accepted:
        lines.append("(No paired tensions found yet — run lens-build with more decisions.)")
    for i, p in enumerate(accepted, 1):
        lines.append(f"### {i}. {p.pole_a} ↔ {p.pole_b}")
        lines.append(f"- Pure-{p.pole_a} fails as: **{p.failure_a or 'unspecified'}**")
        lines.append(f"- Pure-{p.pole_b} fails as: **{p.failure_b or 'unspecified'}**")
        lines.append(f"- Tension evidence spans basins: {', '.join(p.basins_spanned) or '(none)'}")
        sig = support.get((p.pole_a, p.pole_b))
        if sig:
            n = sig.get("support_count", 0)
            first = (sig.get("first_seen") or "")[:10]
            last = (sig.get("last_confirmed") or "")[:10]
            stability = f"stable since {first}" if first and first == last else (
                f"first seen {first}, last confirmed {last}" if first or last else ""
            )
            caveat = " _(low confidence — seen in few decisions)_" if n < LOW_CONFIDENCE_BELOW else ""
            support_line = f"- Supported by {n} decision{'s' if n != 1 else ''}"
            if stability:
                support_line += f" · {stability}"
            lines.append(support_line + caveat)
        lines.append("")
    if orderings:
        lines.append("## Domain-specific taste (preferences local to one topic)")
        lines.append("")
        lines.append(
            "_Not cross-domain lenses, but real taste the chairman should honor "
            "inside that domain — kept with its topic, not flattened away (#267)._"
        )
        lines.append("")
        for p in orderings:
            where = f" _(in {', '.join(p.basins_spanned)})_" if p.basins_spanned else ""
            lines.append(f"- {p.pole_a} > {p.pole_b}{where}")
        lines.append("")
    # (The "⚠ Tensions in tension" section — Stage 4b #141 slice 3 — was retired
    # 2026-06-05; the generators pass surfaces cross-domain contradictions semantically.)
    if preference_acts:
        # EXTRACT-unification Stage 1: render rejections AND decisions as
        # ONE evidence stream — every act of the user expressing taste,
        # discriminated by `trigger`. model_miss = corrections of the
        # model; self_expressed = trade-offs the user stated directly.
        # (When `preference_acts` is absent, the legacy rejections-only
        # section below still renders — back-compat for callers not yet
        # migrated.)
        from collections import defaultdict

        from .preference_acts import MODEL_MISS, SELF_EXPRESSED

        miss = [a for a in preference_acts if a.trigger == MODEL_MISS]
        self_exp = [a for a in preference_acts if a.trigger == SELF_EXPRESSED]
        lines.append("## Preference acts")
        lines.append("")
        lines.append(
            "Every act of you expressing taste — the model-miss corrections "
            "AND the trade-offs you stated directly. The user is the final "
            "authority; weight what the user privileged over what was offered."
        )
        lines.append("")
        if miss:
            groups: dict[str, list] = defaultdict(list)
            for a in miss:
                groups[a.kind].append(a)
            lines.append(f"### Model-miss — you corrected the model ({len(miss)})")
            for kind in ("REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"):
                items = groups.get(kind, [])
                if not items:
                    continue
                lines.append(f"#### {kind} ({len(items)})")
                for a in items[:5]:  # cap per kind so lens.md stays readable
                    lines.append(f"- model: \"{a.sacrificed[:100]}\"")
                    lines.append(f"  you: \"{a.privileged[:100]}\"")
                    if a.why:
                        lines.append(f"  why: {a.why[:140]}")
                if len(items) > 5:
                    lines.append(f"  _({len(items) - 5} more)_")
            lines.append("")
        if self_exp:
            lines.append(f"### Self-expressed — your stated trade-offs ({len(self_exp)})")
            for a in self_exp[:8]:
                row = f"- **{a.privileged}** > {a.sacrificed}"
                if a.kind:
                    row += f" _({a.kind})_"
                lines.append(row)
                if a.why:
                    lines.append(f"  would flip if: {a.why[:140]}")
            if len(self_exp) > 8:
                lines.append(f"  _({len(self_exp) - 8} more)_")
            lines.append("")
    elif rejections:
        # Group by type so the chairman sees the signal-type distribution.
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for sig in rejections:
            groups[sig.type].append(sig)
        lines.append("## Implicit rejections (turn-pair gaps)")
        lines.append("")
        lines.append(
            "Mined from (model_response, user_next_turn) pairs. The user is the "
            "final authority — chairman should weight what the user actually did "
            "next over what the model proposed."
        )
        lines.append("")
        for sig_type in ("REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING"):
            items = groups.get(sig_type, [])
            if not items:
                continue
            lines.append(f"### {sig_type} ({len(items)})")
            for sig in items[:5]:  # cap per type so lens.md stays readable
                lines.append(f"- model: \"{sig.model_quote[:100]}\"")
                lines.append(f"  user: \"{sig.user_substitute[:100]}\"")
                if sig.why_signal:
                    lines.append(f"  why: {sig.why_signal[:140]}")
            if len(items) > 5:
                lines.append(f"  _({len(items) - 5} more)_")
            lines.append("")
    # Trajectory lens (#182): diachronic pulls aggregated across threads.
    # Rendered last so the chairman reads the synchronic acts first, then the
    # sustained arcs they compose into.
    if trajectories:
        from .arc_mining import render_trajectory_lines
        lines.extend(render_trajectory_lines(trajectories))
    return "\n".join(lines)
