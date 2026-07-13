"""Compose `~/.trinity/memories/lens.md` via the transcript lens pipeline.

The live `lens` build is the 5-stage pipeline in `build_me_via_lens_pipeline`:
turn-pair preference acts, basin topology, decision extraction, paired-tension
mining, then deterministic post-filter/render. `lens --deep` runs the same
build after mining cross-provider history. The former single-chairman
`build_me_via_council` path was thinner than the pipeline and is retired.
"""
from __future__ import annotations

import sys as _sys

import dataclasses
import re
from pathlib import Path


# A numbered tension heading the renderer emits per paired tension, e.g.
# "### 1. concrete ↔ abstract" (render_me_markdown, me/pipeline.py). Its
# presence in an existing lens.md is the unambiguous signal that the lens is
# already POPULATED with real taste axes — distinct from the cold-start
# "(No paired tensions found yet …)" placeholder, which has no such heading.
# Kept in sync with the renderer by test_lens_clobber_guard.py, which renders a
# real LensPair and asserts this pattern matches it (anti-rot, principle #33).
_POPULATED_TENSION_HEADING = re.compile(r"^### \d+\. .+ ↔ ", re.MULTILINE)


def _would_clobber_populated_lens(
    has_new_tensions: bool, existing_text: str | None
) -> bool:
    """Return True when writing the freshly-rendered lens would silently
    DOWNGRADE an already-populated lens.md to a tension-less placeholder.

    The 5-stage path's protection against a poisoned/empty Stage 3 is the
    tension *registry* (Stage 4.5): even when this run extracts zero pairs,
    the lens re-renders from the accumulated registry. But the registry layer
    is wrapped in try/except — "accretion is additive, never load-bearing" —
    so a registry-read exception (e.g. a schema-version skew after an upgrade)
    coinciding with an empty Stage 3 (chairman timeout / quota exhaustion)
    leaves render_pairs == [] and render_me_markdown emits the
    "(No paired tensions found yet …)" placeholder. Writing that over the
    founder's accumulated 16-tension lens silently strips the chairman's
    primary signal until the next good build.

    Fires ONLY on a genuine downgrade — a cold start (no file) or an existing
    placeholder writes normally, because there's nothing populated to protect.
    """
    if has_new_tensions:
        return False  # the new render carries real tensions — never a downgrade
    if not existing_text:
        return False  # cold start — write the placeholder, nothing to preserve
    return bool(_POPULATED_TENSION_HEADING.search(existing_text))


# Sampling size: enough turns for the chairman to detect patterns, small
# enough to fit in a single prompt with their preceding-assistant context.
ME_SAMPLE_SIZE = 80

# Stage 0 turn-pair batch size (#195). 200 pairs in one prompt was
# ~37K tokens, which claude -p returned EMPTY for. Lowered 40→20 after a
# real run showed a 40-pair batch could still exceed the 8-min per-call
# timeout: smaller batches mean shorter per-call generation, returning
# well under the ceiling (paired with the low-effort extractor). Each
# batch parses independently; rejections accumulate and save once (so the
# #194 clobber guard sees the full count).
_STAGE0_BATCH_SIZE = 20


# Stage 0 batch concurrency (#3). The batches are independent (each
# classifies a disjoint set of turn-pairs), so they run in parallel —
# blocking `claude -p` subprocess calls, so threads (I/O-bound) suffice.
# Capped to avoid spawning a swarm of chairman subprocesses that contend
# on rate limits / cold-start; 4 is a conservative middle ground.
_STAGE0_MAX_CONCURRENCY = 4


def _lens_build_state_path() -> Path:
    """`~/.trinity/me/lens_build_state.json` — records the corpus
    fingerprint of the last successful build, so an unchanged rebuild can
    skip the whole pipeline (#1 skip-if-unchanged)."""
    from .me.basins import me_dir
    return me_dir() / "lens_build_state.json"


def _corpus_fingerprint() -> str:
    """A cheap content fingerprint of the prompt corpus: count + a hash of
    the prompt-node ids. If it matches the last successful build's, nothing
    new was ingested and the lens is already current — skip (no model
    calls). Reading the id index is ~1s vs the ~22s embedder load a full
    build pays, so the gate saves the most by running first."""
    import hashlib

    from .memory.store import iter_prompt_nodes

    ids = sorted(
        (getattr(n, "id", "") or "") for n in iter_prompt_nodes(limit=None)
    )
    digest = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:16]
    return f"{len(ids)}:{digest}"


def _extracted_pair_ids() -> set[str]:
    """The set of turn-pair prompt_ids Stage 0 has already classified in a
    prior successful build (#210 delta-extraction). A pair in this set —
    whether or not it yielded a rejection — is NOT re-sent to the chairman;
    its rejections (if any) are reloaded from the ledger instead. Read from
    ``lens_build_state.json``; empty on cold start or malformed state."""
    import json as _json

    sp = _lens_build_state_path()
    if not sp.exists():
        return set()
    try:
        data = _json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    ids = data.get("extracted_pair_ids")
    return set(ids) if isinstance(ids, list) else set()


def _stage0_batch_failed(result) -> bool:
    """True if a Stage 0 chairman batch call failed (#203). A timed-out
    call sets returncode == -1 (providers._run_command sentinel); an empty
    response (the #195 cliff) yields blank stdout. Either means the batch
    contributed nothing — and since the loop persists the ACCUMULATED set
    once at the end, accepting a failed batch would silently save a partial
    corpus that slips past the #194 clobber guard (which only catches a
    near-total cliff-drop). The caller aborts the whole build instead."""
    return getattr(result, "returncode", 0) == -1 or not (result.stdout or "").strip()


def me_path() -> Path:
    """The lens file. Renamed from `me.md` → `memories/lens.md` per the
    brand axis (lens is one of the three thinking memories in the
    post-v1.7 lens hierarchy: lens.md tensions, topics.json basins,
    vocabulary.md anchors). The migration happens automatically inside
    state_paths.memories_dir() on first access; callers don't need to
    handle it. Back-compat alias kept so existing imports still work."""
    from .state_paths import lens_path
    return lens_path()


def _sample_diverse_with_embeddings(*, top_k: int, candidate_pool: int) -> list:
    """Pull recent PromptNodes and pick top_k via rejection-aware MMR.

    Three signals are combined:
      - quality: replay_value heuristic (high-signal prompts)
      - diversity: embedding distance from already-selected (MMR)
      - rejection_signal: cosine distance between (preceding_assistant_text
        embedding, user text embedding) for each candidate. High distance
        means the user said something semantically far from what the model
        had just said — the rejection-flavored pairwise data the chairman
        builds /me's "Implicit rejections" section from.

    The rejection signal requires embedding the assistant texts at runtime
    (seed only stored embeddings for user prompts). Loads nomic; ~10s extra
    on the cron-scheduled lens-build.

    Returns SearchResult-shaped objects. Falls back to None when embeddings
    or numpy are unavailable.
    """
    try:
        import numpy as np
    except ImportError:
        return None

    from .embeddings import embed_batch
    from .memory import iter_prompt_nodes
    from .memory.index import SearchResult
    from .memory.replay_value import (
        infer_hardness,
        replay_value_score,
        staleness_score,
        theme_score,
    )

    # Quality filter: drop very short prompts AND validate embedding shape.
    # `n.embedding` could be wrong-dim (legacy 4d test fixtures, partial
    # writes), contain NaN/Inf (numpy poisons MMR), or be empty. The chairman
    # can't extract patterns from "No." or "ok thanks." either.
    from .embeddings import is_finite_embedding
    EXPECTED_DIM = 768

    def _valid_embedding(emb) -> bool:
        return is_finite_embedding(emb) and len(emb) == EXPECTED_DIM

    nodes = [
        n for n in iter_prompt_nodes(limit=candidate_pool)
        if _valid_embedding(n.embedding) and len((n.text or "").strip()) >= 60
    ]
    if len(nodes) < top_k:
        return None

    # Score each by replay value. The chairman gets prompts that are
    # high-signal AND diverse, not just diverse.
    quality_scores: list[float] = []
    for n in nodes:
        recently_run = 1.0 if staleness_score(n.last_replayed_at) < 0.25 else 0.0
        q = replay_value_score(
            prompt_similarity=0.0,
            known_theme=theme_score(n.themes),
            uncertainty=infer_hardness(n),
            importance=n.importance or 0.0,
            staleness=staleness_score(n.last_replayed_at),
            recently_run=recently_run,
        )
        quality_scores.append(q)
    quality = np.asarray(quality_scores, dtype=np.float32)

    matrix = np.asarray([n.embedding for n in nodes], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix_n = matrix / norms
    similarities = matrix_n @ matrix_n.T

    # Rejection-signal: embed the preceding_assistant_text for each candidate
    # and compute cosine distance to the user's prompt embedding. High distance
    # = the user said something semantically far from the model's preceding
    # turn = a redirect/rejection candidate. Pairs with no preceding context
    # (e.g. session openers) get a neutral 0.0 — the chairman extracts other
    # patterns from those, and they don't crowd out true rejections.
    asst_texts = [(n.preceding_assistant_text or "").strip() for n in nodes]
    has_asst = [bool(t) for t in asst_texts]
    rejection_signal = np.zeros(len(nodes), dtype=np.float32)
    if any(has_asst):
        # Truncate so the embed call is bounded — nomic still captures topic
        # well from ~600 chars of assistant prefix.
        embed_inputs = [
            f"search_document: {t[:600]}" if h else "search_document: -"
            for t, h in zip(asst_texts, has_asst)
        ]
        try:
            asst_vecs = embed_batch(embed_inputs, dim=768)
        except Exception:
            asst_vecs = None
        if asst_vecs:
            asst_matrix = np.asarray(asst_vecs, dtype=np.float32)
            asst_norms = np.linalg.norm(asst_matrix, axis=1, keepdims=True)
            asst_norms[asst_norms == 0] = 1.0
            asst_n = asst_matrix / asst_norms
            # Cosine sim, then convert to distance. Same shape as user matrix.
            cos = (asst_n * matrix_n).sum(axis=1)
            distance = (1.0 - cos).clip(0.0, 1.5)
            # Only count distance for rows that actually had assistant context;
            # zero-context rows stay at the neutral 0.0 floor.
            mask = np.asarray(has_asst, dtype=np.float32)
            rejection_signal = (distance * mask).astype(np.float32)

    # Combined "score" for the seed pick AND the MMR objective: quality
    # baseline + rejection bonus (REJECTION_WEIGHT=0.4 chosen so a strong
    # rejection-signal pair can outrank a moderately-higher-quality but flat
    # pair). Tuned by inspection — the chairman explicitly asks for the
    # rejection cards, so we want those over-represented in the sample.
    REJECTION_WEIGHT = 0.4
    base_score = quality + REJECTION_WEIGHT * rejection_signal

    # Seed: highest combined score. Subsequent picks maximize the standard
    # MMR objective using the combined score as quality.
    LAMBDA = 0.6
    selected: list[int] = [int(np.argmax(base_score))]
    while len(selected) < top_k:
        max_sim_to_selected = similarities[:, selected].max(axis=1)
        max_sim_to_selected[selected] = 1.0
        mmr = LAMBDA * base_score - (1.0 - LAMBDA) * max_sim_to_selected
        mmr[selected] = -np.inf
        next_idx = int(np.argmax(mmr))
        if mmr[next_idx] == -np.inf:
            break
        selected.append(next_idx)

    return [
        SearchResult(
            prompt_id=node.id,
            text=node.text,
            score=float(base_score[i]),
            prompt_similarity=float(rejection_signal[i]),
            window_similarity=0.0,
            transcript_similarity=0.0,
            hardness=infer_hardness(node),
            reasons=(
                ["Rejection signal"] if rejection_signal[i] > 0.4 else ["Diverse sample"]
            ),
            chairman_winner=node.chairman_winner,
            council_count=len(node.council_run_ids),
            provider=node.provider,
            timestamp=node.timestamp,
            preceding_assistant_text=node.preceding_assistant_text or "",
            transcript_id=node.transcript_id,
            turn_index=node.turn_index,
        )
        for i, node in ((i, nodes[i]) for i in selected)
    ]


def _stage_run_with_fallback(prompt, config, chairman, cwd, *, low_effort=False):
    """Run a lens-build stage chairman call, falling back through the other
    enabled providers when the primary is rate-limited / token-exhausted (same
    resilience as the council chairman fallback). `low_effort` runs each
    candidate at effort=low (the mechanical Stage 0/2 extraction).

    Returns a usable ProviderResult, or — if every chair fails — a final empty
    one so the stage's existing empty-output handling degrades gracefully
    instead of crashing the build."""
    from .providers import (
        ProviderResult,
        _result_is_usable,
        chairman_fallback_order,
        make_provider,
    )

    order = chairman_fallback_order(config, chairman) if config else [chairman]
    last = None
    for name in order:
        cfg = config.providers.get(name) if config else None
        if cfg is None or not getattr(cfg, "enabled", False):
            continue
        if low_effort:
            cfg = dataclasses.replace(cfg, effort="low")
        try:
            res = make_provider(cfg).run(prompt, cwd)
        except Exception as exc:  # noqa: BLE001 — any failure → next chair
            last = f"{name}: {exc}"
            continue
        if _result_is_usable(res):
            return res
        last = f"{name}: unusable (rc={res.returncode})"
    return ProviderResult(
        provider=chairman, stdout="", stderr=str(last or "all chairmen failed"),
        returncode=1,
    )


def build_me_via_lens_pipeline(
    *,
    sample_size: int = ME_SAMPLE_SIZE,
    k_basins: int | None = None,
    seed: int = 42,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[Path, dict]:
    """Run the 5-stage lens-discovery pipeline (Option C + Stage 0).

    Stage 0: turn-pair gap extraction (chairman batch call; rejection
             signals — REFRAME / COMPRESSION / REDIRECT / SHARPENING —
             with deterministic post-validators in me/turn_pairs.py)
    Stage 1: numpy k-means basins (no LLM)
    Stage 2: chairman extracts decisions.jsonl
    Stage 3: chairman applies the three tests + JSON verifier contract
             over decisions.jsonl (single chairman call for the first
             cut; the wrapping 3-member council via run_council is a
             forward-arc item — see inline comment at the call site)
    Stage 4: deterministic basin post-filter — drops single-basin pairs

    `dry_run=True` runs Stage 1 + sampling only (no LLM calls), useful
    to inspect the corpus topology before committing to a full rebuild.

    Stage 0 was ratified into the pipeline by council_6892781d06ac3fa8
    (highest-leverage import from taste-terminal) + council_e7560934cb1f1d72
    (Option A with deterministic post-validators); see me/turn_pairs.py.
    """
    from .config import load_config
    from .me.pipeline import (
        collect_turn_pairs,
        render_me_markdown,
        stage0_parse_and_validate,
        stage0_turn_pair_prompt,
        stage1_basins,
        stage2_extraction_prompt,
        stage2_parse,
        stage3_pair_mining_prompt,
        stage3_parse,
        stage4_post_filter,
    )
    from .memory import search_prompt_nodes
    from .ranker import predict_strongest_chairman

    # Upgrade recovery (review finding #3): seed the ledger from any legacy
    # rejections.jsonl / decisions.jsonl a pre-#209 build left behind. Runs
    # BEFORE the fingerprint-skip — on an unchanged-corpus upgrade the skip
    # would otherwise return with an empty ledger and never migrate.
    # Idempotent + no-op once migrated or when no legacy files exist.
    try:
        from .me.preference_acts import _migrate_legacy_preference_stores
        _recovered = _migrate_legacy_preference_stores()
        if _recovered:
            print(f"  Recovered {_recovered} preference act(s) from legacy "
                  f"stores into the unified ledger.", flush=True, file=_sys.stderr)
    except Exception:
        pass

    # #1 skip-if-unchanged: if the corpus hasn't changed since the last
    # successful build AND a lens already exists, there's nothing to
    # re-extract — the registry + lens.md are current. Skip the whole
    # pipeline (zero model calls). `force=True` or `dry_run` bypasses.
    # Runs before sampling so we skip the ~22s embedder load too.
    fingerprint = _corpus_fingerprint()
    if not force and not dry_run and me_path().exists():
        import json as _json
        sp = _lens_build_state_path()
        if sp.exists():
            try:
                prior = _json.loads(sp.read_text(encoding="utf-8")).get("fingerprint")
            except (OSError, ValueError):
                prior = None
            if prior and prior == fingerprint:
                print("  Skipped — corpus unchanged since last build "
                      "(use --force to rebuild anyway).", flush=True, file=_sys.stderr)
                return me_path(), {
                    "ok": True,
                    "skipped": True,
                    "reason": "no_corpus_change",
                    "fingerprint": fingerprint,
                }

    samples = _sample_diverse_with_embeddings(
        top_k=sample_size,
        candidate_pool=max(1000, sample_size * 12),
    ) or search_prompt_nodes("", top_k=sample_size)
    if not samples:
        path = me_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# /me\n\n_No prompt history indexed yet. Run "
            "`trinity-local import-export <path>` first._\n",
            encoding="utf-8",
        )
        return path, {"skipped": True, "reason": "no_prompts"}

    # #242 live progress: this build is committing to run (past the skip +
    # empty guards). Clear any stale cancel flag and start the progress bar.
    from .lens_progress import clear_cancel, raise_if_canceled, write_progress
    clear_cancel()
    write_progress("basins")

    basins = stage1_basins(k=k_basins, seed=seed)
    sample_dicts = [
        {"prompt_id": getattr(s, "prompt_id", None) or getattr(s, "id", None), "text": getattr(s, "text", "")}
        for s in samples
    ]

    if dry_run:
        # Junk-drawer health: the largest basin's share of clustered prompts.
        # `--dry-run` exists to "inspect the corpus topology before committing to
        # a rebuild", but it only emitted the top-10 of N basins with no skew
        # signal — so an operator couldn't actually tell a healthy topology from
        # a junk-drawering one (the build-time guard keeps the top basin under
        # ~20% by auto-sizing k, #245/#255; surface that same number here).
        # Mirrors the hidden-count note pattern the routing reader / cheat-sheet
        # use (#290) so the truncation is honest, not silent.
        total_clustered = sum(b.size for b in basins)
        top_basin_share = (
            round(max(b.size for b in basins) / total_clustered, 4)
            if total_clustered else 0.0
        )
        return me_path(), {
            "skipped": True,
            "dry_run": True,
            "samples": len(samples),
            "basins": len(basins),
            "clustered_prompts": total_clustered,
            "top_basin_share": top_basin_share,
            "hidden_basins": max(0, len(basins) - 10),
            "basin_summary": [
                {"id": b.id, "size": b.size, "top_terms": b.top_terms}
                for b in basins[:10]
            ],
        }

    config = load_config()
    available = [
        name for name, p in (config.providers if config else {}).items()
        if p.enabled and p.type in ("cli", "codex")
    ]
    chairman = predict_strongest_chairman(
        "Build a /me persona document from sampled prompt history.",
        available_providers=available or ["claude"],
    )
    chairman_config = (config.providers.get(chairman) if config else None)
    if chairman_config is None or not chairman_config.enabled:
        chairman = available[0] if available else ""
        chairman_config = config.providers.get(chairman) if (config and chairman) else None
    if chairman_config is None:
        raise RuntimeError("lens-build requires at least one enabled provider")
    # Each stage dispatches through `_stage_run_with_fallback` (chairman +
    # provider-fallback when the primary is token-exhausted). Stage 0/2 are
    # MECHANICAL extraction (classify a turn-pair gap into one of four rejection
    # types; pull a decision's privileged/sacrificed poles) — not the deep
    # reasoning Stage 3 pair-mining or Stage 5 distill need. Running them at the
    # council's full effort is the wrong tradeoff: on real data a 40-pair Stage 0
    # batch at `high` effort blew past the 8-min per-call timeout (returncode=-1)
    # and #203 aborted the build. So extraction passes `low_effort=True` — far
    # under the timeout, no quality cost for classification — while Stage 3 runs
    # at the chairman's configured effort.

    # Stage 0: turn-pair gap extraction (the highest-signal source per
    # taste-terminal spec). One batch chairman call classifies turn pairs
    # into REFRAME/COMPRESSION/REDIRECT/SHARPENING; deterministic
    # post-validators drop chairman-skim labels.
    # Progress messages added per persona audit P51 (silent for 30-60s).
    raise_if_canceled()
    write_progress("stage0")
    print(f"  Stage 0: turn-pair rejection extraction (chairman: {chairman})…", flush=True, file=_sys.stderr)
    turn_pairs, pair_index = collect_turn_pairs(limit=max(200, sample_size * 2))
    rejections: list = []
    rejected_records: list = []
    # The set of pair prompt_ids actually classified this run (new pairs +
    # whatever was already extracted). Pinned to lens_build_state.json after a
    # successful build so the next build skips them (#210).
    processed_pair_ids: set[str] = set()
    if turn_pairs:
        import concurrent.futures

        # #209: legacy rejections.jsonl retired — Stage 0 rejections flow
        # in-memory into the unified ledger save downstream. The #194/#203
        # degenerate-abort guard now checks against the ledger's existing
        # model-miss count (constants still live in turn_pairs).
        from .me.preference_acts import (
            MODEL_MISS as _MODEL_MISS,
            load_preference_acts as _load_acts,
            to_rejection as _to_rejection,
        )
        from .me.turn_pairs import _CLOBBER_MIN_EXISTING, _CLOBBER_MIN_FRACTION

        existing_acts = _load_acts()
        existing_mm_acts = [a for a in existing_acts if a.trigger == _MODEL_MISS]

        # #210 delta-extraction: only classify turn-pairs Stage 0 hasn't seen
        # before. The previously-extracted pairs' rejections are reloaded from
        # the ledger and merged with the new ones — so a corpus that grew by a
        # few threads pays for only those threads' chairman calls, not a full
        # re-classification of the whole 200-pair window. `--force` disables
        # the delta (re-extracts everything, the pre-#210 behavior) so a user
        # who suspects stale extraction can always get a clean full pass.
        delta_enabled = not force
        already = _extracted_pair_ids() if delta_enabled else set()
        existing_rejections = (
            [_to_rejection(a) for a in existing_mm_acts] if delta_enabled else []
        )
        new_pairs = (
            [p for p in turn_pairs if p.get("prompt_id") not in already]
            if delta_enabled
            else turn_pairs
        )
        # Every collected pair is "processed" after this run: the new ones via
        # the chairman, the old ones by carry-forward from the ledger.
        processed_pair_ids = {
            p.get("prompt_id") for p in turn_pairs if p.get("prompt_id")
        } | already
        skipped_seen = len(turn_pairs) - len(new_pairs)
        if delta_enabled and skipped_seen:
            print(
                f"           → delta: {len(new_pairs)} new pair(s), "
                f"{skipped_seen} already extracted (reusing ledger)",
                flush=True, file=_sys.stderr)

        # Chunk the batch (#195) — packing all 200 turn-pairs into ONE
        # prompt produced a ~37K-token call claude -p returned EMPTY for.
        # The batches are INDEPENDENT (each classifies a disjoint slice),
        # so run them concurrently (#3) — capped at _STAGE0_MAX_CONCURRENCY
        # blocking subprocesses. Parse-without-save, accumulate, save once
        # so the #194 guard sees the full count. Results are kept in batch
        # order (deterministic) even though they complete out of order.
        batches = [
            new_pairs[i:i + _STAGE0_BATCH_SIZE]
            for i in range(0, len(new_pairs), _STAGE0_BATCH_SIZE)
        ]
        results: list = [None] * len(batches)

        def _run_stage0_batch(idx: int):
            prompt = stage0_turn_pair_prompt(batches[idx], basins)
            return idx, _stage_run_with_fallback(prompt, config, chairman, Path.cwd(), low_effort=True)

        if batches:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=_STAGE0_MAX_CONCURRENCY
            ) as pool:
                for fut in concurrent.futures.as_completed(
                    pool.submit(_run_stage0_batch, i) for i in range(len(batches))
                ):
                    idx, res = fut.result()
                    results[idx] = res

        # Per-batch failure detection (#203), now across all parallel
        # results: if ANY batch timed out (returncode == -1) or returned
        # empty (the #195 cliff), abort the whole build rather than save a
        # silently-partial corpus that slips past the #194 clobber guard.
        for idx, res in enumerate(results):
            if _stage0_batch_failed(res):
                print(
                    f"  Stage 0 ABORTED — batch {idx + 1}/{len(batches)} failed "
                    f"(returncode={res.returncode}, empty={not (res.stdout or '').strip()}); "
                    f"refusing partial save",
                    flush=True, file=_sys.stderr)
                return me_path(), {
                    "ok": False,
                    "aborted": "stage0_batch_failed",
                    "reason": (res.stderr or "chairman returned empty output")[:500],
                    # The merge into `rejections` hasn't run yet at this abort
                    # point; report the carried-forward count so the telemetry
                    # isn't a misleading 0 in delta mode (review finding #1).
                    "extracted": len(existing_rejections),
                }
        new_rejections: list = []
        for res in results:
            batch_kept, batch_dropped = stage0_parse_and_validate(
                (res.stdout or "").strip(), basins, pair_index,
            )
            new_rejections.extend(batch_kept)
            rejected_records.extend(batch_dropped)
        # Merge carried-forward (ledger) + freshly-extracted rejections,
        # deduped by the content-stable id (a re-extracted pair collapses
        # onto its existing row; distinct rejections never collide — see
        # parse_rejections' id scheme). New wins on collision so a re-run
        # picks up any refinement.
        merged: dict[str, object] = {r.id: r for r in existing_rejections}
        for r in new_rejections:
            merged[r.id] = r
        rejections = list(merged.values())

        # Degenerate-Stage-0 abort (#194/#203), now against the LEDGER —
        # legacy rejections.jsonl was retired (#209). If a populated corpus
        # would be cliff-dropped to near-zero model-miss acts (the #195 empty
        # symptom that slips past the #203 batch check), abort rather than let
        # the later ledger save overwrite the user's corpus. With delta on,
        # the carried-forward rejections keep the count ≥ existing, so this
        # only bites on a `--force` full re-extraction that came back empty.
        existing_mm = len(existing_mm_acts)
        floor = max(1, int(existing_mm * _CLOBBER_MIN_FRACTION))
        if existing_mm >= _CLOBBER_MIN_EXISTING and len(rejections) < floor:
            print(
                f"  Stage 0 ABORTED — degenerate extraction: {len(rejections)} "
                f"rejections vs {existing_mm} existing (cliff-drop below {floor}); "
                f"preserving the ledger",
                flush=True, file=_sys.stderr)
            return me_path(), {
                "ok": False,
                "aborted": "degenerate_stage0",
                "reason": f"{len(rejections)} < floor {floor} (existing {existing_mm})",
                "extracted": len(rejections),
            }
        print(
            f"           → {len(rejections)} rejection signals "
            f"({len(new_rejections)} new, {len(existing_rejections)} carried), "
            f"{len(rejected_records)} dropped by validators",
            flush=True, file=_sys.stderr)
    else:
        print("           → no turn pairs yet, skipping", flush=True, file=_sys.stderr)

    # Stage 2: decision extraction (one chairman call). Rejections
    # produced by Stage 0 are mixed into the sampled corpus as
    # additional high-signal source — turn-pair gaps are usually
    # higher-yield than user-prompt-only sampling.
    augmented_samples = list(sample_dicts)
    for sig in rejections:
        if sig.prompt_id and sig.user_substitute:
            # The user_substitute is verbatim from the user turn; tag it
            # so Stage 2 sees it as decision-shaped material.
            augmented_samples.append({
                "prompt_id": sig.prompt_id,
                "text": f"[{sig.type}] model said \"{sig.model_quote}\"; I went with: {sig.user_substitute}. {sig.why_signal}",
            })

    raise_if_canceled()
    write_progress("stage2")
    print(f"  Stage 2: decision extraction (chairman: {chairman}, "
          f"{len(augmented_samples)} samples)…", flush=True, file=_sys.stderr)
    stage2_prompt = stage2_extraction_prompt(augmented_samples, basins)
    # Mechanical extraction → low effort (same rationale as Stage 0 above).
    stage2_result = _stage_run_with_fallback(stage2_prompt, config, chairman, Path.cwd(), low_effort=True)
    decisions = stage2_parse(stage2_result.stdout or "", basins)

    # Prepend high-weight decisions from two sources:
    #   1. user-authored `~/.trinity/me/decision_log.jsonl` → user_logged
    #      (weight 2.0). The interactive `decision-log` CLI was retired
    #      2026-05-27 (see retired_names.py); the loader still reads any
    #      JSONL the user wrote previously or wrote by hand.
    #   2. lens.md edits → lens_edit (weight 3.0). The strongest signal
    #      Trinity collects — the user is directly editing the lens, not
    #      just reacting to council output. Plan iter 1 (2026-05-23),
    #      task #140 slice 2.
    # Both prepended so id collisions resolve in their favor over
    # transcript-extracted (the canonical entries are the user-asserted
    # ones).
    try:
        from .me.decisions import load_decision_log
        logged = load_decision_log(basins)
    except Exception:
        logged = []
    try:
        from .me.lens_edits import load_lens_edits_as_decisions
        edited = load_lens_edits_as_decisions(basins)
    except Exception:
        edited = []
    augmentations = edited + logged  # lens-edit FIRST (highest priority)
    if augmentations:
        # De-dupe by (privileged, sacrificed, verbatim) — same trade-off
        # captured twice survives as one entry (highest-weight wins
        # because it comes first).
        seen_keys: set[tuple[str, str, str]] = set()
        deduped: list = []
        for d in augmentations + decisions:
            key = (d.privileged.lower(), d.sacrificed.lower(), d.verbatim.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(d)
        summary_parts = [f"{len(decisions)} decisions extracted"]
        if edited:
            summary_parts.append(f"+ {len(edited)} from lens_edits.jsonl (weight=3.0)")
        if logged:
            summary_parts.append(f"+ {len(logged)} from decision_log.jsonl (weight=2.0)")
        print("           → " + ", ".join(summary_parts), flush=True, file=_sys.stderr)
        decisions = deduped
    else:
        print(f"           → {len(decisions)} decisions extracted", flush=True, file=_sys.stderr)

    if not decisions:
        return me_path(), {
            "skipped": True, "reason": "no_decisions_extracted",
            "samples": len(samples), "basins": len(basins),
            "rejections": len(rejections),
            "stage2_stderr": (stage2_result.stderr or "")[:500],
        }

    # Stage 3: pair mining (one chairman call wraps the 3-member council
    # via the standard mcp run_council path; for the first cut we run a
    # single pass through chairman over decisions.jsonl).
    raise_if_canceled()
    write_progress("stage3")
    print(f"  Stage 3: pair mining (chairman: {chairman})…", flush=True, file=_sys.stderr)
    stage3_prompt = stage3_pair_mining_prompt(decisions)
    stage3_result = _stage_run_with_fallback(stage3_prompt, config, chairman, Path.cwd())
    pairs = stage3_parse(stage3_result.stdout or "")
    print(f"           → {len(pairs)} candidate pairs proposed", flush=True, file=_sys.stderr)

    # Stage 4: deterministic basin post-filter. The report callback prints the
    # verdict distribution BEFORE the guarded write, so when the cliff-drop
    # guard refuses a shrink (e.g. 10 proposed → 0 accepted, 2026-07-02) the
    # log records which gate killed the candidates instead of just the refusal.
    accepted, orderings = stage4_post_filter(
        pairs, decisions,
        report=lambda line: print(f"           → stage 4 {line}", flush=True, file=_sys.stderr),
    )

    # Stage 4b (the literal same-axis-opposite-pole contradiction detector, #141)
    # was RETIRED 2026-06-05: it produced an empty conflicts.json on the real corpus
    # (literal pole-swaps almost never occur — it was surface-form logic on a semantic
    # problem, the anti-pattern that sank the moves substrate #184), and the generators
    # pass's contradiction-split self-critique now does the SEMANTIC version AND resolves
    # it (names the missing cross-domain generator). The cross-topic layer subsumes it.
    # See retired_names.py / docs/historical/retirement-log.md.

    # Persist Stage 0 drop log so chairman drift can be audited across
    # rebuilds. If validators start rejecting >50% it means the chairman
    # is skim-classifying — signal to revisit the prompt.
    if rejected_records:
        from .me.basins import me_dir as _me_dir
        drop_log_path = _me_dir() / "rejections_dropped.jsonl"
        with drop_log_path.open("w") as f:
            import json as _json
            for r in rejected_records:
                f.write(_json.dumps(r) + "\n")

    # Stage 4.5 (#197): accumulation. Reconcile this rebuild's accepted
    # candidates into the durable tension registry by cosine identity,
    # then render the lens from the registry's *active* tensions
    # (highest-support first) instead of this run's raw output. This is
    # what turns the lens from stateless (every rebuild replaces the
    # surface) into accumulating (a rebuild reinforces or extends). Falls
    # back to raw `accepted` if the registry layer fails — accretion is
    # additive, never load-bearing for producing *a* lens.
    render_pairs = accepted
    tension_support: dict | None = None
    active_count = 0
    try:
        from .me.lens_registry import (
            active_tensions_sorted,
            blast_cap_enabled,
            blast_cap_seeded,
            load_registry,
            persistent_registry_tensions,
            save_blast_cap_seed,
            support_index,
        )

        # Blast-cap flush (lens-substrate step 3): on the FIRST clean rebuild
        # after the flag is enabled, seed the protected drift-stable core =
        # (persistent in the OLD trajectory) ∩ (present in THIS clean rebuild's
        # ACTIVE set), with persistent captured BEFORE reconcile mutates the
        # registry. The flush itself stays wholesale (reconcile runs normally);
        # the cap arms on the NEXT build via is_active's seed-exemption.
        # Best-effort: a seeding failure leaves it unseeded so the next build
        # retries — never breaks lens-build, never fires when the flag is off.
        _do_flush = blast_cap_enabled() and not blast_cap_seeded()
        _old_persistent = persistent_registry_tensions(load_registry()) if _do_flush else []

        # Constitution VALIDATOR (Phase C): gate the WRITE. Default OFF
        # (TRINITY_REGRESSION_GATE) → byte-identical to reconcile(accepted). Armed → drop
        # candidates whose optimized-for pole is contradicted by the HELD-OUT ledger (the
        # prior corrections, still on disk — this build saves the refreshed ledger below),
        # shrink-only; save_registry's clobber guard still fires on a cliff-drop.
        from .me.preference_acts import load_preference_acts as _heldout_acts
        from .me.regression_gate import commit_through_gate

        commit_through_gate(accepted, acts=_heldout_acts())

        active = active_tensions_sorted()

        if _do_flush:
            try:
                from .embeddings import embed
                from .me.lens_registry import compute_flush_seed

                # "present in THIS clean rebuild" = the tensions that survive into the
                # rebuilt ACTIVE lens — NOT the freshly-mined `accepted` candidates. A
                # durable axis is an already-active tension (re-confirmed across builds,
                # never re-proposed as a NEW candidate), so intersecting persistent_old
                # against `accepted` structurally pins ~nothing — the 2026-06-04 arm
                # pinned 0 that way (a seeded:true/protected:[] degenerate green).
                clean_probes = [e.probe_text for e in active]
                seed_ids = compute_flush_seed(clean_probes, _old_persistent, embed_fn=embed)
                # None ⇒ degenerate rebuild (no active tensions) — do NOT consume the
                # one-time flush; the next build retries. A real rebuild (possibly empty
                # intersection) returns a list and seeds.
                if seed_ids is not None:
                    save_blast_cap_seed(seed_ids)
                    print(f"  Blast-cap flush: pinned {len(seed_ids)} drift-stable tension(s)", flush=True, file=_sys.stderr)
            except Exception:
                pass  # unseeded → retried next build; the lens build is untouched
        if active:
            render_pairs = [e.to_lens_pair() for e in active]
            tension_support = support_index(active)
            active_count = len(active)
            print(
                f"  Stage 4.5: registry has {active_count} active tension(s); "
                f"rendering by support",
                flush=True, file=_sys.stderr)
        # #254: cache the taste signature (the embedding-derived adjectives) so
        # the cold-open can read it cheaply at every paint instead of
        # re-embedding. Best-effort — the embedder is already loaded here.
        try:
            from .me.correction_lens import save_taste_signature, taste_signature
            save_taste_signature(taste_signature())
        except Exception:
            pass
    except OSError:
        # A4400 #204-A3: a disk error in reconcile()'s save_registry() must
        # NOT be swallowed — silently losing this run's accumulation is the
        # exact corruption class accretion exists to prevent. Propagate.
        raise
    except Exception as exc:
        print(
            f"  Stage 4.5: registry skipped ({exc}); rendering raw accepted",
            flush=True, file=_sys.stderr)

    write_progress("registry")

    # EXTRACT-unification: render rejections + decisions as one
    # preference-act stream and refresh the unified ledger from it.
    from .me.preference_acts import (
        MODEL_MISS as _MODEL_MISS_SAVE,
        from_decision,
        from_rejection,
        load_preference_acts as _load_acts_save,
        save_preference_acts,
    )

    preference_acts = [from_rejection(r) for r in rejections] + [
        from_decision(d) for d in decisions
    ]
    # Preserve provider-imported model_miss acts the build can't reproduce
    # (review finding #2). `eval-import` / `import_provider_memory` append
    # acts with prompt_id=None — they don't originate from a transcript
    # turn-pair, so Stage 0 re-extraction never re-derives them. In the
    # delta path they survive via carry-forward, but a `--force` rebuild
    # sets existing_rejections=[] and would drop them. Re-attach any ledger
    # model_miss act with no prompt_id that this build didn't already
    # produce (fresh acts win on id collision; idempotent in both modes).
    fresh_ids = {a.id for a in preference_acts}
    try:
        imported = [
            a for a in _load_acts_save()
            if a.trigger == _MODEL_MISS_SAVE
            and not a.prompt_id
            and a.id not in fresh_ids
        ]
        preference_acts.extend(imported)
    except Exception:
        pass
    # Constitution Phase B: observational q-attribution (the φ confound guard) + shadow
    # mine. label_q_status writes q_axis/q_status onto each act (geometric tier, no LLM
    # call; abstains under the TF-IDF fallback) so the ledger records the causal status of
    # each correction's axis. mine_evidence is computed for a SHADOW log only — the
    # proposer/validator don't consume the bundle yet (Phase C). Best-effort: never break a
    # build, and never change this build's output (q is additive on the act).
    try:
        from .me.constitution import label_q_status, mine_evidence

        _n_labeled = label_q_status(preference_acts)
        if _n_labeled:
            _bundle = mine_evidence(preference_acts)
            if _bundle.ready:
                print(
                    f"  Constitution (shadow): q-labeled {_n_labeled} act(s) → "
                    f"{len(_bundle.fix_clusters)} fix-cluster(s)",
                    flush=True, file=_sys.stderr)
    except Exception as exc:
        print(f"  Constitution miner skipped ({exc})", flush=True, file=_sys.stderr)
    # Refresh the unified ledger (canonical export of every preference
    # act). Best-effort — never let the export break a build.
    try:
        save_preference_acts(preference_acts)
    except Exception:
        pass
    # Trajectory lens (#182): detect diachronic arcs from the model_miss
    # acts (deterministic — no LLM), aggregate to directional preferences,
    # persist, and feed the new lens.md "Trajectories" section. Best-effort:
    # the trajectory layer is additive, never load-bearing for producing a
    # lens, so any failure degrades to the synchronic lens.
    trajectories: list = []
    try:
        from .me.arc_mining import (
            aggregate_trajectories,
            detect_arcs,
            save_arcs,
            save_trajectories,
        )
        from .memory.store import iter_prompt_nodes as _iter_nodes

        node_lookup = {
            n.id: (getattr(n, "transcript_id", "") or "", getattr(n, "turn_index", 0) or 0)
            for n in _iter_nodes(limit=None)
        }
        arcs = detect_arcs(preference_acts, node_lookup)
        trajectories = aggregate_trajectories(arcs)
        if arcs:
            save_arcs(arcs)
            save_trajectories(trajectories)
            print(
                f"  Trajectory lens: {len(arcs)} arc(s) → "
                f"{len(trajectories)} directional preference(s)",
                flush=True, file=_sys.stderr)
    except Exception as exc:
        print(f"  Trajectory lens skipped ({exc})", flush=True, file=_sys.stderr)
        trajectories = []
    me_doc = render_me_markdown(
        render_pairs, orderings, rejections, tension_support, preference_acts,
        trajectories,
    )
    path = me_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Corruption guard: refuse to overwrite a populated lens with a tension-less
    # render (empty Stage 3 + registry layer unavailable). Deterministic
    # rendering can still produce a valid-looking placeholder; the invariant is
    # "never downgrade a populated lens to no tensions."
    existing_text = path.read_text(encoding="utf-8") if path.exists() else None
    preserved_existing = _would_clobber_populated_lens(
        bool(render_pairs or orderings), existing_text
    )
    if preserved_existing:
        print(
            "  ⚠ lens-build produced a tension-less lens (empty Stage 3 + tension "
            "registry unavailable); existing lens.md preserved. Re-run lens-build.",
            flush=True, file=_sys.stderr)
        # Deliberately skip the corpus-fingerprint write too: a degenerate build
        # must NOT mark this corpus state "done", or a retry would skip the
        # pipeline and the lens would stay stuck on the preserved-but-stale copy.
    else:
        path.write_text(me_doc, encoding="utf-8")
        # Pin this build's lens.md as the next build's hand-edit baseline.
        # Without this, the lens-edit diff is computed against a stale snapshot,
        # so this pipeline build's own regeneration (changed tensions, refreshed
        # support/stability lines) is miscounted as hundreds of "hand-edits" —
        # the launchpad/viewer then tells the user "N hand-edits, weight=3.0
        # strongest signal" and the next capture_lens_edits() would feed
        # lens-build's OWN output back as the strongest user signal
        # (generator-over-generated pollution of the lens).
        # Live on 2026-05-31: a May-30 pipeline build left a May-28 snapshot →
        # 237 phantom edits. Best-effort: a snapshot-write failure must never fail
        # an otherwise-successful build.
        try:
            from .me.lens_edits import write_lens_snapshot
            write_lens_snapshot(me_doc)
        except Exception:
            pass
        # #1: record the corpus fingerprint so the next unchanged rebuild
        # skips the whole pipeline. Best-effort — a state-write failure must
        # never fail an otherwise-successful build (worst case: the next build
        # doesn't skip).
        try:
            import json as _json
            from .utils import now_iso as _now_iso
            _lens_build_state_path().write_text(
                _json.dumps({
                    "fingerprint": fingerprint,
                    "built_at": _now_iso(),
                    # #210: the pairs Stage 0 has now classified — the next build
                    # skips them (delta-extraction). Sorted for a stable diff.
                    "extracted_pair_ids": sorted(processed_pair_ids),
                }),
                encoding="utf-8",
            )
        except OSError:
            pass
    # Generators auto-wire (the optional "lift"): abstract the freshly-rendered
    # task tensions into cross-domain generating invariants → generators.md.
    # Flag-gated (TRINITY_LENS_GENERATORS, default OFF) so it's opt-in, and only
    # on a real rebuild (not the preserved-existing degenerate path). When this
    # build ran in-session (#263), the generators pass samples too — no claude -p,
    # no pollution. Best-effort: the multi-minute pass must NEVER fail an
    # otherwise-good lens build, and does NOT touch lens.md.
    if not preserved_existing:
        try:
            from .me.generators import build_generators, generators_enabled

            if generators_enabled():
                from .state_paths import generators_path

                print("  Generators: lifting task tensions to cross-domain invariants…", flush=True, file=_sys.stderr)
                _gen = build_generators()
                if _gen.get("ok"):
                    _gp = generators_path()
                    _gp.parent.mkdir(parents=True, exist_ok=True)
                    _gp.write_text(_gen["cards"], encoding="utf-8")
                    print(
                        f"  Generators: {len(_gen['generators'])} cross-domain "
                        f"invariant(s) → {_gp.name}",
                        flush=True, file=_sys.stderr)
        except Exception as exc:
            print(f"  Generators: skipped ({exc})", flush=True, file=_sys.stderr)

    # Lens-skill auto-emit (the "lens = ambient" closure): render the freshly-
    # built lens as an agent-loadable SKILL.md so a harness that symlinked
    # ~/.trinity/skills/your-taste into ~/.claude/skills auto-refreshes whenever
    # the lens changes. Flag-gated (TRINITY_LENS_SKILL, default OFF) like the
    # generators pass; cheap + deterministic (no LLM); writes ONLY to
    # ~/.trinity/skills (never ~/.claude). Best-effort — never fails a good build.
    if not preserved_existing:
        try:
            from .me.skill import lens_skill_enabled, write_lens_skill

            if lens_skill_enabled():
                _sk = write_lens_skill()
                if _sk.get("ok"):
                    print(f"  Lens-skill: refreshed → {_sk['path']}", flush=True, file=_sys.stderr)
        except Exception as exc:
            print(f"  Lens-skill: skipped ({exc})", flush=True, file=_sys.stderr)

    # #242: the build proper is done. A caller may still distill core.md (fast);
    # it bumps to "distill" then "done" itself. Marking "done" here keeps a
    # build run directly (no distill) honest.
    write_progress("done", status="complete")
    return path, {
        "samples": len(samples),
        "basins": len(basins),
        "turn_pairs": len(turn_pairs),
        "rejections_kept": len(rejections),
        "rejections_dropped": len(rejected_records),
        "decisions": len(decisions),
        "candidates": len(pairs),
        "accepted": len(accepted),
        "active_tensions": active_count,
        "orderings": len(orderings),
        "chairman": chairman,
        "size_chars": len(me_doc),
        "preserved_existing": preserved_existing,
    }


def resync_lens_from_disk() -> tuple[Path, dict]:
    """Build-step-2 migration (#199): seed/refresh the tension registry
    from the already-extracted ``lenses.json`` + ``orderings.json`` and
    re-render ``lens.md`` with the accumulation signal — WITHOUT re-running
    the expensive Stage 0–4 chairman extraction.

    Two jobs:
    - **Migration**: a lens built before the registry existed (#197) has
      no entries; one resync registers its current tensions so the next
      full rebuild reinforces rather than replaces, and the rendered lens
      gains its support lines (#198) immediately.
    - **Cheap refresh**: re-flow the durability signal between full
      rebuilds (no provider calls).

    Mirrors the lens-build discipline: captures any hand-edits to lens.md
    before overwriting (#140) and pins a fresh snapshot after. Refuses to
    do anything when there are no accepted lenses on disk — there's
    nothing to seed, and writing an empty lens.md would be data loss.
    """
    from .me.lens_edits import capture_lens_edits, write_lens_snapshot
    from .me.lens_registry import (
        active_tensions_sorted,
        support_index,
    )
    from .me.pair_mining import load_lenses, load_orderings
    from .me.pipeline import render_me_markdown
    from .me.preference_acts import _migrate_legacy_preference_stores

    # Upgrade recovery (review finding #3): resync is the documented
    # migration verb, so it must pull any legacy rejections.jsonl /
    # decisions.jsonl into the ledger before the (ledger-only) read below —
    # otherwise resync round-trips an empty ledger and recovers nothing.
    try:
        _migrate_legacy_preference_stores()
    except Exception:
        pass

    accepted = load_lenses()
    if not accepted:
        return me_path(), {
            "ok": False,
            "reason": "no accepted lenses on disk — run lens-build first",
        }

    try:
        capture_lens_edits()
    except Exception:
        pass

    orderings = load_orderings()
    # Legacy rejections.jsonl retired (#209); the render uses preference_acts
    # when present (always, post-unification), so the rejections arg is the
    # dead legacy-fallback path — pass empty.
    rejections: list = []
    from .me.preference_acts import iter_preference_acts, save_preference_acts

    preference_acts = iter_preference_acts()
    try:
        save_preference_acts(preference_acts)  # Stage 3: refresh unified ledger
    except Exception:
        pass

    # Constitution VALIDATOR (Phase C): same single gated write path as the full build,
    # default OFF → byte-identical to reconcile(accepted). The held-out set is the ledger
    # just re-flowed above (resync re-extracts nothing).
    from .me.regression_gate import commit_through_gate

    commit_through_gate(accepted, acts=preference_acts)
    active = active_tensions_sorted()
    render_pairs = [e.to_lens_pair() for e in active] if active else accepted
    tension_support = support_index(active) if active else None

    # #182: re-render the diachronic trajectories from disk (resync is a
    # cheap re-flow — no re-detection, just surface what lens-build saved).
    try:
        from .me.arc_mining import load_trajectories
        trajectories = load_trajectories()
    except Exception:
        trajectories = []

    me_doc = render_me_markdown(
        render_pairs, orderings, rejections, tension_support, preference_acts,
        trajectories,
    )
    path = me_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(me_doc, encoding="utf-8")
    try:
        write_lens_snapshot(me_doc)
    except Exception:
        pass

    return path, {
        "ok": True,
        "accepted": len(accepted),
        "active_tensions": len(active),
        "orderings": len(orderings),
        "rejections": len(rejections),
        "size_chars": len(me_doc),
    }


def load_me() -> str:
    """Read the persisted lens document (~/.trinity/memories/lens.md),
    or empty string if not built yet. (Was ~/.trinity/me.md pre-task-#91;
    state_paths.memories_dir() migrates on first access.)"""
    path = me_path()
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
