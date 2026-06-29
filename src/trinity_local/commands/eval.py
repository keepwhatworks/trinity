"""CLI handlers for the corpus-based eval harness (task #122).

Four subcommands:

  trinity-local eval-build [--limit N] [--source rejections]
    Build an eval set from the model_miss subset of the user's
    preference_acts.jsonl ledger + prompt index. Persists to
    ~/.trinity/evals/<eval_id>.json. Returns stats.

  trinity-local eval-stats [--eval-id ID]
    Inspect the LATEST eval set on disk. Shows item count + rejection-
    type distribution + basin distribution + sample items.

  trinity-local eval-run --target <provider> [--judge <provider>]
                         [--eval-id ID] [--limit N] [--no-score]
    Dispatch the eval set's prompts to <target> provider, then score
    each response against the rejected_response using <judge>. Persists
    results to ~/.trinity/evals/results/. THIS IS the empirical
    benchmark — score model X against the user's actual rejections.

  trinity-local eval-show [--target <provider>] [--eval-id ID]
                          [--limit-samples N]
    Inspect a past run result (default: most-recent). Renders aggregate
    score + per-rejection-axis bars + top/bottom sample items.
    Re-inspect without re-running; diff results across model releases.
"""
from __future__ import annotations

import json


def register(subparsers):
    build_p = subparsers.add_parser(
        "eval-build",
        help="Build an eval set from your prompt rejections (task #122)",
    )
    build_p.add_argument(
        "--limit", type=int, default=None,
        help="Cap the eval set to first N items (default: all rejections)",
    )
    build_p.add_argument(
        "--source", default="rejections",
        help="Eval source. MVP supports 'rejections'; cross_provider_pair lands in a follow-up.",
    )
    build_p.set_defaults(handler=handle_eval_build)

    stats_p = subparsers.add_parser(
        "eval-stats",
        help="Show stats for the latest eval set on disk",
    )
    stats_p.add_argument(
        "--eval-id", default=None,
        help="Specific eval_id to inspect. Defaults to the most-recent eval set.",
    )
    stats_p.set_defaults(handler=handle_eval_stats)

    run_p = subparsers.add_parser(
        "eval-run",
        help="Dispatch the eval set against a target provider and score the results (task #122 / #116)",
    )
    run_p.add_argument(
        "--target", required=True,
        help="Provider to benchmark (claude / codex / antigravity / ...).",
    )
    run_p.add_argument(
        "--judge", default=None,
        help="Provider that grades responses against the rejection axis. Defaults to a different provider than --target so the model isn't grading itself.",
    )
    run_p.add_argument(
        "--eval-id", default=None,
        help="Eval set to run. Defaults to the most-recent eval set on disk.",
    )
    run_p.add_argument(
        "--limit", type=int, default=None,
        help="Cap the dispatched items to first N (default: all). Useful for smoke tests before a full run.",
    )
    run_p.add_argument(
        "--no-score", dest="skip_score", action="store_true",
        help="Skip the scorer step. Useful when you want to inspect raw responses before paying the judge dispatch cost.",
    )
    run_p.set_defaults(handler=handle_eval_run)

    show_p = subparsers.add_parser(
        "eval-show",
        help="Inspect the latest eval run result for a target provider (task #122)",
    )
    show_p.add_argument(
        "--target", default=None,
        help="Filter to runs against this provider. Defaults to the latest run regardless of target.",
    )
    show_p.add_argument(
        "--eval-id", default=None,
        help="Filter to a specific eval_id. Useful when comparing the same eval across multiple targets.",
    )
    show_p.add_argument(
        "--limit-samples", type=int, default=3,
        help="How many per-item samples to render (default 3). Set to 0 to skip.",
    )
    show_p.add_argument(
        "--compare",
        action="store_true",
        help=(
            "Cross-provider leaderboard view: list every target_provider "
            "that has been scored against this eval set, sorted by "
            "aggregate score desc. Mirrors the launchpad's leaderboard."
        ),
    )
    show_p.add_argument(
        "--by-axis",
        action="store_true",
        help=(
            "With --compare: render the axis × provider matrix instead "
            "of the aggregate-only table. Surfaces per-rejection-type "
            "leadership splits (e.g. claude wins REFRAME, codex wins "
            "COMPRESSION) that the aggregate flattens. Requires --compare."
        ),
    )
    show_p.set_defaults(handler=handle_eval_show)

    share_p = subparsers.add_parser(
        "eval-share",
        help="Render an eval run result as a 1200×630 PNG you can tweet (task #122 follow-up)",
    )
    share_p.add_argument(
        "--target", default=None,
        help="Filter to runs against this provider. Defaults to the latest run regardless of target.",
    )
    share_p.add_argument(
        "--eval-id", default=None,
        help="Filter to a specific eval_id.",
    )
    share_p.add_argument(
        "--out", default=None,
        help="Output PNG path. Defaults to ~/.trinity/share/eval_card.png.",
    )
    share_p.add_argument(
        "--open", dest="open_after", action="store_true",
        help="Open the produced PNG with the OS default handler (Preview on macOS).",
    )
    share_p.add_argument(
        "--compare",
        action="store_true",
        help=(
            "Render the cross-provider leaderboard card instead of the "
            "single-provider per-axis card. Pair with --eval-id when "
            "providers were run against multiple eval sets."
        ),
    )
    share_p.add_argument(
        "--by-axis",
        action="store_true",
        help=(
            "With --compare: render the axis × provider matrix card "
            "(per-axis bars per provider + per-axis leader callout) "
            "instead of the aggregate-only leaderboard card. The wedge "
            "artifact for 'X is best at this kind of question'."
        ),
    )
    share_p.set_defaults(handler=handle_eval_share)

    judge_p = subparsers.add_parser(
        "eval-judge-check",
        help="Measure each candidate judge's agreement with YOUR own corrections + pick the most-aligned one",
    )
    judge_p.add_argument(
        "--limit", type=int, default=20,
        help="How many preference pairs to validate against (default 20). More = a tighter agreement estimate, more dispatch cost.",
    )
    judge_p.add_argument(
        "--dataset", default=None, metavar="PATH",
        help="Validate against a PUBLIC human-preference dataset file (RewardBench / Arena / HH-RLHF "
             "JSONL or JSON you downloaded) instead of your own corrections — the reproducible-mechanism "
             "trust check (docs/trust-and-validation.md pillar #2). Offline: reads the local file, no network.",
    )
    judge_p.add_argument(
        "--dry-run", action="store_true",
        help="Load + validate the preference pairs and print the per-category coverage WITHOUT dispatching "
             "to any judge (no quota). Use it to confirm a downloaded --dataset file parses and see its "
             "category distribution before spending dispatches on the full validation.",
    )
    judge_p.set_defaults(handler=handle_eval_judge_check)

    audit_p = subparsers.add_parser(
        "eval-audit",
        help="Scan your eval data for methodology bugs (length confound, axis imbalance, small-n, ...) — local, no dispatch, no quota",
    )
    audit_p.add_argument(
        "eval_id", nargs="?", default=None,
        help="Eval set to audit (default: most-recent eval_*.json). Built fresh if none exist.",
    )
    audit_p.add_argument(
        "--json", action="store_true",
        help="Emit the findings as JSON (counts + metrics only — privacy-safe, no raw prompts).",
    )
    audit_p.set_defaults(handler=handle_eval_audit)

    selfpref_p = subparsers.add_parser(
        "eval-selfpref",
        help="Validate that judges do NOT inflate their own model family (self-preference bias) — the cross-provider-eval trust check",
    )
    selfpref_p.add_argument(
        "--n", type=int, default=10,
        help="Responses sampled per target family (default 10). More = a tighter delta, more dispatch.",
    )
    selfpref_p.add_argument(
        "--json", action="store_true",
        help="Emit the result as JSON (model slugs + deltas + verdict only — privacy-safe, no prompts).",
    )
    selfpref_p.set_defaults(handler=handle_eval_selfpref)


def _print_dropped_axis_warning(stats: dict) -> None:
    """#281: warn when a rejection axis had model_miss acts but ZERO scoreable
    items (every act degenerate prompt==gold, or unresolved). Such an axis is
    silently absent from by_rejection_type, so without this the eval reads as if
    it covers the user's full rejection space when a whole axis collapsed — the
    green-while-degenerate shape (data_sampling_principle). COMPRESSION is the
    live example: terse user turns ("Yes" / "5") make user_substitute == prompt
    for 100% of acts, so all 17 drop and the axis vanishes."""
    fully_dropped = stats.get("fully_dropped_types") or []
    if not fully_dropped:
        return
    degen = stats.get("skipped_degenerate_by_type") or {}
    unres = stats.get("skipped_unresolved_by_type") or {}
    print("\n  ⚠ Rejection axes fully dropped (0 scoreable items — absent from the breakdown above):")
    for axis in fully_dropped:
        causes = []
        if degen.get(axis):
            causes.append(f"{degen[axis]} degenerate (prompt==gold)")
        if unres.get(axis):
            causes.append(f"{unres[axis]} unresolved prompt")
        print(f"    {axis:<12} {', '.join(causes) or 'all dropped'}")
    print("    → terse-turn axes: Stage-0 excerpted the whole short user turn as the gold,")
    print("      so prompt == gold for every act. Re-run `trinity-local lens-build` against")
    print("      the live corpus to recover them, or these axes can't be scored.")


def handle_eval_build(args):
    from ..evals.builder import build_eval_set, save_eval_set

    try:
        eval_set = build_eval_set(source=args.source, limit=args.limit)
    except FileNotFoundError as exc:
        print(f"✗ {exc}")
        raise SystemExit(2)
    except NotImplementedError as exc:
        print(f"✗ {exc}")
        raise SystemExit(2)

    path = save_eval_set(eval_set)

    # Human-readable summary: stats first (the marketing-legible
    # artifact), then where it's written.
    stats = eval_set.stats
    print(f"  Built eval set {eval_set.eval_id}")
    print(f"  Source: {eval_set.source}")
    print(f"  Items: {stats.get('items', 0)}")
    by_type = stats.get("by_rejection_type") or {}
    if by_type:
        print("  By rejection_type:")
        for kind, count in by_type.items():
            print(f"    {kind:<12} {count}")
    by_basin = stats.get("by_basin") or {}
    if by_basin:
        print(f"  By basin (top {min(8, len(by_basin))}):")
        for basin, count in list(by_basin.items())[:8]:
            print(f"    {basin:<8} {count}")
    _print_dropped_axis_warning(stats)
    print(f"\n  → {path}")

    # Green-while-degenerate guard (Trinity's #1 bug shape): the ledger existing
    # (`_rejections_available`, the launchpad's first-run gate) is NOT the same as
    # the built set having SCOREABLE items. A ledger of only self_expressed
    # (decision) acts, or model_miss acts that all collapsed degenerate/unresolved,
    # builds a 0-item set — `build_eval_set` raises only on a MISSING ledger, not an
    # empty RESULT. Printing the "Next: eval-run" success CTA on a 0-item set steers
    # the user to dispatch a hollow benchmark (real councils, real quota, no signal).
    # When 0 items survived, REFUSE the runnable CTA and steer back to mining real
    # rejections. The set is still written (its stats are the diagnostic the dropped-
    # axis warning + `eval-stats` read) but it is NOT presented as runnable.
    if stats.get("items", 0) == 0:
        print(
            "\n  ⚠ This eval set has 0 scoreable items — there's nothing to run yet."
            "\n    `eval-run` needs MODEL-MISS rejections (the times you REWROTE a model's"
            "\n    answer); your ledger has acts, but none survived as scoreable items"
            "\n    (decision-only acts, or rejections where the prompt equals the gold)."
            "\n    Mine more rejections from your transcripts, then rebuild:"
            "\n      trinity-local lens        # extract model-miss rejections from turn pairs"
            "\n      trinity-local eval-build  # rebuild once real rejections exist"
        )
        return

    # Re-score nudge: if this isn't the first eval set the user has
    # built, name the providers already scored against PRIOR sets and
    # surface ready-to-paste eval-run commands against the NEW one.
    # Without this, the user has to remember to re-score after every
    # rebuild, and the leaderboard silently drifts out of sync.
    prior_targets = _targets_with_results(exclude_eval_id=eval_set.eval_id)
    if prior_targets:
        print()
        print(
            f"  Note: {len(prior_targets)} provider(s) already scored against prior "
            f"eval sets ({', '.join(sorted(prior_targets))}). Re-run against this "
            f"new set so the leaderboard reflects the fresh signals:"
        )
        for target in sorted(prior_targets):
            print(f"    trinity-local eval-run --target {target} --eval-id {eval_set.eval_id}")
    else:
        print("\n  Next: `trinity-local eval-run --target <provider>` to score a model against this set,"
              "\n        then `trinity-local eval-show` to inspect results.")


def _targets_with_results(exclude_eval_id: str | None = None) -> set[str]:
    """Return the set of target_provider names that have at least one
    eval result on disk against an eval set OTHER than `exclude_eval_id`.

    Used by handle_eval_build to nudge the user toward re-scoring after
    a rebuild. Filename convention from evals.runner.result_path:
      eval_<eval_id>__model_<target>__<ts>.json
    """
    import json

    from ..council_schema import normalize_provider_slug
    from ..evals.builder import results_dir
    rd = results_dir()
    if not rd.exists():
        return set()
    targets: set[str] = set()
    for path in rd.glob("eval_*__model_*.json"):
        # Skip results against the same eval set we just rebuilt — the
        # nudge is about RE-scoring, not re-pointing at fresh data.
        if exclude_eval_id and f"eval_{exclude_eval_id}__" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        target = data.get("target_provider")
        if target:
            # Fold web-era capture slugs to the CLI dispatch slug: this set both
            # counts providers and emits `eval-run --target {target}` commands —
            # a raw `gemini` would double-count Gemini AND print a command that
            # won't dispatch (eval-run expects claude/codex/antigravity).
            targets.add(normalize_provider_slug(target) or target)
    return targets


def handle_eval_stats(args):
    from ..evals.builder import evals_dir, load_eval_set

    eval_id = args.eval_id
    if eval_id is None:
        # Pick the most-recent eval_<...>.json by mtime, tie-broken on the stem
        # so the order is a TOTAL order even when two eval sets share an
        # st_mtime (a same-second eval-build, or a copy). Without the stem
        # key, `sorted(..., reverse=True)` keeps the (unsorted) glob order on
        # an mtime tie — so WHICH set `eval-stats` reports as "latest" flips
        # purely on filesystem glob order. `-mtime` = newest-first; `stem`
        # ascending breaks the tie deterministically (same canon as
        # launchpad_data._compute_eval_summary).
        candidates = sorted(
            evals_dir().glob("eval_*.json"),
            key=lambda p: (-p.stat().st_mtime, p.stem),
        )
        if not candidates:
            print("  No eval sets on disk yet — run `trinity-local eval-build` first.")
            raise SystemExit(1)
        eval_id = candidates[0].stem

    eval_set = load_eval_set(eval_id)
    if eval_set is None:
        print(f"✗ eval set {eval_id} not found at {evals_dir() / f'{eval_id}.json'}")
        raise SystemExit(2)

    stats = eval_set.stats
    print(f"  {eval_set.eval_id}  (built {eval_set.built_at}, source={eval_set.source})")
    print(f"  Items: {stats.get('items', 0)}")
    by_type = stats.get("by_rejection_type") or {}
    if by_type:
        print("\n  Rejection-type distribution:")
        total = sum(by_type.values())
        for kind, count in by_type.items():
            pct = (100.0 * count / total) if total else 0.0
            bar = "█" * int(round(pct / 4))  # 25 chars max bar
            print(f"    {kind:<12} {count:>3}  {pct:5.1f}%  {bar}")
    by_basin = stats.get("by_basin") or {}
    if by_basin:
        print("\n  Basin distribution (top 10):")
        for basin, count in list(by_basin.items())[:10]:
            print(f"    {basin:<8} {count}")
    _print_dropped_axis_warning(stats)

    # A few sample items so the user sees the eval shape, not just counts.
    sample = eval_set.items[:3]
    if sample:
        print("\n  Sample items:")
        for item in sample:
            preview = (item.prompt or "(no prompt text)")
            if len(preview) > 100:
                preview = preview[:100].rstrip() + "…"
            print(f"\n    [{item.rejection_type:<11}] {preview}")
            quote = item.rejected_response[:120].replace("\n", " ")
            print(f"       rejected: {quote}{'…' if len(item.rejected_response) > 120 else ''}")


def _default_judge_provider(target: str, configs: dict) -> str | None:
    """Pick a judge that isn't the model being scored. Prefers cloud
    chairman-grade providers (claude / codex / antigravity) over local
    models — pre-launch real-run discovered MLX was being picked as
    judge by alphabetical default and returning empty stdout for the
    judge prompt, defaulting every score to 0.5. Bias-trap warning
    surfaced in the CLI output.

    (Slug `gemini` is intentionally omitted from the preferred list:
    the legacy Google CLI binary was retired as a Trinity dispatch
    target per task #127's 2026-05-21 Antigravity migration. Before
    iter #61's fix, this function listed `gemini` here, which never
    matched config.json's `antigravity` slug and silently fell
    through to the alphabetical fallback — picking MLX as judge in
    cases where Antigravity was available and preferred.)
    """
    # Preferred chairman-grade providers, in priority order.
    preferred = ("claude", "codex", "antigravity")
    for name in preferred:
        if name != target and name in configs and configs[name].enabled:
            return name
    # Fallback: any enabled non-target provider — but log a warning
    # via the calling CLI when this branch hits, since it likely
    # means an MLX/Ollama judge that may not produce structured output.
    for name in configs:
        if name != target and configs[name].enabled:
            return name
    return None


def _suggest_alt_judge(failed_judge: str, target: str, configs: dict) -> str | None:
    """Suggest a judge to retry with after `failed_judge` returned empty/degenerate
    output (the codex-rate-limit / non-chat-judge failure mode). Prefers a
    chairman-grade provider that is neither the failed judge nor the target being
    scored — claude first (the most reliable structured-verdict judge)."""
    for name in ("claude", "antigravity", "codex"):
        if name not in (failed_judge, target) and name in configs and configs[name].enabled:
            return name
    for name in configs:
        if name not in (failed_judge, target) and configs[name].enabled:
            return name
    return None


def _alignment_report_path():
    """`~/.trinity/evals/judge_alignment.json` — the measured judge-trust report
    (written by `eval-judge-check`). Numbers only, no raw text."""
    from ..evals.builder import evals_dir
    return evals_dir() / "judge_alignment.json"


def _public_alignment_report_path():
    """`~/.trinity/evals/judge_alignment_public.json` — the PUBLIC-dataset trust
    reproduction (`eval-judge-check --dataset`). Kept SEPARATE from the corrections
    report so a public run never overwrites the judge-selection signal: eval-run
    still picks its judge from YOUR corrections, not from generic human preference."""
    from ..evals.builder import evals_dir
    return evals_dir() / "judge_alignment_public.json"


def _load_alignment_report() -> dict | None:
    """The saved judge-alignment report, or None. Never raises — a malformed or
    absent report just means we fall back to the heuristic judge."""
    try:
        p = _alignment_report_path()
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _alignment_chosen_judge(target: str, configs: dict, report: dict | None) -> str | None:
    """The MEASURED most-aligned judge from the report — if it exists, isn't the
    target (no self-grading), and is enabled. This is the trust-first judge pick:
    the model that agreed with the user's own corrections most. Returns None to
    let the caller fall back to the heuristic."""
    if not report:
        return None
    chosen = report.get("chosen_judge")
    if chosen and chosen != target and chosen in configs and configs[chosen].enabled:
        return chosen
    return None


def _record_judge_alignment(run_result, judge: str, report: dict | None) -> None:
    """Stamp the judge's measured agreement (vs the user's own corrections) onto the
    run result, so the card can show 'judged by X — agrees with your corrections Y%'.
    Best-effort; a missing report just leaves the fields None."""
    if not report:
        return
    entry = (report.get("judges") or {}).get(judge)
    if isinstance(entry, dict):
        run_result.judge_agreement = entry.get("agreement")
        run_result.judge_alignment_n = entry.get("n_parsed")


def handle_eval_judge_check(args):
    """Validate each candidate judge against the user's OWN past corrections and
    save the report — the measured-trust artifact behind the eval card.

    Every model_miss act is a human-labelled A/B (the user privileged their rewrite
    over the model's answer). We ask each candidate judge those pairs, position-
    balanced, and measure how often it picks the side the human chose. The best-
    aligned judge is recorded as `chosen_judge`; eval-run prefers it. Real dispatch
    (each judge × N pairs) — `--limit` caps the pairs (default 20) to keep it cheap.
    """
    from pathlib import Path

    from ..config import load_config
    from ..evals.judge_alignment import (
        GENERIC_PREFERENCE_PROMPT,
        JUDGE_VALIDATION_PROMPT,
        build_preference_pairs,
        save_alignment_report,
        select_aligned_judge,
        validate_judge,
    )
    from ..state_paths import lens_path

    config = load_config(getattr(args, "config", None), required=True)
    provider_configs = {name: p for name, p in config.providers.items() if p.enabled}

    dataset = getattr(args, "dataset", None)
    is_public = bool(dataset)
    limit = getattr(args, "limit", None) or 20

    # Pair source: PUBLIC dataset (reproducible-mechanism trust check) vs the user's
    # OWN corrections (the judge-selection signal). Public mode asks the GENERIC
    # preference question with no lens — the public labels are generic human
    # preference, not this user's taste — and is saved to a separate report so it
    # never clobbers the corrections-based judge pick eval-run reads.
    if is_public:
        from ..evals.public_datasets import load_public_pairs
        try:
            pairs = load_public_pairs(dataset, limit=limit)
        except FileNotFoundError as e:
            print(f"  {e}")
            print("  Download a preference set first, e.g. `huggingface-cli download allenai/reward-bench`,")
            print("  then point --dataset at the local JSONL/JSON file.")
            raise SystemExit(2)
        except ValueError as e:
            print(f"  {e}")
            raise SystemExit(2)
        source_label = f"{len(pairs)} pairs from public dataset {Path(dataset).name}"
        prompt_template = GENERIC_PREFERENCE_PROMPT
        lens_text = ""
        report_path = _public_alignment_report_path()
    else:
        pairs = build_preference_pairs(limit=limit)
        if not pairs:
            print("  No human-labelled preference pairs yet (need model_miss acts in your ledger).")
            print("  Run `trinity-local lens` to mine corrections from your transcripts first.")
            print("  (Or validate the mechanism on public data: eval-judge-check --dataset <rewardbench.jsonl>.)")
            raise SystemExit(2)
        source_label = f"{len(pairs)} of your own corrections"
        prompt_template = JUDGE_VALIDATION_PROMPT
        lens_md = lens_path()
        lens_text = lens_md.read_text(encoding="utf-8") if lens_md.exists() else ""
        report_path = _alignment_report_path()

    # --dry-run: validate the pairs + show per-category coverage with NO dispatch
    # (no quota). Lets a user confirm a downloaded --dataset parses and inspect its
    # category distribution before spending judge calls — the same measure-before-
    # you-spend discipline as eval-audit. No providers required (nothing dispatches).
    if getattr(args, "dry_run", False):
        from collections import Counter
        axis_counts = Counter((p.axis or "?") for p in pairs)
        a_side = sum(1 for p in pairs if p.human_side == "A")
        print(f"Dry run — {source_label} (no judge dispatched):")
        print(f"  {len(pairs)} preference pair(s) across {len(axis_counts)} categor(y/ies):")
        for axis, n in axis_counts.most_common():
            print(f"    {axis:<16} {n}")
        # Position-balance is the anti-position-bias invariant: the human-preferred
        # side should be ~50/50 across A/B. Surface it so a skewed file is visible.
        print(f"  position balance: {a_side} A / {len(pairs) - a_side} B "
              f"(~50/50 cancels judge position bias)")
        verb = "eval-judge-check --dataset <file>" if is_public else "eval-judge-check"
        print(f"  Ready. Re-run `{verb}` without --dry-run to validate the judges.")
        return 0

    # Candidate judges: the canonical chairman-grade LLMs that are enabled.
    candidates = [c for c in ("claude", "codex", "antigravity") if c in provider_configs]
    if not candidates:
        print("  No chairman-grade judge providers enabled (need claude / codex / antigravity).")
        raise SystemExit(2)

    print(f"Validating {len(candidates)} judge(s) against {source_label}...")

    def _prog(i, total, pair, verdict):
        print(f"  [{i}/{total}] {pair.axis or '?':<11} judge said {verdict or '—'}")

    # Validate each candidate, printing per-judge progress as we go.
    results = {}
    for judge in candidates:
        print(f"\n  judge: {judge}")
        results[judge] = validate_judge(
            judge, pairs, provider_configs, lens_text,
            progress_callback=_prog, prompt_template=prompt_template,
        )
    # Significance-gated selection (shared with pick_most_aligned_judge): only choose
    # a judge when the lead is real, not noise (the n=12-Gemini-by-2-pairs trap).
    chosen, reason = select_aligned_judge(results)

    out = save_alignment_report(chosen, results, report_path)

    against = "verified human preference" if is_public else "YOUR own past corrections"
    print(f"\n  Judge alignment (agreement with {against}):")
    for name, r in sorted(results.items(), key=lambda kv: -(kv[1].agreement if kv[1].agreement is not None else -1.0)):
        if r.agreement is None:
            print(f"    {name:<12} n={r.n_parsed:<3} — no parseable verdicts")
            continue
        print(f"    {name:<12} {r.agreement*100:5.1f}%  (agreed {r.n_agreed}/{r.n_parsed}; {r.unparsed} unparsed)")
        # Per-axis breakdown — where the agreement comes from, not just the total.
        # For a public benchmark these are the dataset's categories (chat / reasoning
        # / safety — the standard RewardBench split, where you see a judge that's
        # strong on chat but weak on safety); for your corrections they're the
        # rejection axes (REFRAME / COMPRESSION / ...). Only shown with >=2 axes —
        # one axis is just the overall number again. Counts only (privacy-safe).
        axes = r.by_axis or {}
        if len(axes) >= 2:
            parts = []
            for ax, slot in sorted(axes.items(), key=lambda kv: -(kv[1].get("n", 0) or 0)):
                n = slot.get("n", 0) or 0
                if not n:
                    continue
                parts.append(f"{ax or '?'} {slot.get('agreed', 0) / n * 100:.0f}% ({n})")
            if parts:
                print(f"        per-axis: {', '.join(parts)}")
        # Length-controlled disclosure: does the agreement hold when the preferred
        # answer is the LONGER one, or only when it's shorter (a conciseness prior)?
        gap = r.length_gap
        if gap is not None:
            s = r.agreement_when_human_shorter or 0.0
            l = r.agreement_when_human_longer or 0.0
            verdict = "holds regardless of length ✓" if abs(gap) < 0.15 else "⚠ partly a length prior"
            print(f"        length-controlled: {s*100:.0f}% when the preferred side was shorter "
                  f"({r.n_short_parsed}), {l*100:.0f}% when longer ({r.n_long_parsed}) — {verdict}")
        # Shuffle-null negative control: shuffle the human labels → agreement must
        # collapse to ~chance. A null near 50% proves the agreement above is earned,
        # not an artifact; a null that stays high means the number is rigged.
        if r.shuffle_null is not None:
            rigged = abs(r.shuffle_null - 0.5) > 0.15
            note = "⚠ stays high — agreement may be an artifact, not real" if rigged \
                   else "≈chance ✓ (agreement is earned, not an artifact)"
            print(f"        shuffle-null: {r.shuffle_null*100:.0f}% — {note}")
    if is_public:
        # Public mode is a MECHANISM reproduction, not a judge pick for eval-run.
        if chosen:
            print(f"\n  → {chosen} agrees with public human preference at the strongest rate ({reason}).")
        else:
            print(f"\n  → No judge cleared the bar on this set: {reason}.")
        print("    This is the reproducible-mechanism check (trust pillar #2): the same harness,")
        print("    public ground truth, no private data. eval-run still picks its judge from YOUR corrections.")
    elif chosen:
        print(f"\n  → Chosen judge: {chosen} — {reason}. eval-run will prefer it.")
    else:
        print(f"\n  → No judge chosen: {reason}.")
        print("    eval-run falls back to a non-target heuristic judge; pass --judge to force one.")
    print(f"  Report: {out}")
    return 0


_SEV_GLYPH = {"risk": "✗", "warn": "⚠", "info": "ℹ", "ok": "✓"}


def handle_eval_audit(args):
    """Scan the eval data for METHODOLOGY bugs — the quiet kind that don't crash,
    they just make the headline number mean something other than what it claims
    (the green-while-degenerate shape). Local + privacy-safe: reviews the built
    eval set + the human-labelled preference pairs, prints findings by severity.
    No model dispatch — runs offline, costs no quota, leaks no prompt text.

    Answers the founder's "review data and see if there are ways to scan for and
    fix methodology bugs" — the same class that surfaced the length confound and
    the n=12 noise pick by hand, now a one-command scan.
    """
    from ..evals.builder import build_eval_set, evals_dir, load_eval_set
    from ..evals.judge_alignment import build_preference_pairs
    from ..evals.methodology_check import audit_eval_methodology

    eval_id = getattr(args, "eval_id", None)
    if eval_id is None:
        # (-mtime, stem) = newest-first with a deterministic tie-break, so the
        # methodology audit reports the SAME "latest" set under any glob order
        # when two eval sets share an st_mtime. See handle_eval_stats.
        candidates = sorted(evals_dir().glob("eval_*.json"), key=lambda p: (-p.stat().st_mtime, p.stem))
        if candidates:
            eval_id = candidates[0].stem
    eval_set = load_eval_set(eval_id) if eval_id else None
    if eval_set is None:
        # No set on disk — build one fresh so the audit still runs. On an empty
        # home `build_eval_set` raises FileNotFoundError (no preference-act ledger
        # yet); degrade like the sibling `handle_eval_build` does — print the
        # (already-actionable) message and exit cleanly, NOT a raw traceback for a
        # brand-new user running a documented command on a cold home.
        try:
            eval_set = build_eval_set()
        except FileNotFoundError as exc:
            print(f"  {exc}")
            raise SystemExit(1)
        if not eval_set.items:
            print("  No eval items yet — run `trinity-local lens` then `trinity-local eval-build` first.")
            raise SystemExit(1)

    pairs = build_preference_pairs()
    findings = audit_eval_methodology(eval_set, pairs)

    if getattr(args, "json", False):
        print(json.dumps({
            "eval_id": eval_set.eval_id,
            "n_items": len(eval_set.items),
            "n_pairs": len(pairs),
            "findings": [f.to_dict() for f in findings],
        }, indent=2))
        return 0

    risks = sum(1 for f in findings if f.severity == "risk")
    warns = sum(1 for f in findings if f.severity == "warn")
    print(f"  Methodology audit — {eval_set.eval_id}  ({len(eval_set.items)} items, {len(pairs)} preference pairs)\n")
    for f in findings:
        glyph = _SEV_GLYPH.get(f.severity, "·")
        print(f"  {glyph} {f.name:<22} {f.metric}")
        print(f"      {f.detail}")
    print()
    if risks:
        print(f"  {risks} risk(s), {warns} warning(s) — address the ✗ items before quoting the headline number publicly.")
    elif warns:
        print(f"  No risks; {warns} warning(s) — report the per-axis breakdown alongside the aggregate.")
    else:
        print("  ✓ No methodology risks flagged on the local checks.")
    print("\n  (Local scan only — no model dispatch. Judge-behaviour checks (position bias,")
    print("   placebo/shuffle null) need dispatch; run `eval-judge-check` for the measured alignment.)")
    return 0


def handle_eval_run(args):
    from ..config import load_config
    from ..evals.builder import evals_dir, load_eval_set
    from ..evals.runner import run_eval, save_run_result
    from ..evals.scorer import score_run

    eval_id = args.eval_id
    if eval_id is None:
        # (-mtime, stem) = newest-first with a deterministic tie-break, so
        # `eval-run` (no --eval-id) dispatches against the SAME "latest" set
        # under any glob order when two eval sets share an st_mtime. See
        # handle_eval_stats.
        candidates = sorted(evals_dir().glob("eval_*.json"),
                            key=lambda p: (-p.stat().st_mtime, p.stem))
        if not candidates:
            print("  No eval sets on disk. Run `trinity-local eval-build` first.")
            raise SystemExit(2)
        eval_id = candidates[0].stem

    eval_set = load_eval_set(eval_id)
    if eval_set is None:
        print(f"✗ eval set {eval_id} not found")
        raise SystemExit(2)

    # Accept user-facing names (gemini/gpt/chatgpt/…) for --target and
    # --judge — the most viral feature ("score the new model against my
    # taste") must not fail because the user typed the brand instead of the
    # internal slug (`antigravity`). Resolve to the slug; canonical slugs +
    # unknown names pass through unchanged.
    from ..council_schema import resolve_provider_alias
    args.target = resolve_provider_alias(args.target)
    if getattr(args, "judge", None):
        args.judge = resolve_provider_alias(args.judge)

    config = load_config(getattr(args, "config", None), required=True)
    provider_configs = {name: p for name, p in config.providers.items() if p.enabled}
    if args.target not in provider_configs:
        print(f"✗ target provider {args.target!r} not enabled. Available: {sorted(provider_configs)}")
        raise SystemExit(2)

    def _progress(idx, total, item_run):
        pad_axis = item_run.rejection_type.ljust(11)
        status = "✗" if item_run.target_error else "→"
        print(f"  [{idx}/{total}] {status} {pad_axis} {item_run.elapsed_seconds:5.1f}s")

    print(f"Running eval {eval_id} against {args.target}...")
    run_result = run_eval(
        eval_set,
        args.target,
        provider_configs,
        limit=args.limit,
        progress_callback=_progress,
    )

    if not args.skip_score:
        # Judge priority: explicit --judge > the MEASURED most-aligned judge (the
        # eval-judge-check report, validated against the user's own corrections) >
        # the default non-target heuristic. Trust-first selection.
        alignment = _load_alignment_report()
        aligned_judge = _alignment_chosen_judge(args.target, provider_configs, alignment)
        judge = args.judge or aligned_judge or _default_judge_provider(args.target, provider_configs)
        if judge is None:
            print("✗ no judge provider available (need a second enabled provider, or pass --judge).")
            raise SystemExit(2)
        if judge == args.target:
            print(f"⚠  judge ({judge}) is the same as target ({args.target}) — bias-trap warning.")
        elif judge == aligned_judge and not args.judge:
            entry = (alignment.get("judges") or {}).get(judge) or {} if alignment else {}
            agr = entry.get("agreement")
            if agr is not None:
                print(f"  Judge {judge} picked by measured alignment — agrees with your "
                      f"corrections {agr*100:.0f}% (n={entry.get('n_parsed')}).")
        from ..state_paths import lens_path
        lens_md = lens_path()
        lens_text = lens_md.read_text(encoding="utf-8") if lens_md.exists() else ""
        print(f"Scoring with judge={judge}...")
        score_run(run_result, lens_text, judge, provider_configs,
                  progress_callback=lambda i, t, _: print(f"  judged {i}/{t}"))
        _record_judge_alignment(run_result, judge, alignment)

    path = save_run_result(run_result)

    print()
    print(f"  Eval run complete: {run_result.items_completed}/{run_result.items_total} dispatched, "
          f"{run_result.items_failed} failed")
    if run_result.aggregate_score is not None:
        if getattr(run_result, "self_judge", False):
            # self-judge = judge is the same provider family as the target.
            # Measured 2026-06-09 (self-preference experiment, n=30 paired deltas):
            # judges are NOT self-preferential — own-minus-cross was −0.19 for the
            # claude family (self-CRITICAL) and +0.02 for antigravity. So a self-judge
            # run is NOT penalized; we note the same-family relationship for
            # transparency (it can still look like a conflict of interest externally)
            # but the score is a valid ranking like any other judge's.
            print(f"  Aggregate score:  {run_result.aggregate_score:.3f}  "
                  f"({args.target} vs rejected_responses; self-judge — same family as target, "
                  f"measured non-self-preferential)")
        else:
            print(f"  Aggregate score:  {run_result.aggregate_score:.3f}  ({args.target} vs rejected_responses)")
        if run_result.by_rejection_type:
            from ..evals.scorer import AXIS_ONELINER
            print("  By rejection axis (what the user wanted that the rejected response missed):")
            for axis, stats in sorted(run_result.by_rejection_type.items()):
                hint = AXIS_ONELINER.get(axis, "")
                print(f"    {axis:<12} n={stats['count']:>3}  mean={stats['mean_score']:.3f}  "
                      f"(min {stats['min_score']:.2f} max {stats['max_score']:.2f})"
                      + (f"  — {hint}" if hint else ""))
    elif getattr(run_result, "scoring_degraded", False) and not args.skip_score:
        # #246 nulled the aggregate because the JUDGE returned empty/unparseable
        # on most items — the live cause is a rate-limited judge (codex "you've
        # hit your usage limit") or a non-chat judge (MLX/Ollama). Without this
        # the user sees "N/N dispatched, 0 failed" and no score, with zero clue
        # why or what to do. Surface the dominant reason + the one-line fix:
        # re-run with a judge that isn't the one that just failed.
        from collections import Counter
        judge_used = (
            next((it.judge_provider for it in run_result.items
                  if getattr(it, "judge_provider", None)), None)
            or getattr(args, "judge", None)
            or "the judge"
        )
        reasons = Counter(
            (it.score_reason or "").split(":", 1)[0].strip()
            for it in run_result.items
            if it.score is not None and (it.score_reason or "")
        )
        print(
            f"  ⚠ No benchmark score: the judge ({judge_used}) returned an "
            f"empty/unparseable verdict on most items, so the fabricated 0.5 "
            f"fallback was suppressed (a fake score is worse than none)."
        )
        if reasons:
            reason_text, n = reasons.most_common(1)[0]
            print(f"    Dominant reason: {reason_text!r} ({n}/{run_result.items_completed} items).")
            if "usage limit" in reason_text.lower() or "empty output" in reason_text.lower():
                print("    Likely a rate-limited or non-chat judge — the target "
                      "responses ARE on disk; only the grading step failed.")
        alt = _suggest_alt_judge(judge_used, args.target, provider_configs)
        if alt:
            print(
                "    → Re-grade with a different judge (no target re-dispatch needed if "
                "you keep the same set):\n"
                f"        trinity-local eval-run --target {args.target} --judge {alt} --eval-id {eval_id}"
            )
    print(f"\n  → {path}")

    # #218 celebration: if this run scored the provider's CURRENT canonical
    # model (per the manifest), it just closed a new-model loop — front-door
    # the shareable eval-card, the viral lab-impossible artifact. Always offer
    # the share command; lead with 🎉 when it's the current model.
    if run_result.aggregate_score is not None:
        is_current = False
        try:
            from ..models import current_models
            cur = (current_models().get(args.target) or {}).get("model")
            is_current = bool(cur) and cur == run_result.target_model
        except Exception:
            is_current = False
        lead = "  🎉 You scored the latest model! " if is_current else "  "
        print(
            f"\n{lead}Share it — a number no lab can produce:\n"
            f"    trinity-local eval-share --target {args.target}"
        )


def _latest_result_path(target: str | None, eval_id: str | None):
    """Find the most-recent result file under ~/.trinity/evals/results/,
    optionally filtered by target and/or eval_id. Returns None if none.

    Prefers the most-recent run that actually produced a score
    (aggregate_score not null) — a degenerate/placeholder run (null score)
    with a newer mtime must NOT mask real scored results, or eval-share
    renders the cold "run eval-run" CTA while real scores sit on disk
    (live 2026-05-31: the share card showed the empty state while the
    launchpad leaderboard had claude 0.82 / codex 0.78). Same rule the
    launchpad headline uses (launchpad_data._eval_summary). Falls back to
    the most-recent file when no scored run exists.

    Filename shape (from runner.result_path()):
      eval_<eval_id>__model_<target>__<ts>.json
    """
    import json
    from ..evals.builder import results_dir

    candidates = list(results_dir().glob("eval_*__model_*.json"))
    if not candidates:
        return None
    if target:
        candidates = [p for p in candidates if f"__model_{target}__" in p.name]
    if eval_id:
        candidates = [p for p in candidates if p.name.startswith(f"eval_{eval_id}__")]
    if not candidates:
        return None
    # (-mtime, stem) is a TOTAL order: two same-second result files (e.g. a
    # provider re-run within one second, or copied files) would otherwise keep
    # the unsorted glob order on the mtime tie, so WHICH run counts as the
    # "latest scored result" — and therefore the score eval-share renders —
    # would flip purely on filesystem glob order. Matches the launchpad canon
    # (launchpad_data._compute_eval_summary) so the CLI + launchpad agree on a
    # provider's latest run.
    candidates.sort(key=lambda p: (-p.stat().st_mtime, p.stem))
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("aggregate_score") is not None:
            return path
    return candidates[0]


def _collect_leaderboard_rows(eval_id: str | None) -> tuple[list[dict], set[str]]:
    """Return (rows-sorted-desc, eval_ids_seen).

    Shared by eval-show --compare and eval-share --compare; same per-target
    dedup policy the launchpad uses (launchpad_data._compute_eval_summary).
    Returns ([], set()) when no candidates match.
    """
    import json
    from ..evals.builder import results_dir
    from ..utils import finite_float_or_none

    candidates = list(results_dir().glob("eval_*__model_*.json"))
    if eval_id:
        candidates = [p for p in candidates if p.name.startswith(f"eval_{eval_id}__")]
    if not candidates:
        return [], set()
    # (-mtime, stem) total order so the per-target dedup below (newest run wins
    # the merged leaderboard slot) is deterministic when two of a provider's
    # runs share an st_mtime — otherwise the leaderboard WINNER + per-target
    # score flip on glob order. Matches the launchpad leaderboard canon (#292,
    # launchpad_data._compute_eval_summary), which this function mirrors.
    candidates.sort(key=lambda p: (-p.stat().st_mtime, p.stem))

    by_target: dict[str, dict] = {}
    eval_ids_seen: set[str] = set()
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
        except (OSError, json.JSONDecodeError):
            continue
        target = data.get("target_provider")
        # Shape-guard the provider slug at the read boundary (#258/#304 corrupt-
        # state vein, the iter-455 me-card sibling): a provider slug is a string.
        # A hand-edited / half-migrated eval result whose `target_provider` is a
        # wrong TYPE — a dict/list (UNHASHABLE) — slips past the `if not target`
        # falsy gate (truthy) and past normalize_provider_slug (non-str passes
        # through), then `if target in by_target` / `by_target[target] = ...` →
        # `TypeError: unhashable type: 'dict'`, crashing `eval-show --compare` /
        # `eval-share --compare` with a raw traceback. `if not target` already
        # drops None/empty/0; this also drops a non-str slug entirely (a row with
        # no usable provider identity is not leaderboard material).
        if not target or not isinstance(target, str):
            continue
        # Fold web-era capture slugs (gemini/chatgpt/claude_ai) to the CLI
        # dispatch slug BEFORE the per-target dedup — else the same provider's
        # runs under two slugs (e.g. a `gemini` run and an `antigravity` run)
        # render as two leaderboard rows for one provider. Symmetric to the
        # launchpad leaderboard canon (#292); candidates are mtime-desc so the
        # newest run wins the merged slot.
        from ..council_schema import normalize_provider_slug

        target = normalize_provider_slug(target) or target
        if target in by_target:
            continue
        # Skip degenerate/placeholder runs (null aggregate_score) so a fresh
        # placeholder doesn't mask a target's real scored run — the per-target
        # dedup takes the newest file, and a null-score run with a fresh mtime
        # would otherwise win the slot (live 2026-05-31: claude's real 0.82 and
        # gemini's score were masked by 3-item null placeholders; the leaderboard
        # showed them as "—"). A target whose ONLY result is degenerate gets no
        # row — same rule as the launchpad leaderboard (launchpad_data
        # ._eval_summary), which this function's docstring promises to mirror.
        if data.get("aggregate_score") is None:
            continue
        items = data.get("items") or []
        judge = None
        for item in items:
            if isinstance(item, dict) and item.get("judge_provider"):
                judge = item["judge_provider"]
                break
        eid = data.get("eval_id")
        if eid:
            eval_ids_seen.add(eid)
        # Per-axis means for the --by-axis matrix view. Keep as nested
        # dict so a caller doing aggregate-only work pays no parse cost.
        # by_axis_n stores per-axis sample counts so leader-suppression
        # can refuse to declare a winner on noise (n < MIN_AXIS_SAMPLES).
        by_axis = {}
        by_axis_n = {}
        # Shape-guard (guard_shape_not_just_parse / #304): a valid-JSON-but-wrong-
        # type `by_rejection_type` (a LIST/STRING where the per-axis map is
        # expected — schema drift / hand-edit) is truthy, so the bare `or {}`
        # passes it to `.items()` → `AttributeError: 'list' object has no
        # attribute 'items'` crashing `eval-show --compare` with a raw traceback.
        # Mirrors the launchpad leaderboard guard (launchpad_data._eval_summary) —
        # same data, same coercion, can't drift. Coerce non-dict to {}.
        _bt_raw = data.get("by_rejection_type")
        for axis_name, stats in (_bt_raw if isinstance(_bt_raw, dict) else {}).items():
            if isinstance(stats, dict) and "mean_score" in stats:
                # Shape-guard the per-axis numerics (#304): the CLI sibling of the
                # launchpad `_eval_summary` leaderboard loop. A non-numeric
                # `mean_score` / `count` in ONE corrupt eval result crashed
                # `eval-show --compare` / `eval-share --compare` with a raw
                # traceback (`float("abc")` / `int(NaN)` → ValueError). Mirror the
                # launchpad's shared coercion so the two surfaces can't drift; a
                # poisoned axis degrades to 0.0/0 instead of taking the command down.
                mean = finite_float_or_none(stats.get("mean_score"))
                if mean is None:
                    continue
                by_axis[axis_name] = mean
                by_axis_n[axis_name] = int(finite_float_or_none(stats.get("count")) or 0)
        by_target[target] = {
            "target": target,
            "model": data.get("target_model"),
            # Coerce a NaN/Inf/non-numeric aggregate to None (#304): a corrupt
            # result's aggregate would otherwise paint "scored nan" on the public
            # eval-share --compare PNG and print "nan" in the eval-show terminal
            # table. None falls back to the honest "—" / empty-state everywhere.
            "aggregate_score": finite_float_or_none(data.get("aggregate_score")),
            "items_completed": data.get("items_completed", 0),
            "items_total": data.get("items_total", 0),
            "items_failed": data.get("items_failed", 0),
            "judge": judge,
            "eval_id": eid,
            "ran_at": data.get("completed_at") or data.get("started_at"),
            "by_axis": by_axis,
            "by_axis_n": by_axis_n,
        }
    rows = sorted(
        by_target.values(),
        key=lambda r: r.get("aggregate_score") if r.get("aggregate_score") is not None else -1.0,
        reverse=True,
    )
    return rows, eval_ids_seen


def _print_exclusion_note(rows: list[dict]) -> None:
    """Disclose per-provider items dropped from the aggregate.

    The aggregate is a mean over COMPLETED items only — dispatch failures and
    timeouts are excluded, not scored 0. So a provider that timed out on its
    hardest/slowest items isn't penalized for them, and providers often fail
    DIFFERENT items, so two aggregates can span slightly different subsets of
    the eval set. The single-provider detail view already prints
    "N/M dispatched, K failed"; the leaderboard — the surface that RANKS
    providers head-to-head — must match that honesty bar, else the ranking
    reads as more apples-to-apples than the data warrants (live 2026-05-31:
    claude 0.818 over 39/42 vs antigravity 0.496 over 41/42; claude's 3
    excluded timeouts are a 0.059 swing, larger than its 0.040 lead).
    """
    excluded = [
        (r["target"], int(r.get("items_failed") or 0))
        for r in rows
        if int(r.get("items_failed") or 0) > 0
    ]
    if not excluded:
        return
    parts = ", ".join(f"{t}: {n}" for t, n in excluded)
    print(f"  ⚠ excluded from aggregate (timed out / dispatch error, not scored): {parts}.")
    print("    Means are over COMPLETED items only; providers may fail different items.")


def _handle_eval_compare(args):
    """Cross-provider leaderboard. CLI parity with the launchpad's
    evalSummary.comparison view: one row per target_provider, sorted by
    aggregate_score desc. When --eval-id is set, filter to that eval
    set so the columns are commensurate (different eval sets mean
    different items, so unfiltered comparison is suggestive only —
    surface a warning).
    """
    rows, eval_ids_seen = _collect_leaderboard_rows(args.eval_id)
    if not rows:
        msg = "  No eval results found on disk."
        if args.eval_id:
            msg += f" Filter: eval_id={args.eval_id!r}."
        msg += " Run `trinity-local eval-run --target <provider>` to produce one."
        print(msg)
        raise SystemExit(1)

    by_axis_mode = bool(getattr(args, "by_axis", False))

    print("  Cross-provider leaderboard · YOUR corpus" + ("  ·  per-axis matrix" if by_axis_mode else ""))
    if len(eval_ids_seen) > 1 and not args.eval_id:
        print(
            f"  ⚠ rows span {len(eval_ids_seen)} different eval sets — scores are NOT "
            "directly comparable. Pass --eval-id <id> to scope to one."
        )
    elif len(eval_ids_seen) == 1:
        print(f"  eval set: {next(iter(eval_ids_seen))}")
    print()

    if by_axis_mode:
        # Build the axes column list from union of all rows' axes, in a
        # stable order (alphabetical) so the header matches the data rows.
        axes_seen: set[str] = set()
        for row in rows:
            axes_seen.update((row.get("by_axis") or {}).keys())
        axes_ordered = sorted(axes_seen)
        if not axes_ordered:
            print("  (no per-axis breakdown available — runs predate by_rejection_type)")
            print("  Re-run with `trinity-local eval-run --target <provider>` to populate.")
            return None

        # Header
        axis_cols = "  ".join(f"{a[:11]:>11}" for a in axes_ordered)
        print(f"    {'target':<14} {'n':<5} {'agg':>6}  {axis_cols}")
        for row in rows:
            agg = row.get("aggregate_score")
            agg_str = f"{agg:.2f}" if agg is not None else "—"
            row_axes = row.get("by_axis") or {}
            axis_vals = "  ".join(
                f"{row_axes[a]:>11.3f}" if a in row_axes else f"{'—':>11}"
                for a in axes_ordered
            )
            print(
                f"    {row['target']:<14} {row['items_completed']:<5} {agg_str:>6}  {axis_vals}"
            )

        # Per-axis leader callouts — names the wedge claim ("X is best
        # for kind-of-question Y") in publishable form.
        #
        # SUPPRESSED in two cases:
        # 1. Mixed eval sets (commit 02f354d) — scores aren't comparable.
        # 2. Any contender on the axis has n < 3 — sample too small to
        #    declare a winner. Live trigger: COMPRESSION had n=2 per
        #    provider, mean spreads of 0.7 between providers, but n=2
        #    is noise. Better to surface no claim than a wrong one.
        # Matrix bars stay — per-provider scores are meaningful per se,
        # only the head-to-head SYNTHESIS gets suppressed.
        mixed = len(eval_ids_seen) > 1 and not args.eval_id
        # ONE source of truth shared with the launchpad chips + eval-share PNG
        # (evals.composition_floor.MIN_AXIS_LEADER_N) — import, don't re-hardcode.
        from ..evals.composition_floor import (
            MIN_AXIS_LEADER_CONTENDERS,
            MIN_AXIS_LEADER_N as MIN_AXIS_SAMPLES,
            TIE_DP_AXIS,
            distinct_target_count,
            scores_tied,
        )
        # A per-axis "leader" needs a CONTEST — with one provider on disk the lone
        # row "leads" every axis because nobody else ran (the council-card solo-
        # overclaim shape, #35). The PNG matrix card already demotes this; this
        # text path was the asymmetric sibling that printed "Per-axis leader:
        # REFRAME → claude" off a single-provider eval. Suppress the head-to-head
        # callout below the contender floor; the per-axis matrix table stays (each
        # provider's own scores are meaningful per se).
        enough_contenders = distinct_target_count(rows) >= MIN_AXIS_LEADER_CONTENDERS
        if not mixed and enough_contenders:
            print()
            leader_lines = []
            for axis in axes_ordered:
                scored = [
                    (r["target"], r["by_axis"][axis], (r.get("by_axis_n") or {}).get(axis, 0))
                    for r in rows
                    if axis in (r.get("by_axis") or {})
                ]
                if not scored:
                    continue
                # Sample-size guard
                if any(n < MIN_AXIS_SAMPLES for _, _, n in scored):
                    continue
                # TIE DEMOTION (#35): when the top-two axis scorers round equal at
                # the displayed 2dp, "X is best at REFRAME" names a winner of a
                # tied axis (the slug tie-break below would pick a deterministic
                # but ARBITRARY name). Suppress the leader callout for that axis —
                # the matrix table still shows each provider's own per-axis score.
                top2 = sorted((s for _, s, _ in scored), reverse=True)[:2]
                if len(top2) >= 2 and scores_tied(top2[0], top2[1], dp=TIE_DP_AXIS):
                    continue
                # Tie-break on the slug so the named leader is deterministic on
                # an axis-score tie (mirrors the launchpad wedge chip canon). max
                # score, lexically-smallest slug.
                leader_target, leader_score, _ = min(scored, key=lambda kv: (-kv[1], kv[0]))
                leader_lines.append(f"{axis} → {leader_target} ({leader_score:.2f})")
            if leader_lines:
                print("  Per-axis leader:  " + "  |  ".join(leader_lines))
        print()
        _print_exclusion_note(rows)
        return None

    print(f"    {'rank':<5} {'target':<14} {'n':<5} {'aggregate':>10}   {'judge':<14} {'ran'}")
    for i, row in enumerate(rows, 1):
        agg = row.get("aggregate_score")
        agg_str = f"{agg:.3f}" if agg is not None else "—"
        judge = row.get("judge") or "—"
        ran = (row.get("ran_at") or "")[:19]  # YYYY-MM-DDThh:mm:ss
        print(
            f"    {i:<5} {row['target']:<14} {row['items_completed']:<5} {agg_str:>10}"
            f"   {judge:<14} {ran}"
        )
    print()
    _print_exclusion_note(rows)
    # Suppress the "X leads Y by ±Z" head-to-head when rows span
    # different eval sets — same consistency rule shipped to the
    # per-axis leader synthesis (commits 83b9e99, 02f354d). The
    # warning above the table already says scores aren't comparable;
    # a leader-margin line that subtracts them anyway contradicts it.
    mixed = len(eval_ids_seen) > 1 and not args.eval_id
    if not mixed and len(rows) >= 2:
        from ..evals.composition_floor import top_two_tied
        leader, runner_up = rows[0], rows[1]
        leader_agg = leader.get("aggregate_score")
        runner_agg = runner_up.get("aggregate_score")
        if leader_agg is not None and runner_agg is not None:
            # TIE DEMOTION (#35 green-while-degenerate): when the top two scores
            # round equal at the displayed 3dp, "X leads Y by +0.000" names a
            # winner of a contest that ended TIED — a false leader on the surface
            # that RANKS providers. Mirror the routing cheat-sheet's "tied" shape:
            # state the tie honestly instead of a fabricated +0.000 lead.
            if top_two_tied(rows):
                print(
                    f"  {leader['target']} and {runner_up['target']} are tied "
                    f"at {leader_agg:.3f} on YOUR rejection signal — no clear leader."
                )
            else:
                print(
                    f"  {leader['target']} leads {runner_up['target']} "
                    f"by {leader_agg - runner_agg:+.3f} on YOUR rejection signal."
                )
    return None


def handle_eval_show(args):
    from ..evals.runner import load_run_result

    # --by-axis is meaningful only inside the leaderboard view (axis
    # × provider matrix). Without --compare it has no row dimension.
    if getattr(args, "by_axis", False) and not getattr(args, "compare", False):
        print(
            "  --by-axis only applies to the leaderboard view. Pass "
            "--compare --by-axis to render the axis × provider matrix.",
        )
        raise SystemExit(2)

    # --compare: flip to leaderboard view. Mirrors the launchpad's
    # cross-provider comparison (launchpad_data.py:_compute_eval_summary
    # builds the same shape). Different return path because --compare
    # aggregates across targets while the default view drills into one.
    if getattr(args, "compare", False):
        return _handle_eval_compare(args)

    path = _latest_result_path(args.target, args.eval_id)
    if path is None:
        msg = "No eval results found on disk."
        if args.target or args.eval_id:
            msg += " Filters: "
            if args.target:
                msg += f"target={args.target!r} "
            if args.eval_id:
                msg += f"eval_id={args.eval_id!r}"
            msg += " — try without filters or run `trinity-local eval-run --target <provider>` first."
        else:
            msg += " Run `trinity-local eval-run --target <provider>` to produce one."
        print(f"  {msg}")
        raise SystemExit(1)

    result = load_run_result(path)
    if result is None:
        print(f"✗ result at {path} unreadable")
        raise SystemExit(2)

    # Header: which eval, which model, when
    print(f"  {result.eval_id}  →  {result.target_provider}"
          f"{f' ({result.target_model})' if result.target_model else ''}")
    print(f"  ran {result.started_at} → {result.completed_at}")
    print(f"  {result.items_completed}/{result.items_total} dispatched"
          f"{f', {result.items_failed} failed' if result.items_failed else ''}")

    if result.aggregate_score is not None:
        print()
        print(f"  Aggregate score: {result.aggregate_score:.3f}  "
              f"(vs the rejected_responses the original prompts elicited)")
        if result.by_rejection_type:
            from ..evals.scorer import AXIS_ONELINER
            print("\n  By rejection axis (what the user wanted that the rejected response missed):")
            for axis, stats in sorted(result.by_rejection_type.items()):
                # Visual bar — 25-char max width, scaled by mean_score
                width = int(round(stats["mean_score"] * 25))
                bar = "█" * width + "·" * (25 - width)
                hint = AXIS_ONELINER.get(axis, "")
                print(f"    {axis:<12} n={stats['count']:>3}  "
                      f"mean={stats['mean_score']:.3f}  [{bar}]  "
                      f"min {stats['min_score']:.2f} max {stats['max_score']:.2f}")
                if hint:
                    print(f"                 — {hint}")
    else:
        print("\n  (No aggregate score — run completed without --no-score, or scoring failed.)")

    if args.limit_samples > 0 and result.items:
        # Show a few items with the strongest signal: extremes are
        # informative — the top + bottom score tell the user where the
        # model wins and where it loses on their corpus.
        scored = [it for it in result.items if it.score is not None]
        if scored:
            # Score DESC, eval_item_id ASC as a stable tie-break so the top-N /
            # bottom-N sample slices are a TOTAL order: items tied on score
            # straddling either cut would otherwise keep result.items order, so
            # which samples print would flip on that order.
            scored.sort(key=lambda it: (-(it.score or 0.0), it.eval_item_id))
            best = scored[:args.limit_samples]
            worst = scored[-args.limit_samples:] if len(scored) > args.limit_samples else []
            print(f"\n  Top {len(best)} scored items:")
            for it in best:
                _print_sample_line(it)
            if worst:
                print(f"\n  Bottom {len(worst)} scored items:")
                for it in worst:
                    _print_sample_line(it)
        else:
            print("\n  (No scored items to sample.)")

    print(f"\n  → {path}")


def _print_sample_line(item):
    """Render a single scored item compactly. Used by eval-show sample list."""
    score = f"{item.score:.2f}" if item.score is not None else "—  "
    prompt_preview = (item.prompt or "")[:70].replace("\n", " ")
    if len(item.prompt or "") > 70:
        prompt_preview += "…"
    print(f"    [{item.rejection_type:<11}] {score}  {prompt_preview}")


def _open_if_requested(open_after: bool, path) -> bool:
    """Best-effort `open` for macOS / Linux. Never raises — the PNG is
    already on disk; opening the viewer is a convenience, not a contract."""
    if not open_after:
        return False
    try:
        import subprocess
        import sys
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
            return True
        if sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(path)], check=False)
            return True
    except OSError:
        return False
    return False


def handle_eval_share(args):
    """Render the latest (or filtered) eval run result as a 1200×630
    PNG share card. The artifact the user's pitch produces — "I ran my
    evals on Gemini, here's where it landed."

    Defaults to ~/.trinity/share/eval_card.png to match the me-card
    convention. Prints a small JSON summary to stdout for scriptability.
    """
    from pathlib import Path
    from ..evals.runner import load_run_result
    from ..eval_card import (
        CompareCardData,
        collect_card_data_from_result,
        render_compare_card,
        render_compare_matrix_card,
        render_eval_card,
    )
    from ..state_paths import share_dir

    # --by-axis without --compare doesn't make sense for the share card
    # either (no rows to break out by axis from a single-provider view).
    if getattr(args, "by_axis", False) and not getattr(args, "compare", False):
        print(
            "  --by-axis only applies to --compare. Pass --compare "
            "--by-axis to render the per-axis matrix PNG.",
        )
        raise SystemExit(2)

    # --compare: cross-provider leaderboard card. Different shape, same
    # canvas. The wedge artifact for #116 ("Trinity scored Claude,
    # Codex, and Gemini against my taste").
    if getattr(args, "compare", False):
        rows, eval_ids_seen = _collect_leaderboard_rows(args.eval_id)
        if not rows:
            msg = "  No eval results found on disk."
            if args.eval_id:
                msg += f" Filter: eval_id={args.eval_id!r}."
            msg += " Run `trinity-local eval-run --target <provider>` to produce one."
            print(msg)
            raise SystemExit(1)
        compare_data = CompareCardData(
            rows=rows,
            eval_id=args.eval_id if args.eval_id else (next(iter(eval_ids_seen)) if len(eval_ids_seen) == 1 else None),
            mixed_eval_sets=len(eval_ids_seen) > 1 and not args.eval_id,
        )
        by_axis_mode = bool(getattr(args, "by_axis", False))
        if by_axis_mode:
            png_bytes = render_compare_matrix_card(compare_data)
            default_filename = "eval_compare_matrix_card.png"
        else:
            png_bytes = render_compare_card(compare_data)
            default_filename = "eval_compare_card.png"
        out = Path(args.out) if args.out else (share_dir() / default_filename)
        out.write_bytes(png_bytes)
        opened = _open_if_requested(args.open_after, out)
        # Per-axis leader summary — useful in the JSON output for
        # scripted callers that want the wedge string.
        # Suppressed in THREE cases: mixed_eval_sets; any contender on
        # the axis has n < MIN_AXIS_SAMPLES (sample too small); OR fewer
        # than MIN_AXIS_LEADER_CONTENDERS distinct providers (a "leader"
        # with one contender is the solo-overclaim shape #35 — the lone
        # provider "leads" because nobody else ran). Same rules as the
        # launchpad + eval-show surfaces + the PNG matrix card.
        per_axis_leader: dict[str, dict] = {}
        # Same shared floor as the launchpad chips + the eval-show leader lines
        # (evals.composition_floor.MIN_AXIS_LEADER_N) — import, don't re-hardcode.
        from ..evals.composition_floor import (
            MIN_AXIS_LEADER_CONTENDERS,
            MIN_AXIS_LEADER_N as MIN_AXIS_SAMPLES,
            TIE_DP_AXIS,
            distinct_target_count,
            scores_tied,
        )
        enough_contenders = distinct_target_count(rows) >= MIN_AXIS_LEADER_CONTENDERS
        if by_axis_mode and not compare_data.mixed_eval_sets and enough_contenders:
            axes_seen: set[str] = set()
            for row in rows:
                axes_seen.update((row.get("by_axis") or {}).keys())
            for axis in sorted(axes_seen):
                scored = [
                    (r["target"], r["by_axis"][axis], (r.get("by_axis_n") or {}).get(axis, 0))
                    for r in rows
                    if axis in (r.get("by_axis") or {})
                ]
                if not scored or any(n < MIN_AXIS_SAMPLES for _, _, n in scored):
                    continue
                # TIE DEMOTION (#35): a per-axis "leader" whose top-two scorers
                # round equal at 2dp is the winner of a TIED axis — don't emit it
                # into the scripted-caller JSON (it would re-render the same false
                # "X is best at REFRAME" chip a consumer trusts). Same gate as the
                # eval-show text + launchpad chip + matrix PNG paths.
                top2 = sorted((s for _, s, _ in scored), reverse=True)[:2]
                if len(top2) >= 2 and scores_tied(top2[0], top2[1], dp=TIE_DP_AXIS):
                    continue
                # Slug tie-break so the per-axis leader emitted into the JSON is
                # deterministic on an axis-score tie (mirrors the launchpad wedge
                # chip + eval-show leader-line canon). max score, smallest slug.
                leader_target, leader_score, _ = min(scored, key=lambda kv: (-kv[1], kv[0]))
                per_axis_leader[axis] = {"target": leader_target, "score": leader_score}
        summary = {
            "ok": True,
            "mode": "compare-by-axis" if by_axis_mode else "compare",
            "path": str(out),
            "bytes": len(png_bytes),
            "eval_id": compare_data.eval_id,
            "mixed_eval_sets": compare_data.mixed_eval_sets,
            "rows": [
                {
                    "target": r["target"],
                    "aggregate_score": r["aggregate_score"],
                    "items_completed": r["items_completed"],
                    "judge": r["judge"],
                    **({"by_axis": r["by_axis"]} if by_axis_mode and r.get("by_axis") else {}),
                }
                for r in rows
            ],
            **({"per_axis_leader": per_axis_leader} if by_axis_mode else {}),
            "opened": opened,
        }
        print(json.dumps(summary, indent=2))
        return None

    path = _latest_result_path(args.target, args.eval_id)
    if path is None:
        msg = "No eval results found on disk."
        if args.target or args.eval_id:
            msg += " Try without --target/--eval-id, or run `trinity-local eval-run --target <provider>` first."
        else:
            msg += " Run `trinity-local eval-run --target <provider>` to produce one."
        print(f"  {msg}")
        raise SystemExit(1)

    result = load_run_result(path)
    if result is None:
        print(f"✗ result at {path} unreadable")
        raise SystemExit(2)

    card_data = collect_card_data_from_result(result)
    png_bytes = render_eval_card(card_data)

    out = Path(args.out) if args.out else (share_dir() / "eval_card.png")
    out.write_bytes(png_bytes)

    opened = _open_if_requested(args.open_after, out)

    summary = {
        "ok": True,
        "path": str(out),
        "bytes": len(png_bytes),
        "target_provider": card_data.target_provider,
        "target_model": card_data.target_model,
        "aggregate_score": card_data.aggregate_score,
        "items_completed": card_data.items_completed,
        "axes": [a for a, _, _ in card_data.by_axis],
        "opened": opened,
    }
    print(json.dumps(summary, indent=2))


def handle_eval_selfpref(args):
    """`eval-selfpref` — validate that judges do NOT inflate their own model family.

    The cross-provider-eval trust check: if a same-lab judge favored its own model,
    the whole comparison would be rigged. Paired within-response design (every judge
    scores the IDENTICAL response text → item difficulty cancels). Validated
    2026-06-09: judges are non-self-preferential (claude −0.19 self-critical,
    antigravity +0.02). Re-run per model change; persists a scores-only record."""
    import json as _json

    from ..config import load_config
    from ..state_paths import lens_path
    from ..evals.self_preference import (
        VERDICT_NO_PREFERENCE,
        VERDICT_SELF_PREFERENCE,
        run_self_preference,
        save_self_preference_record,
    )

    config = load_config(getattr(args, "config", None), required=True)
    provider_configs = {name: p for name, p in config.providers.items() if p.enabled}
    judges = [j for j in ("claude", "codex", "antigravity") if j in provider_configs]
    if len(judges) < 2:
        print("✗ self-preference needs ≥2 enabled chairman-grade judges (claude / codex / antigravity); "
              f"have {sorted(provider_configs)}.")
        return 1

    lens_md = lens_path()
    lens_text = lens_md.read_text(encoding="utf-8") if lens_md.exists() else ""

    print(f"Validating judge self-preference — judges={judges}, n={args.n} per family.")
    print("(paired within-response: each judge scores the IDENTICAL saved response → item difficulty cancels;")
    print(" statistic = own-family-judge minus cross-family-mean; >0 ⇒ a judge inflates its own kin)")

    def _progress(fam, judge, n):
        print(f"  scored {fam} × {judge}  ({n} responses)")

    result = run_self_preference(
        provider_configs, lens_text, judges=judges, n_per_family=args.n, progress=_progress,
    )

    # A model is "validated" when its family's self-cell was computable this run.
    validated_models = []
    for d in result.family_deltas:
        if d.computable:
            pc = config.providers.get(d.family)
            if pc and getattr(pc, "model", None):
                validated_models.append(pc.model)
    save_self_preference_record(result, validated_models)

    if args.json:
        print(_json.dumps(result.to_dict(), indent=2))
        return 0

    # ── human-readable matrix + deltas + verdict ──
    print("\n  Judge health (>20% dispatch-fail drops a column):")
    for h in result.judge_health:
        flag = "  ← DROPPED" if not h.healthy else ""
        print(f"    {h.judge:<12} fail {h.n_failed}/{h.n_total} = {h.fail_rate:.0%}{flag}")
    if result.partial:
        print(f"  PARTIAL: dropped {result.dropped_judges} (their families' self-cell is untested this run).")

    if result.matrix and result.healthy_judges:
        print("\n  Mean score — rows=target family, cols=healthy judge:")
        hdr = "    " + "family".ljust(14) + "".join(j.ljust(14) for j in result.healthy_judges)
        print(hdr)
        for fam, cols in sorted(result.matrix.items()):
            cells = "".join((f"{cols[j]:.3f}".ljust(14) if j in cols else "—".ljust(14))
                            for j in result.healthy_judges)
            print("    " + fam.ljust(14) + cells)

    print("\n  Self-preference (own-family judge minus cross-family mean; <0 = harsher on own):")
    for d in result.family_deltas:
        if not d.computable:
            print(f"    {d.family:<12} —  ({d.reason})")
            continue
        tag = ("inflates own family" if d.delta and d.delta > 0.05
               else ("harsher on own" if d.delta and d.delta < -0.05 else "no preference"))
        print(f"    {d.family:<12} Δ={d.delta:+.3f}  (n={d.n}, +{d.n_positive}/{d.n}, se={d.se:.3f})  ← {tag}")

    if result.overall_delta is not None:
        zstr = f"{result.overall_z:+.2f}" if result.overall_z is not None else "n/a"
        print(f"\n  OVERALL Δ={result.overall_delta:+.3f}  (n={result.overall_n}, z={zstr})")

    print()
    if result.verdict == VERDICT_NO_PREFERENCE:
        extra = " — in fact self-CRITICAL" if result.self_critical else ""
        print(f"  ✓ VERDICT: no self-preference{extra}. Judges do not inflate their own family; "
              f"same-family judging is sound.")
    elif result.verdict == VERDICT_SELF_PREFERENCE:
        print("  ✗ VERDICT: SELF-PREFERENCE DETECTED — a judge inflates its own model family. "
              "Use a cross-family judge for those targets; do not trust a same-family score.")
    else:
        print("  ? VERDICT: inconclusive — need ≥2 healthy judges and saved responses for a judge's "
              "own family. Re-run when all judges dispatch cleanly (codex credit outage drops its column).")
    if result.partial:
        print("    (partial run — re-run for the full matrix once the dropped judge(s) dispatch.)")
    return 0
