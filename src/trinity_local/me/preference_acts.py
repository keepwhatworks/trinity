"""Unified preference-act evidence type — Stage 1 of the EXTRACT
unification (docs/lens-redesign.md beauty audit).

The lens pipeline extracts user taste through two passes that are really
one shape:

- **Stage 0 rejections** (`RejectionSignal`): the model offered X, the
  user substituted Y. REFRAME / REDIRECT / COMPRESSION / SHARPENING.
- **Stage 2 decisions** (`Decision`): the user privileged value A over
  value B, with a valence (satisfaction / regret / …).

A rejection *is* a decision — the user privileged their substitute over
the model's offering. Decisions ⊇ rejections; a rejection is the
model-miss-triggered subset. The beautiful form is **one evidence type
with a `trigger` discriminator**:

- `trigger="model_miss"`   ← rejections (model got it wrong, user fixed it)
- `trigger="self_expressed"` ← decisions (user stated a trade-off directly)

This module is the **read/type layer** of the unification (Strangler-Fig
step 1): it introduces `PreferenceAct` + adapters from the two existing
shapes + a unified reader over the two existing on-disk stores. The
writers (Stage 0 → rejections.jsonl, Stage 2 → decisions.jsonl), the
eval harness, and provider-import are all UNCHANGED at this stage — they
keep their current schemas. Later stages merge the extraction pass and
migrate storage. Doing it this way means each stage is independently
shippable and reversible, and the risky storage migration comes last,
after the type is proven on real data.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

# Trigger discriminator values.
MODEL_MISS = "model_miss"
SELF_EXPRESSED = "self_expressed"

# q-field causal-status values (the constitution's φ(r)=(c,q,m) q-component — the
# confound guard). Labeled OBSERVATIONALLY at lens-build from the correction context,
# never from a user pick. UNCERTAIN is the safe default: an unlabeled or thin-evidence
# act is never treated as proof that its axis was the operative driver, so the lens
# can't enshrine a confound (the "learned brevity when the real axis was statute-first"
# failure). See me/constitution.py + the q-labeling in me_builder Stage 2.
Q_OPERATIVE = "OPERATIVE"
Q_CONFOUND = "CONFOUND"
Q_UNCERTAIN = "UNCERTAIN"
_Q_STATUSES = (Q_OPERATIVE, Q_CONFOUND, Q_UNCERTAIN)


@dataclass
class PreferenceAct:
    """One act of the user expressing taste — the union of a rejection and
    a decision. `privileged` is what the user chose; `sacrificed` is what
    was given up. `trigger` says which extraction surfaced it."""

    id: str
    trigger: str  # MODEL_MISS | SELF_EXPRESSED
    privileged: str
    sacrificed: str
    # For model_miss: the rejection type (REFRAME/REDIRECT/COMPRESSION/
    # SHARPENING). For self_expressed: the decision valence (satisfaction/
    # regret/unresolved/correction/cost). One "category of this act" field.
    kind: str = ""
    why: str = ""  # why_signal (rejection) | would_flip_if (decision)
    prompt_id: str | None = None
    # Inline original prompt, used when `prompt_id` doesn't resolve to a local
    # prompt-node. Provider-imported rejections (eval-import / import_provider_
    # memory) have no prompt_id — the provider doesn't know our node ids — but
    # CAN supply the original prompt; carrying it here lets eval-build score them
    # instead of dropping them as unresolved (#280). Empty for lens-build acts
    # (their prompt_id resolves locally).
    prompt_text: str = ""
    # For model_miss: the ORIGINAL question the rejected answer responded to (the
    # prior user turn). Stage 0 carries it so the eval reads it directly instead of
    # re-walking the transcript index (the route-around the accretion audit flagged).
    # Empty for self_expressed acts and for older acts extracted before this field.
    question_text: str = ""
    basin: str | None = None
    # For model_miss: the user's next turn (REFRAME persistence context).
    # For self_expressed: the verbatim user words from that moment.
    context: str = ""
    source: str = "lens-build"  # provenance; decisions carry transcript/user_logged/lens_edit
    weight: float = 1.0  # pair-mining evidence weight (user_logged decisions = 2.0)
    # q-field (φ(r) causal status — the confound guard). `q_axis` = the correction_lens
    # axis this act moved (e.g. "terse↔verbose"); `q_status` = whether that axis was the
    # OPERATIVE driver, a CONFOUND, or UNCERTAIN. Empty = unlabeled → read as UNCERTAIN via
    # ``causal_status()`` (the safe default: never enshrine an un-attributed axis).
    # Populated observationally at lens-build (Phase B); old ledger lines carry neither and
    # load clean.
    q_axis: str = ""
    q_status: str = ""

    def causal_status(self) -> str:
        """The act's causal status, defaulting to UNCERTAIN for unlabeled/blank/unknown —
        the safe default so an un-attributed (or thin-evidence) axis is never treated as the
        operative driver of the preference."""
        return self.q_status if self.q_status in _Q_STATUSES else Q_UNCERTAIN

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "trigger": self.trigger,
            "privileged": self.privileged,
            "sacrificed": self.sacrificed,
        }
        # Filter defaults/empties to keep the serialized line compact, same
        # convention as RejectionSignal/Decision.
        if self.kind:
            out["kind"] = self.kind
        if self.why:
            out["why"] = self.why
        if self.prompt_id:
            out["prompt_id"] = self.prompt_id
        if self.prompt_text:
            out["prompt_text"] = self.prompt_text
        if self.question_text:
            out["question_text"] = self.question_text
        if self.basin:
            out["basin"] = self.basin
        if self.context:
            out["context"] = self.context
        if self.source != "lens-build":
            out["source"] = self.source
        if self.weight != 1.0:
            out["weight"] = self.weight
        if self.q_axis:
            out["q_axis"] = self.q_axis
        if self.q_status:
            out["q_status"] = self.q_status
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PreferenceAct":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def from_rejection(r) -> PreferenceAct:
    """RejectionSignal → PreferenceAct. The user substituted their phrasing
    for the model's, so they privileged the substitute over the quote."""
    return PreferenceAct(
        id=r.id,
        trigger=MODEL_MISS,
        privileged=r.user_substitute,
        sacrificed=r.model_quote,
        kind=r.type,
        why=r.why_signal,
        prompt_id=r.prompt_id,
        prompt_text=getattr(r, "prompt_text", "") or "",
        question_text=getattr(r, "question_text", "") or "",
        basin=r.basin,
        context=r.next_user_turn,
        source="lens-build",
    )


def to_rejection(a: "PreferenceAct"):
    """PreferenceAct (model_miss) → RejectionSignal — the inverse of
    ``from_rejection``. Lets lens-build's delta-extraction (#210) reload the
    previously-extracted rejections from the ledger and merge new ones into
    them without re-running Stage 0 over the whole corpus. Lossless for
    model_miss acts (id is content-stable, so a re-extraction collapses onto
    the same row). Imported lazily to avoid a turn_pairs↔preference_acts
    import cycle."""
    from .turn_pairs import RejectionSignal

    return RejectionSignal(
        id=a.id,
        type=a.kind,
        model_quote=a.sacrificed,
        user_substitute=a.privileged,
        why_signal=a.why,
        prompt_id=a.prompt_id,
        prompt_text=a.prompt_text,
        question_text=a.question_text,
        basin=a.basin,
        next_user_turn=a.context,
    )


def from_decision(d) -> PreferenceAct:
    """Decision → PreferenceAct. The decision already names privileged vs
    sacrificed directly; the valence becomes the `kind`."""
    return PreferenceAct(
        id=d.id,
        trigger=SELF_EXPRESSED,
        privileged=d.privileged,
        sacrificed=d.sacrificed,
        kind=d.valence,
        why=d.would_flip_if,
        prompt_id=d.prompt_id,
        basin=d.basin,
        context=d.verbatim,
        source=d.source,
        weight=d.weight,
    )


def iter_preference_acts() -> list[PreferenceAct]:
    """The unified read layer: every preference act, model-miss and
    self-expressed, as one stream. Order: model_miss first, then
    self_expressed.

    EXTRACT-unification Stage 4b (legacy retirement): reads the unified
    ledger (``preference_acts.jsonl``) as the SOLE store. The legacy
    rejections.jsonl + decisions.jsonl stores were retired here — every
    writer (Stage 0/2 lens-build, eval-import) now writes only the ledger,
    so there's nothing left to merge. Pure read."""
    acts = load_preference_acts()
    acts.sort(key=lambda a: 0 if a.trigger == MODEL_MISS else 1)
    return acts


def preference_acts_path():
    """``~/.trinity/me/preference_acts.jsonl`` — the unified ledger, the
    SOLE store of every preference act (EXTRACT-unification complete at
    #209). Refreshed by lens-build / lens-resync; every reader sources it.
    The legacy rejections.jsonl + decisions.jsonl stores were retired in
    #209 (a one-time `_migrate_legacy_preference_stores` recovers them into
    the ledger on the next lens-build/resync for upgrades)."""
    from .basins import me_dir

    return me_dir() / "preference_acts.jsonl"


def save_preference_acts(acts: list[PreferenceAct], *, allow_shrink: bool = False):
    """Write the unified ledger atomically (one JSON object per line).

    Carries the #194 clobber guard, because this ledger is on its way to
    becoming the source of truth: refuse to overwrite a populated ledger
    with a cliff-drop (empty when >= _CLOBBER_MIN_EXISTING rows exist, or
    below _CLOBBER_MIN_FRACTION of the existing count) — almost always a
    transient empty build, not a real shrink. The live ledger is
    preserved and the would-be result is stashed to a `.degenerate`
    sidecar. `allow_shrink=True` is the escape hatch for a genuine
    shrink. The callers (lens-build / lens-resync) wrap this best-effort,
    so a raised guard preserves the existing ledger on disk and the build
    proceeds with the prior ledger contents (the legacy
    rejections.jsonl / decisions.jsonl stores were retired in #209 — the
    unified ledger is the sole store now)."""
    import json

    from ..utils import atomic_write_text
    from .turn_pairs import (
        _CLOBBER_MIN_EXISTING,
        _CLOBBER_MIN_FRACTION,
        DegenerateExtractionError,
    )

    path = preference_acts_path()
    existing = 0
    if path.exists():
        try:
            existing = sum(
                1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()
            )
        except OSError:
            existing = 0
    floor = max(1, int(existing * _CLOBBER_MIN_FRACTION))
    if not allow_shrink and existing >= _CLOBBER_MIN_EXISTING and len(acts) < floor:
        sidecar = path.parent / (path.name + ".degenerate")
        try:
            sidecar.write_text(
                "\n".join(json.dumps(a.to_dict()) for a in acts) + ("\n" if acts else ""),
                encoding="utf-8",
            )
        except OSError:
            pass
        raise DegenerateExtractionError(
            f"Refusing to overwrite {existing} preference acts with "
            f"{len(acts)} (cliff-drop below {floor}). Live ledger preserved; "
            f"degenerate result written to {sidecar.name}. Pass "
            f"allow_shrink=True only if the corpus genuinely shrank."
        )
    body = "\n".join(json.dumps(a.to_dict()) for a in acts)
    atomic_write_text(path, body + "\n" if body else "")
    return path


def append_preference_acts(acts: list[PreferenceAct]) -> None:
    """Append acts to the ledger (append-only, mirroring the legacy
    rejections.jsonl writer). Used by provider-import (`eval-import` /
    `import_provider_memory`) for incremental adds; lens-build / lens-resync
    use ``save_preference_acts`` (full rewrite). Append never shrinks, so it
    needs no clobber guard — the caller is responsible for id-dedup."""
    import json

    if not acts:
        return
    path = preference_acts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for a in acts:
            fh.write(json.dumps(a.to_dict()) + "\n")


def _migrate_legacy_preference_stores() -> int:
    """One-time, idempotent recovery of the unified ledger from any legacy
    ``rejections.jsonl`` / ``decisions.jsonl`` a pre-#209 build left behind.

    #209 retired the legacy file readers and made ``preference_acts.jsonl``
    the sole store, but shipped no migration. A user upgrading from a build
    whose ledger predates v1.7.32 (when the ledger first started being
    written) would otherwise see an empty/stale ledger — every reader
    (eval-build, the launchpad lens card, lens-acts) silently empty — until
    the next ``lens-build`` re-extracts. And self-expressed decisions live
    ONLY in decisions.jsonl, so they'd be unrecoverable without a Stage 2
    chairman re-run (review finding #3).

    This reads the legacy files inline (the named loaders are retired, so we
    can't call them) and appends only acts whose content-stable id isn't
    already in the ledger — safe to call repeatedly, a no-op once migrated
    or when the legacy files are absent. Called from the ledger-repopulating
    entry points (lens-build, lens-resync, eval-build); never from the pure
    ``iter_preference_acts`` read path. Returns the count recovered."""
    import json

    from .basins import me_dir
    from .decisions import Decision
    from .turn_pairs import RejectionSignal

    d = me_dir()
    rej_path = d / "rejections.jsonl"
    dec_path = d / "decisions.jsonl"
    if not rej_path.exists() and not dec_path.exists():
        return 0

    # One-time BY CONSTRUCTION (#316). This recovery exists only to populate an
    # EMPTY ledger for a pre-#209 upgrade (see docstring). The legacy files are
    # never deleted, so once the ledger holds ANY act — a lens-build ran, or a
    # prior call already recovered them — re-reading the legacy stores re-injects
    # STALE old-prompt rejections on EVERY call: a fresh `lens` re-extraction
    # produces new content (new ids), so the old acts don't dedup and the ledger
    # silently grew 120→190 the instant eval-build ran after a clean re-extract,
    # diluting the eval with the exact noise the re-extraction removed. A
    # non-empty ledger IS the source of truth; the legacy stores are vestigial.
    if load_preference_acts():
        return 0

    def _rows(path):
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
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id"):
                out.append(obj)
        return out

    seen = {a.id for a in load_preference_acts()}
    recovered: list[PreferenceAct] = []

    for obj in _rows(rej_path):
        if not (obj.get("model_quote") and obj.get("user_substitute")):
            continue
        try:
            act = from_rejection(RejectionSignal(
                id=obj["id"], type=obj.get("type", ""),
                model_quote=obj.get("model_quote", ""),
                user_substitute=obj.get("user_substitute", ""),
                why_signal=obj.get("why_signal", ""),
                prompt_id=obj.get("prompt_id"), basin=obj.get("basin"),
                next_user_turn=obj.get("next_user_turn", ""),
            ))
        except (TypeError, KeyError):
            continue
        if act.id not in seen:
            seen.add(act.id)
            recovered.append(act)

    for obj in _rows(dec_path):
        if not (obj.get("privileged") and obj.get("sacrificed")):
            continue
        try:
            act = from_decision(Decision(
                id=obj["id"], privileged=obj.get("privileged", ""),
                sacrificed=obj.get("sacrificed", ""), valence=obj.get("valence", ""),
                basin=obj.get("basin"), verbatim=obj.get("verbatim", ""),
                prompt_id=obj.get("prompt_id"),
                would_flip_if=obj.get("would_flip_if", ""),
                source=obj.get("source", "transcript"),
                weight=float(obj.get("weight", 1.0) or 1.0),
            ))
        except (TypeError, KeyError, ValueError):
            continue
        if act.id not in seen:
            seen.add(act.id)
            recovered.append(act)

    if recovered:
        append_preference_acts(recovered)
    return len(recovered)


def load_preference_acts() -> list[PreferenceAct]:
    """Read the unified ledger back. Tolerant: skips malformed /
    under-specified lines. The raw store reader; `iter_preference_acts`
    is the same data sorted (model_miss first). Both read the ledger as
    the sole store post-#209."""
    import json

    path = preference_acts_path()
    if not path.exists():
        return []
    out: list[PreferenceAct] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not (
            isinstance(d, dict)
            and d.get("id")
            and d.get("trigger")
            and d.get("privileged")
            and d.get("sacrificed")
        ):
            continue
        # Defense in depth: even past the guard, a record could be missing
        # a required dataclass field (partial write, external import) and
        # blow up from_dict's positional construction. Skip it, don't crash
        # the whole load.
        try:
            out.append(PreferenceAct.from_dict(d))
        except (TypeError, KeyError):
            continue
    return out
