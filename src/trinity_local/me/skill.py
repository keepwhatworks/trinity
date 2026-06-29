"""Render the lens as an agent-loadable `SKILL.md` (the "lens = ambient" move).

SkillOpt (Microsoft, arXiv:2605.23904) validated the paradigm Trinity already
runs: *train the document, not the model* — a frozen agent + an evolving
markdown skill doc loaded as the system prompt. The skill ecosystem
(SKILL.md + MCP: skillz / skillport / Claude Code's native `~/.claude/skills/`)
has converged on the same artifact. Trinity's lens IS that document — but for the
one skill no benchmark can score: the USER's taste. This module emits it in the
ecosystem's format so any skill-aware harness loads it ambiently, with zero tool
call, calibrating every response to how this person actually thinks.

Deliberately SAFE: writes under `~/.trinity/skills/your-taste/SKILL.md` (Trinity's
own state), never silently into `~/.claude/skills/`. The CLI verb prints the
one-line symlink the user runs to make it ambient — a deliberate install step,
not a surprise mutation of their agent config.

Deterministic + LLM-free: composes from the already-built lens artifacts
(core.md prose portrait + taste_signature adjectives + the paired tensions). No
chairman call, no quota. Re-run after a lens rebuild to refresh.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..state_paths import trinity_home

_LENS_SKILL_FLAG = "TRINITY_LENS_SKILL"


def lens_skill_enabled() -> bool:
    """Auto-emit the lens as a SKILL.md at the end of lens-build ONLY when the
    flag is set (default OFF — mirrors TRINITY_LENS_GENERATORS). The render is
    cheap + deterministic (no LLM), but writing the skill on every build is a
    side-effect the user opts into; once on, a harness that symlinked
    ~/.trinity/skills/your-taste into ~/.claude/skills gets the ambient skill
    auto-refreshed whenever the lens changes."""
    return os.environ.get(_LENS_SKILL_FLAG, "").strip().lower() in ("1", "true", "yes", "on")

# Curated calibration guidance for the common taste-signature adjectives (the
# signature is computed from a bounded vocabulary, so a small map covers most;
# anything unmapped renders as a plain "Be <adj>." so the skill never lies by
# omission). Kept short — the agent reads these as operating instructions.
_ADJECTIVE_GUIDANCE: dict[str, str] = {
    "terse": "Be terse — cut preamble, hedging, and restating the question; one tight pass beats three caveated ones.",
    "concrete": "Lead with the concrete artifact (the code, the number, the decision), not a description of it.",
    "decisive": "Give a recommendation, not an option menu — pick, and say why; surface the runner-up only if it's genuinely close.",
    "direct": "Be direct — say the load-bearing thing first; don't bury it under throat-clearing.",
    "rigorous": "Show your work and your verification; don't assert what you haven't checked.",
    "skeptical": "Treat confident claims, expert labels, and green checks as hypotheses until verified.",
    "pragmatic": "Optimize for the thing that ships and works, not the most elegant or complete answer.",
    "analytical": "Reason from primary evidence, not vibes; name the mechanism, not just the conclusion.",
}


def _yaml_dq(s: str) -> str:
    """Emit `s` as a YAML double-quoted scalar so the frontmatter PARSES no matter
    what the description contains. A plain (unquoted) scalar silently breaks YAML
    on a `: ` (read as a nested mapping), a leading `[`/`{`/`"`/`@`/etc., or `#` —
    and a SKILL.md with unparseable frontmatter just never loads (silent failure).
    The value is single-line, so only `\\` and `"` need escaping. No PyYAML dep
    (not a declared runtime dep) — manual + minimal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def skill_dir(home: Path | None = None) -> Path:
    """`~/.trinity/skills/your-taste/` — Trinity's own state, NOT ~/.claude."""
    base = home if home is not None else trinity_home()
    return base / "skills" / "your-taste"


def skill_path(home: Path | None = None) -> Path:
    return skill_dir(home) / "SKILL.md"


def _read_taste_signature(home: Path) -> tuple[list[str], int]:
    p = home / "me" / "taste_signature.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], 0
    if not isinstance(data, dict):
        return [], 0
    adjs = data.get("adjectives")
    adjs = [a for a in adjs if isinstance(a, str)] if isinstance(adjs, list) else []
    n = data.get("n")
    return adjs, (n if isinstance(n, int) else 0)


def _read_tensions(home: Path) -> list[dict]:
    """The paired tensions from me/lenses.json (pole_a/pole_b/failure_a/failure_b)."""
    p = home / "me" / "lenses.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lenses = data.get("lenses") if isinstance(data, dict) else None
    if not isinstance(lenses, list):
        return []
    out = []
    for t in lenses:
        if isinstance(t, dict) and t.get("pole_a") and t.get("pole_b"):
            out.append(t)
    return out


def render_lens_skill(home: Path | None = None) -> str | None:
    """Compose the SKILL.md text from the built lens. Returns None when there's
    no lens to emit (cold home / lens never built) — the caller surfaces the
    cold-start hint rather than writing an empty, misleading skill."""
    base = home if home is not None else trinity_home()
    core_file = base / "core.md"  # core_path() == state_dir()/core.md == ~/.trinity/core.md
    try:
        core = core_file.read_text(encoding="utf-8").strip() if core_file.exists() else ""
    except OSError:
        core = ""
    if not core:
        # core.md is the load-bearing portrait; without it the skill is empty.
        return None

    adjectives, n = _read_taste_signature(base)
    tensions = _read_tensions(base)

    sig_line = " · ".join(adjectives) if adjectives else "your distilled decision-making taste"
    # frontmatter description = the relevance signal the agent reads to decide to load.
    desc = (
        "How this person thinks and what \"good\" looks like for them — distilled by "
        "Trinity from their own cross-provider history. Load before any non-trivial "
        "answer to calibrate depth, tone, structure, and tradeoffs to THIS person"
    )
    if adjectives:
        desc += f". They are {sig_line}."
    else:
        desc += "."

    lines: list[str] = []
    lines.append("---")
    lines.append("name: your-taste")
    lines.append(f"description: {_yaml_dq(desc)}")
    lines.append("---")
    lines.append("")
    lines.append("# Calibrate to the person you're helping")
    lines.append("")
    lines.append(
        "The portrait below is **this person's own decision-making taste**, mined by "
        "Trinity from how they actually rephrase, judge, and decide across Claude, "
        "ChatGPT, and Gemini. Read it as *the person you are assisting* — the \"you\" "
        "in the portrait is **them**, not you. Match it in every substantive reply."
    )
    lines.append("")
    lines.append(f"## In one line\n\n{sig_line}.")
    lines.append("")

    # How to answer them — from the signature adjectives (curated guidance).
    guidance = [
        _ADJECTIVE_GUIDANCE.get(a.lower(), f"Be {a}.") for a in adjectives
    ]
    if guidance:
        lines.append("## How to answer them\n")
        for g in guidance:
            lines.append(f"- {g}")
        lines.append("")

    # How they weigh tradeoffs — the paired tensions (grounded, concrete).
    if tensions:
        lines.append("## How they weigh tradeoffs\n")
        lines.append(
            "They hold each pair below in deliberate tension — they want BOTH, and "
            "they notice the failure mode of over-indexing on either side:\n"
        )
        for t in tensions[:8]:
            a, b = t.get("pole_a"), t.get("pole_b")
            fa, fb = t.get("failure_a"), t.get("failure_b")
            bullet = f"- **{a} ↔ {b}**"
            if fa and fb:
                bullet += f" — pure-{a} reads as *{fa}*; pure-{b} reads as *{fb}*."
            lines.append(bullet)
        lines.append("")

    lines.append("## Their own words (the lens)\n")
    lines.append(core)
    lines.append("")
    lines.append("---")
    provenance = "cross-provider history"
    if n:
        provenance = f"{n} distilled decisions across their cross-provider history"
    lines.append(
        f"*Generated by Trinity (`trinity-local lens-skill`) from {provenance}. "
        "Re-run after a lens rebuild to refresh; the lens is the trainable "
        "artifact — this skill is its agent-loadable projection.*"
    )
    return "\n".join(lines) + "\n"


def write_lens_skill(home: Path | None = None) -> dict:
    """Render + write the SKILL.md to ~/.trinity/skills/your-taste/. Returns a
    JSON-able result with the path and the deliberate install hint. NEVER writes
    to ~/.claude — the user runs the printed symlink to make it ambient."""
    base = home if home is not None else trinity_home()
    text = render_lens_skill(base)
    if text is None:
        return {
            "ok": False,
            "reason": "no lens to emit — run `trinity-local lens` first (core.md is empty)",
        }
    out = skill_path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    from ..utils import atomic_write_text

    atomic_write_text(out, text)
    # The deliberate, user-run install step (per-harness skills dir).
    install_hint = (
        f"ln -s {out.parent} ~/.claude/skills/your-taste   "
        "# (or copy) — then any Claude Code session loads your taste ambiently"
    )
    return {
        "ok": True,
        "path": str(out),
        "bytes": len(text.encode("utf-8")),
        "install_hint": install_hint,
        "ambient": False,  # written to ~/.trinity; user installs deliberately
    }
