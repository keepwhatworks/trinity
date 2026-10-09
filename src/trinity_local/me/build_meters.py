"""The LLM-free records every lens build must leave behind.

- the palate snapshot (`palate_registry.record_direction_snapshot`): freezes the current
  preference direction and scores the acts that arrived under the outgoing one, so the
  prospective palate canary keeps accumulating trials;
- the residual snapshot (`residual_log.record_snapshot`): one prediction-quality row per
  build, so the learning-progress derivative is computable later;
- the lens history (`lens_history.snapshot_lens`): a dated, hashed copy of the lens whenever
  it changed, so a past lens can be read back (amd_0313).

Both used to live only in `commands/me.py::_post_build_hooks`, i.e. only on the manual
`trinity-local lens` path. The MCP server rebuilds the lens in the background
(`cold_start.maybe_kick_lens_refresh` and the first-build kick) through the same pipeline but
never ran them, so from 2026-08-24, when every rebuild came through the background path,
the canary scored no trial and the residual log recorded nothing, with no error anywhere
(found 2026-10-07). Every build path now calls this one function.

None of them calls a model. Each refuses on its own terms (the palate snapshot returns
`ok: False, reason: "needs real embeddings"` under the TF-IDF fallback) and the refusal is
returned to the caller so it can be shown, never swallowed.
"""
from __future__ import annotations


def record_build_meters() -> dict:
    out: dict = {}
    try:
        from .palate_registry import record_direction_snapshot
        out["palate_snapshot"] = record_direction_snapshot()
    except Exception as exc:  # noqa: BLE001 — a meter must never break the build
        out["palate_snapshot"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .residual_log import record_snapshot
        snap = record_snapshot()
        out["residual_snapshot"] = snap if snap else {"ok": False, "reason": "residual snapshot not written"}
    except Exception as exc:  # noqa: BLE001
        out["residual_snapshot"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from .lens_history import snapshot_lens
        out["lens_history"] = snapshot_lens()
    except Exception as exc:  # noqa: BLE001
        out["lens_history"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out
