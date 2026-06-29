"""Shared helpers for chunked bulk-ingest of transcript exports.

Three callers use this engine:

  - `commands/import_export.py` — auto-detect Takeout / claude.ai /
    chatgpt-export at any path (production-facing, task #148)
  - `incremental_ingest.py` — tool-triggered cursor-based ingest fired
    from MCP `ask` + the Chrome extension on every fresh capture
  - (formerly) `commands/seed.py` — personal-rig
    `~/projects/taste-terminal/data/exports/` ingester (retired
    2026-05-27, see retired_names.py)

The helpers were originally defined inside `commands/seed.py`. When
the seed CLI was retired, the live callers (import-export + the
incremental-ingest hot path) needed them out of the dead module.
This file is that consolidation. Bonus: the dedup-set lookup now
uses the optimized `iter_prompt_nodes_no_embedding` variant
universally — it skips the 768-element float-array parse per row
(~1.85s saved on the maintainer's 1GB corpus), which is correct
since the dedup set only reads `node.id`.

Functions:

  - `existing_prompt_node_ids()` — build a `set[str]` of every
    PromptNode id currently on disk. Uncapped; the dedup contract
    needs to see every prior ID, not just the 5000 most-recent.
  - `stage_session(session, existing_ids)` — Phase 1: parse one
    SessionRecord into a chunk-ready dict, or None when there's
    nothing to do (no turns or all already-indexed).
  - `flush_chunk(staged, existing_ids, *, dim, batch_size)` —
    Phase 2+3: embed all staged texts in one batched call, then
    write PromptNodes (and TurnWindows for multi-turn sessions).
"""
from __future__ import annotations

from typing import Any

from .memory import PromptNode, TurnWindow, upsert_prompt_node, upsert_turn_window
from .memory.store import iter_prompt_nodes_no_embedding
from .task_types import guess_task_type
from .utils import now_iso, stable_id


def existing_prompt_node_ids() -> set[str]:
    """Return the set of every PromptNode id currently on disk.

    Uncapped (limit=None) — the dedup contract needs EVERY existing ID,
    not just the 5000 most-recent. Without this, re-running a bulk
    ingest against an 18k-node corpus would re-ingest the older 13k as
    if they were new (file bloats; subsequent loads slow down).

    Uses ``iter_prompt_nodes_no_embedding`` so we skip the
    768-element float array parse per row — the dedup set only reads
    ``node.id``, so paying ~1.85s of embedding-array parse per cold
    call (real 1GB corpus) is pure waste.
    """
    return {node.id for node in iter_prompt_nodes_no_embedding(limit=None)}


def _embed_in_batches(texts: list[str], *, dim: int, batch_size: int) -> list[list[float]]:
    from .embeddings import embed_batch

    if not texts:
        return []
    return embed_batch(texts, dim=dim, batch_size=batch_size)


def _truncate_to_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n[...]\n" + text[-half:]


def _build_window_text(turn: Any) -> str:
    """Compact context window for TurnWindow embedding.

    Capped at 1500 chars. Longer windows blow up MPS memory during
    batched encode without improving retrieval quality — Nomic gets
    diminishing returns past ~1.5k chars and the 8192-token ceiling
    is a *capacity*, not a target.
    """
    chunks: list[str] = []
    if turn.preceding_assistant_text:
        chunks.append(f"Previous assistant: {turn.preceding_assistant_text}")
    chunks.append(f"User: {turn.text}")
    if turn.following_assistant_text:
        chunks.append(f"Assistant: {turn.following_assistant_text}")
    return _truncate_to_chars("\n\n".join(chunks), 1500)


def stage_session(session: Any, existing_ids: set[str]) -> dict | None:
    """Phase 1: walk a session, decide what needs embedding.

    Returns a 'staged session' dict the chunk processor will consume,
    or None if the session has nothing to do (no turns, all already
    indexed).
    """
    from .ingest import iter_prompt_turns

    turns = list(iter_prompt_turns(session))
    if not turns:
        return None

    node_ids = [
        stable_id("pnode", t.transcript_id, str(t.turn_index), t.text[:200])
        for t in turns
    ]
    if all(node_id in existing_ids for node_id in node_ids):
        return {"already_indexed": True, "session": session}

    keepers: list[tuple] = []
    prompt_texts: list[str] = []
    for turn, node_id in zip(turns, node_ids):
        if node_id in existing_ids:
            continue
        prompt_texts.append(f"search_document: {turn.text}")
        keepers.append((turn, node_id, guess_task_type(turn.text)))

    is_multi_turn = len(turns) > 1
    window_texts: list[str] = []
    if is_multi_turn:
        for turn, _, _ in keepers:
            window_texts.append(f"search_document: {_build_window_text(turn)}")

    return {
        "already_indexed": False,
        "session": session,
        "keepers": keepers,
        "prompt_texts": prompt_texts,
        "window_texts": window_texts,
        "is_multi_turn": is_multi_turn,
    }


def flush_chunk(
    staged: list[dict],
    existing_ids: set[str],
    *,
    dim: int,
    batch_size: int,
) -> tuple[int, int, int]:
    """Phase 2 + 3: embed all texts in the chunk in ONE batched call,
    then write all PromptNodes / TurnWindows.

    Returns (prompts_written, windows_written, transcripts_written).
    The TranscriptNode tier was retired in Tier 2 #5 (task #51); the
    third element is always 0 for backwards-compat with the progress
    reporter's existing tuple shape.
    """
    texts: list[str] = []
    for s in staged:
        if s["already_indexed"]:
            continue
        texts.extend(s["prompt_texts"])
        texts.extend(s["window_texts"])

    if not texts:
        return (0, 0, 0)

    vectors = _embed_in_batches(texts, dim=dim, batch_size=batch_size)

    prompts_written = 0
    windows_written = 0
    cursor = 0

    for s in staged:
        if s["already_indexed"]:
            continue
        keepers = s["keepers"]
        is_multi_turn = s["is_multi_turn"]

        n_prompts = len(s["prompt_texts"])
        prompt_vectors = vectors[cursor:cursor + n_prompts]
        cursor += n_prompts

        session_node_records: list[tuple[PromptNode, list[float]]] = []
        for (turn, node_id, theme), embedding in zip(keepers, prompt_vectors):
            node = PromptNode(
                id=node_id,
                transcript_id=turn.transcript_id,
                provider=turn.provider,
                source_path=turn.source_path,
                turn_index=turn.turn_index,
                text=turn.text,
                embedding=embedding,
                created_at=now_iso(),
                timestamp=turn.timestamp,
                preceding_assistant_text=turn.preceding_assistant_text,
                following_assistant_text=turn.following_assistant_text,
                themes=[theme] if theme else [],
            )
            upsert_prompt_node(node)
            existing_ids.add(node_id)
            prompts_written += 1
            session_node_records.append((node, embedding))

        if is_multi_turn and session_node_records:
            n_windows = len(s["window_texts"])
            window_vectors = vectors[cursor:cursor + n_windows]
            cursor += n_windows
            for (node, _), window_text, vec in zip(
                session_node_records,
                [w.split("search_document: ", 1)[-1] for w in s["window_texts"]],
                window_vectors,
            ):
                window = TurnWindow(
                    id=stable_id("twin", node.transcript_id, str(node.turn_index)),
                    transcript_id=node.transcript_id,
                    center_prompt_id=node.id,
                    text=window_text,
                    embedding=vec,
                    turn_start=max(0, node.turn_index - 1),
                    turn_end=node.turn_index + 1,
                )
                upsert_turn_window(window)
                windows_written += 1

    return (prompts_written, windows_written, 0)
