"""The local decision model stays out of the product.

Architectural commitment #1 allows no LLM calls outside councils. The founder
bent it for ONE offline experiment (amd_0271, 2026-09-30): a local decision
model (Ollama /v1/systemone, e.g. Nimble) may score things inside
internal/experiments only. A council ruled every product use -- runtime
annotations, the trust-ledger resolver, ingest -- a break, not a bend
(council_7e875626483f8d7a). This guard keeps the bend where it was granted.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = [ROOT / "src", ROOT / "plugins" / "trinity-local" / "engine"]
# The decision API and its models only. Plain Ollama chat on :11434 is the older
# sanctioned bend (core_gate.py: local model, build time, never judging answers).
DECISION_MODEL = re.compile(r"systemone|\bnimble\b", re.I)


def offenders() -> list[str]:
    hits = []
    for base in SHIPPED:
        for f in base.rglob("*.py"):
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if DECISION_MODEL.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    return hits


def test_no_shipped_code_calls_a_local_decision_model():
    bad = offenders()
    assert not bad, ("A local decision model is referenced from shipped code, which commitment #1 "
                     "forbids outside the founder's offline bend (internal/experiments only):\n  "
                     + "\n  ".join(bad))


def test_the_pattern_would_catch_a_real_call():
    """The guard is only as good as its pattern; prove it matches the ways a call is written."""
    # The first sample is split so the slow-marker scanner does not read it as a real call.
    for line in ('requests' '.post("http://localhost:11434/v1/systemone", json=q)',
                 'MODEL = "nimble"', "url = f'{host}/v1/systemone'"):
        assert DECISION_MODEL.search(line), line
