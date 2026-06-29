"""Eval-set builder. Reads the model_miss subset of the unified
~/.trinity/me/preference_acts.jsonl ledger (legacy rejections.jsonl /
decisions.jsonl are migrated in by _migrate_legacy_preference_stores)
+ the prompt index, emitting a replayable JSON eval set with one item
per labeled rejection.

Schema is intentionally narrow for MVP (#122). v1 ships:

  {
    "eval_id": "eval_<8-char-hash>",
    "built_at": "2026-05-14T...",
    "source": "rejections",          # cross_provider_pair source comes later
    "stats": {
      "items": 44,
      "by_rejection_type": {"REFRAME": 12, "COMPRESSION": 5, ...},
      "by_basin": {"b00": 3, "b12": 8, ...},
    },
    "items": [
      {
        "eval_item_id": "ei_<hash>",
        "prompt": "<original user prompt that elicited the rejected response>",
        "rejection_type": "REFRAME",
        "rejected_response": "<model_quote>",
        "user_substitute": "<what the user actually wanted in their next turn>",
        "rubric_signal": "<chairman-extracted why_signal>",
        "basin_id": "b12",
        "source": "rejections",
        "source_id": "r_002",
        "prompt_id": "pnode_db27791f15a2d260",
        "provider_of_rejected_response": null,  # populated when prompt_node carries provider
      },
      ...
    ]
  }

The eval set is content-addressed by hash so the same corpus state
produces a stable eval_id — re-running `build_eval_set()` on an
unchanged corpus is idempotent.

Future runner consumes this shape: for each item, dispatch `prompt`
to a target provider, capture response, ask chairman-judge "given
the user's lens, is target_response better than rejected_response
on the {rejection_type} axis?"
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..state_paths import state_dir


def evals_dir() -> Path:
    """`~/.trinity/evals/` — eval sets + per-run results live here."""
    path = state_dir() / "evals"
    path.mkdir(parents=True, exist_ok=True)
    return path


def results_dir() -> Path:
    """`~/.trinity/evals/results/` — populated by future runner ticks."""
    path = evals_dir() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(frozen=True)
class EvalItem:
    """One eval item: an empirically-rejected (prompt, response) pair
    that any candidate model can be scored against."""
    eval_item_id: str
    prompt: str
    rejection_type: str
    rejected_response: str
    user_substitute: str
    rubric_signal: str
    basin_id: str | None
    source: str  # "rejections" today; "cross_provider_pair" later
    source_id: str
    prompt_id: str | None
    provider_of_rejected_response: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvalSet:
    """A complete eval set; serializes to one JSON file."""
    eval_id: str
    built_at: str
    source: str
    stats: dict
    items: list[EvalItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eval_id": self.eval_id,
            "built_at": self.built_at,
            "source": self.source,
            "stats": self.stats,
            "items": [item.to_dict() for item in self.items],
        }


def _stable_id(prefix: str, *parts: str) -> str:
    """sha1 hash truncated to 12 chars, prefixed. Same shape as
    utils.stable_id but localized to the evals module since we slice
    differently (12 chars vs 16) — eval IDs need to be readable in
    CLI output."""
    blob = "|".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha1(blob).hexdigest()[:12]}"


from ._textnorm import norm_for_compare as _norm_eval_text  # noqa: E402 (shared #247 gate primitive)


def _lookup_prompt_text(prompt_id: str | None) -> tuple[str, str | None]:
    """Return (prompt_text, provider) for a given prompt_id, or
    (empty, None) if not in the index.

    The rejection record carries the prompt_id; we want the actual
    prompt text plus the provider that produced the rejected response
    so eval results can attribute "this is what we expected $provider
    to do better than".
    """
    if not prompt_id:
        return "", None
    # Late-import to avoid pulling memory.store at module-load time
    # (heavy import chain). Only the builder needs it.
    from ..memory.store import load_prompt_node

    node = load_prompt_node(prompt_id)
    if node is None:
        return "", None
    return (node.text or "").strip(), getattr(node, "provider", None)


def _prior_user_turn_map() -> dict[str, str]:
    """Map each prompt-node id → the text of the PRIOR user turn in the same
    transcript — i.e. the original QUESTION the rejected model answer was
    responding to.

    A rejection is a three-turn shape: [question] → [model answer] → [user
    reaction]. The rejection record's prompt_id points at the REACTION turn (that
    is where user_substitute is excerpted from). Scoring a candidate against the
    reaction-as-prompt is degenerate (prompt ≈ gold) and asks the model to
    "answer" a mid-conversation fragment. The question is recoverable for free —
    it is the immediately-preceding user turn in the same transcript (#316).

    Built fresh per build (NOT module-cached) so a test that mocks the node
    source can't leak a stale map into a later test (see the module-cache
    keying incident). O(N) once over the index; build_eval_set is one-shot.
    Skips empty transcript_id (can't order safely) and first-in-transcript
    nodes (no preceding question)."""
    from collections import defaultdict

    from ..memory.store import iter_prompt_nodes_no_embedding

    by_tx: dict[str, list] = defaultdict(list)
    for n in iter_prompt_nodes_no_embedding(limit=None):
        tid = getattr(n, "transcript_id", "") or ""
        if tid:
            by_tx[tid].append(n)
    prior: dict[str, str] = {}
    for nodes in by_tx.values():
        # Order within a transcript by turn_index when present, else keep the
        # index's append order (capture-chronological). Stable on ties.
        nodes.sort(key=lambda n: getattr(n, "turn_index", 0) or 0)
        for i in range(1, len(nodes)):
            nid = getattr(nodes[i], "id", "")
            q = (getattr(nodes[i - 1], "text", "") or "").strip()
            if nid and q:
                prior[nid] = q
    return prior


def build_eval_set(*, source: str = "rejections", limit: int | None = None) -> EvalSet:
    """Assemble an eval set from the current corpus.

    `source="rejections"` is the only path live in MVP. The shape is
    stable; future sources (e.g. cross_provider_pair) append to the same
    items list.

    Raises FileNotFoundError if the preference_acts.jsonl ledger doesn't
    exist — better than silently returning an empty set, which would mask
    a misconfig.
    """
    from ..me.preference_acts import preference_acts_path

    if source != "rejections":
        raise NotImplementedError(
            f"source={source!r} not yet wired. MVP supports 'rejections' only."
        )

    # #209: the unified ledger is the sole store. Eval items still draw the
    # model_miss subset (via iter_preference_acts below); the existence check
    # points at the ledger. First, best-effort recover any legacy
    # rejections.jsonl / decisions.jsonl into the ledger (review finding #3)
    # so an un-migrated upgrade doesn't see an empty eval set.
    try:
        from ..me.preference_acts import _migrate_legacy_preference_stores
        _migrate_legacy_preference_stores()
    except Exception:
        pass
    ledger_path = preference_acts_path()
    if not ledger_path.exists():
        raise FileNotFoundError(
            f"No preference-act ledger at {ledger_path}. "
            f"Run `trinity-local lens` to mine rejections from turn pairs first."
        )

    items: list[EvalItem] = []
    skipped_degenerate = 0  # #247: items dropped because prompt == gold
    skipped_unresolved = 0  # prompt_id GC'd → no honest prompt → would be degenerate
    # #281: per-axis drop counts. The bare totals above hide WHICH rejection axis
    # collapsed — and the collapse is axis-correlated (COMPRESSION is 100%
    # degenerate on the live corpus because terse user turns make
    # user_substitute == prompt). Breaking the drops down by axis turns a silent
    # whole-axis loss into an actionable signal (data-sampling skew floor-guard).
    skipped_degenerate_by_type: dict[str, int] = {}
    skipped_unresolved_by_type: dict[str, int] = {}
    source_types: set[str] = set()  # every rejection axis with ≥1 valid model_miss act
    by_type: dict[str, int] = {}
    by_basin: dict[str, int] = {}

    # EXTRACT-unification Stage 2: source eval items through the unified
    # PreferenceAct read layer, filtered to model-miss (the rejection
    # subset — "the model got it wrong, can a model avoid it?"). This is
    # behavior-preserving (model_miss acts come from the same
    # preference_acts.jsonl ledger the loud-failure check above guards), but routes
    # the eval harness through the one evidence type. Self-expressed acts
    # (decisions) stay out of the eval set for now — including them is a
    # future enhancement, not this stage.
    from ..me.preference_acts import MODEL_MISS, iter_preference_acts

    # #316: prompt = the recovered QUESTION (prior user turn), not the reaction.
    prior_question = _prior_user_turn_map()

    for act in iter_preference_acts():
        if act.trigger != MODEL_MISS:
            continue
        rej_type = act.kind
        model_quote = (act.sacrificed or "").strip()
        user_sub = (act.privileged or "").strip()
        source_id = act.id or ""
        prompt_id = act.prompt_id
        if not (rej_type and model_quote and source_id):
            continue
        # This axis had at least one structurally-valid model_miss act; record it
        # so a later all-degenerate / all-unresolved collapse is detectable as a
        # FULLY-DROPPED axis (vs an axis that simply never appeared in the corpus).
        source_types.add(rej_type)
        # #316: the eval PROMPT is the ORIGINAL QUESTION the rejected answer
        # responded to — the prior user turn in the transcript — NOT the reaction
        # turn prompt_id points at. The reaction-as-prompt made prompt ≈ gold
        # (user_substitute is excerpted from that same reaction) → degenerate, and
        # dispatched a mid-conversation fragment to the candidate. provider stays
        # the rejected-response provider (from the reaction node).
        _, provider = _lookup_prompt_text(prompt_id)
        # Prefer the question the lens now CARRIES (Stage 0 persists question_text
        # — the unification that lets the eval consume lens output instead of
        # re-deriving it). Fall back to the transcript walk for acts extracted
        # before question_text existed, then to the provider-imported inline prompt.
        prompt_text = (getattr(act, "question_text", "") or "").strip()
        if not prompt_text:
            prompt_text = prior_question.get(prompt_id or "", "")
        if not prompt_text:
            # Provider-imported rejections (#280) have no local node to walk back
            # from, but may carry the original prompt inline. Use it — it's a real
            # prompt the provider supplied, distinct from the gold.
            prompt_text = (getattr(act, "prompt_text", "") or "").strip()
        # If the prompt_id no longer resolves (corpus churn GC'd the prompt
        # between lens-build and eval-build), there is no honest prompt to score
        # against. The old behaviour fell back to prompt_text = user_sub, but
        # that makes prompt == gold BY CONSTRUCTION — the candidate is fed the
        # user's preferred phrasing AS its prompt (runner.py:
        # provider.run(item.prompt)), echoes it, and the judge (whose gold IS
        # user_sub) scores ~1.0 for every model. That is the exact #247
        # degeneracy, except WORSE: resolved-degenerate items get dropped below,
        # whereas these unresolved ones were kept AND scored — inflating the
        # "Model X scored Y on YOUR prompts" aggregate with zero rejection-axis
        # signal. Every unresolved item is degenerate this way, so drop them all;
        # count + surface in stats so the loss is visible, never silent
        # (data-sampling floor-guard). The dropped fraction grows as the corpus
        # churns, so a rising skipped_unresolved is the canary to re-run
        # lens-build against the live corpus.
        if not prompt_text:
            skipped_unresolved += 1
            skipped_unresolved_by_type[rej_type] = skipped_unresolved_by_type.get(rej_type, 0) + 1
            continue
        # Drop trivially-passable items: when the prompt IS the gold (#247). The
        # Stage-0 schema excerpts user_substitute from the same user turn
        # prompt_id points to, so for short turns (median 9 words < the 25-word
        # cap) the excerpt == the full turn == the prompt — 71% of the newest
        # set. The judge's gold then equals the prompt, so any echo passes and
        # the rejection-axis delta has no signal. Skip (data-sampling
        # floor-guard).
        if _norm_eval_text(prompt_text) == _norm_eval_text(user_sub):
            skipped_degenerate += 1
            skipped_degenerate_by_type[rej_type] = skipped_degenerate_by_type.get(rej_type, 0) + 1
            continue
        items.append(EvalItem(
            eval_item_id=_stable_id("ei", source_id, rej_type),
            prompt=prompt_text,
            rejection_type=rej_type,
            rejected_response=model_quote,
            user_substitute=user_sub,
            rubric_signal=(act.why or "").strip(),
            basin_id=act.basin,
            source="rejections",
            source_id=source_id,
            prompt_id=prompt_id,
            provider_of_rejected_response=provider,
        ))
    # #281: derive the fully-dropped axes BEFORE limit truncation, so the signal
    # means "this axis had model_miss acts but ZERO survived the degeneracy /
    # unresolved gates" — never "limit happened to truncate it away". COMPRESSION
    # is the live example: 17 acts in, 0 scoreable out. A fully-dropped axis is the
    # canary that Stage-0 excerpting collapsed user_substitute onto the prompt for
    # that whole axis (terse-turn axes), so it needs an upstream lens-build fix —
    # not something the eval can paper over.
    kept_types = {it.rejection_type for it in items}
    fully_dropped_types = sorted(t for t in source_types if t not in kept_types)

    # #269 EVAL nomination: when limiting, draw the benchmark from the user's
    # HIGHEST-SIGNAL threads (real, multi-turn, substantive work) rather than
    # the first N in ledger order — the best threads make the best evals. Rank
    # each rejection by its originating thread's signal, then truncate.
    if limit is not None and len(items) > limit:
        try:
            from ..me.thread_signal import compute_thread_signals
            from ..memory.store import iter_prompt_nodes_no_embedding

            sig = compute_thread_signals()
            pid2tid = {
                getattr(n, "id", ""): getattr(n, "transcript_id", "") or ""
                for n in iter_prompt_nodes_no_embedding(limit=None)
            }
            # Signal DESC, eval_item_id ASC as a stable tie-break so the
            # items[:limit] cut below is a TOTAL order: two items whose threads
            # share a signal score straddling the limit boundary would otherwise
            # keep the prior order, so WHICH items land in the eval set would
            # flip on that order (changing the set contents + its eval_id).
            items.sort(key=lambda it: (-sig.get(pid2tid.get(it.prompt_id, ""), 0.0), it.eval_item_id))
        except Exception:
            pass  # ranking is a preference, never a hard dependency
        items = items[:limit]

    # Recompute the type/basin histograms over the FINAL (possibly truncated)
    # item set so the stats describe what's actually in the eval.
    for it in items:
        by_type[it.rejection_type] = by_type.get(it.rejection_type, 0) + 1
        if it.basin_id:
            by_basin[it.basin_id] = by_basin.get(it.basin_id, 0) + 1

    # Content-addressed eval_id: hash of the source_ids so re-running on
    # the same corpus produces the same eval_id (idempotent), but adding
    # new rejections produces a new id (so historical eval results stay
    # pinned to the corpus state they were scored against).
    fingerprint = "|".join(sorted(it.source_id for it in items))
    eval_id = _stable_id("eval", source, fingerprint)

    stats = {
        "items": len(items),
        "by_rejection_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "by_basin": dict(sorted(by_basin.items(), key=lambda kv: -kv[1])),
        # #247 visibility: how many model_miss acts were dropped as
        # trivially-passable (prompt == gold). High values flag the Stage-0
        # excerpt==prompt degeneracy at its source.
        "skipped_degenerate": skipped_degenerate,
        # Unresolved-prompt drops: prompt_id no longer in the corpus, so there's
        # no honest prompt to score against (the old user_sub fallback was
        # degenerate-by-construction). A rising value is the canary to re-run
        # lens-build so eval items point at live prompts.
        "skipped_unresolved": skipped_unresolved,
        # #281 per-axis skew: which rejection axes lost items, and how many. The
        # bare totals above can't tell you a whole axis collapsed — these can.
        "skipped_degenerate_by_type": dict(
            sorted(skipped_degenerate_by_type.items(), key=lambda kv: -kv[1])
        ),
        "skipped_unresolved_by_type": dict(
            sorted(skipped_unresolved_by_type.items(), key=lambda kv: -kv[1])
        ),
        # Axes with model_miss acts but ZERO scoreable items — silently absent
        # from by_rejection_type without this. Drives the eval-build/eval-stats
        # warning so the loss is visible + actionable, never a green-while-degenerate.
        "fully_dropped_types": fully_dropped_types,
    }

    return EvalSet(
        eval_id=eval_id,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source=source,
        stats=stats,
        items=items,
    )


def save_eval_set(eval_set: EvalSet) -> Path:
    """Persist to ~/.trinity/evals/<eval_id>.json. Returns the path.

    Contract: schemas/eval_set.schema.json declares `stats.items` (the
    integer item count) as required. The dataclass types stats as a
    bare dict — no shape enforcement at construction time, so a future
    code path that builds an EvalSet with the wrong stats shape would
    silently write a schema-invalid JSON. Fail fast at the boundary
    (same pattern as save_council_outcome — sweep iter #106).
    """
    if not isinstance(eval_set.stats, dict) or "items" not in eval_set.stats:
        raise ValueError(
            f"save_eval_set refused: eval_set.stats must be a dict with "
            f"`items` (the integer count of eval items). Got "
            f"{type(eval_set.stats).__name__} with keys "
            f"{list(eval_set.stats.keys()) if isinstance(eval_set.stats, dict) else 'n/a'}. "
            f"Schema declares `stats.items` required."
        )

    path = evals_dir() / f"{eval_set.eval_id}.json"
    path.write_text(
        json.dumps(eval_set.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_eval_set(eval_id: str) -> EvalSet | None:
    """Read back an eval set by id. Returns None if not found."""
    path = evals_dir() / f"{eval_id}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
    except (OSError, json.JSONDecodeError):
        return None
    items = [
        EvalItem(
            eval_item_id=it.get("eval_item_id", ""),
            prompt=it.get("prompt", ""),
            rejection_type=it.get("rejection_type", ""),
            rejected_response=it.get("rejected_response", ""),
            user_substitute=it.get("user_substitute", ""),
            rubric_signal=it.get("rubric_signal", ""),
            basin_id=it.get("basin_id"),
            source=it.get("source", "rejections"),
            source_id=it.get("source_id", ""),
            prompt_id=it.get("prompt_id"),
            provider_of_rejected_response=it.get("provider_of_rejected_response"),
        )
        for it in raw.get("items", [])
    ]
    return EvalSet(
        eval_id=raw.get("eval_id", eval_id),
        built_at=raw.get("built_at", ""),
        source=raw.get("source", "rejections"),
        stats=raw.get("stats", {}),
        items=items,
    )
