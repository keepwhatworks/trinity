"""Compute the user's personal routing table on demand.

Two entry points:

  aggregate_routing_table(councils)
      Pure aggregation — given a list of {task_type, routing_label,
      chairman_winner} dicts, count chairman wins per provider per
      task_type. The chairman's pick IS the supervision signal (per
      the 2026-05-21 prime directive and commit bb817b6); no user-verdict
      signal is blended in — the whole user-pick layer was retired so the
      user just chats while the chairman decides.

  compute_personal_routing_table()
      Walk every council outcome on disk and aggregate. Called from
      the launchpad render and from chairman_picker. No file is written;
      the council_outcomes/ directory IS the source of truth, divergence
      becomes structurally impossible. Cached in-process by directory mtime.

The table shape:
    {
      "computed_at": iso,
      "councils_aggregated": int,
      "by_task_type": {
          "<task_type>": {
              "<provider>": {"overall": float, "n": int, "wins": int},
              ...
          },
          ...
      },
      "best_per_task_type": {"<task_type>": "<provider>", ...},
      "wins_per_task_type": {"<task_type>": {"<provider>": int, ...}, ...},
    }
"""
from __future__ import annotations

import statistics
from typing import Any, Iterable

from .utils import finite_float_or_none, now_iso

# The routing table still CONSUMES the analytics that moved out in step 2b.
# One-way by design: analytics has no routing dependencies, so it survives
# when this module dies with the router.
from .council_analytics import _is_real_contest, _scan_outcomes, _slug_tiebreak, _outcomes_signature


# Per the prime directive (2026-05-21): "Run any hard question through
# Claude, Codex, and Gemini in parallel. The chairman synthesizes through
# your taste lens and picks the answer YOU would have picked, not the
# generic one." The chairman's `winner` field IS the signal — counted as
# wins per provider per task_type. The chairman's pick IS the supervision
# signal — there is no user-verdict blend. Asking the user to pick the model
# they liked was sunset 2026-05-21 ("one more task on them, they don't want
# to do"); the routing table trains purely on what the chairman chose, so the
# user just chats without changing any of their behavior.




def aggregate_routing_table(councils: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Group routing labels by task_type and count chairman wins per provider.

    Each item should have:
        - routing_label: dict (with provider_scores + task_type + winner)
        - task_type: str (fallback when label lacks task_type)
        - chairman_winner: str | None (the provider the chairman picked)

    Two derived stats per (task_type, provider):
        - wins: count of councils where chairman picked this provider
        - overall: mean of chairman.provider_scores[provider].overall
                   (kept for the per-cell numeric bars in the table)

    `best_per_task_type[task_type]` is the provider with the most chairman
    wins (ties broken by mean overall). This is "chairman picked codex
    4 of 5 times for code-refactor" — the prime directive made visible.
    """
    by_task_scores: dict[str, dict[str, list[float]]] = {}
    by_task_wins: dict[str, dict[str, int]] = {}
    # Real-contest councils per task_type (>= 2 members gave a substantive
    # answer). A task_type backed ONLY by WALKOVERS — councils where one
    # provider was the sole substantive voice, so the chairman "picked" it by
    # default, not on quality — must NOT crown a confident "Best: X" chip: that
    # provider "won" only because nobody else ran (the council-card solo-
    # overclaim shape #35, the un-fixed sibling of the value-proof's
    # `_is_real_contest` gate — council_value_proof already restricts to real
    # contests so its headline measures quality, but the routing cheat-sheet +
    # routing.json reader's "Best" column never inherited the same gate). Reuse
    # the SHARED `_is_real_contest` predicate so the contest definition can't
    # drift from the value-proof / picks.json gate.
    by_task_real_contests: dict[str, int] = {}
    materialised = list(councils)
    skipped_degraded = 0
    for c in materialised:
        # DEGRADED COUNCILS DO NOT TEACH ROUTING (founder feedback doc v3,
        # 2026-08-24, confirmed on council_2797722d0cf6e1e3: two of three
        # members timed out, the sole survivor was named winner, and the
        # outcome carried a routing_lesson — a verdict selected by latency,
        # not quality). metadata.failed_members records the casualties; until
        # this line, nothing downstream read it, so provider_scores silently
        # shrank and the aggregation counted a walkover as a win.
        _meta = c.get("metadata")
        _failed = _meta.get("failed_members") if isinstance(_meta, dict) else None
        if isinstance(_failed, list) and _failed:
            skipped_degraded += 1
            continue
        label = c.get("routing_label") or {}
        # `task_type` and the chairman winner BOTH become dict keys below
        # (by_task_real_contests[task_type], by_task_wins[task_type][winner],
        # by_task_scores.setdefault(task_type, ...)). A council_outcomes/*.json
        # is a state file a user can hand-edit, and `routing_label.task_type` /
        # `routing_label.winner` can land as a wrong-type LIST or DICT (valid
        # JSON, wrong shape — `CouncilRoutingLabel.from_dict` coerces winner via
        # normalize_provider_slug, which passes a non-str through UNCHANGED, and
        # does NOT coerce task_type at all). An unhashable list/dict key raised
        # `TypeError: unhashable type: 'list'` out of aggregate_routing_table —
        # and since `_load_personal_routing_table` wraps the whole call in
        # `except Exception: return None`, ONE corrupt council_outcome blanked
        # the ENTIRE routing cheat-sheet card (every other healthy council lost),
        # exactly like the `overall`-coercion sibling below. Coerce both to a
        # clean str at the read boundary (the same `isinstance(...) else ...`
        # guard lens_routing.compute_basin_routing already applies to its
        # winner/task_text, so the two readers share one shape contract). A
        # non-str task_type degrades to "general"; a non-str winner degrades to
        # "" (treated as no-winner, falsy).
        _raw_task_type = label.get("task_type") or c.get("task_type") or "general"
        task_type = _raw_task_type if isinstance(_raw_task_type, str) else "general"
        scores = label.get("provider_scores") or {}
        if _is_real_contest(c):
            by_task_real_contests[task_type] = by_task_real_contests.get(task_type, 0) + 1
        # Chairman's explicit pick — load-bearing for the prime directive.
        # Falls back to the routing_label.winner (canonical) then
        # outcome-level winner_provider supplied by the caller.
        _raw_winner = (
            label.get("winner")
            or c.get("chairman_winner")
            or c.get("winner_provider")
        )
        chairman_winner = _raw_winner if isinstance(_raw_winner, str) else ""
        for provider, sub in scores.items():
            overall = sub.get("overall") if isinstance(sub, dict) else None
            if overall is None:
                continue
            # Shape-guard the per-provider score (guard_shape_not_just_parse /
            # #304): a council_outcomes/*.json is a state file a user can hand-edit
            # and `provider_scores[provider].overall` can land as a non-numeric
            # string ("abc"), a bool, or a NaN/Inf. A bare `float(overall)` raised
            # ValueError that bubbled out of aggregate_routing_table — and since
            # `_load_personal_routing_table` wraps the whole call in `except
            # Exception: return None`, ONE corrupt council_outcome blanked the
            # ENTIRE routing cheat-sheet card (every other healthy council lost)
            # AND silently skipped the scoreboard/routing.json freeze in
            # write_portal_html. A NaN that survived `float(...)` would poison the
            # `statistics.fmean` -> serialize as bare `NaN` and break the client's
            # JSON.parse. `finite_float_or_none` (the shared coercer that also
            # backs launchpad_data._safe_number) skips the one bad value so the
            # mean stays honest and the surface still renders.
            overall_f = finite_float_or_none(overall)
            if overall_f is None:
                continue
            by_task_scores.setdefault(task_type, {}).setdefault(provider, []).append(overall_f)
        # The chairman's pick is the supervision signal — one win per council.
        # No user-verdict blend: the user just chats, the chairman decides.
        if chairman_winner:
            by_task_wins.setdefault(task_type, {})[chairman_winner] = (
                by_task_wins.get(task_type, {}).get(chairman_winner, 0) + 1
            )

    by_task_type: dict[str, dict[str, dict[str, float]]] = {}
    best_per_task_type: dict[str, str] = {}
    wins_per_task_type: dict[str, dict[str, int]] = {}
    # Task types whose "best" provider has NO strict chairman-win lead over the
    # runner-up — a tie / coin-flip, not a pattern. `best_per_task_type` still
    # carries a provider (tie-broken by mean overall, so the chip has a name),
    # but a confident "Best: X" overclaims a pick the chairman split evenly.
    # This is the count-domain analog of the cortex WINNER_MARGIN_FLOOR gate
    # (lens_routing.py): the picks/topology surface already says "Lean X ·
    # near-tie" below the floor; the routing cheat-sheet + routing.json reader
    # must demote the same way. Consumers read this set and render a
    # "no clear pick" treatment instead of a confident chip. (green-gate #35:
    # a "Best" green must self-demote when the data has no margin.)
    pick_is_tie: dict[str, bool] = {}
    # Minimum council sample size before declaring a "best" per task_type.
    # Live trigger 2026-05-25: 89% of the user's 246 task_types had their
    # winner declared from n=1 council ("X wins task_type Y based on a
    # single sample"). That's noise, not signal. The chairman_picker
    # already sigmoid-blends low-n personal data with global benchmarks
    # via _blended_pick (reads by_task_type directly, not
    # best_per_task_type), so suppressing low-n entries here is purely
    # a display correctness fix — doesn't affect routing decisions.
    MIN_BEST_SAMPLES = 3
    for task_type, providers in by_task_scores.items():
        provider_summary: dict[str, dict[str, float]] = {}
        wins_here = by_task_wins.get(task_type, {})
        for provider, overalls in providers.items():
            mean_overall = statistics.fmean(overalls) if overalls else 0.0
            provider_summary[provider] = {
                "overall": round(mean_overall, 3),
                "n": len(overalls),
                "wins": wins_here.get(provider, 0),
            }
        by_task_type[task_type] = provider_summary
        wins_per_task_type[task_type] = dict(wins_here)
        # Total councils for this task_type — sample-size gate for the
        # "best" claim. We sum wins (chairman picks) when present, else
        # fall back to summing council counts from provider_summary.
        total_n = sum(wins_here.values()) if wins_here else sum(
            int(s.get("n", 0)) for s in provider_summary.values()
        )
        if total_n < MIN_BEST_SAMPLES:
            continue  # don't claim a best — let the consumer (or
            # chairman_picker's sigmoid blend) handle low-n explicitly.
        # Best = most chairman wins, tie-broken by mean overall, then by SLUG.
        # The slug tie-break is load-bearing: wins_here is a plain dict whose
        # iteration order follows the council-scan order, and `overall` is
        # rounded to 3dp so two providers genuinely tie on (wins, overall) (a
        # 2-2 split with equal mean scores). Without the slug key the
        # rendered "Lean X · no clear pick" provider would flip on which
        # winner was scanned first — and adding ONE new council with a fresh
        # hash-named file reorders the scan, flipping the displayed lean.
        if wins_here:
            best_provider = max(
                wins_here.items(),
                key=lambda kv: (
                    kv[1],
                    provider_summary.get(kv[0], {}).get("overall", 0),
                    _slug_tiebreak(kv[0]),
                ),
            )[0]
            best_per_task_type[task_type] = best_provider
            # Tie iff the best provider has NO strict win-count lead over the
            # runner-up. A 2-2 (debug) or 1-1-1 (strategy) split is a coin-flip
            # the cheat-sheet must not paint as a confident "Best".
            win_counts = sorted(wins_here.values(), reverse=True)
            runner_up = win_counts[1] if len(win_counts) > 1 else 0
            if win_counts[0] <= runner_up:
                pick_is_tie[task_type] = True
            # Walkover demotion (#35 solo-overclaim): even with a strict win-count
            # lead, a "Best: X" off a task_type with ZERO real contests is the lone
            # provider winning because nobody else ran. Demote to the same "no clear
            # pick / Lean X" treatment a tie gets — the chip's confidence isn't
            # earned without a contest. Demote, don't hide: the row + per-provider
            # scores still render; only the unjustified confident verdict drops.
            elif by_task_real_contests.get(task_type, 0) == 0:
                pick_is_tie[task_type] = True
        else:
            # No chairman winner recorded for any council in this task
            # type — fall back to highest mean overall so the column
            # isn't empty for historical data missing the winner field.
            best_provider = max(
                provider_summary.items(),
                key=lambda kv: (kv[1].get("overall", 0), _slug_tiebreak(kv[0])),
                default=(None, {}),
            )[0]
            if best_provider:
                best_per_task_type[task_type] = best_provider
                # No chairman supervision at all for this task type → the
                # "best" is a bare mean-score lead, not a chairman pick. Treat
                # as a tie so the surface doesn't claim "the chairman picks X".
                pick_is_tie[task_type] = True

    return {
        "computed_at": now_iso(),
        "councils_aggregated": len(materialised) - skipped_degraded,
        # Disclosed, not silent: a degraded council (a member failed mid-run)
        # is excluded because its chairman pick is partly a walkover. The count
        # keeps the exclusion visible in routing.json rather than making rows
        # quietly disappear.
        "councils_skipped_degraded": skipped_degraded,
        "by_task_type": by_task_type,
        "best_per_task_type": best_per_task_type,
        # Per-task-type chairman wins; the launchpad table can render
        # "chairman picked codex 4/5" using this.
        "wins_per_task_type": wins_per_task_type,
        # Task types where the "best" is a tie / coin-flip (no strict win-count
        # lead, or no chairman supervision at all). Render surfaces demote the
        # confident "Best" chip to a "no clear pick" treatment for these.
        "pick_is_tie": {k: True for k in pick_is_tie},
    }






# Substantive-output detection. A flat 200-char floor (the original) systematically
# misread Gemini's terser register — complete concise answers in the 145-199 char
# band (the Barcelona-route directions, the Electron diagnosis) were demoted to
# non-answers, under-crediting antigravity and over-counting "won by default"
# (#249: 181 councils stuck at exactly 1 substantive member, ~127 should be real
# 2-way contests). So: a low floor to kill empties/echoes, PLUS a completeness
# signal — ends in terminal punctuation but NOT a bare colon/heading (a
# colon-opener like "Here are some stores:" ends "cleanly" but is a truncation
# whose body never arrived). Feeds the value-proof DISPLAY only, not routing.



# Below this many real contests the aggregate isn't worth a headline — the
# confidence-honesty rule (n<3 suppress) generalized to the proof surface.

# The HEADLINE is a value claim — "the chairman picked a DIFFERENT model than
# your default X% of the time, so X% of the time one tab would've shipped the
# worse answer." A volume floor alone is not enough: a single-provider-loyal
# user whose chairman usually agrees with their default clears N>=10 yet has a
# LOW changed-pick rate, so the card would tout "differed 0% / 7% of the time"
# — a SELF-DEFEATING claim that argues AGAINST Trinity on the flagship home
# proof surface ("you'd have been fine with one tab"). Same green-gate class as
# the n<3 suppress rule: a headline must self-hide when the data doesn't support
# the claim it makes. So the card also gates on the VALUE the copy displays:
#   - a rate floor (the % must be high enough to be a painkiller), AND
#   - an absolute-count floor (a 25% rate off the N=10 floor is only ~3 flips;
#     the count guard closes the thin-evidence hole a bare rate floor admits at
#     low N — without it a 2-flip card could render on a flagship surface).
# Trinity-council-decided 2026-06-17 (council_78c065889d1c1b5c, winner codex,
# unanimous on "fixed rate floor + count guard, not a binomial test"). Pinned as
# named constants with a degenerate-data refusal test (test_council_value_proof_
# value_floor.py) per the green-gate checklist.

# A coarse category family needs at least this many real contests AND a
# win-margin this large before we'll name a leader — otherwise it's noise
# (the per-task-type grain is 400+ near-unique chairman labels; coarsening to
# the head token gives families like product_* → "product", strategic_* →
# "strategic" that carry real signal).








_CACHE: dict[str, Any] | None = None
_CACHE_KEY: tuple[float, int] | None = None




def compute_personal_routing_table() -> dict[str, Any]:
    """Walk rated council outcomes and aggregate. Cached on outcomes-dir mtime.

    The launchpad and chairman_picker both call this; with the cache, the
    walk is paid once per process per outcomes-dir change. No state file —
    the council_outcomes/ directory is canonical, can't drift from itself.

    A scan that hit ANY unreadable outcome (partial write, corrupt JSON) is
    returned but NOT promoted to the cache — so the next call after the
    transient finishes gets a clean recompute, not a frozen partial result.
    """
    global _CACHE, _CACHE_KEY
    signature = _outcomes_signature()
    if _CACHE is not None and _CACHE_KEY == signature:
        return _CACHE
    records, all_clean = _scan_outcomes()
    table = aggregate_routing_table(iter(records))
    if all_clean:
        _CACHE = table
        _CACHE_KEY = signature
    return table


def invalidate_cache() -> None:
    """Force the next compute_personal_routing_table call to re-walk disk."""
    global _CACHE, _CACHE_KEY
    _CACHE = None
    _CACHE_KEY = None


def freeze_routing_to_disk() -> dict[str, Any]:
    """Write the current routing table to `~/.trinity/scoreboard/routing.json`.

    The table is otherwise lazy-computed on every call from
    `council_outcomes/`. Freezing lets the chairman context loader, Phase 5
    distill, and any external reader see the empirical-memory entry without
    re-walking the outcomes dir each time.

    Returns the table that was written (same shape as
    compute_personal_routing_table). Skips writing if the table is empty.
    """
    import json
    from .state_paths import routing_path

    table = compute_personal_routing_table()
    # `table` is always a dict with metadata keys (computed_at,
    # councils_aggregated) even when no councils have been rated. The real
    # "is there routing signal" check is whether the per-task-type bucket
    # has entries.
    if not table.get("by_task_type"):
        return table
    from .utils import atomic_write_text
    atomic_write_text(routing_path(), json.dumps(table, indent=2, sort_keys=True))
    return table
