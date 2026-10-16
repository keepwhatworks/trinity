"""The chairman pick (chairman_picker.py). The routing stack that lived here (heuristic,
k-NN advisory, fallback ranker) served only the ask/route MCP handlers, deleted 2026-10-15."""
from __future__ import annotations

from .chairman_picker import chairman_pick_reason, predict_strongest_chairman

__all__ = ["chairman_pick_reason", "predict_strongest_chairman"]
