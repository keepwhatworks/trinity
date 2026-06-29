"""`lens-prompt` + `lens-import` — provider-side lens loop.

User direction this session: stop relying on the Chrome extension to
scrape transcripts for lens-build. Providers (Claude, ChatGPT/Codex,
Gemini) have the user's full conversation history on their side — ask
them to synthesize a lens directly, then merge their JSON output into
Trinity's local lens state.

Two verbs ship together because they're two halves of the same loop:

* ``lens-prompt`` — prints the canonical prompt (from
  ``docs/lens-from-provider.md``) so the user can pipe to ``pbcopy`` /
  ``xclip`` and paste into any provider chat.

* ``lens-import`` — reads the JSON the provider returns and merges it
  into ``~/.trinity/me/lenses.json`` and ``orderings.json`` (same files
  the local ``lens-build`` pipeline writes). Provider-imported tensions
  are tagged ``verdict="imported"`` so they coexist with locally-built
  ``verdict="accepted"`` ones without overwriting.

Schema mapping is in ``_provider_dict_to_lens_pair`` — keep it in sync
with ``docs/lens-from-provider.md`` if either side changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ..me.pair_mining import (
    LensPair,
    VALID_HORIZONS,
    load_lenses,
    load_orderings,
    save_lenses,
)
from .provider_import import read_provider_import


# Resolved at runtime because the docs/ tree sits at the project root
# in dev, but the same file may live under the installed package dir
# once we ship as a wheel. Resolve once at module import.
def _prompt_doc_path() -> Path | None:
    """Return the on-disk path to ``docs/lens-from-provider.md`` if we
    can find it, else None. Checked in this order:

    1. Repo-relative (running from a git checkout):
       ``<repo>/docs/lens-from-provider.md`` where <repo> = parents[3]
       of this file.
    2. ``$TRINITY_LENS_PROMPT_DOC`` env var (escape hatch for installs
       where the docs tree isn't shipped alongside the package).
    """
    import os
    override = os.environ.get("TRINITY_LENS_PROMPT_DOC")
    if override:
        p = Path(override).expanduser()
        return p if p.exists() else None
    # src/trinity_local/commands/lens_import.py → parents[3] is the repo root
    candidate = Path(__file__).resolve().parents[3] / "docs" / "lens-from-provider.md"
    return candidate if candidate.exists() else None


def register(subparsers):
    prompt = subparsers.add_parser(
        "lens-prompt",
        help=(
            "Print the canonical provider-side lens prompt (paste into "
            "Claude/Codex/Gemini, save JSON, then `lens-import`)."
        ),
    )
    prompt.add_argument(
        "--with-instructions",
        action="store_true",
        help=(
            "Include the full doc (intro + verification steps), not just "
            "the prompt body. Default: just the prompt body, so the "
            "common `lens-prompt | pbcopy` flow lands a clean paste."
        ),
    )
    prompt.set_defaults(handler=handle_lens_prompt)

    imp = subparsers.add_parser(
        "lens-import",
        help=(
            "Merge a provider's JSON-shaped lens output (see "
            "docs/lens-from-provider.md) into ~/.trinity/me/lenses.json "
            "+ orderings.json."
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
        help=(
            "Read the JSON payload from stdin instead of a file path. "
            "Same `--from-json` convention used by `eval-import`."
        ),
    )
    imp.add_argument(
        "--provider",
        default=None,
        help=(
            "Override the `source_provider` field in the payload. Useful "
            "when the provider's JSON omits it or you want to attribute "
            "the import differently (e.g. `--provider claude` when "
            "ingesting a hand-rewritten payload)."
        ),
    )
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + print merge plan; do not write to lenses.json.",
    )
    imp.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit structured JSON to stdout instead of the human summary.",
    )
    imp.set_defaults(handler=handle_lens_import)


# ---------------------------------------------------------------------
# lens-prompt
# ---------------------------------------------------------------------

_PROMPT_BODY_MARKER = "## The prompt — copy below this line"


def handle_lens_prompt(args) -> int:
    doc = _prompt_doc_path()
    if doc is None:
        print(
            "error: couldn't find docs/lens-from-provider.md. Set "
            "TRINITY_LENS_PROMPT_DOC to its absolute path if you're "
            "running from a non-checkout install.",
            file=sys.stderr,
        )
        return 1
    text = doc.read_text(encoding="utf-8")
    if args.with_instructions:
        sys.stdout.write(text)
        return 0
    # Default: just the prompt body so `lens-prompt | pbcopy` is paste-ready.
    idx = text.find(_PROMPT_BODY_MARKER)
    if idx < 0:
        # Doc lost its anchor marker — fall back to full content so we
        # don't silently print nothing. Surface a warning.
        print(
            "warning: prompt-body marker not found in doc; emitting "
            "full file. Update docs/lens-from-provider.md to restore "
            f"the '{_PROMPT_BODY_MARKER}' anchor.",
            file=sys.stderr,
        )
        sys.stdout.write(text)
        return 0
    # Skip past the marker line itself + the blank line after it.
    after = text[idx:].split("\n", 2)
    body = after[-1] if len(after) == 3 else text[idx:]
    sys.stdout.write(body)
    return 0


# ---------------------------------------------------------------------
# lens-import
# ---------------------------------------------------------------------


def _provider_dict_to_lens_pair(t: dict, source_provider: str) -> LensPair | None:
    """Map a single provider 'tension' dict → LensPair.

    Returns None when the input is missing required fields rather than
    raising — the import command surfaces a per-item warning and skips,
    so one malformed tension doesn't abort the whole import.
    """
    pole_a = (t.get("pole_a") or "").strip()
    pole_b = (t.get("pole_b") or "").strip()
    if not pole_a or not pole_b or pole_a == pole_b:
        return None
    failure_a = (t.get("failure_a") or "").strip()
    failure_b = (t.get("failure_b") or "").strip()
    if not failure_a or not failure_b:
        return None
    horizon = (t.get("horizon") or "tactical").strip().lower()
    if horizon not in VALID_HORIZONS:
        horizon = "tactical"
    evidence = [str(e).strip() for e in (t.get("evidence") or []) if str(e).strip()]
    confidence = (t.get("confidence") or "medium").strip().lower()
    why_matters = (t.get("why_matters") or "").strip()
    # `dual_evidence` is the existing field shape ({pole_name: [evidence_id, ...]}).
    # We piggy-back metadata that has no native LensPair home into it —
    # downstream consumers that don't know about these keys ignore them.
    dual = {
        "source_provider": [source_provider],
        "confidence": [confidence],
    }
    if why_matters:
        dual["why_matters"] = [why_matters]
    return LensPair(
        pole_a=pole_a,
        pole_b=pole_b,
        failure_a=failure_a,
        failure_b=failure_b,
        tension_decisions=evidence,
        dual_evidence=dual,
        basins_spanned=[],
        verdict="imported",
        horizon=horizon,
    )


def _provider_dict_to_ordering_pair(o: dict, source_provider: str) -> LensPair | None:
    """Map a single provider 'ordering' dict → LensPair (verdict=imported_ordering)."""
    pole_a = (o.get("pole_a") or "").strip()
    pole_b = (o.get("pole_b") or "").strip()
    if not pole_a or not pole_b or pole_a == pole_b:
        return None
    evidence = [str(e).strip() for e in (o.get("evidence") or []) if str(e).strip()]
    return LensPair(
        pole_a=pole_a,
        pole_b=pole_b,
        failure_a="",  # orderings have no dual-regret structure
        failure_b="",
        tension_decisions=evidence,
        dual_evidence={"source_provider": [source_provider]},
        basins_spanned=[],
        verdict="imported_ordering",
        horizon="tactical",
    )


def _normalize_pole_pair(pair: LensPair) -> tuple[str, str]:
    """Canonical key for de-dup: lowercased poles, order-independent."""
    a, b = pair.pole_a.strip().lower(), pair.pole_b.strip().lower()
    # order-independent so (focus ↔ breadth) and (breadth ↔ focus) collide
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def _merge(
    existing: list[LensPair],
    incoming: list[LensPair],
) -> tuple[list[LensPair], int, int]:
    """Merge incoming into existing; return (merged, new_count, augmented_count).

    Policy:
    - If poles already present (normalized match): augment evidence and
      dual_evidence — never overwrite the existing LensPair (preserves
      locally-built `verdict="accepted"` data).
    - Else: append.
    """
    by_key: dict[tuple[str, str], LensPair] = {_normalize_pole_pair(p): p for p in existing}
    new_count = 0
    augmented_count = 0
    for pair in incoming:
        key = _normalize_pole_pair(pair)
        if key in by_key:
            existing_pair = by_key[key]
            # Append new evidence (de-duped) and merge dual_evidence lists.
            seen_evidence = set(existing_pair.tension_decisions)
            for ev in pair.tension_decisions:
                if ev not in seen_evidence:
                    existing_pair.tension_decisions.append(ev)
                    seen_evidence.add(ev)
            for k, vs in pair.dual_evidence.items():
                merged_list = list(existing_pair.dual_evidence.get(k, []))
                for v in vs:
                    if v not in merged_list:
                        merged_list.append(v)
                existing_pair.dual_evidence[k] = merged_list
            augmented_count += 1
        else:
            existing.append(pair)
            by_key[key] = pair
            new_count += 1
    return existing, new_count, augmented_count


def handle_lens_import(args) -> int:
    # Shared front-end (read → parse → dict-guard → source_provider → the
    # present-but-wrong-type list guard) with eval-import; the rest is lens-specific.
    _read = read_provider_import(args, list_fields=("tensions", "orderings"))
    if isinstance(_read, int):
        return _read
    payload, source_provider = _read
    raw_tensions = payload.get("tensions") or []
    raw_orderings = payload.get("orderings") or []

    # Map + skip malformed.
    incoming_tensions: list[LensPair] = []
    skipped_tensions = 0
    for t in raw_tensions:
        if not isinstance(t, dict):
            skipped_tensions += 1
            continue
        mapped = _provider_dict_to_lens_pair(t, source_provider)
        if mapped is None:
            skipped_tensions += 1
            continue
        incoming_tensions.append(mapped)

    incoming_orderings: list[LensPair] = []
    skipped_orderings = 0
    for o in raw_orderings:
        if not isinstance(o, dict):
            skipped_orderings += 1
            continue
        mapped = _provider_dict_to_ordering_pair(o, source_provider)
        if mapped is None:
            skipped_orderings += 1
            continue
        incoming_orderings.append(mapped)

    # Load existing local state, merge, save.
    existing_lenses = load_lenses()
    existing_orderings = load_orderings()
    merged_lenses, lens_new, lens_aug = _merge(list(existing_lenses), incoming_tensions)
    merged_orderings, ord_new, ord_aug = _merge(
        list(existing_orderings), incoming_orderings
    )

    result = {
        "ok": True,
        "source_provider": source_provider,
        "tensions": {
            "incoming": len(incoming_tensions),
            "new": lens_new,
            "augmented": lens_aug,
            "skipped_malformed": skipped_tensions,
        },
        "orderings": {
            "incoming": len(incoming_orderings),
            "new": ord_new,
            "augmented": ord_aug,
            "skipped_malformed": skipped_orderings,
        },
        "dry_run": bool(args.dry_run),
    }

    if not args.dry_run:
        lp, op = save_lenses(merged_lenses, merged_orderings)
        result["lenses_path"] = str(lp)
        result["orderings_path"] = str(op)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        if args.dry_run:
            print(f"DRY-RUN — would import from provider '{source_provider}'")
        else:
            print(f"imported from provider '{source_provider}'")
        t = result["tensions"]
        o = result["orderings"]
        print(
            f"  tensions:  {t['new']} new, {t['augmented']} augmented "
            f"(skipped {t['skipped_malformed']} malformed)"
        )
        print(
            f"  orderings: {o['new']} new, {o['augmented']} augmented "
            f"(skipped {o['skipped_malformed']} malformed)"
        )
        if not args.dry_run:
            print(f"  → {result['lenses_path']}")
            print(f"  → {result['orderings_path']}")
    return 0
