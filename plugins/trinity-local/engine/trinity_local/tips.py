"""Staged onboarding tips — introduce the next feature AFTER the first win, one
rung at a time, in dependency order.

The funnel is fusion-first: a fresh user gets the free council (the win), then the
ladder nudges them up toward the lens add-on. Each rung declares a prerequisite
read from LIVE ~/.trinity state, so the ladder ORDER *is* the dependency graph —
a rung can't surface until its prereq is achieved, and a step done out-of-band
(the user ran the command directly) auto-skips. Exactly one rung surfaces per
interaction; once shown, a rung is marked seen and never nags again.

Trimmed launch set (founder 2026-06-16):
  1. create-lens     — the collapsed enable+embedder+build action ("create your lens")
  2. add-web-history — enrich the CLI-only lens with ChatGPT/Gemini/claude.ai history
  3. view-lens       — once it's built, show the user what the chairman reads
  4. lens-health     — self-test the lens isn't degenerate
Telemetry (default-on, disclosed in terms), routing-picks, and generators were
cut as too-early. The per-council "open the council page?" prompt is a separate
EVENT (mcp_features.elicit), not a ladder rung.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Tip:
    key: str
    kind: str            # "text" (informational) | "elicit" (actionable, one-click)
    message: str
    cta: str             # the command / action the tip points at
    prereq: Callable[["TipState"], bool]
    # once=True → show this rung a single time, then advance (it has no natural
    # "adopted" signal, so without this it'd fire forever and STARVE later rungs).
    # once=False → persist until the prereq flips (the conversion gate: create-lens
    # keeps nudging until the user opts in).
    once: bool = False

    def render(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "message": self.message, "cta": self.cta}


@dataclass
class TipState:
    councils_run: int = 0
    lens_enabled: bool = False
    lens_built: bool = False
    web_surfaces_present: bool = False


# ── live state (all signals are cheap reads; never raises) ───────────────────


def gather_state() -> TipState:
    s = TipState()
    try:
        from .state_paths import council_outcomes_dir

        d = council_outcomes_dir()
        if d.is_dir():
            s.councils_run = sum(1 for _ in d.glob("council_*.json"))
    except Exception:
        pass
    try:
        from .lens_addon import lens_enabled

        s.lens_enabled = lens_enabled()
    except Exception:
        pass
    try:
        from .state_paths import lens_path

        p = lens_path()
        s.lens_built = p.exists() and len(p.read_text(encoding="utf-8").strip()) > 50
    except Exception:
        pass
    try:
        from .state_paths import state_dir

        conv = state_dir() / "conversations"
        s.web_surfaces_present = conv.is_dir() and any(conv.iterdir())
    except Exception:
        pass
    return s


# ── the ladder (order == dependency order) ───────────────────────────────────

LADDER: tuple[Tip, ...] = (
    Tip(
        key="create-lens",
        kind="elicit",
        message=("You just fused Claude, GPT, and Gemini — free, on your own subscriptions. "
                 "Want the answers in YOUR voice? Build your taste lens from your history."),
        cta="trinity-local lens-setup",
        # First win landed, and they haven't started a lens yet.
        prereq=lambda s: s.councils_run >= 1 and not s.lens_enabled and not s.lens_built,
    ),
    Tip(
        key="add-web-history",
        kind="text",
        message=("Your lens learns from your CLIs. Add your ChatGPT / Gemini / claude.ai history "
                 "so it sees all six surfaces — the cross-provider corpus no single lab can."),
        cta="trinity-local import-export <takeout>   (or install the capture extension)",
        # They're in the lens flow, but the web surfaces aren't captured yet.
        prereq=lambda s: s.lens_enabled and not s.web_surfaces_present,
        once=True,   # nudge enrichment once; don't block view-lens/lens-health behind it
    ),
    Tip(
        key="view-lens",
        kind="elicit",
        message="Your taste lens is ready — this is what the chairman reads on every council.",
        cta="trinity-local lens-show",
        prereq=lambda s: s.lens_built,
        once=True,   # the "viewed" payoff has no natural flip — show once, then advance
    ),
    Tip(
        key="lens-health",
        kind="elicit",
        message="Make sure your lens reflects you and isn't degenerate — a 5-second self-check.",
        cta="trinity-local lens-health",
        prereq=lambda s: s.lens_built,
        once=True,   # likewise — show once
    ),
)


# ── seen-set persistence (the only new state) ────────────────────────────────


def _tips_seen_path():
    from .state_paths import state_dir

    return state_dir() / "settings" / "tips_seen.json"


def _seen() -> set[str]:
    try:
        raw = json.loads(_tips_seen_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("seen"), list):
            return {str(k) for k in raw["seen"]}
    except Exception:
        pass
    return set()


def mark_tip_seen(key: str) -> None:
    try:
        seen = _seen()
        if key in seen:
            return
        seen.add(key)
        p = _tips_seen_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"seen": sorted(seen)}), encoding="utf-8")
    except Exception:
        pass


# ── the entry point ──────────────────────────────────────────────────────────


def next_tip(*, mark: bool = False) -> dict | None:
    """The first ladder rung whose prereq is met (live state) and that hasn't been
    shown yet — or None. One per interaction. `mark=True` records it as seen (so
    the caller that surfaces it won't see it again). Never raises."""
    try:
        state = gather_state()
        seen = _seen()
        for tip in LADDER:
            if tip.key in seen:
                continue
            try:
                if tip.prereq(state):
                    # once-tips self-mark so the ladder ADVANCES (else they fire
                    # forever and starve later rungs); persistent tips only mark
                    # when the caller asks (mark=True).
                    if mark or tip.once:
                        mark_tip_seen(tip.key)
                    return tip.render()
            except Exception:
                continue
    except Exception:
        pass
    return None
