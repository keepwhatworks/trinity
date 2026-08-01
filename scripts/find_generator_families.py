#!/usr/bin/env python3
"""Surface CANDIDATE machine-generator families the ingest filter does not know.

THE PROBLEM THIS SOLVES
=======================
Trinity's purity filter (`ingest.is_user_facing_text`) is a hand-maintained list
of prefixes. It works — but every pattern in it was added because a human went
looking. The source comments date the discoveries to 2026-06-02 ("per-source
audit found each source has its OWN block dialect the aggregate top-N missed")
and 2026-07-26 (`<subagent_notification>`, 88 nodes, "the SECOND-largest
workstream in the last 90 days"). Months apart, by luck and effort.

Between those audits, a new harness dialect or agent template flows into the
lens as if the user typed it. This script turns that lucky catch into a routine
one: it ranks TEMPLATE-SHAPED clusters among the nodes the filter currently
ACCEPTS, so a human can eyeball the top of the list and add a pattern.

WHY NOT A CLASSIFIER — the thing I had to correct
=================================================
`machine_text_separability.py` measured a lexical detector at 0.987 recall on
held-out `trinity_resolver` text with no embedder, which reads like something to
wire straight into ingest. It is not. Its operating point costs a **5% human
false-positive rate**, and 5% of ~34,500 accepted nodes is roughly 1,700 GENUINE
prompts quarantined to catch a few hundred machine ones. As an inline filter
that is far worse than the disease.

What the same evidence DOES support is a human-in-the-loop audit, where a false
positive costs someone ten seconds of reading rather than a lost prompt. Run it
from a USAGE gate, not a cron: `stale_pass.py` records the 2026-06-09 founder
call — staleness marker + lock + daemon thread on a real usage event, 'never on
a timer that burns cycles while the tool sits idle'.
So this script never filters, never writes, and never decides. It ranks and
shows. The decision to add a pattern stays with a person.

METHOD
  Group filter-ACCEPTED nodes by a normalised leading-text signature (default
  120 chars — long enough that a template's boilerplate head is the key, short
  enough that per-run variation in the tail does not split one family into
  hundreds). Then rank each cluster by signals that separate a MACHINE TEMPLATE
  from a person with a habit:

    nodes            how much corpus mass it occupies
    distinct         distinct full texts sharing the signature. A template with
                     a variable body has many; a copy-pasted human phrase has few.
    source_conc      share from the single most common provider. Machine
                     templates are emitted by ONE harness; humans use several.
    span_days        first-to-last. A cron template runs for months at a steady
                     clip; a human topic burns out.
    head             the shared signature itself — what you actually read.

  No score is invented to rank these. A weighted "templateness" number would be
  a green over data nobody inspected, which is this repo's #1 bug shape. The
  signals are printed side by side and sorted by node count.

POSITIVE CONTROL (`--control`)
  Runs the identical grouping over the nodes the filter REJECTS. Those are the
  families the filter already knows, so the method MUST re-discover them at the
  top. If it does not, the grouping is wrong and nothing below it is meaningful.
  Always read the control before trusting the candidate list.

USAGE
    scripts/find_generator_families.py                 # candidates (accepted nodes)
    scripts/find_generator_families.py --control       # positive control first
    scripts/find_generator_families.py --min-nodes 40 --top 30

READ-ONLY. Opens the prompt store, writes nothing, anywhere.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import unicodedata
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from trinity_local.ingest import is_user_facing_text  # noqa: E402
from trinity_local.state_paths import prompts_dir  # noqa: E402

SIG_CHARS = 120
MIN_NODES = 20
TOP = 20


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or "")).lower()).strip()


def load(path: pathlib.Path):
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def group(rows, sig_chars: int):
    fams: dict[str, list] = defaultdict(list)
    for r in rows:
        t = norm(r.get("text"))
        if len(t) < 30:
            continue          # too short to carry a template signature
        fams[t[:sig_chars]].append(r)
    return fams


def batch_keys():
    """Families the BATCH CAP already de-weights, so the report can say so.

    Load-bearing for safety, not decoration. The loudest clusters in the
    candidate list are `/loop` cron drivers: one distinct text, hundreds of
    nodes, one source, months of span. They look exactly like machine templates
    because their REPETITION is machine — but the text is the founder's, written
    once and replayed by a cron. The filter is right to accept them and
    `cap_repeated_prompts` is what handles them (10,048 of 46,709 accepted nodes
    = 21.5% collapse to 119 unit-weight entries).

    Without this column the report would invite a reader to add a filter pattern
    for user-authored text, which deletes real prompts. A tool that recommends
    the wrong action confidently is worse than no tool.
    """
    try:
        from trinity_local.me.turn_pairs import _corpus_batch_keys, _dedup_key

        return _corpus_batch_keys(), _dedup_key
    except Exception:                                            # noqa: BLE001
        return set(), None


def describe(sig: str, rows: list, keys=frozenset(), keyfn=None) -> dict:
    texts = {norm(r.get("text")) for r in rows}
    providers: dict[str, int] = defaultdict(int)
    for r in rows:
        providers[r.get("provider") or "?"] += 1
    top_src, top_n = max(providers.items(), key=lambda kv: kv[1])
    stamps = sorted(s for s in (r.get("created_at") or r.get("timestamp") for r in rows) if s)
    span = ""
    if len(stamps) >= 2:
        try:
            from datetime import datetime

            a = datetime.fromisoformat(str(stamps[0]).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(stamps[-1]).replace("Z", "+00:00"))
            span = f"{(b - a).days}d"
        except ValueError:
            span = ""
    capped = bool(keyfn and keys and keyfn(rows[0].get("text") or "") in keys)
    return {
        "capped": capped,
        "nodes": len(rows),
        "distinct": len(texts),
        "source_conc": top_n / len(rows),
        "top_source": top_src,
        "span_days": span,
        "head": sig,
    }


def report(fams, *, min_nodes: int, top: int, title: str, keys=frozenset(), keyfn=None) -> int:
    picked = [describe(s, r, keys, keyfn) for s, r in fams.items() if len(r) >= min_nodes]
    picked.sort(key=lambda d: -d["nodes"])
    print(f"\n{title}")
    print(f"  clusters with >= {min_nodes} nodes: {len(picked)}"
          f"   (total nodes in them: {sum(d['nodes'] for d in picked)})")
    if not picked:
        print("  (none)")
        return 0
    n_capped = sum(1 for d in picked if d["capped"])
    if n_capped:
        print(f"  of these, {n_capped} are ALREADY de-weighted by the batch cap "
              "(marked 'cap' below — do NOT add a filter pattern for them)")
    print(f"\n  {'nodes':>6} {'distinct':>9} {'src%':>6} {'span':>7} {'cap':>4}  head")
    for d in picked[:top]:
        print(f"  {d['nodes']:>6} {d['distinct']:>9} {d['source_conc']*100:>5.0f}% "
              f"{d['span_days']:>7} {'cap' if d['capped'] else '':>4}  {d['head'][:82]!r}")
    if len(picked) > top:
        print(f"  ... {len(picked)-top} more (raise --top to see them)")
    return len(picked)


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--min-nodes", type=int, default=MIN_NODES)
    ap.add_argument("--top", type=int, default=TOP)
    ap.add_argument("--sig-chars", type=int, default=SIG_CHARS)
    ap.add_argument("--control", action="store_true",
                    help="also run the positive control over filter-REJECTED nodes")
    args = ap.parse_args()

    path = prompts_dir() / "prompt_nodes.jsonl"
    if not path.exists():
        print(f"no prompt store at {path}", file=sys.stderr)
        return 2

    accepted, rejected = [], []
    for r in load(path):
        (accepted if is_user_facing_text(r.get("text") or "") else rejected).append(r)
    print(f"store   : {path}")
    print(f"accepted: {len(accepted)}   rejected-by-current-filter: {len(rejected)}")

    keys, keyfn = batch_keys()
    if args.control:
        n = report(group(rejected, args.sig_chars), min_nodes=args.min_nodes, top=args.top,
                   title="POSITIVE CONTROL — families the filter ALREADY knows.",
                   keys=keys, keyfn=keyfn)
        print("\n  ^ these must look like obvious machine templates. If they do not,")
        print("    the grouping is broken and the candidate list below means nothing.")
        if n == 0:
            print("\n  CONTROL FOUND NOTHING — refusing to print candidates off a "
                  "grouping that cannot even re-find the known families.")
            return 2

    report(group(accepted, args.sig_chars), min_nodes=args.min_nodes, top=args.top,
           title="CANDIDATES — clusters among nodes the filter currently ACCEPTS.",
           keys=keys, keyfn=keyfn)
    print("\n  Read the heads. A machine template is one emitter, many near-identical")
    print("  bodies, a long steady span. A human habit is short, varied, and bursty.")
    print("  A 'cap' row is the founder's OWN prompt replayed by a cron: the text is")
    print("  human, only the repetition is machine, and the batch cap already handles")
    print("  it. Adding a filter pattern for one of those DELETES A REAL PROMPT.")
    print("  Nothing here is filtered or removed; adding a pattern is a human call.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
