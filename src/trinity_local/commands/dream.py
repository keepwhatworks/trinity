"""Deep history mining behind ``trinity-local lens --deep``.

``lens`` owns the canonical Stage 0–4 transcript build. This compatibility
module owns only the expensive *deep* prefix: discover cross-provider pairs,
synthesize virtual councils, and consolidate their outcomes. It then invokes
the same pipeline directly; it must not re-enter the CLI handler or recreate
the lens post-build hooks.

The former ``dream`` command remains registered for old scripts and launchpad
dispatch, but it is not a second memory architecture.
"""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace


def register(subparsers):
    from ..me_builder import ME_SAMPLE_SIZE

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
        "--skip-consolidate",
        action="store_true",
        help="Skip the cortex consolidation phase (you'll need to run `trinity-local consolidate` separately).",
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
    sp.add_argument(
        "--sample-size",
        type=int,
        default=ME_SAMPLE_SIZE,
        help=f"Representative prompts for the Lens build (default {ME_SAMPLE_SIZE}).",
    )
    sp.add_argument(
        "--k-basins",
        type=int,
        default=None,
        help="Force the Lens topic-basin count (default: corpus-size-aware).",
    )
    sp.add_argument(
        "--force",
        action="store_true",
        help="Force Stage 0–4 even when the prompt corpus fingerprint is unchanged.",
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
        print("lens --only-distill: distilling memories → core.md…",
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
    from ..embeddings import EmbedderNotReadyError, require_embedder_ready
    try:
        require_embedder_ready()
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
    print("lens --deep phase 1/3 (discover): scanning embeddings for cross-provider pairs…", file=sys.stderr)
    nodes = _all_prompt_nodes_uncapped()
    with_emb = sum(1 for n in nodes if n.embedding)
    clusters = find_cross_provider_clusters(
        nodes,
        similarity_threshold=args.similarity_threshold,
        min_providers=2,
    )
    if args.max_clusters:
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
            f"lens --deep phase 2/3 (synthesize): {len(clusters)} virtual council(s)…",
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

    # ── Phase 3: re-consolidate cortex ─────────────────────────────────
    if args.skip_consolidate:
        print("lens --deep phase 3/3 (consolidate): SKIPPED (--skip-consolidate)", file=sys.stderr)
        report["phases"]["consolidate"] = {"skipped": True}
    else:
        print("lens --deep phase 3/3 (consolidate): consolidating routing rules…", file=sys.stderr)
        consolidate_report = _consolidate(args.primary_provider or "claude")
        report["phases"]["consolidate"] = consolidate_report

    # ── Phase 4: rebuild lenses + freeze routing to disk ───────────────
    if args.skip_me_build:
        print("lens build: SKIPPED (--skip-lens-build)", file=sys.stderr)
        report["phases"]["me_build"] = {"skipped": True}
        # The historical skip switch means no build and therefore no canonical
        # post-build refresh. Preserve its old independent vocabulary/core
        # behavior for compatibility without creating another build path.
        if getattr(args, "skip_vocabulary", False):
            report["phases"]["vocabulary"] = {"skipped": True}
        else:
            from ..vocabulary import distill_vocabulary
            report["phases"]["vocabulary"] = distill_vocabulary()
        if getattr(args, "skip_distill", False):
            report["phases"]["distill"] = {"skipped": True}
        else:
            report["phases"]["distill"] = _distill(
                args.primary_provider or "claude"
            )
    else:
        print("lens build: running the canonical Stage 0–4 pipeline…", file=sys.stderr)
        me_report = _me_build(args)
        # The canonical orchestrator returns its post-build refresh reports in
        # the same payload. Keep the compatibility command's phase-shaped JSON
        # without reimplementing those refreshes here.
        report["phases"]["vocabulary"] = me_report.pop(
            "vocabulary", {"skipped": bool(getattr(args, "skip_vocabulary", False))}
        )
        report["phases"]["distill"] = me_report.pop(
            "distill", {"skipped": bool(getattr(args, "skip_distill", False))}
        )
        report["phases"]["me_build"] = me_report
        if not me_report.get("ok", True):
            report["ok"] = False

    report["total_ms"] = int((time.monotonic() - started) * 1000)
    print(json.dumps(report, indent=2))
    # 100-persona audit C2 fix: tell the user where to go next.
    print(
        "\n→ Deep Lens build complete. Open your lens:\n"
        "    open ~/.trinity/portal_pages/launchpad.html       # the dashboard\n"
        "    open ~/.trinity/portal_pages/memory.html          # the lens viewer\n"
        "    trinity-local me-card --out /tmp/me.png           # share-card PNG",
        file=sys.stderr,
    )
    return 0 if report["ok"] else 1


def _distill(provider: str) -> dict:
    """Phase 5 — collapse the three thinking memories (lens.md tensions,
    topics.json basins, vocabulary.md anchors) into one core.md paragraph."""
    from ..distill import distill_via_chairman
    return distill_via_chairman(provider=provider)


def _synthesize_all(clusters, primary_provider):
    """Run one chairman synth per cluster. Reuses the MCP machinery so the
    persisted CouncilOutcomes flow into personal_routing / consolidate
    via the standard path."""
    import asyncio
    from ..cross_provider_pairs import cluster_to_synthesis_args
    from ..mcp_server import _synthesize_responses

    synthesized = 0
    failed = 0
    for i, cluster in enumerate(clusters, 1):
        synth_args = cluster_to_synthesis_args(cluster)
        if primary_provider:
            synth_args["primary_provider"] = primary_provider
        try:
            asyncio.run(_synthesize_responses(synth_args, synth_args["responses"]))
            synthesized += 1
            if i % 10 == 0 or i == len(clusters):
                print(f"    {i}/{len(clusters)} synthesized…", file=sys.stderr)
        except Exception as exc:
            failed += 1
            print(
                f"    ! cluster {i} synth failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    return synthesized, failed


def _consolidate(provider: str) -> dict:
    """Invoke the `consolidate` handler in-process.

    Post-collapse (#298) consolidate is LLM-free (a lens-basin winner tally), so
    the `provider` arg is no longer used by the handler — kept in the signature
    for the dream pipeline's call shape."""
    from .cortex import handle_consolidate

    consolidate_args = SimpleNamespace(dry_run=False)
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = handle_consolidate(consolidate_args)
    except SystemExit as exc:
        return {"ok": False, "error": f"consolidate exited: {exc}"}
    captured = buf.getvalue().strip()
    try:
        payload = json.loads(captured) if captured else {}
    except json.JSONDecodeError:
        payload = {"raw": captured}
    payload["rc"] = rc
    return payload


def _me_build(args) -> dict:
    """Run the canonical Lens build orchestration for the deep build.

    Calling ``handle_me_build`` here used to look convenient, but it crossed a
    CLI boundary with an incomplete ``SimpleNamespace``. Calling the Stage 0–4
    function directly then duplicated its routing/vocabulary/core refreshes.
    Reuse ``_run_lens_build`` so normal, guided, and deep builds share both
    halves of the contract.
    """
    try:
        from .me import _run_lens_build
        from ..me_builder import ME_SAMPLE_SIZE
    except ImportError:
        return {"ok": False, "error": "lens pipeline not importable"}
    try:
        _path, summary = _run_lens_build(
            sample_size=getattr(args, "sample_size", None) or ME_SAMPLE_SIZE,
            k_basins=getattr(args, "k_basins", None),
            dry_run=False,
            force=bool(getattr(args, "force", False)),
            skip_vocabulary=bool(getattr(args, "skip_vocabulary", False)),
            skip_distill=bool(getattr(args, "skip_distill", False)),
            distill_provider=getattr(args, "primary_provider", None) or "claude",
        )
        return {"ok": True, **summary}
    except SystemExit as exc:
        return {"ok": False, "error": f"lens pipeline exited: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
