#!/usr/bin/env python3
"""Recompute every stored prompt-node embedding with the fixed, single-prefix path.

WHY (measured 2026-08-01)
=========================
`backend_mlx_native` prepended `search_document: ` unconditionally, with no
already-prefixed guard, while several production callers pre-prefix:

    ingest_helpers.py:117   f"search_document: {turn.text}"   (CLI ingest)
    stale_pass.py:238       f"search_document: {node.text}"   (embed heal)
    me_builder.py:276       f"search_document: {t[:600]}"

Their text became `search_document: search_document: ...`, embedding away from
the correct vector for the SAME string. Measured on a 300-node sample BEFORE the
producer fix: **24.7% of live nodes were double-prefixed** — roughly 10k of
40,236 — split WITHIN providers, because it tracks the code path, not the source.

The error scales INVERSELY with text length, because the doubled 17-char prefix
is a larger share of a short string:

    "ok" / "done"  (1-7 chars)   cos 0.76   prefix dominates the vector
    ~100-600 chars              cos 0.97-0.98
    >900 chars                  negligible

The producer is fixed (`_ensure_doc_prefix`, guarded + mutation-proved). This
script repairs the DATA that the broken producer wrote.

WHAT IT DOES NOT DO
===================
  * Does not remove, add, or reorder any line. It rewrites ONE FIELD in place.
    Line count in == line count out, asserted before the swap.
  * Does not change dimension. Stays 768. Matryoshka (512 is statistically
    indistinguishable at 67% storage) is a SEPARATE change: `me_builder.py:229`
    hard-asserts `EXPECTED_DIM = 768`, so shrinking here would silently drop
    every node from lens-build. That consumer must move first.
  * Does not touch derived artifacts. topics.json basins, scoreboard/picks.json
    and the topology are computed FROM these vectors and become stale the moment
    this runs — rebuild them afterwards (`trinity-local lens --force`, then
    `consolidate`). This is reported, not silently assumed.

VERIFICATION (the point of the exercise)
========================================
`--apply` re-checks the property that exposed the bug: byte-identical text must
embed to the same vector. It samples duplicate groups BEFORE and AFTER and
prints both, so the run either demonstrates the repair or shows it failed. A
rewrite that cannot show its own effect is indistinguishable from a no-op.

USAGE
    scripts/reembed_prompt_store.py                 # report only (default)
    scripts/reembed_prompt_store.py --sample 400    # widen the before/after probe
    scripts/reembed_prompt_store.py --apply         # rewrite, after a backup
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import shutil
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from trinity_local.state_paths import prompts_dir  # noqa: E402

MIN_STORE_LINES = 1000
# RESUMABILITY. Measured 2026-08-01: the corpus embeds at ~73 texts/s regardless
# of batch size (mean text is 1,526 chars; batch 4096 is 6x WORSE on memory
# pressure), so a full pass is ~550s. That is long enough to be interrupted, and
# twice was. Vectors are checkpointed to a sidecar as they are computed, so a
# re-run resumes instead of discarding minutes of GPU work. The store itself is
# only rewritten once EVERY vector is present.
CHECKPOINT_NAME = "prompt_nodes.reembed-checkpoint.jsonl"
MAX_UNCHANGED_FRACTION = 0.999   # a run that changes nothing is a no-op, not a fix
BATCH = 256


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or "")).lower()).strip()


def duplicate_agreement(records, sample: int, seed: int = 20260801):
    """Min pairwise cosine within byte-identical-text groups. THE metric: the
    bug's signature was identical text disagreeing."""
    import numpy as np

    groups = defaultdict(list)
    for r in records:
        e = r.get("embedding")
        if isinstance(e, list) and e:
            groups[norm(r.get("text"))].append(e)
    multi = [(t, v) for t, v in groups.items() if len(v) > 1]
    if not multi:
        return None
    random.Random(seed).shuffle(multi)
    mins = []
    for _text, v in multi[:sample]:
        A = np.asarray(v, dtype=np.float32)
        A /= (np.linalg.norm(A, axis=1, keepdims=True) + 1e-9)
        off = (A @ A.T)[np.triu_indices(len(A), 1)]
        if len(off):
            mins.append(float(off.min()))
    if not mins:
        return None
    mins_arr = np.asarray(mins)
    return {
        "groups": len(mins),
        "min": float(mins_arr.min()),
        "p05": float(np.percentile(mins_arr, 5)),
        "median": float(np.median(mins_arr)),
        "below_0.999": int((mins_arr < 0.999).sum()),
        "below_0.95": int((mins_arr < 0.95).sum()),
    }


def _fmt(a) -> str:
    if not a:
        return "  (no duplicate groups to measure)"
    return (f"  groups={a['groups']}  min={a['min']:.4f}  p05={a['p05']:.4f}  "
            f"median={a['median']:.4f}  <0.999={a['below_0.999']}  <0.95={a['below_0.95']}")


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


def _prune_backups(path, marker: str, keep: int) -> None:
    """Keep the newest `keep` backups matching `marker`; delete older ones.

    Unbounded retention is how a 0.75GiB store accumulated 6.4GiB of copies —
    8.9x the live data on a volume at 93%. Newest-first by mtime so an
    interrupted run cannot strand the most recent good copy.
    """
    import os

    olds = sorted(path.parent.glob(f"{path.name}.{marker}*"),
                  key=_backup_stamp_key, reverse=True)
    for stale in olds[keep:]:
        try:
            size = stale.stat().st_size
            os.unlink(stale)
            print(f"pruned old backup: {stale.name} ({size / 1e9:.2f} GB)")
        except OSError as exc:
            print(f"could not prune {stale.name}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="rewrite the store (backs up first)")
    ap.add_argument("--sample", type=int, default=250, help="duplicate groups to probe")
    args = ap.parse_args()

    path = prompts_dir() / "prompt_nodes.jsonl"
    if not path.exists():
        print(f"no prompt store at {path}", file=sys.stderr)
        return 2

    raw = path.read_text(encoding="utf-8").splitlines()
    records, blanks = [], 0
    for line in raw:
        if not line.strip():
            blanks += 1
            records.append(None)
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append(line)          # keep verbatim, never re-serialise

    parsed = [r for r in records if isinstance(r, dict)]
    embedded = [r for r in parsed if isinstance(r.get("embedding"), list) and r["embedding"]]
    print(f"store            : {path}")
    print(f"lines            : {len(raw)}  (parsed {len(parsed)}, blank {blanks})")
    print(f"with an embedding: {len(embedded)}   <- these get recomputed")
    print(f"without          : {len(parsed) - len(embedded)}   <- left alone (no vector to fix)")
    if len(raw) < MIN_STORE_LINES:
        print(f"REFUSING: {len(raw)} lines < MIN_STORE_LINES={MIN_STORE_LINES} — this is a "
              "fixture, not the corpus.")
        return 2

    from trinity_local.embeddings import embed_batch, embedder_fingerprint, require_real_embedder

    fp = str(embedder_fingerprint())
    try:
        require_real_embedder()
    except Exception:                                            # noqa: BLE001
        print(f"REFUSING: embedder is not the real model (fingerprint {fp}). Re-embedding the "
              "corpus with the SHA-1 TF-IDF stub would replace a slightly-wrong vector space "
              "with a fake one.")
        return 2
    print(f"embedder         : {fp}")

    before = duplicate_agreement(embedded, args.sample)
    print("\nBEFORE — agreement within byte-identical-text groups:")
    print(_fmt(before))

    t0 = time.time()
    probe = [r["text"] for r in embedded[:BATCH]]
    embed_batch(probe)
    rate = len(probe) / max(1e-6, time.time() - t0)
    print(f"\nthroughput       : ~{rate:.0f} texts/s  -> ETA ~{len(embedded)/max(1,rate):.0f}s "
          f"for {len(embedded)} vectors")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to rewrite the store.")
        print("NOTE: --apply makes topics.json basins / scoreboard picks.json / topology STALE.")
        print("      Rebuild after: `trinity-local lens --force` then `trinity-local consolidate`.")
        return 0

    # NOTE: the backup is NOT taken here. It used to be, and that single ordering
    # left ~3.5GiB of corpses on disk: this point sits ABOVE the KeyboardInterrupt
    # handler and both REFUSING gates below, so every run that declined to write —
    # including three no-op runs after the producer fix landed — still copied the
    # whole ~900MB store first. A backup exists to protect a WRITE; taking one
    # before deciding to write protects nothing and costs a gigabyte. It now lives
    # immediately before the atomic swap (search: BACKUP TAKEN HERE).

    # ---- resume from any prior partial run --------------------------------
    ckpt = path.parent / CHECKPOINT_NAME
    done_vecs: dict[str, list] = {}
    if ckpt.exists():
        for line in ckpt.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                done_vecs[row["id"]] = row["v"]
            except (json.JSONDecodeError, KeyError):
                continue
        print(f"resuming         : {len(done_vecs)} vectors already checkpointed")

    todo = [r for r in embedded if r.get("id") not in done_vecs]
    print(f"to compute       : {len(todo)}")
    try:
        with ckpt.open("a", encoding="utf-8") as cf:
            for s in range(0, len(todo), BATCH):
                chunk = todo[s:s + BATCH]
                vecs = embed_batch([r["text"] for r in chunk])
                for r, v in zip(chunk, vecs):
                    done_vecs[r["id"]] = v
                    cf.write(json.dumps({"id": r["id"], "v": v}) + "\n")
                cf.flush()
                done = min(s + BATCH, len(todo))
                if s % (BATCH * 10) == 0 or done == len(todo):
                    print(f"  re-embedded {done}/{len(todo)} (this run)", flush=True)
    except KeyboardInterrupt:
        # No backup to clean up: it is now taken below, after the refusal gates.
        print("\ninterrupted — checkpoint retained; re-run to resume. Store untouched.")
        return 130

    changed = 0
    for r in embedded:
        v = done_vecs.get(r.get("id"))
        if v is None:
            print(f"REFUSING to write: node {r.get('id')} has no vector after the pass. "
                  "Store untouched; checkpoint retained.")
            return 2
        if r["embedding"] != v:
            changed += 1
        r["embedding"] = v

    unchanged_frac = 1 - changed / max(1, len(embedded))
    if unchanged_frac > MAX_UNCHANGED_FRACTION:
        print(f"REFUSING to write: {unchanged_frac:.1%} of vectors were already identical — "
              "this run would be a no-op, which means the producer fix is not in effect. "
              "Store left untouched.")
        return 2

    # BACKUP TAKEN HERE — after the two CONTENT refusal gates (missing-vector,
    # no-op-run) and immediately before the swap. Precise scope, because the commit
    # that moved it overstated: ONE gate still fires after this point — the
    # line-count guard below, which refuses the swap if the rewrite changed the row
    # count. That path deliberately KEEPS the backup ("backup retained" in its
    # message) because at that moment a .tmp exists and the store is suspect, so a
    # copy is exactly what you want. The three corpses this move eliminated came
    # from the gates ABOVE, which decide not to write at all.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".jsonl.bak-reembed-{stamp}")
    shutil.copy2(path, backup)
    print(f"\nbackup written   : {backup}")
    _prune_backups(path, "bak-reembed-", KEEP_BACKUPS)

    # tmp is cleaned on ANY failure path — an interrupted run left a stale
    # multi-hundred-MB .tmp beside the store on 2026-08-01.
    tmp = path.with_suffix(".jsonl.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            for orig, rec in zip(raw, records):
                if rec is None:
                    f.write("\n")
                elif isinstance(rec, dict):
                    # ensure_ascii=True (the DEFAULT) is load-bearing, not style.
                    # The store was written escaped; json.loads turns an escaped
                    # \u2028/\u2029 into the RAW character, and dumping it back
                    # with ensure_ascii=False emits that raw separator — which
                    # str.splitlines() counts as a line break. Measured: exactly
                    # 555 extra lines across the corpus (53,427 -> 53,982), which
                    # is what the line-count guard refused to swap on.
                    f.write(json.dumps(rec) + "\n")
                else:
                    f.write(orig + "\n")
        out_lines = len(tmp.read_text(encoding="utf-8").splitlines())
        if out_lines != len(raw):
            print(f"REFUSING to swap: rewrote {out_lines} lines but read {len(raw)}. "
                  "Store left untouched; backup retained.")
            return 2
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    ckpt.unlink(missing_ok=True)

    after = duplicate_agreement(embedded, args.sample)
    print(f"\nvectors changed  : {changed}/{len(embedded)}  ({changed/len(embedded):.1%})")
    print("AFTER — agreement within byte-identical-text groups:")
    print(_fmt(after))
    if before and after:
        print(f"\n  min      {before['min']:.4f} -> {after['min']:.4f}")
        print(f"  <0.999   {before['below_0.999']} -> {after['below_0.999']}")
        print(f"  <0.95    {before['below_0.95']} -> {after['below_0.95']}")
    print("\nSTALE NOW — rebuild before trusting anything derived from these vectors:")
    print("  trinity-local lens --force      # topics.json basins")
    print("  trinity-local consolidate       # scoreboard/picks.json + topology")
    return 0


if __name__ == "__main__":
    sys.exit(main())
