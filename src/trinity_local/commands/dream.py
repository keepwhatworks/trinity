"""`trinity-local dream` — the once-or-nightly cold-start pass.

The user's analog to Anthropic's *Dreaming*. Walks ALL embedded prompts
on disk, finds cross-provider question pairs, turns each into a virtual
council via chairman synthesis, then re-consolidates cortex rules and
re-builds the /me lenses.

One command, four phases, end-to-end cold-start without fresh dispatch
beyond chairman calls.

Cost model (typical first run):
  - Phase 1 (discover): free, embeddings already on disk
  - Phase 2 (synthesize): ~one flagship call per cross-provider cluster.
    Usually 10–100 clusters.
    outcomes. Caps at the cortex `--min-basin-size` default (3).
  - Phase 4 (lens-build): three flagship calls total (turn-pairs, decisions,
    pair-mining) per the existing lens-discovery pipeline.

So a full dream = (n_clusters + n_basins + 3) flagship calls. For a
fresh install with 18k seeded nodes, that's typically $5–15 of
subscription credit — small for a one-time bootstrap that produces a
fully populated routing table + lenses.
"""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace


def register(subparsers):
    sp = subparsers.add_parser(
        "dream",
        help="(Compatibility alias for `lens --deep`, 2026-07-04 — one concept.) "
             "Mine your history: discover cross-provider question pairs, synthesize "
             "each as a virtual council, re-consolidate routing, rebuild the lens.",
    )
    sp.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.85,
        help="Cosine sim floor for two prompts to count as the same question (default: 0.85)",
    )
    sp.add_argument(
        "--max-clusters",
        type=int,
        default=None,
        help="Cap the number of clusters synthesized this run. Default: all discovered.",
    )
    sp.add_argument(
        "--skip-lens-build",
        action="store_true",
        # Backward-compat short option until anyone scripting against the
        # pre-rename CLI surfaces (no one has, but it's a safe alias).
        dest="skip_me_build",
        help="Skip the lens rebuild phase (Phase 4 — `lens-build`).",
    )
    sp.add_argument(
        "--skip-vocabulary",
        action="store_true",
        help="Skip Phase 2.5: scanning vocabulary for homonyms + synonyms.",
    )
    sp.add_argument(
        "--skip-distill",
        action="store_true",
        help="Skip Phase 5: emitting the one-paragraph core.md distillation.",
    )
    sp.add_argument(
        "--only-distill",
        action="store_true",
        help=(
            "Skip every upstream phase and run ONLY Phase 5 (refresh "
            "core.md from existing memories). Fast path for clearing "
            "the 'stale core.md' status warning when the upstream "
            "memories are still current. Mutually exclusive with "
            "--skip-distill (would do nothing)."
        ),
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover clusters and print the plan; don't call any flagship.",
    )
    sp.add_argument(
        "--primary-provider",
        default=None,
        help="Force a specific chairman provider for synthesis + consolidation.",
    )
    sp.set_defaults(handler=handle_dream)


def _all_prompt_nodes_uncapped() -> list:
    """Back-compat alias — the canonical uncapped walker is
    `iter_prompt_nodes(limit=None)` from trinity_local.memory.store, which
    has been there all along. This helper used to reinvent it; kept as a
    thin wrapper so existing tests that monkey-patch it stay green.

    New code should call `iter_prompt_nodes(limit=None)` directly — it's
    cached in-process by file mtime so dream/vocabulary/basins all share
    the parse cost on a hot session."""
    from ..memory.store import iter_prompt_nodes
    return list(iter_prompt_nodes(limit=None))


def handle_dream(args):
    from ..lens_addon import enable_lens
    enable_lens()  # explicit dream = opting into the lens add-on
    started = time.monotonic()

    # --only-distill fast path: skip every upstream phase (which all
    # need the embedder) and just refresh core.md. The use case is
    # clearing the "⚠️ stale core.md" status warning when upstream
    # memories are still current. No embedder needed; no cross-provider
    # pair discovery; one flagship call.
    if getattr(args, "only_distill", False):
        if getattr(args, "skip_distill", False):
            print(
                "error: --only-distill and --skip-distill are mutually "
                "exclusive (would do nothing).",
                file=sys.stderr,
            )
            sys.exit(2)
        print("dream phase 6/5: distilling memories → core.md (only-distill mode)…",
              file=sys.stderr)
        distill_report = _distill(args.primary_provider or "claude")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        print(json.dumps({
            "ok": True,
            "phases": {"distill": distill_report},
            "total_ms": elapsed_ms,
            "mode": "only-distill",
        }, indent=2))
        return 0

    # Fail fast if the embedder model isn't downloaded — dream uses
    # embeddings end-to-end (cross-provider pair discovery, basin
    # k-means, lens distillation). Without this gate the user would
    # discover the ~600 MB requirement mid-Phase-1.
    from ..embeddings import EmbedderNotReadyError, require_real_embedder
    try:
        require_real_embedder()
    except EmbedderNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    from ..cross_provider_pairs import find_cross_provider_clusters

    report = {
        "ok": True,
        "phases": {},
        "total_ms": 0,
    }

    # ── Phase 1: discover ──────────────────────────────────────────────
    print("dream phase 1/5 (discover): scanning embeddings for cross-provider pairs…", file=sys.stderr)
    nodes = _all_prompt_nodes_uncapped()
    with_emb = sum(1 for n in nodes if n.embedding)
    clusters = find_cross_provider_clusters(
        nodes,
        similarity_threshold=args.similarity_threshold,
        min_providers=2,
    )
    # `is not None`, not truthiness: 0 is falsy, so `--max-clusters 0` used to mean
    # ALL 419 clusters rather than none — the opposite of what a user asking for
    # zero wants, and the only flag that can skip a phase that costs one chairman
    # call per cluster.
    if args.max_clusters is not None:
        clusters = clusters[: args.max_clusters]
    report["phases"]["discover"] = {
        "checked_nodes": len(nodes),
        "with_embedding": with_emb,
        "clusters_found": len(clusters),
    }
    print(
        f"  ✓ {len(clusters)} cross-provider cluster(s) from {with_emb} embedded nodes "
        f"(of {len(nodes)} total)",
        file=sys.stderr,
    )

    if args.dry_run:
        report["phases"]["discover"]["cluster_preview"] = [
            {
                "prompt": c.representative_prompt[:160],
                "providers": sorted(c.providers),
                "coherence": round(c.coherence, 3),
            }
            for c in clusters[:10]
        ]
        report["total_ms"] = int((time.monotonic() - started) * 1000)
        print(json.dumps(report, indent=2))
        return 0

    if not clusters:
        print(
            "  no cross-provider pairs to synthesize — try --similarity-threshold 0.80 "
            "or run `trinity-local import-export <path>` to populate embeddings.",
            file=sys.stderr,
        )

    # ── Phase 2: synthesize each cluster as a virtual council ──────────
    if clusters:
        print(
            f"dream phase 2/5 (synthesize): {len(clusters)} virtual council(s)…",
            file=sys.stderr,
        )
        synthesized, failed = _synthesize_all(clusters, args.primary_provider)
        report["phases"]["synthesize"] = {
            "attempted": len(clusters),
            "synthesized": synthesized,
            "failed": failed,
        }
        print(
            f"  ✓ {synthesized}/{len(clusters)} virtual councils landed "
            f"({failed} failed)",
            file=sys.stderr,
        )
    else:
        report["phases"]["synthesize"] = {
            "attempted": 0, "synthesized": 0, "failed": 0,
        }

    # Phase 3 was `consolidate` — rebuilding picks.json from the lens basins.
    # Removed 2026-08-11 (res_022) with the verb itself: the router that read
    # picks.json went on the same day, so the phase rebuilt a file with zero
    # readers on every deep build.

    # ── Phase 4: rebuild lenses + freeze routing to disk ───────────────
    if args.skip_me_build:
        print("dream phase 4/5 (lens-build): SKIPPED (--skip-lens-build)", file=sys.stderr)
        report["phases"]["me_build"] = {"skipped": True}
    else:
        print("dream phase 4/5 (lens-build): rebuilding lenses + freezing routing…", file=sys.stderr)
        me_report = _me_build(args.primary_provider or "claude")
        # Freeze the empirical-memory entry to scoreboard/routing.json so the
        # chairman context loader (and Phase 5 distill) sees the routing
        # signal without re-walking council_outcomes/ on every call.
        try:
            from ..personal_routing import freeze_routing_to_disk
            table = freeze_routing_to_disk()
            me_report["routing_frozen"] = {
                "task_types": len(table or {}),
            }
        except Exception as exc:
            me_report["routing_frozen"] = {"error": f"{type(exc).__name__}: {exc}"}
        report["phases"]["me_build"] = me_report

    # ── Phase 2.5: vocabulary distillation ─────────────────────────────
    # Pure-geometric scan; zero LLM calls. Builds the language-memory
    # entry in the core-memories set.
    if getattr(args, "skip_vocabulary", False):
        print("dream phase 5/5 (vocabulary): SKIPPED (--skip-vocabulary)", file=sys.stderr)
        report["phases"]["vocabulary"] = {"skipped": True}
    else:
        print("dream phase 5/5 (vocabulary): scanning for overloads + anchors…", file=sys.stderr)
        report["phases"]["vocabulary"] = _vocabulary_scan()

    # ── Phase 5: distill the three thinking memories (lens, topics,
    #              vocabulary) into singular core.md ──
    # Always runs (cheap — one flagship call). Even if upstream phases
    # were skipped, distill emits a core.md from whatever memories DO
    # exist on disk.
    if getattr(args, "skip_distill", False):
        print("dream phase 6/5 (distill): SKIPPED (--skip-distill)", file=sys.stderr)
        report["phases"]["distill"] = {"skipped": True}
    else:
        print("dream phase 6/5 (distill): distilling memories → core.md…", file=sys.stderr)
        distill_report = _distill(args.primary_provider or "claude")
        report["phases"]["distill"] = distill_report

    report["total_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(report, indent=2))
    # 100-persona audit C2 fix: tell the user where to go next.
    print(
        "\n→ Dream complete. Open your lens:\n"
        "    open ~/.trinity/portal_pages/launchpad.html       # the dashboard\n"
        "    open ~/.trinity/portal_pages/memory.html          # the lens viewer\n"
        "    trinity-local me-card --out /tmp/me.png           # share-card PNG",
        file=sys.stderr,
    )
    return 0


def _vocabulary_scan() -> dict:
    """Phase 2.5 — geometric scan of the user's terminology."""
    from ..vocabulary import distill_vocabulary
    return distill_vocabulary()


def _distill(provider: str) -> dict:
    """Phase 5 — collapse the three thinking memories (lens.md tensions,
    topics.json basins, vocabulary.md anchors) into one core.md paragraph."""
    from ..distill import distill_via_chairman
    return distill_via_chairman(provider=provider)


def _cluster_fingerprint(cluster) -> str:
    """Stable id for a cluster: its prompt plus who answered and with what.

    Two clusters are "the same" when the same providers answered the same
    prompt with the same text. Response text is hashed rather than stored so
    the sidecar carries no prompt or answer content.
    """
    import hashlib

    parts = [str(getattr(cluster, "representative_prompt", ""))]
    for m in sorted(getattr(cluster, "members", []),
                    key=lambda x: str(getattr(x, "provider", ""))):
        parts.append(str(getattr(m, "provider", "")))
        parts.append(hashlib.sha1(
            str(getattr(m, "response_text", "")).encode("utf-8", "replace")).hexdigest()[:16])
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()[:24]


def _synthesized_ledger():
    """Path + the set of fingerprints already synthesized."""
    from ..state_paths import trinity_home

    path = trinity_home() / "dream_synthesized.jsonl"
    seen = set()
    if path.exists():
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                seen.add(line.split()[0])
    return path, seen


# Five is comfortably above the isolated-failure rate measured before the
# 2026-08-24 quota event (2 failures in 378 clusters = 0.53%, so five in a
# row is ~1 in 10^11 by chance) and well below the 47 that actually ran.
_CONSECUTIVE_FAILURE_ABORT = 5


def _synthesize_all(clusters, primary_provider):
    """Run one chairman synth per cluster, SKIPPING clusters already done.

    The spend pre-flight has always told the user "re-runs only pay for NEW
    clusters". Until 2026-08-18 that was copy with no mechanism behind it: this
    function called the chairman for every cluster, every time, with no
    existence check. A re-run of a 419-cluster corpus paid 419 chairman calls
    to redo work already on disk (res_069). The claim is now true.
    """
    import asyncio
    from ..cross_provider_pairs import cluster_to_synthesis_args
    from ..mcp_server import _synthesize_responses

    ledger_path, seen = _synthesized_ledger()
    synthesized = 0
    failed = 0
    skipped = 0
    # CIRCUIT BREAKER (res_081). On 2026-08-24 the provider hit its session
    # limit at cluster ~378 of 433. Every later chairman call returned the quota
    # notice instead of a synthesis, which carries no routing-json fence, so all
    # 47 were correctly refused — and the loop kept dispatching anyway, spending
    # quota it no longer had on work that could not succeed, for another 55
    # clusters. The same error string then reached the distill stage and was
    # admitted into core.md as the founder's identity.
    #
    # Isolated failures are normal and must not stop a 3-hour run (the true
    # parse failure rate before the event was 2/378 = 0.53%). A RUN of them is
    # not a rate, it is a regime change, and the only useful response is to
    # stop.
    consecutive = 0
    for i, cluster in enumerate(clusters, 1):
        fp = _cluster_fingerprint(cluster)
        if fp in seen:
            skipped += 1
            continue
        synth_args = cluster_to_synthesis_args(cluster)
        if primary_provider:
            synth_args["primary_provider"] = primary_provider
        try:
            asyncio.run(_synthesize_responses(synth_args, synth_args["responses"]))
            synthesized += 1
            consecutive = 0
            with ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(fp + "\n")
            seen.add(fp)
            if i % 10 == 0 or i == len(clusters):
                print(f"    {i}/{len(clusters)} synthesized…", file=sys.stderr)
        except Exception as exc:
            failed += 1
            consecutive += 1
            print(
                f"    ! cluster {i} synth failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            if consecutive >= _CONSECUTIVE_FAILURE_ABORT:
                print(
                    f"    ✗ ABORTING: {consecutive} consecutive synthesis failures. "
                    f"This is a regime change (provider quota, auth, or outage), not "
                    f"a run of bad luck — continuing would spend quota on calls that "
                    f"cannot succeed. {synthesized} cluster(s) completed and are "
                    f"recorded; re-run when the provider recovers and only the "
                    f"remaining clusters are paid for.",
                    file=sys.stderr,
                )
                break
    if skipped:
        print(f"    {skipped} cluster(s) already synthesized — skipped",
              file=sys.stderr)
    return synthesized, failed




def _me_build(provider: str) -> dict:
    """Invoke the `lens-build` handler in-process (the underlying Python
    function kept its pre-rename name `handle_me_build` — internal
    detail). Best-effort — if the lens pipeline doesn't have enough data
    yet, it'll skip phases gracefully and report that."""
    try:
        from .me import handle_me_build
    except ImportError:
        return {"ok": False, "error": "lens-build handler not importable"}

    # sample_size and k_basins are read as args.X DIRECTLY by handle_me_build
    # (everything else it touches goes through getattr with a default). Omitting
    # them crashed the whole me/ stage with
    #   AttributeError: 'types.SimpleNamespace' object has no attribute 'sample_size'
    # and a `lens --deep` run then completed "successfully" with its central stage
    # dead — 2.3 hours and 419 chairman calls that changed 87 bytes (res_068).
    # The defaults mirror the CLI parser's so a deep build behaves like a plain one.
    # tests/test_dream_me_args_contract.py derives the required set by AST, so a
    # newly added args.X cannot silently reopen this.
    from ..me_builder import ME_SAMPLE_SIZE as _ME_SAMPLE_SIZE

    me_args = SimpleNamespace(
        provider=provider,
        limit=None,
        stages=None,
        force=False,
        sample_size=_ME_SAMPLE_SIZE,
        k_basins=None,
        # handle_dream returns early when dry_run is set, so reaching this
        # point means it is False. Passed explicitly because handle_me_build
        # reads args.dry_run DIRECTLY at me.py:393 (and via getattr elsewhere —
        # a getattr somewhere is not a shield for a direct read).
        dry_run=False,
    )
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            handle_me_build(me_args)
    except SystemExit as exc:
        return {"ok": False, "error": f"lens-build exited: {exc}"}
    except TypeError as exc:
        # handle_me_build's actual signature may differ — surface the gap
        # without breaking dream.
        return {"ok": False, "error": f"lens-build args mismatch: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    captured = buf.getvalue().strip()
    try:
        return json.loads(captured) if captured else {"ok": True, "raw_empty": True}
    except json.JSONDecodeError:
        return {"ok": True, "raw": captured[:1000]}
