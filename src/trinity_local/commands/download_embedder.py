"""trinity-local download-embedder — pull the modernbert-embed-base weights.

The CLI gate added in tick e6d1d44 surfaces a clear error when the
embedder model isn't on disk. Before this verb, the error told the
user to run `huggingface-cli download nomic-ai/modernbert-embed-base`
— a raw external command that's awkward to surface in agent UX and
requires the user to know HF cache semantics.

This wraps the same setup in a Trinity verb. The error from
require_embedder_ready() now points users at this command instead of
the raw huggingface-cli line. Same on-disk result; in-product story.

Status messages match the CLI gate's framing:
  - Pre-download: "Trinity is downloading the memory model (~600 MB)…"
  - Post-download: "Model ready. Re-run your previous command."

Idempotent: re-running on an already-downloaded model is a fast no-op
since SentenceTransformer caches in ~/.cache/huggingface/hub/.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "download-embedder",
        help="Download the modernbert-embed-base memory model (~600 MB). "
             "Required for lens-build / dream / vocabulary.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download even if the model is already in the HF cache. "
             "Useful when a previous download was interrupted.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of progress lines.",
    )
    parser.set_defaults(handler=handle_download_embedder)


def handle_download_embedder(args: SimpleNamespace) -> int:
    """Download the embedder model. Returns 0 on success, 1 on failure.

    Idempotent and side-effect-free: if the model is already in the
    HF cache, SentenceTransformer returns it immediately without
    re-downloading.
    """
    force = bool(getattr(args, "force", False))
    json_mode = bool(getattr(args, "json", False))

    if not json_mode:
        print(
            "Downloading modernbert-embed-base (~600 MB) to "
            "~/.cache/huggingface/hub/ — this is a one-time download. "
            "Trinity won't contact the Hub again once it's cached.",
            file=sys.stderr,
        )

    from ..embeddings import setup_model

    message = setup_model(force=force)

    # setup_model returns a human-readable status string. Distinguish
    # success/failure by sniffing the message — the underlying
    # download_model() helper writes "Model ready: ..." on success
    # and "Download failed: ..." / "MLX dependencies not installed" on
    # failure. Keep this in sync with backend_mlx.download_model.
    success = message.startswith("Model ready")

    if json_mode:
        print(json.dumps({
            "ok": success,
            "message": message,
            "force": force,
        }, indent=2))
    else:
        if success:
            print(f"✓ {message}")
            print(
                "Re-run your previous command (lens-build / dream / vocabulary). "
                "The model lives in ~/.cache/huggingface/hub/ and never "
                "re-downloads.",
                file=sys.stderr,
            )
        else:
            print(f"✗ {message}", file=sys.stderr)
    return 0 if success else 1
