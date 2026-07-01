"""The preference-ledger firewall — the convention, turned into a CI guarantee.

Convention being pinned:
    ``preference_acts.jsonl`` is **observer-only and append-only**. The regression
    gate may open it read-only for T3; the optimizer (``dream`` / the promotion
    path) may **never** write it.

The rule is **scale-invariant**. A rewrite of a derived layer is legal *iff* it is a
**pure re-derivation** from the immutable layer beneath it; what's forbidden at every
level is **injecting content that isn't a projection of ground truth**. You don't need
a different firewall for the ledger than for the lens — you need the same firewall
applied recursively, anchored on the one layer that is nobody's derivation: the
**transcripts**. So a full rewrite of the ledger by lens-build is fine (it re-derives
from transcripts); a synthesizer touching the ledger is not (it injects). Four guards:

  1. ``test_reextraction_idempotent_on_frozen_transcripts`` — re-deriving the ledger
     from frozen transcripts twice yields a byte-identical ledger. A legal rewrite
     injects nothing.
  2. ``test_every_ledger_entry_resolves_to_transcript`` — every act carries a real
     turn-pair provenance (an extraction trigger + a transcript anchor), never a
     synthesized source.
  3. ``test_synthesis_modules_absent_from_ledger_write_closure`` — the modules that
     synthesize content (chairman synthesis / distill / cross-provider) are absent
     from the set of modules that can write the ledger; only sanctioned observers may.
  4. ``test_transcripts_append_only`` — the ground-truth layer is append-only +
     latest-wins; nothing rewrites it.

Same two-layer shape as ``test_preference_corpus_schemas.py``: a synthetic round-trip
(catches code drift) plus real-corpus sampling (catches on-disk drift; skipped on a
fresh/empty home).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from trinity_local.me.decisions import Decision
from trinity_local.me.preference_acts import (
    MODEL_MISS,
    SELF_EXPRESSED,
    PreferenceAct,
    from_decision,
    from_rejection,
    load_preference_acts,
    preference_acts_path,
    save_preference_acts,
)
from trinity_local.me.turn_pairs import RejectionSignal

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "trinity_local"

# Substrings that mark a `source` as SYNTHESIZED — injected content, not a projection
# of ground truth. A ledger act whose source contains any of these is a firewall
# breach (the optimizer wrote taste instead of the observer recording it).
SYNTHESIZED_SOURCE_MARKERS = (
    "synthes", "chairman", "distill", "dream", "virtual", "generated", "council", "llm",
)

# The functions that WRITE the ledger. A module that calls one of these is in the
# ledger's "write closure".
LEDGER_WRITE_FUNCS = {"save_preference_acts", "append_preference_acts"}

# The ONLY modules sanctioned to write the ledger (paths relative to src/trinity_local):
#   me/preference_acts.py  — the ledger module itself (defines the writers; the #209
#                            migration appends recovered legacy rows).
#   me_builder.py          — lens-build's re-derivation from transcripts. A full
#                            rewrite, but a *pure re-derivation* (guarded by test #1).
#   commands/eval_import.py — imports another tool's REAL user rejections (an observer
#                            of ground truth that happens to live in someone else's
#                            transcript).
# A new writer outside this set is a breach until justified and added here.
SANCTIONED_LEDGER_WRITERS = {
    "me/preference_acts.py",
    "me_builder.py",
    "commands/eval_import.py",
}

# Modules whose job is to SYNTHESIZE content (chairman synthesis, virtual councils,
# distillation, cross-provider clustering→synthesis). None may ever write the ledger.
SYNTHESIS_MODULES = {
    "distill.py",
    "cross_provider_pairs.py",
    "mcp_server.py",
    "council_runtime.py",
}


# --------------------------------------------------------------------------- helpers


def _ledger_write_closure() -> set[str]:
    """Every source module under ``src/trinity_local`` that *calls* a ledger write
    function. Static AST scan: a module is a writer iff it contains a ``Call`` whose
    callee name is one of ``LEDGER_WRITE_FUNCS``. Mere references in comments/docstrings
    (or the retired-names registry) don't count — only real calls."""
    writers: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "retired_names.py":  # a registry of dead names — references, not calls
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (
                    fn.id if isinstance(fn, ast.Name)
                    else fn.attr if isinstance(fn, ast.Attribute)
                    else None
                )
                if name in LEDGER_WRITE_FUNCS:
                    writers.add(rel)
                    break
    return writers


def _called_names(pyfile: Path) -> set[str]:
    tree = ast.parse(pyfile.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            out.add(fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", ""))
    return out


def _source_is_synthesized(source: str) -> bool:
    s = (source or "").lower()
    return any(marker in s for marker in SYNTHESIZED_SOURCE_MARKERS)


def _act_provenance_problem(act: PreferenceAct) -> str | None:
    """Return a reason string if ``act``'s provenance is illegal, else None.

    Legal provenance = (a) an extraction trigger (model_miss | self_expressed — the
    two shapes that *come from* a turn-pair), (b) a non-synthesized source, and (c) a
    transcript anchor. A ``model_miss`` act IS a ``[question]→[answer]→[reaction]``
    turn-pair, so it must carry an anchor: a ``prompt_id`` (resolves to a local node)
    or the inline prompt/question the provider-import shape carries. A
    ``self_expressed`` decision may be user-logged (no prompt_id), so an inline anchor
    or an explicit user-logged/lens-edit source suffices."""
    if act.trigger not in (MODEL_MISS, SELF_EXPRESSED):
        return f"unknown trigger {act.trigger!r} (only model_miss / self_expressed come from turn-pairs)"
    if _source_is_synthesized(act.source):
        return f"synthesized source {act.source!r} — injected, not a projection of ground truth"
    anchored = bool(act.prompt_id or act.prompt_text or act.question_text)
    if act.trigger == MODEL_MISS:
        if not anchored:
            return "model_miss act resolves to no turn-pair (no prompt_id / prompt_text / question_text)"
    else:  # self_expressed
        if not (anchored or act.context or act.source in {"user_logged", "lens_edit"}):
            return "self_expressed act carries no turn-pair anchor"
    return None


def _ledger_records(path: Path) -> list[dict]:
    """Parsed, order-insensitive view of the on-disk ledger (for fixpoint compares)."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return sorted((json.loads(ln) for ln in lines), key=lambda d: d.get("id", ""))


# ---- frozen ground truth (the transcript layer) + its deterministic extraction -----


def _seed_frozen_transcripts(home: Path) -> None:
    """The immutable layer: a fixed pair of transcripts. ``write_prompt_node`` seeds a
    ``[question]→[reaction]`` pair whose reaction node the ``prompt_id`` resolves to."""
    from tests.conftest import write_prompt_node

    write_prompt_node(home, "pn_reframe", "write the migration, don't explain it")
    write_prompt_node(home, "pn_terse", "we shipped the terse variant")


def _frozen_signals() -> tuple[list[RejectionSignal], list[Decision]]:
    """The DETERMINISTIC extraction output over the frozen transcripts. The chairman
    classification step is an LLM call and out of scope for a unit test, so its output
    is frozen here — this pins the derivation+export layer that turns frozen ground
    truth into the ledger. Same ground truth in ⇒ same ledger out, forever."""
    rejections = [
        RejectionSignal(
            id="r_reframe_01",
            type="REFRAME",
            model_quote="Here is a five-part rollout plan...",
            user_substitute="just write the migration",
            why_signal="user swapped the frame",
            prompt_id="pn_reframe",
            next_user_turn="yes, exactly that",
            question_text="write the migration, don't explain it",
        ),
    ]
    decisions = [
        Decision(
            id="d_terse_01",
            privileged="terse",
            sacrificed="thorough",
            valence="satisfaction",
            basin="b01",
            verbatim="we shipped the terse variant",
            prompt_id="pn_terse",
            source="transcript",
        ),
    ]
    return rejections, decisions


def _derive_and_export(rejections, decisions) -> None:
    acts = [from_rejection(r) for r in rejections] + [from_decision(d) for d in decisions]
    save_preference_acts(acts, allow_shrink=True)


# --------------------------------------------------------------- 1. idempotent rewrite


class TestReextractionIdempotent:
    """A rewrite is legal iff it's a pure re-derivation from the layer beneath it.
    Re-deriving the ledger from the SAME frozen transcripts must produce the SAME
    ledger — no growth, no mutation, nothing injected."""

    def test_reextraction_idempotent_on_frozen_transcripts(self, patch_trinity_home: Path):
        home = patch_trinity_home
        _seed_frozen_transcripts(home)
        rejections, decisions = _frozen_signals()

        _derive_and_export(rejections, decisions)
        first = preference_acts_path().read_bytes()

        # Re-derive from the identical ground truth: byte-identical, no drift.
        _derive_and_export(rejections, decisions)
        second = preference_acts_path().read_bytes()
        assert second == first, (
            "ledger re-derivation is not idempotent — a second pass over the same "
            "frozen transcripts changed the bytes. A legal rewrite injects nothing."
        )

        # And re-serializing an already-derived ledger must be a fixed point: the
        # export never mutates what it re-reads.
        before = _ledger_records(preference_acts_path())
        save_preference_acts(load_preference_acts(), allow_shrink=True)
        after = _ledger_records(preference_acts_path())
        assert after == before, "load→save round-trip mutated the ledger — export is not a pure projection"


# ---------------------------------------------------- 2. every entry ties to a turn-pair


class TestEntryResolvesToTranscript:
    """Every ledger act must trace to a real transcript turn-pair, never a synthesized
    source. Synthetic layer proves the resolver; real-corpus layer catches on-disk
    drift."""

    def test_synthetic_good_act_resolves(self, patch_trinity_home: Path):
        home = patch_trinity_home
        from tests.conftest import write_prompt_node
        from trinity_local.memory.store import load_prompt_node

        write_prompt_node(home, "pn_ok", "the original question")
        save_preference_acts(
            [from_rejection(RejectionSignal(
                id="r_ok", type="REFRAME", model_quote="a lecture", user_substitute="tldr",
                prompt_id="pn_ok",
            ))],
            allow_shrink=True,
        )
        acts = load_preference_acts()
        assert acts, "ledger empty after write"
        for act in acts:
            assert _act_provenance_problem(act) is None, _act_provenance_problem(act)
            # and its prompt_id genuinely resolves to a seeded transcript node
            assert act.prompt_id and load_prompt_node(act.prompt_id) is not None, (
                f"{act.prompt_id!r} did not resolve to a transcript node"
            )

    def test_synthesized_source_is_rejected(self):
        """A model_miss act whose provenance is a chairman synthesis, not a turn-pair,
        must be caught — this is exactly what the optimizer-writes-taste breach looks
        like."""
        bad = PreferenceAct(
            id="r_bad", trigger=MODEL_MISS, privileged="x", sacrificed="y",
            prompt_id="pn_x", source="chairman-synthesis",
        )
        problem = _act_provenance_problem(bad)
        assert problem and "synthesized" in problem

    def test_unanchored_model_miss_is_rejected(self):
        """A model_miss with no transcript anchor at all can only be fabricated."""
        floating = PreferenceAct(
            id="r_float", trigger=MODEL_MISS, privileged="x", sacrificed="y", source="lens-build",
        )
        assert _act_provenance_problem(floating) is not None

    def test_every_ledger_entry_resolves_to_transcript(self):
        """Real-corpus: every act on disk carries a real turn-pair provenance and no
        synthesized source. Skipped on a fresh/empty home (CI)."""
        home = Path.home() / ".trinity"
        if not home.exists():
            pytest.skip("no real ~/.trinity/ on this machine")
        ledger = home / "me" / "preference_acts.jsonl"
        if not ledger.exists():
            pytest.skip("no preference_acts.jsonl on real home")

        failures: list[tuple[str, str]] = []
        with ledger.open(encoding="utf-8") as fh:
            for idx, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    act = PreferenceAct.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    failures.append((f"line {idx}", f"unreadable: {exc}"))
                    continue
                problem = _act_provenance_problem(act)
                if problem:
                    failures.append((act.id or f"line {idx}", problem))
        if failures:
            msg = "Real preference_acts.jsonl has entries with broken provenance:\n"
            for who, why in failures[:15]:
                msg += f"  {who}: {why}\n"
            if len(failures) > 15:
                msg += f"  ... and {len(failures) - 15} more\n"
            pytest.fail(msg)


# ---------------------------------------------- 3. synthesizers can't write the ledger


class TestSynthesisAbsentFromWriteClosure:
    """The same firewall, one level up: the modules that inject synthesized content
    must not hold a write handle to the ledger — and the write closure must be exactly
    the sanctioned observers."""

    def test_synthesis_modules_absent_from_ledger_write_closure(self):
        closure = _ledger_write_closure()
        offenders = closure & SYNTHESIS_MODULES
        assert not offenders, (
            f"synthesis module(s) hold a ledger write handle: {sorted(offenders)} — "
            f"a synthesizer writing the ledger injects non-ground-truth content."
        )
        unexpected = closure - SANCTIONED_LEDGER_WRITERS
        assert not unexpected, (
            f"unsanctioned module(s) write the ledger: {sorted(unexpected)}. If this is "
            f"a legitimate observer, add it to SANCTIONED_LEDGER_WRITERS with a "
            f"justification; if it's an optimizer/synthesizer, it must not write."
        )

    def test_dream_does_not_directly_write_the_ledger(self):
        """The optimizer coordinates; it does not write ground truth. dream.py itself
        must never call a ledger write function directly (it may drive lens-build,
        which re-derives — but that write is lens-build's, guarded by test #1)."""
        called = _called_names(SRC / "commands" / "dream.py")
        leaked = LEDGER_WRITE_FUNCS & called
        assert not leaked, f"dream.py directly calls ledger writer(s): {sorted(leaked)}"


# ---------------------------------------------------------- 4. the ground-truth anchor


class TestTranscriptsAppendOnly:
    """The transcripts are nobody's derivation — the whole firewall is anchored on
    them being immutable: append-only, latest-wins, never rewritten."""

    def test_transcripts_append_only(self, patch_trinity_home: Path):
        from trinity_local.memory.schemas import PromptNode
        from trinity_local.memory.store import (
            load_prompt_node,
            prompt_nodes_path,
            upsert_prompt_node,
        )

        def _node(text: str) -> PromptNode:
            return PromptNode(
                id="pn_ao", transcript_id="t_ao", provider="claude",
                source_path="/fake.json", turn_index=0, text=text,
                embedding=None,  # type: ignore[arg-type]  # "no embedding" sentinel (same as conftest)
                created_at="2026-05-01T10:00:00", timestamp="2026-05-01T10:00:00",
                preceding_assistant_text="", following_assistant_text="", themes=[],
            )

        upsert_prompt_node(_node("v1"))
        upsert_prompt_node(_node("v2"))  # upsert the SAME id

        # The store must live under the patched temp home, not the real one.
        assert str(patch_trinity_home) in str(prompt_nodes_path())
        lines = [ln for ln in prompt_nodes_path().read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2, (
            f"upsert overwrote instead of appending ({len(lines)} line(s)) — the "
            f"transcript store is not append-only."
        )
        # Latest-wins on read over the append-only log.
        latest = load_prompt_node("pn_ao")
        assert latest is not None and latest.text == "v2"

    def test_transcript_writer_is_append_mode(self):
        """Static guard: the transcript write primitive opens in append mode, and the
        public writers route through it — so nothing truncates ground truth."""
        import inspect

        from trinity_local.memory import store

        prim = inspect.getsource(store._append_jsonl)
        assert '"a"' in prim or "'a'" in prim, "the transcript write primitive is not append-mode"
        assert "_append_jsonl" in inspect.getsource(store.upsert_prompt_node)
        assert "_append_jsonl" in inspect.getsource(store.upsert_turn_window)
