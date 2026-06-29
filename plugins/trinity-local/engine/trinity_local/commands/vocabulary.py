"""`trinity-local vocabulary` — Phase 2.5 stand-alone.

Scans the prompt corpus and emits ~/.trinity/memories/vocabulary.md with
two sections: homonyms (one word, multiple meanings) and synonyms
(multiple words, one meaning). Also runs as dream Phase 2.5.

Pure-geometric — no LLM call. Reads PromptNode embeddings, applies the
same bimodality machinery cortex uses for its routing patterns, applied
to the user's tokens.
"""
from __future__ import annotations

import json


def register(subparsers):
    sp = subparsers.add_parser(
        "vocabulary",
        help="Scan your prompts for terminology overloads (one word ↔ two meanings; two words ↔ one meaning). Emits ~/.trinity/memories/vocabulary.md.",
    )
    sp.add_argument(
        "--min-freq",
        type=int,
        default=5,
        help="Minimum occurrences for a token to qualify (default: 5).",
    )
    sp.add_argument(
        "--top-homonyms",
        type=int,
        default=10,
        help="How many bimodal tokens to surface (default: 10).",
    )
    sp.add_argument(
        "--top-synonyms",
        type=int,
        default=10,
        help="How many synonym candidate pairs to surface (default: 10).",
    )
    sp.add_argument(
        "--synonym-threshold",
        type=float,
        default=0.92,
        help="Cosine similarity floor for a synonym pair (default: 0.92).",
    )
    sp.set_defaults(handler=handle_vocabulary)


def handle_vocabulary(args):
    # Fail fast if the embedder model isn't downloaded — vocabulary
    # distillation uses synonym embeddings to cluster anchor terms.
    # Without this gate the user gets a multi-minute startup followed
    # by an HF_HUB_OFFLINE error.
    import sys
    from ..embeddings import EmbedderNotReadyError, require_embedder_ready
    try:
        require_embedder_ready()
    except EmbedderNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    from ..vocabulary import distill_vocabulary

    report = distill_vocabulary(
        min_freq=args.min_freq,
        top_homonyms=args.top_homonyms,
        top_synonyms=args.top_synonyms,
        synonym_threshold=args.synonym_threshold,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1
