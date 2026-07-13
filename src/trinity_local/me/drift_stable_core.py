"""Protect only tensions that are both persistent and present after a clean rebuild.

Persistence alone is unsafe: an automation-derived tension can be maximally
persistent because the same driver text appeared in every build. The blast-cap
therefore protects the intersection of the old persistent set and the clean
rebuild. This is the only trust-region primitive the live Lens registry uses.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence, TypeVar


T = TypeVar("T")


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return num / (na * nb)


# Deliberately stricter than the registry's 0.80 accretion threshold. The
# protected intersection must lean toward non-match so coincidental semantic
# rhyme cannot freeze contamination.
_DRIFT_STABLE_MATCH_MIN = 0.85


def drift_stable_core(
    persistent_old: list[T],
    clean_probes: Sequence[str],
    *,
    key: Callable[[T], str],
    embed_fn: Callable[[str], Sequence[float]],
    match_threshold: float = _DRIFT_STABLE_MATCH_MIN,
) -> list[T]:
    """Return persistent items strongly restated by the clean rebuild.

    Failure modes are conservative-safe: persistent contamination absent from
    the clean rebuild is excluded; one-off clean noise cannot enter through the
    persistent set; genuinely new tensions remain ordinary unprotected
    tensions until later builds reconfirm them.
    """
    if not persistent_old or not clean_probes:
        return []
    clean_vecs = [embed_fn(probe) for probe in clean_probes if probe]
    if not clean_vecs:
        return []
    out: list[T] = []
    for item in persistent_old:
        probe = key(item)
        if not probe:
            continue
        vector = embed_fn(probe)
        if any(_cos(vector, clean_vector) >= match_threshold for clean_vector in clean_vecs):
            out.append(item)
    return out
