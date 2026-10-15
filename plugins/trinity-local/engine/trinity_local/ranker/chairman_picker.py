"""Pick the chairman: a fixed order, Claude, then Codex, then any other chair.

WHY A FIXED ORDER (coherence review 2026-10-15). The pick used to be a sigmoid
blend of a personal routing table and global benchmark scores. Neither half
worked where users run Trinity:

- The personal half never fired. The table is keyed on the chairman's own
  free-form task labels; the picker read it with `guess_task_type()`'s five
  labels. Coverage was 0/716 (res_018), and council_df5e0f2dcc5acc76 ruled to
  cut its decision authority and keep routing.json as a display scoreboard.
- The global half read `data/reference_evals.json`, which exists only in a
  source checkout. Installed builds fell back to a hardcoded HumanEval table
  that ranks Gemini first, so every installed coding council was chaired by
  Gemini, while the repo's tests, run from a checkout, asserted Codex.

The standing rule is a fixed strong chair, Claude or GPT, never Gemini: in the
chairman-selection experiment the chairs agreed 8/10 with no self-preference,
and Gemini judged too fast (~9 s). Gemini still chairs as a last resort when no
preferred chair is enabled; `providers.chairman_fallback_order` applies the same
order when a chair fails.

Manual override (`--primary-provider`, `primary_provider=`) bypasses this.
"""
from __future__ import annotations

import math

from ..providers import chair_order
from ..task_types import guess_task_type

# Kept only for the launchpad's "X% personalized" column, which reports a blend
# this module no longer runs; that column goes in the UI batch of the same review.
PERSONAL_MIDPOINT = 5
PERSONAL_STEEPNESS = 2.0


def sigmoid_alpha(n: int) -> float:
    return 1.0 / (1.0 + math.exp(-(n - PERSONAL_MIDPOINT) / PERSONAL_STEEPNESS))


def predict_strongest_chairman(task_text: str, *, available_providers: list[str]) -> str:
    """The chair for this council, from the caller's enabled providers ("" if none).

    `task_text` is accepted for the callers' signature; the pick does not read it."""
    order = chair_order(available_providers)
    return order[0] if order else ""


def chairman_pick_reason(task_text: str, *, available_providers: list[str]) -> dict[str, object]:
    """The pick plus why, for callers that surface it."""
    chair = predict_strongest_chairman(task_text, available_providers=available_providers)
    if not chair:
        return {"chairman": "", "source": "none", "task_type": ""}
    return {"chairman": chair, "source": "fixed_order", "task_type": guess_task_type(task_text)}
