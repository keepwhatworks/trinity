"""Council analytics — descriptive statistics over council_outcomes/.

Extracted from personal_routing.py on 2026-08-10, step 2b of the
council-ratified routing removal (council_8817ca0c57a2e4ff, amd_0165:
symbol-level extraction, rehome non-routing symbols before deleting).

These are DESCRIPTIVE, not prescriptive. They answer "what did the councils
do" for the status verb and the launchpad -- how often a council changed the
answer, which lab wins which category. `ask` never consumes them. The routing
table that DOES consume councils prescriptively (aggregate_routing_table,
compute_personal_routing_table, freeze_routing_to_disk) stays behind in
personal_routing.py and dies with the router.

An AST check established the dependency runs ONE WAY before anything moved:
every function here has zero routing dependencies, while two routing
functions call into here. Analytics is the lower layer, so it survives the
removal untouched -- which is the property that made the split safe.
"""
from __future__ import annotations

from typing import Any, Iterable
from .council_runtime import load_council_outcome
from .state_paths import council_outcomes_dir



_SUBSTANTIVE_MIN_CHARS = 50  # hard floor below which it's empty/echo regardless

_TERMINAL_PUNCT = (".", "!", "?", '"', "'", "`", ")", "]")

_VALUE_PROOF_MIN_COUNCILS = 10

_VALUE_PROOF_MIN_CHANGED_PCT = 25

_VALUE_PROOF_MIN_CHANGED_COUNT = 3

_WEDGE_MIN_CONTESTS = 8

_WEDGE_MIN_MARGIN = 3

def _slug_tiebreak(slug: str) -> tuple[int, ...]:
    """Stable, deterministic secondary key for a `max(... key=...)` over
    providers that tie on the primary score. Returns codepoints NEGATED so
    that the lexically SMALLEST slug yields the LARGEST tie-break key — i.e.
    a tie resolves to the same provider (`antigravity` < `claude` < `codex`)
    every run, instead of whichever winner the council scan / dict order
    happened to surface first this launch."""
    return tuple(-ord(ch) for ch in slug)

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
