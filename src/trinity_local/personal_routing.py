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

from .council_runtime import load_council_outcome
from .state_paths import council_outcomes_dir
from .utils import finite_float_or_none, now_iso


# Per the prime directive (2026-05-21): "Run any hard question through
# Claude, Codex, and Gemini in parallel. The chairman synthesizes through
# your taste lens and picks the answer YOU would have picked, not the
# generic one." The chairman's `winner` field IS the signal — counted as
# wins per provider per task_type. The chairman's pick IS the supervision
# signal — there is no user-verdict blend. Asking the user to pick the model
# they liked was sunset 2026-05-21 ("one more task on them, they don't want
# to do"); the routing table trains purely on what the chairman chose, so the
# user just chats without changing any of their behavior.


def _slug_tiebreak(slug: str) -> tuple[int, ...]:
    """Stable, deterministic secondary key for a `max(... key=...)` over
    providers that tie on the primary score. Returns codepoints NEGATED so
    that the lexically SMALLEST slug yields the LARGEST tie-break key — i.e.
    a tie resolves to the same provider (`antigravity` < `claude` < `codex`)
    every run, instead of whichever winner the council scan / dict order
    happened to surface first this launch."""
    return tuple(-ord(ch) for ch in slug)


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
    for c in materialised:
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
        "councils_aggregated": len(materialised),
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


def _scan_outcomes() -> tuple[list[dict[str, Any]], bool]:
    """Walk council_outcomes/, return (records, all_clean).

    `all_clean` is False when ANY outcome JSON failed to parse — partial
    scans are returned but the caller (compute_personal_routing_table)
    will not promote them to the in-process cache, so a later complete
    scan supersedes them. Without this, a transient half-written outcome
    file could permanently poison the cached aggregate.
    """
    from .council_schema import normalize_provider_slug

    outcomes_dir = council_outcomes_dir()
    records: list[dict[str, Any]] = []
    if not outcomes_dir.exists():
        return records, True
    all_clean = True
    for outcome_path in sorted(outcomes_dir.glob("*.json")):
        council_id = outcome_path.stem
        try:
            outcome = load_council_outcome(council_id)
        except Exception:
            all_clean = False
            continue
        label = outcome.routing_label
        if label is None:
            continue
        try:
            label_dict = label.to_dict()
        except Exception:
            try:
                label_dict = dict(vars(label))
            except Exception:
                all_clean = False
                continue
        task_type = (outcome.metadata or {}).get("task_type")
        # The chairman's pick IS the supervision signal per the prime
        # directive (2026-05-21). The user-verdict path was sunset alongside
        # the rest of the rating UX — refinement prompts on each council are
        # the post-pivot signal path.
        chairman_winner = (
            (label_dict or {}).get("winner")
            or outcome.winner_provider
        )
        # How many members actually produced a real answer (>= 200 chars).
        # A council where only one member responded substantively isn't a
        # real contest — its "winner" won by default, not on quality. The
        # value proof (#236) filters on this so the headline measures
        # answer quality, not dispatch reliability (a third of the captured
        # ledger predates the dispatch fixes and has empty/echoed members).
        substantive_results = [
            m for m in (outcome.member_results or [])
            if _is_substantive_output(getattr(m, "output_text", "") or "")
        ]
        substantive_members = len(substantive_results)
        # How many DISTINCT provider families gave a substantive answer. A
        # same-family council — e.g. a chain `sequence=["claude","claude",
        # "claude"]`, which `mode="chain"` does NOT dedupe (it legitimately
        # revisits a provider) — yields 2+ substantive members but ONE distinct
        # voice. Counting raw substantive_members would let that through
        # `_is_real_contest` as a "real contest", so the value-proof headline
        # ("the chairman picked a DIFFERENT model than your default X% of the
        # time") and the per-lab win split tally three identical claude voices
        # as a cross-provider win — a fabricated painkiller stat. This is the
        # value-proof sibling of the same-family contest the share card / review
        # page / recent-councils rail already suppress on the DISTINCT-voice gate
        # (commit 00f37adc); those three LIST surfaces gate `solo` on distinct
        # providers, but the AGGREGATE value-proof was left on the raw count.
        # Fold legacy web-capture brand slugs (chatgpt/claude_ai/gemini → the
        # canonical trio) so a `gemini` capture + an `antigravity` CLI run of the
        # SAME family don't read as two voices.
        distinct_substantive_providers = len({
            normalize_provider_slug(getattr(m, "provider", "") or "")
            for m in substantive_results
            if getattr(m, "provider", None)
        })
        records.append({
            "council_run_id": council_id,
            "task_type": task_type,
            "routing_label": label_dict,
            "chairman_winner": chairman_winner,
            "winner_provider": outcome.winner_provider,
            "primary_provider": outcome.primary_provider,
            "substantive_members": substantive_members,
            "distinct_substantive_providers": distinct_substantive_providers,
        })
    return records, all_clean


def _iter_rated_councils() -> Iterable[dict[str, Any]]:
    """Yield {task_type, routing_label} dicts for every council outcome on disk
    that carries a routing_label, so the personal routing table reflects ALL
    council evidence the user has accumulated. The chairman's `provider_scores`
    and pick are the signal — no manual rating step gates an outcome out.
    """
    records, _ = _scan_outcomes()
    yield from records


# Substantive-output detection. A flat 200-char floor (the original) systematically
# misread Gemini's terser register — complete concise answers in the 145-199 char
# band (the Barcelona-route directions, the Electron diagnosis) were demoted to
# non-answers, under-crediting antigravity and over-counting "won by default"
# (#249: 181 councils stuck at exactly 1 substantive member, ~127 should be real
# 2-way contests). So: a low floor to kill empties/echoes, PLUS a completeness
# signal — ends in terminal punctuation but NOT a bare colon/heading (a
# colon-opener like "Here are some stores:" ends "cleanly" but is a truncation
# whose body never arrived). Feeds the value-proof DISPLAY only, not routing.
_SUBSTANTIVE_MIN_CHARS = 50  # hard floor below which it's empty/echo regardless
_TERMINAL_PUNCT = (".", "!", "?", '"', "'", "`", ")", "]")


def _is_substantive_output(text: str) -> bool:
    """True when a council member's output is a real, complete answer — not
    empty/echoed and not a truncated opener (#249)."""
    t = (text or "").strip()
    if len(t) < _SUBSTANTIVE_MIN_CHARS:
        return False
    if t.endswith(":"):
        return False  # "Here are the options:" — the body never arrived
    # Long answers are substantive even without clean terminal punctuation
    # (code blocks, tables, lists); short ones must look finished.
    return len(t) >= 200 or t.endswith(_TERMINAL_PUNCT)

# Below this many real contests the aggregate isn't worth a headline — the
# confidence-honesty rule (n<3 suppress) generalized to the proof surface.
_VALUE_PROOF_MIN_COUNCILS = 10

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
_VALUE_PROOF_MIN_CHANGED_PCT = 25
_VALUE_PROOF_MIN_CHANGED_COUNT = 3

# A coarse category family needs at least this many real contests AND a
# win-margin this large before we'll name a leader — otherwise it's noise
# (the per-task-type grain is 400+ near-unique chairman labels; coarsening to
# the head token gives families like product_* → "product", strategic_* →
# "strategic" that carry real signal).
_WEDGE_MIN_CONTESTS = 8
_WEDGE_MIN_MARGIN = 3


def _is_real_contest(record: dict[str, Any]) -> bool:
    """A council is a real contest when >= 2 members gave a substantive
    answer AND those came from >= 2 DISTINCT provider families. Records
    predating either field default to True (assume real) so synthetic/legacy
    records aren't silently dropped.

    The DISTINCT-provider clause closes the same-family hole: a chain
    `sequence=["claude","claude","claude"]` (which `mode="chain"` does NOT
    dedupe) yields 3 substantive members but ONE distinct voice — not a
    cross-provider contest. Without it, the value-proof headline counted that
    as "the chairman picked a DIFFERENT model than your default" and the
    per-lab win split tallied three identical claude voices as a Claude win
    (a fabricated painkiller stat on the flagship home surface). This is the
    AGGREGATE sibling of the same-family contest the share card / review page /
    recent-councils rail already suppress on the distinct-voice gate (00f37adc).
    `distinct_substantive_providers` is recorded by `_scan_outcomes` from the
    canonicalized member-result providers; when absent (legacy records), default
    to 2 so it doesn't retro-disqualify the existing ledger.

    Single source of truth for this gate — lens_routing.compute_basin_routing
    (picks.json) calls this same predicate so the routing rules and the value-proof
    headline can't drift on the threshold. `int(... or 0)` coerces None/str/0 to a
    real number (None/0 → not a contest) without crashing on a malformed record."""
    if int(record.get("substantive_members", 2) or 0) < 2:
        return False
    return int(record.get("distinct_substantive_providers", 2) or 0) >= 2


def council_value_proof() -> dict[str, Any]:
    """The council-first value proof, computed from the council_outcomes/
    ledger — no new eval, no model calls (#236).

    The painkiller, in one stat: a single-provider user gets their default
    model's answer every time. Trinity's chairman, having heard all three
    labs, picks a DIFFERENT model than the user's default a large fraction
    of the time — meaning that fraction of the time the default would have
    been the worse answer. We also surface the per-lab win split (provider
    names canonicalized at the load boundary so web-capture brand names —
    chatgpt/claude_ai/gemini — fold into codex/claude/antigravity).

    Restricted to REAL contests (>= 2 members gave a substantive answer) so
    the number measures answer quality, not dispatch reliability — a third of
    the captured ledger predates the dispatch fixes and has empty/echoed
    members whose "winner" won by default. The filter is CONSERVATIVE: on the
    current ledger it LOWERS the headline (56% across all 562 records -> 52% on
    the 475 real contests), because walkover councils inflate the apparent
    "chairman changed the pick" rate. We report the lower, defensible number.
    (The original comment claimed "56% before and after" — true when the filter
    landed, but the ledger has since grown and the filter now matters; corrected
    2026-06-02 after an independent recompute.) `tests/test_council_value_proof_
    conservative.py` pins the DIRECTION — the filter must never INFLATE the
    headline — so a future "looks like a no-op, drop it" can't silently push the
    public number up.

    Returns `{"ready": False, ...}` below the headline threshold so callers
    can stay quiet on a thin ledger rather than tout a noisy number.
    """
    from .council_schema import normalize_provider_slug

    all_records, _ = _scan_outcomes()
    total = len(all_records)
    records = [r for r in all_records if _is_real_contest(r)]
    n = len(records)
    if n < _VALUE_PROOF_MIN_COUNCILS:
        return {"ready": False, "n": n, "total": total,
                "min_councils": _VALUE_PROOF_MIN_COUNCILS}

    win_counts: dict[str, int] = {}
    changed = 0
    comparable = 0  # real contests where both winner and default are known
    for r in records:
        winner = normalize_provider_slug(r.get("chairman_winner") or r.get("winner_provider") or "")
        default = normalize_provider_slug(r.get("primary_provider") or "")
        # The win split MUST count the SAME `comparable` population the card's
        # headline rests on — "Across your {comparable} councils … wins: Claude X%
        # · GPT Y%". Tallying every real contest with a winner (incl. those with no
        # recorded default, which `n`/`real_contests` counts but `comparable` does
        # not) made the win counts sum to `n` while the card displays `comparable`,
        # so "Across your 12 councils … Claude (count 18)" disagreed with itself
        # (cross-path divergence: displayed count vs win-split denominator). Gate the
        # tally on the same `winner and default` predicate as `comparable` so every
        # number in the card shares ONE denominator. comparable <= n, so this only
        # ever narrows; on a ledger where every council records a default (comparable
        # == n) the split is unchanged.
        if winner and default:
            comparable += 1
            win_counts[winner] = win_counts.get(winner, 0) + 1
            if winner != default:
                changed += 1

    # Green-gate (principle #35): the HEADLINE is `changed_pct`, which rests on
    # `comparable` (real contests where BOTH the chairman winner AND the user's
    # default are recorded) — NOT on `n` (all real contests). A ledger of councils
    # launched without a recorded primary clears the n-gate yet has a thin
    # comparable base, so the painkiller % would be computed off a handful of
    # councils. Gate on the quantity the claim actually rests on. comparable <= n,
    # so this only ever TIGHTENS (never inflates) — on the founder's ledger
    # comparable == n, so no change; it catches the default-less-ledger user.
    if comparable < _VALUE_PROOF_MIN_COUNCILS:
        return {"ready": False, "n": n, "total": total, "comparable": comparable,
                "min_councils": _VALUE_PROOF_MIN_COUNCILS,
                "reason": "too few councils record both a winner and a default to "
                          "compute the changed-pick rate"}

    changed_pct = round(100 * changed / comparable) if comparable else 0

    # Green-gate (principle #35): the headline IS a value claim, so it must self-
    # hide when the value is too thin to defend — NOT just when the volume is.
    # The disqualifier lives IN the gate: a low changed rate OR too few actual
    # flips both refuse the green. This is what stops the flagship home card from
    # rendering "differed 0% of the time — that's how often one tab would've
    # shipped the worse answer" (a self-defeating claim) for a single-provider-
    # loyal user. Both floors are pre-registered named constants above.
    if changed_pct < _VALUE_PROOF_MIN_CHANGED_PCT or changed < _VALUE_PROOF_MIN_CHANGED_COUNT:
        return {"ready": False, "n": n, "total": total, "comparable": comparable,
                "changed_pick": changed, "changed_pct": changed_pct,
                "min_changed_pct": _VALUE_PROOF_MIN_CHANGED_PCT,
                "min_changed_count": _VALUE_PROOF_MIN_CHANGED_COUNT,
                "reason": "the chairman agreed with the default too often to claim a "
                          "single-tab habit would have shipped the worse answer"}

    # Denominator is `comparable` (not `n`): the win counts are tallied over the
    # comparable population above, so the percentages reconcile against the council
    # count the card displays. See the win-count gate comment for why.
    win_split = {
        p: {"count": c, "pct": round(100 * c / comparable)}
        # Win count DESC, provider slug ASC as a stable tie-break so the
        # value-proof win-split renders providers in a deterministic order:
        # two providers tied on win count would otherwise swap render order on
        # the win_counts dict-iteration order (council-scan derived).
        for p, c in sorted(win_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    }
    return {
        "ready": True,
        "n": n,
        "total": total,
        "real_contests": n,
        "changed_pick": changed,
        "comparable": comparable,
        "changed_pct": changed_pct,
        "win_split": win_split,
    }


def council_category_wedge() -> list[dict[str, Any]]:
    """The asymmetric wedge, per category: which lab wins which KIND of
    question (#236). Different labs genuinely specialize — Claude wins
    deliberation (strategy/architecture/hardware), GPT wins generation
    (product/creative/vendor) — and a single-provider user can't see it.

    Coarsens the 400+ near-unique chairman task_type labels to their head
    token (product_recommendation/product_research → "product"), restricts to
    REAL contests, and names a leader only where the family clears both a
    volume floor and a win-margin floor (else noise). Sorted by volume.
    Empty list on a thin ledger.
    """
    import collections

    from .council_schema import normalize_provider_slug

    all_records, _ = _scan_outcomes()
    fam: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in all_records:
        if not _is_real_contest(r):
            continue
        winner = normalize_provider_slug(r.get("chairman_winner") or r.get("winner_provider") or "")
        # The chairman's task_type lives on the routing_label (551/551 populated);
        # the metadata-sourced record field is mostly empty.
        label = r.get("routing_label") or {}
        task_type = (label.get("task_type") or r.get("task_type") or "").lower()
        if not winner or not task_type:
            continue
        fam[task_type.split("_")[0]][winner] += 1

    wedge: list[dict[str, Any]] = []
    for family, counts in fam.items():
        n = sum(counts.values())
        if n < _WEDGE_MIN_CONTESTS:
            continue
        ranked = counts.most_common()
        leader, lead_n = ranked[0]
        runner_n = ranked[1][1] if len(ranked) > 1 else 0
        if lead_n - runner_n < _WEDGE_MIN_MARGIN:
            continue  # contested — don't crown a leader
        wedge.append({
            "family": family,
            "leader": leader,
            "n": n,
            "lead_count": lead_n,
            "margin": lead_n - runner_n,
        })
    # Contest volume DESC, family name ASC as a stable tie-break. The caller
    # slices council_category_wedge()[:4], so two families tied on `n` at that
    # boundary would otherwise have WHICH family survives the cut flip on the
    # `fam` dict-iteration order (council-scan derived). family is unique.
    wedge.sort(key=lambda w: (-w["n"], w["family"]))
    return wedge


_CACHE: dict[str, Any] | None = None
_CACHE_KEY: tuple[float, int] | None = None


def _outcomes_signature() -> tuple:
    """Per-file (name, mtime_ns, size) tuple for cache invalidation.

    Naive `(latest_mtime, count)` collides when an existing outcome is edited
    in place with the same byte length and a same-second mtime — the cache
    keeps a stale aggregate. Per-file fingerprint catches in-place edits at
    nanosecond resolution and any size change.

    Sorted, hashed-via-tuple-equality. ~18 bytes/file × ~thousands of files =
    cheap; vastly cheaper than re-parsing every JSON when nothing changed.
    """
    outcomes_dir = council_outcomes_dir()
    if not outcomes_dir.exists():
        return ()
    rows: list[tuple[str, int, int]] = []
    for p in sorted(outcomes_dir.glob("*.json")):
        try:
            st = p.stat()
        except OSError:
            continue
        rows.append((p.name, st.st_mtime_ns, st.st_size))
    return tuple(rows)


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
