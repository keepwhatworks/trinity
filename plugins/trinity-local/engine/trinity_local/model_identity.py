"""Canonical model identity — the #239 triple made first-class (2026-07-14).

"Which model to trust" is the product's core value, and the founder's fidelity
requirement is model x SIZE x EFFORT: not "Claude" but "Opus 4.8 at xhigh".
Every behavioral instrument (disagreement ledger, routing picks, exposure
lane) slices on this, so the parse lives in ONE place instead of being
re-derived per surface (the drift-by-two-copies trap).

A ModelIdentity decomposes a (model_string, effort) pair into:
  family  — the lab: claude | openai | google | local | ?
  tier    — the capability class WITHIN the family (the "size" leg):
            opus/sonnet/haiku/fable (claude), flagship/codex/mini (openai),
            pro/flash (google). This is what "size" means operationally.
  version — the release (4.8, 5.6, 3.1) — parsed from BOTH dot and dash forms
            ("claude-opus-4-8" -> 4.8), the bug an earlier probe hit.
  effort  — the reasoning level: low/medium/high/xhigh/max, recovered from an
            explicit effort arg OR baked into the string (agy: "... (high)").

FIDELITY IS DATA-BOUND, and the honest surfaces say so: `.project(dims)`
returns the identity at a requested fidelity, and any leg that couldn't be
parsed is the sentinel "?" — so a slice can report "opus/?/? (effort
unknown)" instead of silently pretending full fidelity. Effort is "?" on
every council dispatched before the 2026-07-14 stamping; it accrues forward
and lives (measured now) in the judged-eval instrument.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNKNOWN = "?"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelIdentity:
    family: str
    tier: str
    version: str
    effort: str

    def project(self, *dims: str) -> tuple[str, ...]:
        """The identity at a requested fidelity — e.g. project('family',
        'tier') for a size-level slice. Order is preserved; unknown legs stay
        the '?' sentinel so a caller can filter or disclose them."""
        return tuple(getattr(self, d) for d in dims)

    def label(self, *dims: str) -> str:
        chosen = dims or ("family", "tier", "version", "effort")
        return " · ".join(getattr(self, d) for d in chosen)

    @property
    def is_full(self) -> bool:
        """True iff every leg parsed — the only identities that can carry an
        effort-fidelity claim."""
        return UNKNOWN not in (self.family, self.tier, self.version, self.effort)


def _tier(m: str) -> tuple[str, str]:
    if "opus" in m:
        return "claude", "opus"
    if "sonnet" in m:
        return "claude", "sonnet"
    if "haiku" in m:
        return "claude", "haiku"
    if "fable" in m or "mythos" in m:
        return "claude", "fable"
    if "codex" in m:
        return "openai", "codex"
    if "gpt" in m or "o1" in m or "o3" in m:
        return "openai", "mini" if "mini" in m else "flagship"
    if "gemini" in m:
        return "google", "flash" if "flash" in m else "pro"
    if any(x in m for x in ("qwen", "llama", "gemma", "mlx", "mistral", "phi")):
        return "local", m.split(":")[0].split("/")[-1][:16] or UNKNOWN
    return UNKNOWN, UNKNOWN


def _version(m: str) -> str:
    # dot form (5.6, 4.8) OR dash form (opus-4-8 -> 4.8); the dash form is what
    # every Claude slug uses and what the earlier probe missed.
    dot = re.search(r"(?<!\d)(\d+\.\d+)(?!\d)", m)
    if dot:
        return dot.group(1)
    dash = re.search(r"-(\d+)-(\d+)(?:\b|-)", m)
    if dash:
        return f"{dash.group(1)}.{dash.group(2)}"
    # bare single-int version at the end of a name (claude-fable-5 -> "5") —
    # fires only when neither dotted nor dashed form matched.
    bare = re.search(r"(?:^|[- ])([a-z]+)-(\d+)$", m)
    if bare:
        return bare.group(2)
    return UNKNOWN


def _effort(m: str, effort: str | None) -> str:
    e = (effort or "").strip().lower()
    if e in _EFFORTS:
        return e
    baked = re.search(r"\(([a-z]+)\)", m)
    if baked and baked.group(1) in _EFFORTS:
        return baked.group(1)
    return UNKNOWN


def parse_identity(model: str | None, effort: str | None = None) -> ModelIdentity:
    """(model_string, effort) -> ModelIdentity. Total: any unparseable leg is
    the '?' sentinel; never raises."""
    m = (model or "").strip().lower()
    fam, tier = _tier(m)
    return ModelIdentity(family=fam, tier=tier, version=_version(m),
                         effort=_effort(m, effort))
