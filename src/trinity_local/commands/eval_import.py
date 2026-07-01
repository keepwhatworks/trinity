"""`eval-prompt` + `eval-import` — provider-side rejection-signal loop.

Sibling to lens-prompt/lens-import. Same shape, different artifact:
this one captures REFRAME / REDIRECT / SHARPENING / COMPRESSION
rejection signals that the user produced naturally during chats with
the provider. Trinity scores any future model dispatch against the
imported signals as a personal eval suite (eval-build → eval-run
chain shipped task #122).

Measurement loop: same set, scored weekly against the current lens.
Score climbing = lens improvement, observable. OpenAI's "eval skills"
pattern — evaluate a skill against the case suite it claims to handle.

Schema mapping is in ``_provider_dict_to_rejection_signal`` — keep in
sync with ``docs/evals-from-provider.md``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..me.preference_acts import preference_acts_path
from ..me.turn_pairs import RejectionSignal
from ..utils import stable_id
from .provider_import import read_provider_import


# Same anchor + lookup logic as lens_import. Kept independent so the
# two doc files can evolve separately if one ever shifts naming.
_PROMPT_BODY_MARKER = "## The prompt — copy below this line"
_VALID_REJECTION_TYPES = {"REFRAME", "REDIRECT", "SHARPENING", "COMPRESSION"}


def _prompt_doc_path() -> Path | None:
    """Resolve docs/evals-from-provider.md (repo-relative or env override)."""
    import os
    override = os.environ.get("TRINITY_EVAL_PROMPT_DOC")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    candidate = Path(__file__).resolve().parents[3] / "docs" / "evals-from-provider.md"
    return candidate if candidate.exists() else None


def register(subparsers):
    prompt = subparsers.add_parser(
        "eval-prompt",
        help=(
            "Print the canonical provider-side eval prompt (paste into "
            "Claude/Codex/Gemini, save JSON, then `eval-import`)."
        ),
    )
    prompt.add_argument(
        "--with-instructions",
        action="store_true",
        help=(
            "Include the full doc (intro + measurement story), not just "
            "the prompt body. Default: prompt body only, so "
            "`eval-prompt | pbcopy` lands a clean paste."
        ),
    )
    prompt.set_defaults(handler=handle_eval_prompt)

    imp = subparsers.add_parser(
        "eval-import",
        help=(
            "Merge a provider's JSON-shaped rejection signals (see "
            "docs/evals-from-provider.md) into ~/.trinity/me/preference_acts.jsonl."
        ),
    )
    imp.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to the JSON file. Omit with --from-json to read stdin.",
    )
    imp.add_argument(
        "--from-json",
        action="store_true",
        help="Read the JSON payload from stdin instead of a file path.",
    )
    imp.add_argument(
        "--provider",
        default=None,
        help=(
            "Override the `source_provider` field in the payload. Useful "
            "when the provider's JSON omits it or you want to attribute "
            "the import differently."
        ),
    )
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + print merge plan; do not write to preference_acts.jsonl.",
    )
    imp.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit structured JSON to stdout instead of the human summary.",
    )
    imp.set_defaults(handler=handle_eval_import)


# ---------------------------------------------------------------------
# eval-prompt
# ---------------------------------------------------------------------


def handle_eval_prompt(args) -> int:
    doc = _prompt_doc_path()
    if doc is None:
        print(
            "error: couldn't find docs/evals-from-provider.md. Set "
            "TRINITY_EVAL_PROMPT_DOC to its absolute path if you're "
            "running from a non-checkout install.",
            file=sys.stderr,
        )
        return 1
    text = doc.read_text(encoding="utf-8")
    if args.with_instructions:
        sys.stdout.write(text)
        return 0
    idx = text.find(_PROMPT_BODY_MARKER)
    if idx < 0:
        print(
            "warning: prompt-body marker not found in doc; emitting "
            "full file. Update docs/evals-from-provider.md to restore "
            f"the '{_PROMPT_BODY_MARKER}' anchor.",
            file=sys.stderr,
        )
        sys.stdout.write(text)
        return 0
    after = text[idx:].split("\n", 2)
    body = after[-1] if len(after) == 3 else text[idx:]
    sys.stdout.write(body)
    return 0


# ---------------------------------------------------------------------
# eval-import
# ---------------------------------------------------------------------


def _provider_dict_to_rejection_signal(
    r: dict,
    source_provider: str,
    seq: int,
) -> RejectionSignal | None:
    """Map a single provider 'rejection' dict → RejectionSignal.

    Returns None when required fields are missing; caller counts skips.
    `seq` is the per-payload index, mixed into the stable id so
    re-imports collide deterministically (same input → same id), but
    two different payloads from the same provider don't collide.
    """
    rtype = (r.get("type") or "").strip().upper()
    if rtype not in _VALID_REJECTION_TYPES:
        return None
    model_quote = (r.get("model_quote") or "").strip()
    user_substitute = (r.get("user_substitute") or "").strip()
    if not model_quote or not user_substitute:
        return None
    why_signal = (r.get("why_signal") or "").strip()
    confidence = (r.get("confidence") or "medium").strip().lower()
    # #280: the original prompt that elicited the rejected response. The provider
    # has it (it lived the conversation); when supplied, eval-build can score this
    # rejection instead of dropping it as unresolved. Drop a prompt that just
    # echoes the gold — that's the #247 degeneracy (prompt == user_substitute ⇒
    # every model scores ~1.0), so an echoed prompt is no better than none.
    original_prompt = (r.get("original_prompt") or "").strip()
    if original_prompt and original_prompt == user_substitute:
        original_prompt = ""
    # Stable id: hash of the substantive content so re-running the
    # provider with the same data doesn't double-import. Source
    # provider is folded in so the same quote captured by two
    # providers stays distinct (they often phrase it differently).
    # Prefix is "r" (not "rej") to match the published rejection_signal
    # schema's ^r_ pattern — the schema is still the interop contract
    # for model-miss acts stored in preference_acts.jsonl.
    rid = stable_id(
        "r",
        source_provider,
        rtype,
        model_quote[:200],
        user_substitute[:200],
    )
    return RejectionSignal(
        id=rid,
        type=rtype,
        model_quote=model_quote,
        user_substitute=user_substitute,
        why_signal=(
            f"[{source_provider}/{confidence}] {why_signal}"
            if why_signal
            else f"[{source_provider}/{confidence}]"
        ),
        # provider doesn't know our prompt-node ids, so prompt_id stays None. The
        # inline `original_prompt` (#280) is the signal's ONLY turn-pair anchor, so it
        # is now load-bearing, not optional: `handle_eval_import` runs every mapped
        # signal through `provenance_gap`, and one with no `original_prompt` (or an
        # echoed-gold prompt, blanked above) has no anchor and is REFUSED — a provider
        # asserting taste is not a record of a correction the user made. Signals that
        # carry it resolve as scoreable eval items (build_eval_set falls back to
        # prompt_text) and feed the lens; prompt-less ones enter neither.
        prompt_id=None,
        prompt_text=original_prompt,
        basin=None,  # provider doesn't know our basin ids
        next_user_turn="",
    )


def _read_existing_ids() -> set[str]:
    """Load ids for dedup from the unified ledger (the sole store post-#209).
    A signal already in the ledger must not be re-appended."""
    from ..me.preference_acts import load_preference_acts
    return {a.id for a in load_preference_acts() if a.id}


def _append_signals(signals: list[RejectionSignal]) -> None:
    """Append each provider rejection to the unified ledger as a model_miss
    act. Append-only; the caller dedups by id via `_read_existing_ids` and gates
    provenance via `_gate_by_provenance` (only anchored signals reach here)."""
    from ..me.preference_acts import append_preference_acts, from_rejection
    append_preference_acts([from_rejection(s) for s in signals])


def _gate_by_provenance(
    signals: list[RejectionSignal],
) -> tuple[list[RejectionSignal], list[tuple[RejectionSignal, str]]]:
    """The provenance firewall at the import boundary — broad-ownership, no exceptions.

    A provider-supplied signal enters the ledger ONLY if the PreferenceAct it maps to
    passes the SAME `provenance_gap` every other ledger write must pass. A provider
    rejection has no local `prompt_id` (the provider doesn't know our node ids), so its
    only turn-pair anchor is the inline `original_prompt` (carried as `prompt_text`). A
    prompt-less rejection is a bare quote/substitute — a provider ASSERTING taste, not a
    record of a correction the user actually made — so it has no anchor and is refused
    here, exactly as an unanchored write from anywhere else would be. Returns
    ``(admitted, rejected)`` where ``rejected`` pairs each dropped signal with its reason."""
    from ..me.preference_acts import from_rejection, provenance_gap

    admitted: list[RejectionSignal] = []
    rejected: list[tuple[RejectionSignal, str]] = []
    for s in signals:
        gap = provenance_gap(from_rejection(s))
        if gap:
            rejected.append((s, gap))
        else:
            admitted.append(s)
    return admitted, rejected


def handle_eval_import(args) -> int:
    # Shared front-end (read → parse → dict-guard → source_provider → the
    # present-but-wrong-type list guard) with lens-import; the rest is eval-specific.
    _read = read_provider_import(args, list_fields=("rejections",))
    if isinstance(_read, int):
        return _read
    payload, source_provider = _read
    raw_rejections = payload.get("rejections") or []  # absent or [] → nothing to import

    # Map + skip malformed.
    parsed: list[RejectionSignal] = []
    skipped = 0
    for i, r in enumerate(raw_rejections):
        if not isinstance(r, dict):
            skipped += 1
            continue
        sig = _provider_dict_to_rejection_signal(r, source_provider, i)
        if sig is None:
            skipped += 1
            continue
        parsed.append(sig)

    # Dedup against existing ids.
    existing_ids = _read_existing_ids()
    new_signals = [s for s in parsed if s.id not in existing_ids]
    duplicates = len(parsed) - len(new_signals)

    # Provenance firewall (broad-ownership, no exceptions): a provider signal enters
    # the ledger ONLY if it passes the SAME provenance_gap as every other ledger write.
    # A prompt-less rejection has no turn-pair anchor — a provider ASSERTION, not a
    # record of a correction the user made — so it is refused, not silently written.
    admitted, rejected = _gate_by_provenance(new_signals)
    rejected_no_provenance = len(rejected)

    # Per-axis breakdown over the ADMITTED signals (what actually lands).
    axis_counts: dict[str, int] = {}
    for s in admitted:
        axis_counts[s.type] = axis_counts.get(s.type, 0) + 1

    # An admitted signal is scoreable as an eval item when it carries the original
    # prompt (inline as prompt_text — build_eval_set falls back to it) or a resolvable
    # prompt_id. Post-gate every admitted signal is anchored, so the "next: eval-build"
    # line is an honest promise, not a false-green.
    scoreable = sum(
        1 for s in admitted
        if getattr(s, "prompt_id", None) or getattr(s, "prompt_text", "")
    )

    result = {
        "ok": True,
        "source_provider": source_provider,
        "rejections": {
            "incoming": len(parsed),
            "new": len(admitted),
            "scoreable_as_eval": scoreable,
            "duplicates": duplicates,
            "skipped_malformed": skipped,
            "rejected_no_provenance": rejected_no_provenance,
            "by_axis": axis_counts,
        },
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run and admitted:
        _append_signals(admitted)
        result["ledger_path"] = str(preference_acts_path())

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        verb = "DRY-RUN — would import" if args.dry_run else "imported"
        print(f"{verb} from provider '{source_provider}'")
        r = result["rejections"]
        axis_str = ", ".join(f"{k}={v}" for k, v in sorted(r["by_axis"].items())) if r["by_axis"] else "—"
        print(
            f"  {r['new']} new ({axis_str}), "
            f"{r['duplicates']} duplicates, "
            f"{r['skipped_malformed']} skipped"
        )
        if not args.dry_run and admitted:
            print(f"  → {result['ledger_path']}")
            print(
                "  next: `trinity-local eval-build` to package these into "
                "an eval set, then `trinity-local eval-run` to score a model"
            )
        if rejected_no_provenance:
            # No silent drop (green-gate discipline): name what was refused and why.
            print(
                f"  {rejected_no_provenance} rejected: no turn-pair anchor. A provider "
                "rejection enters the ledger only WITH its `original_prompt` (the same "
                "anchor every other ledger write carries) — a bare quote/substitute is "
                "an assertion, not a record of your correction. See "
                "docs/evals-from-provider.md."
            )
    return 0
