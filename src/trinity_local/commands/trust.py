"""`trust` — which model you side with when the labs split, and the recurring
cross-provider disagreements you keep returning to.

ONE object answers both: the cross-provider council disagreement.

  trust <query>   the recurring disagreements a topic maps into (the
                  census-validated retrieval) + the raw council record: who
                  argued which side, the chairman's pick, how many councils split
                  on the same question.
  trust           the ledger summary — the per-model win/loss over RESOLVED
                  disagreements IF the resolver clears its trustworthiness gate
                  (K3 band + K4); otherwise the verdict is withheld and only the
                  retrieval + raw record show (green-gate: no verdict over a
                  resolver that's near noise or just parroting the chairman).
  trust --build   (re)resolve the ledger via session sampling (#263): an LLM
                  judges which branch your later work took, riding your live
                  session (no per-token bill), else falling back to `claude -p`.

Retrieval refuses loudly without a real embedder (never on the TF-IDF stub).
"""
from __future__ import annotations

import json
import sys


def register(subparsers):
    p = subparsers.add_parser(
        "trust",
        help="Which model you side with when the labs split + recurring cross-provider disagreements.",
    )
    p.add_argument("query", nargs="?",
                   help="Find the recurring cross-provider disagreements on this topic.")
    p.add_argument("--build", action="store_true",
                   help="(Re)resolve the ledger via session sampling — an LLM judges which branch your work took.")
    p.add_argument("--top-k", type=int, default=5, dest="top_k",
                   help="Max recurring disagreements to show (default 5).")
    p.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON.")
    p.add_argument("--force", action="store_true",
                   help="With --build: re-resolve EVERY claim instead of carrying settled "
                        "verdicts forward. Use when the resolver prompt or the evidence "
                        "knobs change, since carried-forward verdicts came from the OLD "
                        "instrument. Costs one model call per claim; the default "
                        "incremental build costs one per new or still-open claim.")
    p.add_argument("--silver", action="store_true",
                   help="Show the SILVER tier: chairman-adjudicated claim-side win rates "
                        "from your own live council labels (opt-in; opinion, not behaviour; "
                        "cells below SILVER_MIN_N are withheld).")
    p.add_argument("--dissent", action="store_true",
                   help="Whose DISSENT is worth hearing: per-model upheld rate in "
                        "two-sided council disputes, plus how often a model's claim was "
                        "merged even when it LOST the argument. Chairman judgement, not "
                        "behaviour — the behaviour-validated tally is the default view.")
    p.set_defaults(handler=handle_trust)


def _load_summary(home=None) -> dict:
    from ..disagreement_ledger import _ledger_dir
    path = _ledger_dir(home) / "summary.json"
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return d if isinstance(d, dict) else {}


def _print_dissent(as_json: bool) -> int:
    from ..dissent_outcome import summarize
    s = summarize()
    if as_json:
        print(json.dumps(s, indent=2))
        return 0
    src = s["sources"]
    print(f"Dissent outcomes over {s['councils_read']} councils "
          f"(production {src['production']}, re-chaired {src['rechaired']}).\n")
    if not s["councils_read"]:
        print("  No councils carry per-claim `resolution` yet — it ships from 2026-07-22,")
        print("  and a long-running MCP server keeps serving pre-restart code. Restart it,")
        print("  then run a council.")
        return 0
    # graft-when-lost is NOT printed: withdrawn 2026-07-28 as uncomputable (council-wide
    # attribution overcounts, per-claim matches 0.4% of grafts, behavioural test net +0).
    # Printing it as 0% would read as a measurement. Upheld rate is unaffected.
    print(f"  {'model':14}{'upheld':>8}  {'95% CI':<14}{'disputes':>9}")
    for m in s["models"]:
        if not m["trustworthy"]:
            print(f"  {m['model']:14}{'—':>8}  {'(under floor)':<14}{m['disputes']:>9}")
            continue
        lo, hi = m["upheld_ci"]
        print(f"  {m['model']:14}{m['upheld_rate']:>7.0%}  [{lo:.0%},{hi:.0%}]".ljust(46)
              + f"{m['disputes']:>9}")
    print(f"\n  Below {s['min_disputes']} disputes no verdict is published.")
    print(f"  {s['note']}")
    print(f"  graft-when-lost: {s['models'][0]['graft_when_lost_uncomputable']}"
          if s["models"] else "")
    return 0


def handle_trust(args):
    if getattr(args, "silver", False):
        return _print_silver(bool(getattr(args, "as_json", False)))
    if getattr(args, "dissent", False):
        return _print_dissent(bool(getattr(args, "as_json", False)))
    from ..disagreement_ledger import build_ledger, load_disagreements, retrieve_recurring
    from ..embeddings import EmbedderNotReadyError

    if getattr(args, "build", False):
        try:
            force = bool(getattr(args, "force", False))
            if force:
                # Loud on purpose: this is the expensive path and the reason it exists
                # (an instrument change) is exactly when a silent full rebuild would be
                # mistaken for the cheap one.
                print("--force: re-resolving every claim (carried-forward verdicts "
                      "discarded).", file=sys.stderr)
            agg = build_ledger(force=force)
        except EmbedderNotReadyError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(agg, indent=2) if args.as_json else _tally_lines(agg))
        return

    query = getattr(args, "query", None)
    recurring = []
    if query:
        try:
            recurring = retrieve_recurring(query, top_k=args.top_k)
        except EmbedderNotReadyError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    summary = _load_summary()
    if args.as_json:
        print(json.dumps({"query": query, "recurring": recurring, "tally": summary}, indent=2))
        return

    if query:
        if not recurring:
            print(f"No recurring cross-provider disagreement maps to {query!r}.")
        else:
            print(f"Recurring cross-provider disagreements on {query!r}:\n")
            for r in recurring:
                _print_pattern(r)
    else:
        n_xprov = sum(1 for p in load_disagreements() if p.is_cross_provider)
        print(f"{n_xprov} cross-provider disagreements in your corpus.\n")
        if summary:
            print(_tally_lines(summary))
        else:
            print("No ledger built yet. Run `trinity-local trust --build` to resolve which model "
                  "your later work sided with, or `trust <topic>` to find recurring splits.")
        # Council split on a default-output pointer was UNRESOLVED; decided here
        # (logged in amd_0065): the pointer appears ONLY once a silver cell has
        # actually cleared its floor — pointing at an all-withheld tier serves
        # nobody, and silence cannot dilute gold. Never let silver break gold.
        try:
            from ..disagreement_ledger import silver_tally

            if silver_tally().get("cells"):
                print("\n(silver available: `trust --silver` — chairman-adjudicated, "
                      "opinion tier)")
        except Exception:
            pass


def _print_silver(as_json: bool) -> None:
    from ..disagreement_ledger import silver_tally

    sv = silver_tally()
    if as_json:
        print(json.dumps(sv, indent=2))
        return
    print(sv["calibration"] + "\n")
    print(f"claims credited: {sv['claims_credited']}  skipped (unresolved/unparseable): "
          f"{sv['claims_skipped']}  councils: {sv['councils']}")
    if not sv["cells"]:
        print(f"\nAll cells WITHHELD — no model×version has reached "
              f"SILVER_MIN_N={sv['silver_min_n']} credited claims yet "
              f"({sv['withheld_cells']} accruing). This tier fills itself from every "
              "new council; nothing to run.")
        return
    print()
    for mv, c in sorted(sv["cells"].items(), key=lambda kv: -kv[1]["win_rate"]):
        n = c["w"] + c["l"]
        mark = " (CI excludes 50%)" if c["ci_excludes_half"] else ""
        print(f"  {mv:34s} {c['w']}-{c['l']}  {c['win_rate']:.0%}  n={n}{mark}")
    if sv["withheld_cells"]:
        print(f"\n({sv['withheld_cells']} further cell(s) withheld below "
              f"SILVER_MIN_N={sv['silver_min_n']})")


def _print_pattern(r: dict) -> None:
    forp = ", ".join(r.get("providers_for") or [])
    against = ", ".join(r.get("providers_against") or [])
    print(f"• {r.get('claim', '')}")
    n_split = r.get("councils_on_split", 1)
    print(f"    for: {forp}   against: {against}   chairman: {r.get('chairman_winner', '')}"
          f"   ({n_split} council{'s' if n_split != 1 else ''} split on this)")


def _tally_lines(agg: dict) -> str:
    if not agg.get("tally_trustworthy"):
        from ..disagreement_ledger import K4_MIN_RESOLVED
        resolved = int(agg.get("resolved", 0) or 0)
        why = []
        if not agg.get("k3_in_band"):
            why.append("resolver near noise/parroting")
        if not agg.get("k4_discriminates"):
            why.append("no model separates yet")
        return ("Per-model verdict withheld — " + ("; ".join(why) or "not enough resolved")
                + f" ({resolved}/{K4_MIN_RESOLVED} resolved disagreements). It accrues as you run "
                "councils; the retrieval + raw council record work now.")
    from ..disagreement_ledger import MIN_TALLY_N
    records = agg.get("records") or {}
    shown = sorted(((k, v) for k, v in records.items()
                    if (v.get("w", 0) + v.get("l", 0)) >= MIN_TALLY_N),
                   key=lambda kv: -kv[1].get("win_rate", 0))
    lines = [f"On {agg.get('resolved', 0)} resolved cross-provider disagreements, which "
             f"model your work sided with (per model x version, >= {MIN_TALLY_N} decisions):"]
    for ident, rec in shown:
        ci = rec.get("ci") or [0, 0]
        mark = "  <- clear" if rec.get("ci_excludes_half") else ""
        lines.append(f"  {ident:<24} {rec.get('win_rate', 0):.0%}  "
                     f"({rec.get('w', 0)}W {rec.get('l', 0)}L, CI [{ci[0]:.0%}, {ci[1]:.0%}]){mark}")
    omitted = len(records) - len(shown)
    if omitted:
        lines.append(f"  (+{omitted} model(s) under {MIN_TALLY_N} decisions — too thin to call.)")
    # Effort as a SECONDARY read: only sub-cells that independently clear chance
    # (ci_excludes_half) — a non-significant effort split never surfaces as a claim.
    eb = agg.get("effort_breakdown") or {}
    clear = [(mv, eff, r) for mv, effs in eb.items() if isinstance(effs, dict)
             for eff, r in effs.items() if isinstance(r, dict) and r.get("ci_excludes_half")]
    if clear:
        lines.append("  effort (only where it clears chance on its own):")
        for mv, eff, r in sorted(clear, key=lambda x: -x[2].get("win_rate", 0)):
            lines.append(f"    {mv} · {eff}  {r.get('win_rate', 0):.0%}  ({r.get('w', 0)}W {r.get('l', 0)}L)")
    return "\n".join(lines)
