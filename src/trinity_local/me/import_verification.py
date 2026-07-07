"""Import verification + quarantine — the seam closure (council_5ab2854092bcf68f).

The provenance firewall at the import boundary required an anchor to be
PRESENT (an inline original_prompt / evidence quotes) but never checked that
the anchor was TRUE — an in-harness agent could fabricate "the user asked X"
and the act entered the ledger as anchored. Provenance guarded the field, not
the fact. This module closes it, per the council-ratified design:

  * verify-at-import — an anchor VERIFIES when it resolves against the local
    prompt index (~/.trinity/prompts/prompt_nodes.jsonl): the user's real,
    ingested prompts. eval-kind: the claimed original_prompt must match (or
    contain / be contained by) a real indexed prompt. lens-kind: a tension or
    ordering verifies when at least one evidence quote resolves into the
    corpus (evidence is allowed to be a fragment of a real prompt).
  * quarantine-until-verified — well-formed but unverifiable payloads go to a
    SIDECAR (me/quarantine_acts.jsonl / me/quarantine_lens.jsonl), NEVER the
    canonical stores: they cannot touch preference_acts, lenses, orderings,
    routing, or lens-build. The sidecar is a buffer, not a ledger — rows are
    removed when promoted (documented mutation; the append-only contract
    covers the canonical stores the sidecar exists to protect).
  * promotion on ingest — after each ingest pass, quarantined rows re-verify
    against the (now larger) corpus; the session that wasn't on disk at
    import time usually lands shortly after. Verified rows promote through
    the SAME admission path they were quarantined from.
  * fail-closed on malformed — payload rows without the required shape are
    rejected at parse (pre-existing behavior), never quarantined.

Falsifier (pre-registered by the council): if legitimate provider-loop
imports rarely verify (promotion rate ~0 because the harness's transcripts
never land locally), the sidecar becomes a black hole — lens-health surfaces
the pending count so that failure mode is visible, not silent.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..state_paths import trinity_home
from ..utils import now_iso

# Pre-registered floors: below these lengths a match is coincidence, not
# provenance. Anchors shorter than this cannot verify (they quarantine).
MIN_ANCHOR_CHARS = 20   # eval original_prompt
MIN_EVIDENCE_CHARS = 15  # lens evidence quote


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _quarantine_path(kind: str):
    name = "quarantine_acts.jsonl" if kind == "eval" else "quarantine_lens.jsonl"
    return trinity_home() / "me" / name


def load_corpus_texts() -> list[str]:
    """Normalized texts of every indexed real prompt. The verification
    universe: what the user actually said, as ingested from transcripts."""
    path = trinity_home() / "prompts" / "prompt_nodes.jsonl"
    out: list[str] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            t = _normalize(str(d.get("text") or ""))
            if t:
                out.append(t)
    return out


def anchor_resolves(claimed: str, corpus: list[str], *, min_chars: int) -> bool:
    """Does a claimed anchor resolve against the real corpus? Equality after
    normalization, or containment either way (a quote is a fragment of a real
    prompt; a real prompt may be a fragment of a longer claimed context).
    Below min_chars nothing resolves — too short to be provenance."""
    c = _normalize(claimed)
    if len(c) < min_chars:
        return False
    for real in corpus:
        if c == real or c in real or (len(real) >= min_chars and real in c):
            return True
    return False


def quarantine_rows(kind: str, rows: list[dict]) -> int:
    """Append well-formed-but-unverified payload rows to the sidecar."""
    if not rows:
        return 0
    p = _quarantine_path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    stamp = now_iso()
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "quarantined_at": stamp}) + "\n")
    return len(rows)


def quarantine_counts() -> dict[str, int]:
    """Pending sidecar sizes — surfaced by lens-health so a black-hole
    sidecar (the council's falsifier) is visible."""
    out = {}
    for kind in ("eval", "lens"):
        p = _quarantine_path(kind)
        n = 0
        if p.exists():
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
        out[kind] = n
    return out


def _load_quarantine(kind: str) -> list[dict]:
    p = _quarantine_path(kind)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            if isinstance(d, dict):
                rows.append(d)
        except ValueError:
            continue
    return rows


def promote_quarantined() -> dict[str, Any]:
    """Re-verify quarantined rows against the current (post-ingest) corpus and
    promote the ones that now resolve — through the same admission paths they
    were quarantined from. Removes promoted rows from the sidecar (a buffer,
    not a ledger). Never raises; safe to call after every ingest."""
    corpus = load_corpus_texts()
    result: dict[str, Any] = {"eval": {"promoted": 0, "pending": 0},
                              "lens": {"promoted": 0, "pending": 0}}
    if not corpus:
        counts = quarantine_counts()
        for k in ("eval", "lens"):
            result[k]["pending"] = counts[k]
        return result

    # eval-kind: promote acts whose claimed prompt now resolves.
    rows = _load_quarantine("eval")
    if rows:
        promote, keep = [], []
        for r in rows:
            if anchor_resolves(str(r.get("prompt_text") or ""), corpus,
                               min_chars=MIN_ANCHOR_CHARS):
                promote.append(r)
            else:
                keep.append(r)
        if promote:
            try:
                from .preference_acts import PreferenceAct, append_preference_acts, load_preference_acts
                existing = {a.id for a in load_preference_acts()}
                acts = []
                for r in promote:
                    r = {k: v for k, v in r.items() if k != "quarantined_at"}
                    try:
                        act = PreferenceAct.from_dict({**r, "provenance": "verified"} if "provenance" not in r else r)
                    except (TypeError, KeyError):
                        continue
                    if act.id not in existing:
                        acts.append(act)
                if acts:
                    append_preference_acts(acts)
                _rewrite_quarantine("eval", keep)
                result["eval"]["promoted"] = len(promote)
            except Exception:
                pass  # promotion is best-effort; rows stay quarantined
        result["eval"]["pending"] = len(keep) if promote else len(rows)

    # lens-kind: promote tensions/orderings whose evidence now resolves.
    rows = _load_quarantine("lens")
    if rows:
        promote, keep = [], []
        for r in rows:
            ev = [str(e) for e in (r.get("evidence") or [])]
            if any(anchor_resolves(e, corpus, min_chars=MIN_EVIDENCE_CHARS) for e in ev):
                promote.append(r)
            else:
                keep.append(r)
        if promote:
            try:
                from ..commands.lens_import import admit_verified_lens_rows
                admit_verified_lens_rows(promote)
                _rewrite_quarantine("lens", keep)
                result["lens"]["promoted"] = len(promote)
            except Exception:
                pass
        result["lens"]["pending"] = len(keep) if promote else len(rows)
    return result


def _rewrite_quarantine(kind: str, rows: list[dict]) -> None:
    p = _quarantine_path(kind)
    if not rows:
        if p.exists():
            p.write_text("", encoding="utf-8")
        return
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
