"""Capture user edits to ``~/.trinity/memories/lens.md`` as preference signal.

When the user opens lens.md and edits a line by hand, that's the strongest
possible taste signal Trinity can collect — stronger than rejection signal
extracted from transcripts, stronger than chairman-derived would_flip_if.
The user is *actively asserting* taste, not just pushing back on something
the model produced.

This module is the capture side of #140. Mechanism:

1. After every successful ``lens-build``, write a snapshot of lens.md to
   ``~/.trinity/me/lens_snapshot.md``.
2. At the start of the *next* ``lens-build``, diff the current lens.md
   against that snapshot. Any difference = user edit since last build.
3. Persist each line-level delta as one JSON line in
   ``~/.trinity/me/lens_edits.jsonl``: ``{ts, op, before, after, source}``.

The feed-back side (next dream cycle reads the JSONL and weights edits
heavier in Stage 2 corpus) is a separate slice.

Edge cases:
- No snapshot file (cold start, or first-ever lens-build): no edits
  captured — there's nothing to diff against. The post-build write
  establishes the baseline for next time.
- Snapshot identical to current: no edits captured.
- lens.md missing: skip silently (lens-build is what creates it).
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from ..utils import now_iso
from .basins import me_dir


def lens_snapshot_path() -> Path:
    """Path to the post-build snapshot of lens.md used for next-build diffs."""
    return me_dir() / "lens_snapshot.md"


def lens_edits_path() -> Path:
    """Append-only log of user edits captured between builds."""
    return me_dir() / "lens_edits.jsonl"


@dataclass
class LensEdit:
    """One line-level delta captured from a user edit to lens.md.

    ``op`` is one of:
    - ``"add"``: a new line appeared (``before`` is empty, ``after`` has it)
    - ``"remove"``: a line was deleted (``before`` has it, ``after`` empty)

    Modifications surface as adjacent ``remove`` + ``add`` pairs; we don't
    try to pair them at capture time — the chairman reading the JSONL can
    see the sequence and infer intent. Avoids brittle pairing heuristics.
    """
    ts: str
    op: str
    before: str
    after: str
    source: str = "user_edit"

    def to_dict(self) -> dict[str, str]:
        return {
            "ts": self.ts,
            "op": self.op,
            "before": self.before,
            "after": self.after,
            "source": self.source,
        }


def _diff_to_edits(old_text: str, new_text: str, ts: str) -> list[LensEdit]:
    """Compute line-level edits between two lens.md snapshots.

    Uses ``difflib.unified_diff`` (stdlib, no deps). Skips header/hunk
    metadata; emits one ``LensEdit`` per +/- line.
    """
    edits: list[LensEdit] = []
    for line in unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        lineterm="",
        n=0,
    ):
        if not line or line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            edits.append(LensEdit(ts=ts, op="remove", before=line[1:], after=""))
        elif line.startswith("+"):
            edits.append(LensEdit(ts=ts, op="add", before="", after=line[1:]))
    return edits


def capture_lens_edits(current_lens_text: str | None = None) -> list[LensEdit]:
    """Diff current lens.md against the post-last-build snapshot.

    Pass ``current_lens_text`` to compare against an in-memory version
    (useful for tests / for callers that have already read lens.md).
    Otherwise reads ``memories_dir()/lens.md`` from disk.

    Returns the captured edits (empty list when no snapshot or no diff)
    and appends them to ``lens_edits.jsonl``. Idempotent: re-running
    against the same input produces duplicates, so callers should only
    invoke once per build cycle.
    """
    from ..state_paths import memories_dir

    if current_lens_text is None:
        lens_path = memories_dir() / "lens.md"
        if not lens_path.exists():
            return []
        try:
            current_lens_text = lens_path.read_text(encoding="utf-8")
        except OSError:
            return []

    snapshot = lens_snapshot_path()
    if not snapshot.exists():
        # First-ever build, or snapshot deleted — no baseline to diff
        # against. Establishing the baseline happens in
        # write_lens_snapshot() after lens-build completes.
        return []

    try:
        old_text = snapshot.read_text(encoding="utf-8")
    except OSError:
        return []

    if old_text == current_lens_text:
        return []

    edits = _diff_to_edits(old_text, current_lens_text, ts=now_iso())
    if not edits:
        return []

    _append_edits(edits)
    return edits


def pending_lens_edits_count() -> int:
    """Return how many uncommitted line-diffs sit between lens.md and the
    post-last-build snapshot (#140 slice 3, launchpad surfacing).

    This is the *live* signal: the user opened lens.md, edited some lines,
    and hasn't run lens-build yet. The launchpad shows the count as
    "N edits — next dream picks them up" so they know the loop is closed.

    Returns 0 when:
    - lens.md doesn't exist (cold install)
    - snapshot doesn't exist (cold start — pre-first build baseline)
    - current matches snapshot (no pending edits)
    """
    from ..state_paths import memories_dir

    lens_path = memories_dir() / "lens.md"
    snapshot = lens_snapshot_path()
    if not lens_path.exists() or not snapshot.exists():
        return 0
    try:
        current = lens_path.read_text(encoding="utf-8")
        previous = snapshot.read_text(encoding="utf-8")
    except OSError:
        return 0
    if current == previous:
        return 0
    return len(_diff_to_edits(previous, current, ts=""))


def _append_edits(edits: list[LensEdit]) -> None:
    import json

    path = lens_edits_path()
    with path.open("a", encoding="utf-8") as fh:
        for edit in edits:
            fh.write(json.dumps(edit.to_dict()) + "\n")


def write_lens_snapshot(lens_text: str) -> None:
    """Pin the current lens.md as the baseline for next build's diff.

    Called by lens-build after successfully writing lens.md. Any user
    edits between this call and the next lens-build will surface as
    edits in ``capture_lens_edits()``.
    """
    lens_snapshot_path().write_text(lens_text, encoding="utf-8")


def load_lens_edits_as_decisions(basins: list) -> list:
    """Translate captured lens.md edits into high-weight Decision objects
    for Stage 2 corpus augmentation (#140 slice 2).

    Pairing logic — applied at load time so the on-disk capture stays
    simple (each line-level delta is one JSONL entry):

    - Adjacent ``remove`` followed immediately by ``add`` from the same
      timestamp = user modified that line. Emit one Decision with
      ``privileged=<new line>``, ``sacrificed=<old line>``,
      ``valence="correction"``.
    - Lone ``add`` (no remove preceding it in the same ts batch) = user
      asserted a new principle. ``privileged=<new line>``,
      ``sacrificed="(absent before user added it)"``,
      ``valence="satisfaction"``.
    - Lone ``remove`` = user actively removed a principle.
      ``privileged="(user removed this lens)"``,
      ``sacrificed=<old line>``, ``valence="cost"``.

    Weight 3.0 — stronger than user_logged decisions (2.0) because the
    user edited the *lens itself*, not a council outcome. Hierarchy:
    direct lens edit (3.0) > live decision-log capture (2.0) >
    transcript-extracted (1.0).

    ``basins`` is accepted for API symmetry with ``load_decision_log``;
    lens edits don't carry prompt-id context so basin tagging is left
    None (pair-miner uses cross-basin matching as usual).
    """
    from .decisions import Decision

    edits = _load_all_edits()
    if not edits:
        return []

    decisions: list[Decision] = []
    next_id = 1

    # Group by timestamp (one build cycle's batch of edits) so we only
    # pair remove+add WITHIN the same batch, not across builds.
    batches: dict[str, list[LensEdit]] = {}
    order: list[str] = []
    for edit in edits:
        if edit.ts not in batches:
            batches[edit.ts] = []
            order.append(edit.ts)
        batches[edit.ts].append(edit)

    for ts in order:
        batch = batches[ts]
        i = 0
        while i < len(batch):
            e = batch[i]
            if (
                e.op == "remove"
                and i + 1 < len(batch)
                and batch[i + 1].op == "add"
            ):
                # Modify: paired remove+add → correction
                paired = batch[i + 1]
                decisions.append(
                    Decision(
                        id=f"le_{next_id:03d}",
                        privileged=paired.after.strip() or "(empty)",
                        sacrificed=e.before.strip() or "(empty)",
                        valence="correction",
                        basin=None,
                        verbatim=f"User edit: '{e.before}' → '{paired.after}'",
                        source="lens_edit",
                        logged_at=ts,
                        weight=3.0,
                    )
                )
                next_id += 1
                i += 2
                continue
            if e.op == "add":
                decisions.append(
                    Decision(
                        id=f"le_{next_id:03d}",
                        privileged=e.after.strip() or "(empty)",
                        sacrificed="(absent before user added it)",
                        valence="satisfaction",
                        basin=None,
                        verbatim=f"User added to lens: '{e.after}'",
                        source="lens_edit",
                        logged_at=ts,
                        weight=3.0,
                    )
                )
                next_id += 1
            elif e.op == "remove":
                decisions.append(
                    Decision(
                        id=f"le_{next_id:03d}",
                        privileged="(user removed this lens)",
                        sacrificed=e.before.strip() or "(empty)",
                        valence="cost",
                        basin=None,
                        verbatim=f"User removed from lens: '{e.before}'",
                        source="lens_edit",
                        logged_at=ts,
                        weight=3.0,
                    )
                )
                next_id += 1
            i += 1

    return decisions


def _load_all_edits() -> list[LensEdit]:
    """Internal: read the full JSONL without limit/reversal (for
    decision-translation, where ordering by ts ascending matters)."""
    import json

    path = lens_edits_path()
    if not path.exists():
        return []
    out: list[LensEdit] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):  # guard_shape_not_just_parse: .get below crashes on a non-dict line
                continue
            out.append(
                LensEdit(
                    ts=obj.get("ts", ""),
                    op=obj.get("op", ""),
                    before=obj.get("before", ""),
                    after=obj.get("after", ""),
                    source=obj.get("source", "user_edit"),
                )
            )
    except OSError:
        return []
    return out


def load_recent_edits(limit: int = 50) -> list[LensEdit]:
    """Read back recent edits — used by the launchpad surfacing
    ("N edits since last dream"). Returns most-recent-first."""
    edits = _load_all_edits()
    edits.reverse()
    return edits[:limit]
