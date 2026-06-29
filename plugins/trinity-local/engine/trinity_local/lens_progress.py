"""Live lens-build progress + cooperative cancel (#242/#252 first-run UX).

The lens build is a multi-minute, multi-stage chairman pipeline. Until now it
printed stages to stdout only — invisible on the launchpad, unstoppable. This
module is the observability + control surface:

- ``write_progress`` records the current stage to a single JSON file that the
  launchpad polls; ``read_progress`` reads it back.
- ``request_cancel`` drops a flag file the build checks BETWEEN stages
  (``raise_if_canceled``) — a clean abort that never interrupts an in-flight
  chairman call (no half-written state, no orphaned subprocess).

All best-effort: a progress/flag write that fails never breaks the build.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .state_paths import state_dir
from .utils import now_iso

# Ordered pipeline stages with a rough cumulative %-complete for the bar. The
# percentages are coarse wall-clock estimates (Stage 3 pair-mining is the single
# heaviest chairman call), not measured — they exist to make the bar move
# monotonically, not to be accurate to the second.
STAGES: list[tuple[str, str, int]] = [
    ("scan", "Reading your transcripts", 5),
    ("embed", "Embedding your prompts", 15),
    ("basins", "Clustering topics", 25),
    ("stage0", "Mining where models missed your intent", 45),
    ("stage2", "Extracting your decisions", 60),
    ("stage3", "Finding your taste tensions", 85),
    ("registry", "Accumulating the lens", 92),
    ("distill", "Distilling your core memory", 98),
    ("done", "Lens ready", 100),
]
_STAGE_PCT = {key: pct for key, _label, pct in STAGES}
_STAGE_LABEL = {key: label for key, label, _pct in STAGES}


@dataclass
class LensProgress:
    stage: str            # one of STAGES keys (or "")
    label: str            # human label for the stage
    pct: int              # 0-100 cumulative estimate
    status: str           # running | complete | failed | canceled
    started_at: str
    updated_at: str
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LensBuildCanceled(Exception):
    """Raised by ``raise_if_canceled`` when the user requested a stop."""


def progress_path():
    return state_dir() / "lens_build_progress.json"


def cancel_path():
    return state_dir() / "lens_build.cancel"


def write_progress(
    stage: str, *, status: str = "running", error: str | None = None
) -> None:
    """Record the current stage. ``stage`` should be a STAGES key; unknown keys
    still write (pct 0) so callers can't crash the build on a typo."""
    try:
        prior = read_progress()
        started = prior.started_at if (prior and status == "running") else now_iso()
        prog = LensProgress(
            stage=stage,
            label=_STAGE_LABEL.get(stage, stage),
            pct=_STAGE_PCT.get(stage, 0),
            status=status,
            started_at=started,
            updated_at=now_iso(),
            error=error,
        )
        progress_path().write_text(
            json.dumps(prog.to_dict(), indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def read_progress() -> LensProgress | None:
    try:
        raw = json.loads(progress_path().read_text(encoding="utf-8"))
        return LensProgress(
            stage=raw.get("stage", ""),
            label=raw.get("label", ""),
            pct=int(raw.get("pct", 0)),
            status=raw.get("status", ""),
            started_at=raw.get("started_at", ""),
            updated_at=raw.get("updated_at", ""),
            error=raw.get("error"),
        )
    except Exception:
        return None


def request_cancel() -> None:
    """Drop the cancel flag; the running build aborts at the next stage edge."""
    try:
        cancel_path().write_text(now_iso(), encoding="utf-8")
    except Exception:
        pass


def clear_cancel() -> None:
    try:
        cancel_path().unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def is_canceled() -> bool:
    return cancel_path().exists()


def raise_if_canceled() -> None:
    """Call at each stage boundary. Raises LensBuildCanceled if the user asked
    to stop — a clean abort that never interrupts a chairman call mid-flight."""
    if is_canceled():
        raise LensBuildCanceled("lens build canceled by user")
