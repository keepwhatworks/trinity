"""Exposure lane — honest denominators for the behavioral tier (council
2026-07-14, the post-K4 build order's first item).

The ledger records when a model LOSES (a correction is work the user did, so
it leaves a trace). Approval leaves no trace — and the council banned
inferring it: silence, session end, and topic change are NEVER evidence.
What IS honestly extractable with no opinion-inference at all is the
DENOMINATOR: an *exposure* — a user turn that follows a substantive
assistant answer from a known provider. The model had a load-bearing at-bat;
whether it "won" is not claimed.

rejection_rate = attributed model_miss losses / exposures, per canonical
provider family, with a Wilson interval. The number is a RATE with an
honest denominator — NOT a causal quality claim: exposure mix differs by
provider (the user routes different kinds of work to different models), and
that confound is disclosed wherever the rate is shown. De-confounding is
Tier 3's job (exploration), not this module's.

LLM-free (pure index reads), same as every ingest-side lane.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..council_schema import normalize_provider_slug
from ..state_paths import trinity_home

# An assistant answer shorter than this is scaffolding/ack, not a
# load-bearing at-bat — same spirit as the baseline-integrity floor.
MIN_ANSWER_CHARS = 80


def _wilson(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    import math
    if n <= 0:
        return (0.0, 1.0)
    p = min(1.0, max(0.0, wins / n))
    z2 = z * z
    den = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / den
    half = z * math.sqrt(max(0.0, p * (1 - p) / n + z2 / (4 * n * n))) / den
    return (max(0.0, center - half), min(1.0, center + half))


# Family pre-map for capture sources council_schema doesn't canonicalize:
# cowork is Anthropic's app — its answers are the claude family's at-bats.
_FAMILY_PREMAP = {"cowork": "claude"}


def _canon(provider: str | None) -> str | None:
    p = (provider or "").strip().lower()
    if not p:
        return None
    p = _FAMILY_PREMAP.get(p, p)
    return normalize_provider_slug(p) or p


@dataclass
class ExposureRecord:
    provider: str
    exposures: int
    losses: int

    @property
    def rate(self) -> float:
        return self.losses / self.exposures if self.exposures else 0.0

    def to_dict(self) -> dict[str, Any]:
        lo, hi = _wilson(self.losses, self.exposures)
        return {
            "provider": self.provider,
            "exposures": self.exposures,
            "losses": self.losses,
            "rejection_rate": round(self.rate, 5),
            "ci": [round(lo, 5), round(hi, 5)],
        }


def provider_exposures() -> dict[str, int]:
    """Exposures per canonical provider family: user turns whose preceding
    assistant answer is substantive and provider-attributed."""
    path = trinity_home() / "prompts" / "prompt_nodes.jsonl"
    out: dict[str, int] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        pre = (d.get("preceding_assistant_text") or "").strip()
        prov = _canon(d.get("provider"))
        if prov and len(pre) >= MIN_ANSWER_CHARS:
            out[prov] = out.get(prov, 0) + 1
    return out


def _node_providers() -> dict[str, str]:
    path = trinity_home() / "prompts" / "prompt_nodes.jsonl"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("id"):
            prov = _canon(d.get("provider"))
            if prov:
                out[d["id"]] = prov
    return out


def provider_rejection_rates() -> list[dict[str, Any]]:
    """The lane's product: per-family losses / exposures with Wilson CIs,
    sorted by rate ascending (best batting average first). Empty when the
    index or ledger is missing — never raises (analytics never crash)."""
    try:
        exposures = provider_exposures()
        if not exposures:
            return []
        nodes = _node_providers()
        losses: dict[str, int] = {}
        ledger = trinity_home() / "me" / "preference_acts.jsonl"
        if ledger.exists():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(a, dict):
                    continue
                if (a.get("trigger") or "").lower() != "model_miss":
                    continue
                prov = nodes.get(a.get("prompt_id") or "")
                if prov:
                    losses[prov] = losses.get(prov, 0) + 1
        records = [
            ExposureRecord(provider=p, exposures=n, losses=losses.get(p, 0))
            for p, n in exposures.items()
        ]
        records.sort(key=lambda r: r.rate)
        return [r.to_dict() for r in records]
    except Exception:
        return []
