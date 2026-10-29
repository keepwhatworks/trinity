#!/usr/bin/env python3
"""scripts/signature.py — vocabulary signature distillation.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

The vocabulary signature is what makes a term YOUR term: homonyms, words you use with
multiple distinct in-corpus senses (two-means split on context embeddings). (A synonyms
operation wrapped vocabulary.find_synonyms, which was deleted 2026-05-30; it raised ImportError
until it was removed 2026-10-28.)

Wraps trinity_local.vocabulary.find_homonyms for v1.0;
v1.1 inverts so this script owns the canonical implementation.

Dual interface:
  - Shebang: `python3 scripts/signature.py < input.json`
  - Importable: `from scripts.signature import find_homonyms`

CLI Input:
  {"token_contexts": {"<token>": [{"text": "...", "vector": [...]}, ...]},
   "min_freq": 5, "top_n": 50, "operation": "homonyms"}

CLI Output (homonyms):
  {"homonyms": [{"token": "...", "bimodality": float, "n_contexts": int}, ...]}
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from scripts._runtime import (
    audit_log,
    bootstrap_or_continue,
    read_input_json,
    write_output_json,
)


SCRIPT_NAME = "signature"
REQUIREMENTS = ["numpy>=1.26"]


def find_homonyms(token_contexts: dict, *, min_freq: int = 5, top_n: int = 50) -> list[dict]:
    """Find tokens with high two-means bimodality on their context embeddings.

    Input shape: {"<token>": [{"text": "...", "vector": [...]}, ...]}
    Returns: [{"token": str, "bimodality": float, "n_contexts": int}, ...]
    """
    from trinity_local.vocabulary import find_homonyms as _impl

    # Convert script-side shape (list of {text, vector} per token) to
    # the pip tier's shape (list of context tuples). The pip tier
    # accepts a flat sequence; we adapt.
    contexts_for_impl: dict = {}
    for token, contexts in token_contexts.items():
        # Pip tier expects a list of tuples (text, vector) OR objects
        # with attribute access. We use named-tuple-like dicts.
        contexts_for_impl[token] = [
            type("Ctx", (), {"text": c.get("text", ""), "vector": c.get("vector", [])})()
            for c in contexts
        ]

    ranked = _impl(contexts_for_impl, top_n=top_n)
    return [
        {"token": t, "bimodality": float(score), "n_contexts": n}
        for t, score, n in ranked
    ]


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/signature.py",
        description=(
            "Distill the user's vocabulary signature: homonyms (one "
            "token, multiple senses)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dependencies (auto-installed on first run):\n"
            f"  {chr(10).join('  ' + r for r in REQUIREMENTS)}"
        ),
    )
    parser.add_argument("input", nargs="?", default="-")
    parser.add_argument("--out", "-o", default="-")
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    payload = read_input_json(args.input if args.input != "-" else None)
    if not isinstance(payload, dict):
        print("error: input must be a JSON object", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="signature",
                  outcome="bad_input", detail="not a JSON object")
        return 2

    operation = payload.get("operation", "homonyms")
    top_n = int(payload.get("top_n", 50))

    try:
        if operation == "homonyms":
            token_contexts = payload.get("token_contexts", {})
            min_freq = int(payload.get("min_freq", 5))
            homonyms = find_homonyms(token_contexts, min_freq=min_freq, top_n=top_n)
            result = {"homonyms": homonyms, "operation": "homonyms"}
        else:
            print(f"error: unknown operation {operation!r}", file=sys.stderr)
            audit_log(script=SCRIPT_NAME, operation="signature",
                      outcome="bad_input", detail=f"unknown op {operation!r}")
            return 2
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation=operation, outcome="error",
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation=operation,
        args={"top_n": top_n, "elapsed_ms": elapsed_ms},
    )
    result["elapsed_ms"] = elapsed_ms
    write_output_json(result, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
