"""`trinity-local vocabulary` — Phase 2.5 stand-alone.

Scans the prompt corpus and emits ~/.trinity/memories/vocabulary.md with
two sections: anchors (proper nouns you return to across threads) and
homonyms (one word, multiple meanings). Also runs as dream Phase 2.5.

Pure-geometric — no LLM call. Reads PromptNode embeddings and scores each
token's contexts for bimodality.
"""
from __future__ import annotations

import json


def register(subparsers):
    sp = subparsers.add_parser(
        "vocabulary",
        help="Scan your prompts for recurring proper nouns and overloaded terms (one word ↔ two meanings). Emits ~/.trinity/memories/vocabulary.md.",
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
    sp.set_defaults(handler=handle_vocabulary)


def handle_vocabulary(args):
    # Fail fast if the embedder model isn't downloaded — the homonym scan
    # clusters each token's context embeddings.
    # Without this gate the user gets a multi-minute startup followed
    # by an HF_HUB_OFFLINE error.
    import sys
    from ..embeddings import EmbedderNotReadyError, require_real_embedder
    try:
        require_real_embedder()
    except EmbedderNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    from ..vocabulary import distill_vocabulary

    report = distill_vocabulary(
        min_freq=args.min_freq,
        top_homonyms=args.top_homonyms,
    )
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1
