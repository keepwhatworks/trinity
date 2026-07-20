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


def handle_trust(args):
    from ..disagreement_ledger import build_ledger, load_disagreements, retrieve_recurring
    from ..embeddings import EmbedderNotReadyError

    if getattr(args, "build", False):
        try:
            agg = build_ledger()
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


def _print_pattern(r: dict) -> None:
    forp = ", ".join(r.get("providers_for") or [])
    against = ", ".join(r.get("providers_against") or [])
    print(f"• {r.get('claim', '')}")
    n_split = r.get("councils_on_split", 1)
    print(f"    for: {forp}   against: {against}   chairman: {r.get('chairman_winner', '')}"
          f"   ({n_split} council{'s' if n_split != 1 else ''} split on this)")


def _tally_lines(agg: dict) -> str:
    if not agg.get("tally_trustworthy"):
        why = []
        if not agg.get("k3_in_band"):
            why.append("resolver near noise/parroting")
        if not agg.get("k4_discriminates"):
            why.append("no model separates yet")
        return ("Per-model verdict withheld — " + ("; ".join(why) or "not enough resolved")
                + f" (resolved: {agg.get('resolved', 0)}). The retrieval + raw council record still hold.")
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
    return "\n".join(lines)
