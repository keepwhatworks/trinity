#!/usr/bin/env python3
"""Re-apply the CURRENT ingest purity filter to the prompt store already on disk.

THE DEFECT THIS CLOSES (measured 2026-08-01, amd_0047)
======================================================
`ingest.is_user_facing_text` runs ONCE, at ingest. There is no backfill path
anywhere in `commands/`. So every time a new harness dialect is found and added
to the filter, the copies already indexed stay in the lens forever.

Re-applying today's filter to the live store rejects 7,127 of 53,427 lines
(13.3%). The instructive part is not the volume, it is WHICH ones:

     228  <local-command-caveat>            <- explicitly in the filter list
     204  <image name=...>                  <- explicitly in the filter list
     164  <subagent_notification>           <- explicitly in the filter list
     121  [request interrupted by user...]  <- explicitly in the filter list

Those are not pattern gaps. Today's filter rejects them outright. They were
ingested BEFORE their pattern was added (the source comments date the additions
to 2026-06-02 and 2026-07-26) and nothing ever went back for them. That is the
advice-closure failure this repo has a guard class for: applying the documented
fix does not clear the condition for data already on disk.

WHY THIS IS DRY-RUN BY DEFAULT AND WILL STAY THAT WAY
=====================================================
A naive prune is DESTRUCTIVE in a way the node counts hide: machine prompts
EMBED the user's own content.

  - the cron review hook quotes the diff being reviewed
  - Trinity's resolver `_EXTRACT_PROMPT` quotes the contested claim AND the
    decision context, i.e. the user's own council task text

So the user's real words live inside machine-generated wrappers, and deleting
the node deletes them too. Measured blast radius against the disagreement
ledger's 128-row behavioural answer key, BEFORE writing this script:

    87  claims cite evidence in genuine user text ONLY   -> unaffected
    23  claims cite evidence in BOTH a machine and a genuine node -> safe
     1  claim would lose its ONLY evidence               -> named in the report
    17  quotes too short to locate                       -> unknown

That single claim is why `--apply` prints it by id and refuses to run silently.

USAGE
    scripts/refilter_prompt_store.py                  # report only (default)
    scripts/refilter_prompt_store.py --by-shape 25    # widen the shape table
    scripts/refilter_prompt_store.py --apply          # rewrite, after a backup

`--apply` writes `prompt_nodes.jsonl.bak-<utc>` first and refuses if the store
looks degenerate (see REFUSALS). Embeddings are preserved for kept rows; nothing
is re-embedded, so this is cheap and does not touch the model.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from trinity_local.ingest import is_user_facing_text  # noqa: E402
from trinity_local.config import trinity_home  # noqa: E402
from trinity_local.state_paths import prompts_dir  # noqa: E402

# Pre-registered refusals. A maintenance script that deletes most of a corpus
# because a filter regressed is worse than one that never ran.
MAX_REJECT_FRACTION = 0.35
MIN_STORE_LINES = 1000


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or "")).lower()).strip()


def load_store(path: pathlib.Path):
    """Parsed rows, plus the RAW text of any line that would not parse.

    The unparseable lines are returned, not merely counted, because --apply
    rewrites the store from what this returns: counting them while dropping them
    made the report's own words false ("N unparseable, left alone") and deleted
    user data on every apply. They are now carried through verbatim, which is what
    "left alone" has always claimed and what reembed_prompt_store.py already does
    on its own rewrite path.
    """
    rows, unparseable = [], []
    for line in path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            unparseable.append(line.rstrip("\n"))
    return rows, unparseable


def ledger_blast_radius(rejected, kept):
    """How many decided ledger claims lose their ONLY evidence if we drop these.

    This is the number that decides whether a prune is safe, and it is computed
    from the live answer key rather than assumed.
    """
    res_path = trinity_home() / "disagreement_ledger" / "resolutions.jsonl"
    if not res_path.exists():
        return None
    decided = []
    for line in res_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("resolution") in ("followed", "contradicted"):
            decided.append(d)
    rej_t = [norm(r.get("text")) for r in rejected]
    rej_t = [t for t in rej_t if len(t) >= 40]
    kept_t = [norm(r.get("text")) for r in kept]
    kept_t = [t for t in kept_t if len(t) >= 40]
    out = {"decided": len(decided), "only_machine": [], "both": 0, "only_human": 0, "unlocatable": 0}
    for d in decided:
        q = norm(d.get("quote"))
        if len(q) < 40:
            out["unlocatable"] += 1
            continue
        inr = any(q in t for t in rej_t)
        ina = any(q in t for t in kept_t)
        if inr and not ina:
            out["only_machine"].append(d["claim_id"])
        elif inr and ina:
            out["both"] += 1
        elif ina:
            out["only_human"] += 1
        else:
            out["unlocatable"] += 1
    return out


KEEP_BACKUPS = 2


def _backup_stamp_key(p) -> str:
    """Sort key from the timestamp EMBEDDED IN THE FILENAME, not st_mtime.

    shutil.copy2 preserves the SOURCE file's mtime, so every backup inherits the
    store's mtime rather than its own creation time. Measured 2026-08-07: all four
    prompt_nodes.jsonl.bak-reembed-* files carry mtime 2026-07-31T22:15:16 despite
    filename stamps 143207Z/143633Z/145417Z/151721Z, and a same-day sepfix backup
    inherited an mtime OLDER than a backup taken a week earlier. "Keep the newest N
    by mtime" therefore selects arbitrarily among ties and can retain the wrong
    copies. The filename stamp is exact, monotonic, and immune to copy semantics.
    Files without a parseable stamp sort FIRST (oldest) so a stray hand-made copy is
    pruned before a real one.
    """
    import re

    m = re.search(r"(\d{8}T\d{6}Z)$", p.name)
    return m.group(1) if m else ""


def _prune_refilter_backups(path, keep: int) -> None:
    """Keep the newest `keep` copies THIS script made; delete older ones.

    The glob is anchored to `.bak-refilter-` rather than the looser `.bak-2*` it
    shipped with. That earlier pattern also matched
    `prompt_nodes.jsonl.bak-20260801T163306Z` — the one backup a 2026-08-07 audit
    explicitly ruled must be KEPT (it is the only surviving copy of the corrected
    post-reembed corpus) — and matched the sepfix backup too. A pruner that deletes
    the artefact another instrument depends on is worse than no pruner.
    """
    import os

    olds = sorted(path.parent.glob(f"{path.name}.bak-refilter-*"),
                  key=_backup_stamp_key, reverse=True)
    for stale in olds[keep:]:
        try:
            os.unlink(stale)
            print(f"pruned old backup: {stale.name}")
        except OSError as exc:
            print(f"could not prune {stale.name}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite the store (backs up first)")
    ap.add_argument("--by-shape", type=int, default=15, metavar="N",
                    help="how many rejected shapes to list (default 15)")
    args = ap.parse_args()

    # prompts_dir() owns the legacy memory/ -> prompts/ migration; do not
    # hand-build the path or this script would miss a migrated store.
    path = prompts_dir() / "prompt_nodes.jsonl"
    if not path.exists():
        print(f"no prompt store at {path}", file=sys.stderr)
        return 2

    rows, unparseable = load_store(path)
    print(f"store            : {path}")
    print(f"lines            : {len(rows)}"
          + (f"  ({len(unparseable)} unparseable, carried through verbatim)" if unparseable else ""))
    if len(rows) < MIN_STORE_LINES:
        print(f"REFUSING: store has {len(rows)} lines < MIN_STORE_LINES={MIN_STORE_LINES}. "
              "Too small to be the real corpus; refusing rather than pruning a test fixture.")
        return 2

    kept, rejected = [], []
    for r in rows:
        (kept if is_user_facing_text(r.get("text") or "") else rejected).append(r)
    frac = len(rejected) / len(rows)
    print(f"would REJECT     : {len(rejected)} ({frac:.1%})")
    print(f"would KEEP       : {len(kept)}")

    shapes = Counter()
    for r in rejected:
        t = (r.get("text") or "").lstrip().replace("\n", " ")
        shapes[t[:60]] += 1
    print(f"\ntop {args.by_shape} rejected shapes:")
    for t, c in shapes.most_common(args.by_shape):
        print(f"  {c:6d}  {t!r}")

    blast = ledger_blast_radius(rejected, kept)
    if blast is not None:
        print(f"\nledger blast radius ({blast['decided']} decided claims in the answer key):")
        print(f"  evidence in genuine text only : {blast['only_human']}")
        print(f"  evidence in BOTH              : {blast['both']}  (safe — a real copy survives)")
        print(f"  quote too short to locate     : {blast['unlocatable']}")
        print(f"  would LOSE their only evidence: {len(blast['only_machine'])}")
        for cid in blast["only_machine"]:
            print(f"      ! {cid}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to rewrite the store.")
        return 0

    if frac > MAX_REJECT_FRACTION:
        print(f"\nREFUSING: would reject {frac:.1%} > MAX_REJECT_FRACTION={MAX_REJECT_FRACTION:.0%}. "
              "That is filter regression, not corpus drift. Inspect the shapes above first.")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".jsonl.bak-refilter-{stamp}")
    shutil.copy2(path, backup)
    # Same unbounded-retention defect as the reembed script (see its
    # _prune_backups): every apply left a permanent ~1GB copy.
    _prune_refilter_backups(path, KEEP_BACKUPS)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        # Unparseable lines first, verbatim — dropping them silently deleted user
        # data while the report claimed they were left alone.
        for raw in unparseable:
            f.write(raw + "\n")
        for r in kept:
            # ensure_ascii=True (the DEFAULT) is load-bearing, not style — the same
            # lesson reembed_prompt_store.py already carries. The store is written
            # ESCAPED; json.loads turns an escaped \\u2028/\\u2029 back into the RAW
            # character, and dumping it with ensure_ascii=False emits that raw
            # separator, which str.splitlines() counts as a line break. This script
            # shipped with ensure_ascii=False and a 2026-08-0x --apply re-injected
            # exactly 555 phantom lines into the live store: 47,536 physical lines
            # read as 48,091 by splitlines(), and disagreement_ledger._load_nodes()
            # (which splits that way) silently dropped 557 unparseable fragments from
            # the resolver's own evidence corpus.
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)
    print(f"\nbackup written   : {backup}")
    print(f"store rewritten  : {len(kept)} lines kept, {len(rejected)} removed")
    print("NOTE: embeddings on kept rows are preserved; nothing was re-embedded.")
    if blast and blast["only_machine"]:
        print("NOTE: the claim(s) listed above no longer have locatable evidence in the "
              "store. The ledger's recorded resolution is unchanged (resolutions.jsonl "
              "stores the quote text), but a future `trust --build` will not re-derive them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
