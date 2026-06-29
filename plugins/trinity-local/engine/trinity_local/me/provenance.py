"""Provenance — how much of a user turn is the user's own *typed* words vs.
*pasted* external content (a model's output, release notes, an article, a code
dump). The same do-operator discipline as #260: pasted text isn't the user's
authored voice, so the lens must not learn taste from a turn that's mostly a
paste.

Structural-first (#262): cheap, deterministic line-shape tells that scream
"pasted" — code fences, markdown headers, dense bullet/numbered lists, quoted
blocks, JSON/table rows. `pasted_fraction` estimates the share of a turn that is
pasted; `is_mostly_pasted` flags turns past a threshold. Used to discount the
substance of a turn in the thread-signal score, so a question with a small
pasted snippet still counts as the user's voice, but a wall of pasted prose
doesn't.

Embedding-distance-to-the-user's-voice is the validated complement (the #262
prototype), added later behind the embedder; structural alone is the reliable
no-dependency base shipped here.
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*```")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_BULLET = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
_QUOTE = re.compile(r"^\s*>\s?\S")
_STRUCT = re.compile(r"^\s*[\[{].*[\]}],?\s*$|^\s*\"[\w-]+\"\s*:|\|.*\|")  # json/table-ish


def _line_is_pasted(line: str, in_fence: bool) -> bool:
    if in_fence:
        return True
    return bool(
        _MD_HEADER.match(line)
        or _BULLET.match(line)
        or _QUOTE.match(line)
        or _STRUCT.match(line)
    )


def pasted_fraction(text: str) -> float:
    """Estimate the fraction (0–1) of a turn that is pasted external content,
    by line shape. Empty / whitespace-only → 0.0."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return 0.0
    pasted = 0
    in_fence = False
    for ln in lines:
        if _FENCE.match(ln):
            # The fence line itself counts as pasted; toggle the region.
            pasted += 1
            in_fence = not in_fence
            continue
        if _line_is_pasted(ln, in_fence):
            pasted += 1
    return round(pasted / len(lines), 3)


# A turn past this pasted-share reads as "mostly someone else's words".
MOSTLY_PASTED_THRESHOLD = 0.6


def is_mostly_pasted(text: str) -> bool:
    """True when the turn is dominated by pasted content (not the user's voice).
    Short turns (a typed question) are never flagged regardless of shape."""
    if len((text or "").strip()) < 200:
        return False
    return pasted_fraction(text) >= MOSTLY_PASTED_THRESHOLD


def typed_substance(text: str) -> int:
    """Characters of the turn that are the user's own typed words — total length
    discounted by the pasted fraction. Used to weight a turn's real substance so
    a paste-heavy turn doesn't inflate the thread-signal score."""
    t = (text or "").strip()
    if not t:
        return 0
    return int(len(t) * (1 - pasted_fraction(t)))
