"""Shared text helpers for the eval harness — compare-norm + judge-output cleaning.

ONE place for each so the consumers can't drift apart, the bug class tonight
(two "is this the same text?" / "strip the fence" checks diverging silently).
- `norm_for_compare`: was three byte-identical copies (#247 gate + dataset/judge dedup).
- `strip_code_fences`: was two DRIFTED copies (scorer's `(?:json)?` only stripped a
  `json` tag and silently failed on ```text; judge_alignment's `(?:\\w+)?` handled any).
"""
from __future__ import annotations

import re

_FENCE_OPEN = re.compile(r"^```(?:\w+)?\s*")
_FENCE_CLOSE = re.compile(r"\s*```$")


def norm_for_compare(text: str) -> str:
    """Whitespace+case-collapsed text for an equality/dedup comparison."""
    return " ".join((text or "").lower().split())


def strip_code_fences(text: str) -> str:
    """Strip a leading/trailing markdown code fence (```lang … ```) from an
    LLM/judge response before parsing. The `\\w+` language matcher handles json,
    JSON, text, etc. — the narrower `(?:json)?` form silently left the fence in
    (breaking the JSON parse) on any other tag."""
    s = (text or "").strip()
    s = _FENCE_OPEN.sub("", s)
    s = _FENCE_CLOSE.sub("", s)
    return s.strip()
