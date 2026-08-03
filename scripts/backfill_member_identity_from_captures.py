#!/usr/bin/env python3
"""Backfill model×effort identity onto web-era council members — by EXTRACTION.

WHY (measured 2026-08-02)
=========================
1,077 of 1,541 council member rows are web-captured (claude_ai 316, chatgpt 324,
gemini 437) and every one recorded model=None, effort=None — so the trust tally
keys them at lab granularity and the effort secondary has never had a
within-model sibling. But the RAW captures kept the identity all along:

    chatgpt   97% of conversations carry per-MESSAGE metadata.model_slug
              (gpt-5-5-thinking, gpt-5-6-thinking, ...) and 92% carry
              thinking_effort (extended/standard/xhigh)
    claude    97% carry conversation-level .model (claude-opus-4-8, fable-5, ...)
              and 69% carry .settings.effort_level (high×81, max×22, xhigh×5)
    gemini    nothing — the payloads are opaque; value hits are conversation
              CONTENT mentioning model names. Gemini rows stay honestly dark.

This is extraction, not inference: a member row is stamped ONLY when its own
output_text is found verbatim inside a captured assistant message whose
identity is unambiguous. No fuzzy matching, no majority guessing, no defaults.

RULES (each refuses toward abstention)
======================================
  * Match key = NFKC/whitespace/case-normalised text, exact equality first;
    a containment fallback fires only for texts >=200 chars AND a unique match.
  * If the matched messages disagree on (model, effort) -> AMBIGUOUS, no stamp.
  * Only fills NULLS: never overwrites an existing model or effort.
  * chatgpt thinking_effort is stamped VERBATIM (extended/standard/...).
    model_identity._effort does not recognise that vocabulary, so the identity
    slices to effort='?' — deliberately: chatgpt.com's toggle and codex-CLI
    reasoning_effort are DIFFERENT KNOBS, and inventing a translation would
    fabricate comparability. The raw value is preserved for a future decision.
    claude effort_level (high/xhigh/max) IS the CLI vocabulary -> real slices.
  * Labels, resolutions, winners, synthesis: NEVER touched. Member `model`,
    member `metadata.effort`, and a provenance stamp
    `metadata.identity_backfill={source,conversation,message,at}` only.
  * Every modified file is copied to ~/.trinity/council_outcomes.bak-<UTC>/
    BEFORE writing (outside the council_outcomes/ glob, so nothing re-reads it).

AFTER --apply
=============
`reaggregate_ledger()` re-derives summary.json from the EXISTING resolutions —
no re-resolve, no LLM, no quota — and the report prints the tally's effort
breakdown before/after, answering the question this exists for: does any
model×version now hold >=2 effort levels at n>=MIN_TALLY_N?

NOTE: re-keying moves credit from lab-fallback rows to model×version rows, so
the published canonical ledger numbers in CLAUDE.md WILL move. That is the
point (the same resolutions, better identity), and render_docs re-derives the
placeholders — but it must be disclosed, not slipped: run render_docs and read
the diff before committing.

USAGE
    scripts/backfill_member_identity_from_captures.py            # dry-run
    scripts/backfill_member_identity_from_captures.py --apply
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

HOME = pathlib.Path.home() / ".trinity"
MIN_CONTAIN = 200          # containment fallback needs this much text
MIN_EXACT = 60             # exact match still needs a non-trivial string


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(s or "")).lower()).strip()


def build_capture_index():
    """normalised assistant text -> set of (provider, model, effort, conv, msg)."""
    exact: dict[str, set] = defaultdict(set)
    long_texts: list[tuple[str, tuple]] = []   # for the containment fallback

    def add(text, ident):
        t = norm(text)
        if len(t) >= MIN_EXACT:
            exact[t].add(ident)
        if len(t) >= MIN_CONTAIN:
            long_texts.append((t, ident))

    # ---- claude.ai: conversation-level identity ---------------------------
    for f in (HOME / "conversations" / "claude").glob("*.json"):
        if f.name.startswith("_"):
            continue
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        model = d.get("model")
        effort = (d.get("settings") or {}).get("effort_level")
        if not model:
            continue
        for m in d.get("chat_messages") or []:
            if m.get("sender") != "assistant":
                continue
            # index BOTH text projections: some capture messages populate only
            # the joined content parts, others only .text — and the ingested
            # node may have been built from either.
            parts = " ".join(c.get("text") or "" for c in (m.get("content") or []))
            for text in {m.get("text") or "", parts}:
                add(text, ("claude_ai", model, effort, d.get("uuid") or f.stem, m.get("uuid")))

    # ---- chatgpt: per-message identity ------------------------------------
    # TWO sources (measured 2026-08-02): live captures cover only 531 of the
    # 8k chatgpt nodes; 7,956 came from the OpenAI data exports still on disk
    # under ~/projects/taste-terminal/data/exports/. The export carries
    # model_slug on 94% of conversations (gpt-4 -> gpt-5 era) but NO
    # thinking_effort — so export-sourced stamps are model-only, honestly.
    chatgpt_docs = []
    for f in (HOME / "conversations" / "chatgpt").glob("*.json"):
        if not f.name.startswith("_"):
            chatgpt_docs.append((f.stem, f))
    exports = pathlib.Path.home() / "projects" / "taste-terminal" / "data" / "exports"
    seen_conv: set[str] = set()
    for sub in ("chatgpt-2", "chatgpt-merged", "chatgpt"):
        for f in sorted((exports / sub).glob("conversations*.json")):
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            for conv in (data if isinstance(data, list) else [data]):
                cid = conv.get("conversation_id") or conv.get("id") or ""
                if cid in seen_conv:
                    continue
                seen_conv.add(cid)
                chatgpt_docs.append((cid or f.stem, conv))
    for stem, src in chatgpt_docs:
        if isinstance(src, pathlib.Path):
            try:
                d = json.loads(src.read_text())
            except (OSError, ValueError):
                continue
        else:
            d = src
        mapping = d.get("mapping") or {}
        # conversation-unique slug, used ONLY when the message lacks its own
        slugs = {
            (v.get("message") or {}).get("metadata", {}).get("model_slug")
            for v in mapping.values() if isinstance(v, dict) and v.get("message")
        } - {None}
        conv_slug = slugs.pop() if len(slugs) == 1 else None
        for mid, v in mapping.items():
            msg = (v or {}).get("message") if isinstance(v, dict) else None
            if not msg or (msg.get("author") or {}).get("role") != "assistant":
                continue
            meta = msg.get("metadata") or {}
            model = meta.get("model_slug") or conv_slug
            if not model:
                continue
            effort = meta.get("thinking_effort")
            parts = (msg.get("content") or {}).get("parts") or []
            text = " ".join(p for p in parts if isinstance(p, str))
            add(text, ("chatgpt", model, effort, stem, mid))

    return exact, long_texts


def match_member(text_n: str, exact, long_texts):
    """-> ('matched', ident) | ('ambiguous', idents) | ('unmatched', None)"""
    idents = exact.get(text_n, set())
    if not idents and len(text_n) >= MIN_CONTAIN:
        hits = {i for t, i in long_texts if text_n in t or t in text_n}
        idents = hits
    if not idents:
        return "unmatched", None
    keyed = {(i[1], i[2]) for i in idents}       # (model, effort) must agree
    if len(keyed) > 1:
        return "ambiguous", idents
    return "matched", next(iter(idents))


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    print("indexing raw captures ...")
    exact, long_texts = build_capture_index()
    print(f"  assistant texts indexed: exact={len(exact)}  long(containment)={len(long_texts)}")

    outcomes = sorted((HOME / "council_outcomes").glob("council_*.json"))
    stats = defaultdict(Counter)
    stamped_ident = Counter()
    plans: list[tuple[pathlib.Path, dict]] = []   # (file, updated_doc)

    for f in outcomes:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        changed = False
        for m in d.get("member_results") or []:
            prov = m.get("provider")
            if prov not in ("claude_ai", "chatgpt", "gemini"):
                continue
            if m.get("model"):                      # only fill nulls
                stats[prov]["already_had_model"] += 1
                continue
            verdict, ident = match_member(norm(m.get("output_text")), exact, long_texts)
            stats[prov][verdict] += 1
            if verdict != "matched":
                continue
            _p, model, effort, conv, msg = ident
            m["model"] = model
            meta = m.setdefault("metadata", {})
            if effort and not meta.get("effort"):
                meta["effort"] = effort
            meta["identity_backfill"] = {
                "source": "web-capture", "conversation": conv, "message": msg,
                "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
            stamped_ident[f"{model} · {effort or '-'}"] += 1
            changed = True
        if changed:
            plans.append((f, d))

    print("\nper-provider results (rows with model=None):")
    for prov in ("claude_ai", "chatgpt", "gemini"):
        c = stats[prov]
        print(f"  {prov:10s} matched={c['matched']:>4}  ambiguous={c['ambiguous']:>3}  "
              f"unmatched={c['unmatched']:>4}  already_had={c['already_had_model']}")
    print(f"\nidentities to stamp ({sum(stamped_ident.values())} rows in {len(plans)} files):")
    for k, v in stamped_ident.most_common(12):
        print(f"  {v:>4}x {k}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0
    if not plans:
        print("nothing to write.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bdir = HOME / f"council_outcomes.bak-{stamp}"
    bdir.mkdir()
    for f, _ in plans:
        shutil.copy2(f, bdir / f.name)
    print(f"\nbackup of {len(plans)} files -> {bdir}")
    for f, d in plans:
        f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print("stamped.")

    # ---- re-key the tally (no re-resolve, no quota) -----------------------
    from trinity_local.disagreement_ledger import MIN_TALLY_N, reaggregate_ledger

    before = json.loads((HOME / "disagreement_ledger" / "summary.json").read_text())
    after = reaggregate_ledger()
    print("\ntally re-keyed. records before -> after:")
    bkeys, akeys = set(before.get("records") or {}), set(after.get("records") or {})
    for k in sorted(bkeys | akeys):
        b = (before.get("records") or {}).get(k)
        a = (after.get("records") or {}).get(k)
        fmt = lambda r: f"{r['w']}-{r['l']}" if r else "—"
        marker = "" if b == a else "   <-- moved"
        print(f"  {k:34s} {fmt(b):>7} -> {fmt(a):>7}{marker}")
    eb = after.get("effort_breakdown") or {}
    print("\nTHE QUESTION — model×version rows with >=2 effort levels at n>=MIN_TALLY_N:")
    any_sib = False
    for mv, cells in eb.items():
        big = {e: c for e, c in cells.items() if (c["w"] + c["l"]) >= MIN_TALLY_N}
        if len(big) >= 2:
            any_sib = True
            print(f"  {mv}: " + "  ".join(f"{e}={c['w']}-{c['l']} ({c['win_rate']:.0%})"
                                          for e, c in big.items()))
    if not any_sib:
        print("  none yet — effort cells exist but no model×version cleared the floor twice.")
    print("\nNOTE: canonical ledger numbers in CLAUDE.md derive from this summary —")
    print("      run render_docs, READ the diff, and re-run the doc gate before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
