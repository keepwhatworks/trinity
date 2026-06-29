#!/usr/bin/env python3
"""scripts/descriptor.py — rejection-signal validators for lens-build.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).

The lens-build pipeline has two parts:
  1. Chairman call (LLM) — extracts candidate rejection signals from
     (assistant, user_next_turn) pairs. Orchestrated by the pip-tier
     trinity-local lens-build CLI; not this script's concern.
  2. Deterministic validators — drop chairman-skim labels per the
     spec's per-type rules. THIS SCRIPT.

Validators (from trinity_local.me.turn_pairs._validate_one):
  - COMPRESSION: user word count ≤ model_text/10
  - REDIRECT: model_text is structurally multi-part (numbered /
    bulleted / multi-sentence ≥3)
  - SHARPENING: user_text shares ≥2 keywords with model_text
  - REFRAME: substituted frame persists into next_user_turn (else
    rejected unless no next-turn data)

Pure functions on text — no LLM, no embeddings, just word/keyword/
structure heuristics. Tier-equivalence: identical input → identical
output.

Dual interface:
  - Shebang: `python3 scripts/descriptor.py < input.json`
  - Importable: `from scripts.descriptor import validate_signals`

CLI Input:
  {
    "signals": [
      {"type": "COMPRESSION"|"REDIRECT"|"SHARPENING"|"REFRAME",
       "prompt_id": "...", ...},
      ...
    ],
    "pairs": {
      "<prompt_id>": {
        "assistant_text": "...", "user_text": "...",
        "next_user_text": "..."
      },
      ...
    }
  }

CLI Output:
  {
    "kept": [signal, ...],
    "rejected": [{"signal": signal, "reason": "..."}, ...],
    "kept_count": int,
    "rejected_count": int
  }
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


SCRIPT_NAME = "descriptor"
REQUIREMENTS: list[str] = []  # pure stdlib + the pip tier


def validate_signals(
    signals: list[dict],
    pairs: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Apply per-type validators to a list of chairman-extracted
    rejection signals. Returns (kept_dicts, rejected_dicts).

    Delegates to trinity_local.me.turn_pairs.validate_signals to keep
    the algorithm canonical for v1.0. v1.1 inverts the dependency.
    """
    from trinity_local.me.turn_pairs import (
        validate_signals as _impl,
        RejectionSignal,
    )

    # Convert dict signals to RejectionSignal dataclass instances.
    rejection_signals = []
    for sig in signals:
        rejection_signals.append(RejectionSignal(
            id=sig.get("id", ""),
            type=sig.get("type", ""),
            model_quote=sig.get("model_quote", ""),
            user_substitute=sig.get("user_substitute", ""),
            why_signal=sig.get("why_signal", ""),
            prompt_id=sig.get("prompt_id"),
            basin=sig.get("basin"),
            next_user_turn=sig.get("next_user_turn", ""),
        ))

    kept_signals, rejected_dicts = _impl(rejection_signals, pairs)
    # Convert kept signals back to dicts via their to_dict() method.
    kept_dicts = [s.to_dict() for s in kept_signals]
    return kept_dicts, rejected_dicts


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/descriptor.py",
        description=(
            "Apply deterministic validators to chairman-extracted "
            "rejection signals. Pure functions on text — no LLM."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Dependencies: none (pure stdlib + trinity_local package "
            "for the validator implementation in v1.0; v1.1 inverts "
            "so this script owns the canonical impl).\n\n"
            "Tier-equivalence: deterministic — identical input → "
            "identical output across runs and tiers."
        ),
    )
    parser.add_argument("input", nargs="?", default="-")
    parser.add_argument("--out", "-o", default="-")
    args = parser.parse_args(argv)

    started_at = time.monotonic()
    payload = read_input_json(args.input if args.input != "-" else None)
    if not isinstance(payload, dict):
        print("error: input must be a JSON object", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="validate_signals",
                  outcome="bad_input", detail="input not a JSON object")
        return 2

    signals = payload.get("signals")
    pairs = payload.get("pairs")
    if not isinstance(signals, list):
        print("error: 'signals' must be a list", file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="validate_signals",
                  outcome="bad_input", detail="'signals' not a list")
        return 2
    if not isinstance(pairs, dict):
        print("error: 'pairs' must be a dict keyed by prompt_id",
              file=sys.stderr)
        audit_log(script=SCRIPT_NAME, operation="validate_signals",
                  outcome="bad_input", detail="'pairs' not a dict")
        return 2

    try:
        kept, rejected = validate_signals(signals, pairs)
    except Exception as exc:
        audit_log(
            script=SCRIPT_NAME, operation="validate_signals", outcome="error",
            args={"n_signals": len(signals), "n_pairs": len(pairs)},
            detail=f"{type(exc).__name__}: {exc}",
        )
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    audit_log(
        script=SCRIPT_NAME, operation="validate_signals",
        args={
            "n_signals": len(signals),
            "n_pairs": len(pairs),
            "kept_count": len(kept),
            "rejected_count": len(rejected),
            "elapsed_ms": elapsed_ms,
        },
    )
    write_output_json({
        "kept": kept,
        "rejected": rejected,
        "kept_count": len(kept),
        "rejected_count": len(rejected),
        "elapsed_ms": elapsed_ms,
    }, args.out if args.out != "-" else None)
    return 0


if __name__ == "__main__":
    bootstrap_or_continue(script_name=SCRIPT_NAME, requirements=REQUIREMENTS)
    sys.exit(_cli_main())
