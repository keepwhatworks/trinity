"""Trajectory lens — diachronic (arc-pair) preference extraction (#182).

Stage 0 (`turn_pairs.py`) is **synchronic**: it classifies a single
(model_response, user_next_turn) gap into REFRAME / REDIRECT / COMPRESSION /
SHARPENING. That sees one correction at a time.

This is the **diachronic** layer: within ONE thread, did the user steer the
SAME direction repeatedly across multiple turns? "Pulled the conversation
toward concrete examples three times" is a *trajectory*, not a one-off — and
it's the asymmetric advantage no within-session memory (incl. Auto-Dream)
can see, because it spans the whole thread's arc.

Detection is **deterministic** (no LLM — Trinity's architectural commitment:
LLM calls only inside councils). We group the already-extracted model_miss
preference acts by their originating transcript, and when the same rejection
*kind* recurs ≥ ``MIN_ARC_LEN`` times within one thread, that's a `TurnArc`.
Aggregating arcs of the same kind across threads gives a `Trajectory` — the
directional-preference record the lens renders.

The chairman-enrichment path (``render_arc_prompt`` / ``parse_trajectories``)
is built + tested here so a future lens-build stage can name each trajectory
in the user's own words; the deterministic aggregation is what renders today.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..utils import stable_id

# Minimum recurrences of one kind within a single thread to count as an arc.
# 2 is a coincidence; 3 is a pattern (same threshold Stage 4 uses to promote
# a basin-spanning tension).
MIN_ARC_LEN = 3

# The four synchronic kinds an arc can be built from (mirrors Stage 0).
_ARC_KINDS = ("REFRAME", "COMPRESSION", "REDIRECT", "SHARPENING")


@dataclass
class TurnArc:
    """A within-thread preference trajectory: the same rejection `kind`
    recurred ``count`` times across one transcript. Diachronic evidence."""

    transcript_id: str
    kind: str
    count: int
    turn_span: int          # last_turn_index - first_turn_index (0 if unknown)
    act_ids: list[str] = field(default_factory=list)
    exemplars: list[str] = field(default_factory=list)  # user_substitute snippets
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = stable_id("arc", self.transcript_id, self.kind)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "transcript_id": self.transcript_id,
            "kind": self.kind,
            "count": self.count,
            "turn_span": self.turn_span,
        }
        if self.act_ids:
            out["act_ids"] = self.act_ids
        if self.exemplars:
            out["exemplars"] = self.exemplars
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TurnArc":
        return cls(
            transcript_id=d.get("transcript_id", ""),
            kind=d.get("kind", ""),
            count=int(d.get("count", 0) or 0),
            turn_span=int(d.get("turn_span", 0) or 0),
            act_ids=list(d.get("act_ids", []) or []),
            exemplars=list(d.get("exemplars", []) or []),
            id=d.get("id", ""),
        )


@dataclass
class Trajectory:
    """A directional preference aggregated across arcs of one kind — the
    diachronic lens entry. e.g. COMPRESSION sustained across 4 threads."""

    kind: str
    thread_count: int       # distinct transcripts exhibiting this arc
    total_pulls: int        # sum of arc counts across those threads
    exemplars: list[str] = field(default_factory=list)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = stable_id("traj", self.kind)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "kind": self.kind,
            "thread_count": self.thread_count,
            "total_pulls": self.total_pulls,
        }
        if self.exemplars:
            out["exemplars"] = self.exemplars
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trajectory":
        return cls(
            kind=d.get("kind", ""),
            thread_count=int(d.get("thread_count", 0) or 0),
            total_pulls=int(d.get("total_pulls", 0) or 0),
            exemplars=list(d.get("exemplars", []) or []),
            id=d.get("id", ""),
        )


def detect_arcs(acts: list, node_lookup: dict[str, tuple]) -> list[TurnArc]:
    """Find within-thread trajectories from model_miss preference acts.

    `acts` are PreferenceActs (only model_miss acts carry a rejection kind +
    a transcript origin). `node_lookup` maps prompt_id → (transcript_id,
    turn_index). An act whose prompt_id doesn't resolve is skipped — it can't
    be placed on a thread's timeline. Within each transcript, a `kind` that
    recurs ≥ MIN_ARC_LEN times becomes one TurnArc. Deterministic; pure."""
    from .preference_acts import MODEL_MISS

    # transcript_id → kind → list of (turn_index, act)
    by_thread: dict[str, dict[str, list]] = {}
    for a in acts:
        if getattr(a, "trigger", None) != MODEL_MISS:
            continue
        kind = (getattr(a, "kind", "") or "").upper()
        if kind not in _ARC_KINDS:
            continue
        pid = getattr(a, "prompt_id", None)
        if not pid or pid not in node_lookup:
            continue
        tid, turn = node_lookup[pid]
        if not tid:
            continue
        by_thread.setdefault(tid, {}).setdefault(kind, []).append((turn, a))

    arcs: list[TurnArc] = []
    for tid, kinds in by_thread.items():
        for kind, entries in kinds.items():
            if len(entries) < MIN_ARC_LEN:
                continue
            entries.sort(key=lambda e: (e[0] if isinstance(e[0], int) else 0))
            turns = [e[0] for e in entries if isinstance(e[0], int)]
            span = (max(turns) - min(turns)) if turns else 0
            exemplars = [
                (getattr(e[1], "privileged", "") or "").strip()[:120]
                for e in entries
                if (getattr(e[1], "privileged", "") or "").strip()
            ][:3]
            arcs.append(TurnArc(
                transcript_id=tid, kind=kind, count=len(entries),
                turn_span=span,
                act_ids=[getattr(e[1], "id", "") for e in entries],
                exemplars=exemplars,
            ))
    # Stable order: strongest (most pulls) first, then kind for determinism.
    arcs.sort(key=lambda x: (-x.count, x.kind, x.transcript_id))
    return arcs


def aggregate_trajectories(arcs: list[TurnArc]) -> list[Trajectory]:
    """Roll arcs of the same kind up across threads into directional
    preferences. A kind seen as an arc in N threads → one Trajectory with
    thread_count=N. Deterministic."""
    by_kind: dict[str, list[TurnArc]] = {}
    for arc in arcs:
        by_kind.setdefault(arc.kind, []).append(arc)
    trajectories: list[Trajectory] = []
    for kind, group in by_kind.items():
        exemplars: list[str] = []
        for arc in group:
            for ex in arc.exemplars:
                if ex and ex not in exemplars:
                    exemplars.append(ex)
        trajectories.append(Trajectory(
            kind=kind,
            thread_count=len(group),
            total_pulls=sum(a.count for a in group),
            exemplars=exemplars[:3],
        ))
    trajectories.sort(key=lambda t: (-t.total_pulls, t.kind))
    return trajectories


def render_trajectory_lines(trajectories: list[Trajectory]) -> list[str]:
    """Markdown lines for the lens.md "Trajectories" section. Empty list
    when there are no trajectories (the section is omitted)."""
    if not trajectories:
        return []
    _human = {
        "REFRAME": "re-framing the question",
        "COMPRESSION": "cutting to the essentials",
        "REDIRECT": "redirecting to the thread that mattered",
        "SHARPENING": "sharpening the claim",
    }
    lines = ["## Trajectories (diachronic — sustained pulls)", ""]
    lines.append(
        "Directions you steered the SAME conversation, repeatedly, across "
        "multiple turns — not one-off corrections but sustained arcs. This "
        "is the signal within-session memory can't see; weight a sustained "
        "trajectory heavily as settled taste."
    )
    lines.append("")
    for t in trajectories:
        gloss = _human.get(t.kind, t.kind.lower())
        threads = f"{t.thread_count} thread{'s' if t.thread_count != 1 else ''}"
        lines.append(
            f"- **{t.kind}** — you kept {gloss}: sustained across {threads} "
            f"({t.total_pulls} pulls)."
        )
        for ex in t.exemplars:
            lines.append(f"  → \"{ex}\"")
    lines.append("")
    return lines


# ── storage ──────────────────────────────────────────────────────────

def arcs_path():
    from .basins import me_dir
    return me_dir() / "arcs.jsonl"


def trajectories_path():
    from .basins import me_dir
    return me_dir() / "trajectories.jsonl"


def _save_jsonl(path, rows: list) -> None:
    from ..utils import atomic_write_text
    body = "\n".join(json.dumps(r.to_dict()) for r in rows)
    atomic_write_text(path, body + "\n" if body else "")


def _load_jsonl(path, cls) -> list:
    if not path.exists():
        return []
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(cls.from_dict(json.loads(line)))
        except (ValueError, TypeError, KeyError):
            continue
    return out


def save_arcs(arcs: list[TurnArc]) -> None:
    _save_jsonl(arcs_path(), arcs)


def load_arcs() -> list[TurnArc]:
    return _load_jsonl(arcs_path(), TurnArc)


def save_trajectories(trajectories: list[Trajectory]) -> None:
    _save_jsonl(trajectories_path(), trajectories)


def load_trajectories() -> list[Trajectory]:
    return _load_jsonl(trajectories_path(), Trajectory)


# ── chairman enrichment path (built + tested; deterministic render is what
#    ships today — this names each trajectory in the user's words when a
#    future lens-build stage wires it in) ──────────────────────────────

def render_arc_prompt(arcs: list[TurnArc]) -> str:
    """Chairman prompt: given detected within-thread arcs, name each as a
    directional preference in the user's voice. One JSON object per line."""
    blocks = []
    for arc in arcs[:30]:
        ex = "; ".join(arc.exemplars) or "(no exemplars)"
        blocks.append(
            f"[{arc.kind} ×{arc.count} over {arc.turn_span} turns in one thread]\n"
            f"  the user repeatedly pulled toward: {ex}"
        )
    body = "\n\n".join(blocks)
    return (
        "Below are within-thread TRAJECTORIES — the same kind of correction "
        "the user made repeatedly across one conversation. Each is a sustained "
        "preference, not a one-off. For each, name the directional preference "
        "in the user's own register. Emit ONE JSON object per line:\n"
        '{"kind": "<REFRAME|COMPRESSION|REDIRECT|SHARPENING>", "direction": '
        '"<≤12-word name for the pull, in the user\'s voice>"}\n'
        "No commentary, no markdown fences.\n\nTRAJECTORIES:\n\n" + body
    )


def parse_trajectories(raw: str) -> list[dict]:
    """Parse the chairman's directional-preference records. Tolerant —
    skips malformed lines; keeps only the four valid kinds with a direction."""
    out: list[dict] = []
    text = (raw or "").strip()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
        except ValueError:
            continue
        kind = (obj.get("kind") or "").strip().upper()
        direction = (obj.get("direction") or "").strip()
        if kind in _ARC_KINDS and direction:
            out.append({"kind": kind, "direction": direction})
    return out
