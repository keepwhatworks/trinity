"""`lens-build` + `lens-show` — build / inspect the user's lens via a single
chairman call over sampled prompt history. The chairman of every Trinity
council reads `~/.trinity/memories/lens.md` to score council outputs
against THIS user's taste, not the world's.

Tier 1 #2 rename history (task #91): the CLI + MCP + file paths renamed
me/persona → lens pre-launch. Internal symbols (`me_builder`, `me_path`,
`build_me_via_council`, `ME_BUDGET_CHARS`) kept their me_ prefix per the
"code uses internal names; user-facing copy uses canonical name"
convention (same shape as glossary entry for member vs seat). The LLM
prompt that builds the lens (in `me_builder._render_me_build_prompt`)
still instructs the chairman to produce a `/me document` with `# /me`
heading — this gets rendered in the launchpad memory viewer; the
prompt-template rewrite to use "lens" framing is a deferred content-shape
change."""
from __future__ import annotations

import json

from ..me_builder import (
    ME_BUDGET_CHARS,
    ME_SAMPLE_SIZE,
    build_me_via_council,
    build_me_via_lens_pipeline,
    load_me,
    me_path,
    resync_lens_from_disk,
)


def register(subparsers):
    # Q4 surface-collapse (#213): `lens` is the user-facing product word.
    # `lens-build` is kept as an alias so launchpad/extension dispatch and
    # the copy-paste command strings in memory_viewer keep resolving.
    build_parser = subparsers.add_parser(
        "lens",
        aliases=["lens-build"],
        help="Build your lens (~/.trinity/memories/lens.md) from your transcripts.",
    )
    build_parser.add_argument(
        "--budget-chars", type=int, default=ME_BUDGET_CHARS,
        help=f"Soft cap on lens.md size when using --legacy (default {ME_BUDGET_CHARS}).",
    )
    build_parser.add_argument(
        "--sample-size", type=int, default=ME_SAMPLE_SIZE,
        help=f"How many representative prompts to feed the chairman (default {ME_SAMPLE_SIZE}).",
    )
    build_parser.add_argument(
        "--k-basins", type=int, default=None,
        help="Stage 1 k-means cluster count. Default: corpus-size-aware "
             "(≈1 basin per 650 threads, 20–60) so the topic map doesn't "
             "junk-drawer as history grows (#245). Pass an int to force k.",
    )
    build_parser.add_argument(
        "--dry-run", action="store_true",
        help="Stage 1 only — cluster topics and print their summary, no LLM calls.",
    )
    build_parser.add_argument(
        "--legacy", action="store_true",
        help="Use the old single-pass chairman builder (pre-Option C).",
    )
    build_parser.add_argument(
        "--force", action="store_true",
        help="Rebuild even if the corpus is unchanged since the last build "
             "(skips the no-corpus-change shortcut).",
    )
    build_parser.set_defaults(handler=handle_me_build)

    show_parser = subparsers.add_parser(
        "lens-show",
        help="Print the current ~/.trinity/memories/lens.md content.",
    )
    show_parser.set_defaults(handler=handle_me_show)

    resync_parser = subparsers.add_parser(
        "lens-resync",
        help="Seed the tension registry from existing lenses.json + re-render "
             "lens.md with support/stability — no chairman calls.",
    )
    resync_parser.set_defaults(handler=handle_lens_resync)

    acts_parser = subparsers.add_parser(
        "lens-acts",
        help="Show the unified preference-act ledger (model-miss corrections "
             "+ self-expressed trade-offs) — counts by trigger / kind / basin.",
    )
    acts_parser.set_defaults(handler=handle_lens_acts)

    stop_parser = subparsers.add_parser(
        "lens-stop",
        help="Stop a running lens build (#242a) — drops a cancel flag the build "
             "checks between stages, so it aborts cleanly without interrupting a "
             "chairman call mid-flight. The launchpad 'Stop' button dispatches this.",
    )
    stop_parser.set_defaults(handler=handle_lens_stop)

    gen_parser = subparsers.add_parser(
        "lens-generators",
        help="Run the generators pass (the lens 'lift') — abstract the task-level "
             "tensions into the cross-domain GENERATING invariants they project "
             "from. Writes ~/.trinity/memories/generators.md (does NOT touch "
             "lens.md). On-demand; prefers MCP-sampling.",
    )
    gen_parser.add_argument(
        "--dry-run", action="store_true",
        help="Select domain-diverse evidence + build the prompt, no LLM call.",
    )
    gen_parser.set_defaults(handler=handle_lens_generators)

    skill_parser = subparsers.add_parser(
        "install-skill",
        aliases=["lens-skill"],
        help="Register your lens as an agent-loadable SKILL.md — your TASTE skill, "
             "the artifact other agents (Claude Code, Cursor, Codex) load to "
             "answer in your voice. Sibling of install-mcp / install-agent; the "
             "artifact ships as 'your-taste' (the name foreign agents see). Writes "
             "~/.trinity/skills/your-taste/SKILL.md and prints the one-line symlink "
             "to make it ambient in ~/.claude/skills/. The private lens is generated "
             "locally — it is NOT a shared static artifact. LLM-free; re-run after a "
             "lens rebuild.",
    )
    skill_parser.add_argument(
        "--print", dest="print_only", action="store_true",
        help="Print the SKILL.md to stdout instead of writing it.",
    )
    skill_parser.set_defaults(handler=handle_lens_skill)

    setup_parser = subparsers.add_parser(
        "lens-setup",
        help="Create your lens — the guided 'lens add-on' activation. Opts in, "
             "ensures the embedder, ingests your CLI history, and builds the lens. "
             "Free fusion (the council) needs none of this; the lens is the add-on.",
    )
    setup_parser.set_defaults(handler=handle_lens_setup)


def handle_lens_generators(args):
    """Run the generators pass + write the cards to
    ~/.trinity/memories/generators.md. On-demand (the founder triggers it); does
    NOT auto-wire into lens-build or touch lens.md. Prefers MCP-sampling; a plain
    CLI run falls back to claude -p (the generated-shape filter catches the
    transcript)."""
    import sys

    from ..me.generators import (
        _current_tensions,
        build_generate_prompt,
        build_generators,
        select_domain_diverse_evidence,
    )
    from ..state_paths import trinity_home

    if getattr(args, "dry_run", False):
        tensions = _current_tensions()
        evidence = select_domain_diverse_evidence()
        prompt = build_generate_prompt(tensions, evidence) if (tensions and evidence) else ""
        print(json.dumps({
            "ok": bool(tensions and evidence),
            "dry_run": True,
            "task_tensions": len(tensions),
            "evidence_turns": len(evidence),
            "prompt_chars": len(prompt),
        }, indent=2))
        return 0 if (tensions and evidence) else 1

    print("generators pass: domain-diverse evidence -> abstract -> dual self-critique...",
          file=sys.stderr)
    print("  (prefers MCP-sampling; a plain CLI run falls back to claude -p)", file=sys.stderr)
    result = build_generators()
    if not result.get("ok"):
        print(json.dumps({
            "ok": False,
            "reason": result.get("reason"),
            "evidence_turns": result.get("evidence_turns"),
            "task_tensions": result.get("task_tensions"),
        }, indent=2))
        return 1
    out = trinity_home() / "memories" / "generators.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result["cards"], encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "count": len(result["generators"]),
        "generators": [g["name"] for g in result["generators"]],
        "evidence_turns": result["evidence_turns"],
        "written": str(out),
    }, indent=2))
    return 0


def handle_lens_skill(args):
    """Emit the lens as an agent-loadable SKILL.md (the 'lens = ambient' move).
    Writes ~/.trinity/skills/your-taste/SKILL.md (NOT ~/.claude) + prints the
    deliberate one-line symlink to make it ambient. LLM-free; composes from the
    already-built lens (core.md + taste_signature + tensions)."""
    import sys

    from ..me.skill import render_lens_skill, write_lens_skill

    if getattr(args, "print_only", False):
        text = render_lens_skill()
        if text is None:
            print(json.dumps({
                "ok": False,
                "reason": "no lens to emit — run `trinity-local lens` first",
            }, indent=2))
            return 1
        print(text)
        return 0

    result = write_lens_skill()
    print(json.dumps(result, indent=2))
    if result.get("ok"):
        print("\nMake it ambient (deliberate, one-time):", file=sys.stderr)
        print("  " + result["install_hint"], file=sys.stderr)
    return 0 if result.get("ok") else 1


def handle_me_build(args):
    from ..lens_addon import enable_lens
    enable_lens()  # explicitly building the lens = opting into the lens add-on
    # Fail fast if the embedder model isn't downloaded — lens-build
    # uses embeddings for assistant-text reranking + basin clustering.
    # Without this gate the user gets a multi-minute startup followed
    # by an HF_HUB_OFFLINE error mid-call.
    import sys
    from ..embeddings import EmbedderNotReadyError, require_embedder_ready
    try:
        require_embedder_ready()
    except EmbedderNotReadyError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if args.legacy:
        path, summary = build_me_via_council(
            budget_chars=args.budget_chars,
            sample_size=args.sample_size,
        )
    else:
        from ..lens_progress import LensBuildCanceled, write_progress
        try:
            path, summary = build_me_via_lens_pipeline(
                sample_size=args.sample_size,
                k_basins=args.k_basins,
                dry_run=args.dry_run,
                force=getattr(args, "force", False),
            )
        except LensBuildCanceled:
            # #242a — clean stop (the launchpad/CLI cancel). Record the terminal
            # progress state and exit non-zero without a traceback.
            write_progress("canceled", status="canceled")
            print(json.dumps({"ok": False, "canceled": True}, indent=2))
            sys.stderr.write("\n→ Lens build stopped.\n")
            sys.exit(130)
    # Lens was just rewritten → freeze the routing table to disk +
    # auto-fire distill. Both are no-ops if the data hasn't changed
    # (routing is empty without rated councils; distill skips if
    # core.md is already newer than every source memory). Skipped
    # in dry-run since no real changes hit disk.
    routing_summary: dict | None = None
    distill_summary: dict | None = None
    if not getattr(args, "dry_run", False):
        try:
            from ..personal_routing import freeze_routing_to_disk
            table = freeze_routing_to_disk()
            routing_summary = {"task_types": len((table or {}).get("by_task_type") or {})}
        except Exception as exc:
            routing_summary = {"error": f"{type(exc).__name__}: {exc}"}
        try:
            from ..distill import distill_via_chairman
            distill_summary = distill_via_chairman()
        except Exception as exc:
            distill_summary = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    payload = {"ok": True, "path": str(path), **summary}
    if routing_summary is not None:
        payload["routing_frozen"] = routing_summary
    if distill_summary is not None:
        payload["distill"] = distill_summary
    print(json.dumps(payload, indent=2))
    # 100-persona audit P51 fix: tell the user where to go next.
    import sys as _sys
    if getattr(args, "dry_run", False):
        _sys.stderr.write(
            "\n→ Stage 1 dry-run complete (no lens written). To build:\n"
            "    trinity-local lens-build\n"
        )
    else:
        _sys.stderr.write(
            "\n→ Lens built. View it:\n"
            "    trinity-local lens-show\n"
            "    open ~/.trinity/portal_pages/memory.html?file=lens.md\n"
            "  Make your taste ambient (your agents load it with zero tool call):\n"
            "    trinity-local lens-skill   # writes a SKILL.md to symlink into ~/.claude/skills\n"
        )


def handle_lens_setup(args):
    """`lens-setup` — guided "create your lens" (the opt-in lens add-on).

    Free fusion (the council) needs none of this. The lens does: this opts in,
    ensures the embedder, ingests your CLI history, and builds the lens — in one
    command. The Chrome extension + Takeout import are separate enrichment steps
    the wizard points at (they add the web-app surfaces to the corpus)."""
    import sys
    from types import SimpleNamespace

    from ..lens_addon import enable_lens

    enable_lens()
    print("Creating your taste lens — the cross-provider moat.")
    print("(Free fusion needs none of this; the lens is the add-on you're turning on.)\n")

    # Step 1 — the embedder (real 768d semantic vectors). Idempotent; skip the
    # ~600 MB download message when it's already live.
    from ..embeddings import mlx_actually_loaded

    if mlx_actually_loaded():
        print("✓ Step 1/3: embedder already downloaded.")
    else:
        print("→ Step 1/3: the embedder (real semantic vectors).")
        from .download_embedder import handle_download_embedder

        if handle_download_embedder(SimpleNamespace(force=False, json=False)) != 0:
            print("✗ Couldn't ready the embedder — the lens build needs it. "
                  "Fix the above and re-run `trinity-local lens-setup`.", file=sys.stderr)
            return 1

    # Step 2 — ingest the always-local CLI baseline corpus.
    print("→ Step 2/3: ingesting your CLI history (Claude Code / Codex / Antigravity)…")
    try:
        from ..cold_start import detect_available_sources
        from ..incremental_ingest import ingest_recent

        sources = detect_available_sources()
        if sources:
            res = ingest_recent(sources=sources)
            print(f"  ingested {getattr(res, 'added', 0)} prompt(s) from {', '.join(sources)}.")
        else:
            print("  no local CLI transcripts found yet — building from whatever's present.")
    except Exception as exc:  # noqa: BLE001 — ingest is best-effort; the build still runs
        # TYPE only — a raw str(exc) here can leak a transcript FILESYSTEM PATH
        # ("No such file or directory: '/Users/<name>/.claude/...'") into the CLI
        # line; the build proceeds regardless, so the payload isn't actionable.
        print(f"  (ingest skipped: {type(exc).__name__})", file=sys.stderr)

    # Step 3 — build the lens.
    print("→ Step 3/3: building the lens (a multi-minute chairman pass)…")
    try:
        path, _summary = build_me_via_lens_pipeline()
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Lens build failed: {exc}", file=sys.stderr)
        return 1

    print(f"\n✓ Your lens is ready: {path}")
    print("  The chairman now reads it on every council — answers come back in your voice.\n")
    print("Make it richer — add your web AI history (the all-six-surfaces moat):")
    print("  • ChatGPT / Gemini / claude.ai exports:  trinity-local import-export <takeout>")
    print("  • live capture:                          trinity-local install-extension")
    print("Check it's trustworthy:                    trinity-local lens-health")
    return 0


def handle_me_show(args):
    text = load_me()
    if not text:
        print("# lens not built yet — run `trinity-local lens-build`")
        print(f"# expected at: {me_path()}")
        return
    print(text)


def handle_lens_acts(args):
    from collections import Counter

    from ..me.preference_acts import iter_preference_acts, preference_acts_path

    acts = iter_preference_acts()
    by_trigger = Counter(a.trigger for a in acts)
    by_kind = Counter(a.kind for a in acts if a.kind)
    by_basin = Counter(a.basin for a in acts if a.basin)
    payload = {
        "ledger": str(preference_acts_path()),
        "total": len(acts),
        "by_trigger": dict(by_trigger),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_basin": dict(sorted(by_basin.items(), key=lambda kv: -kv[1])[:10]),
    }
    # The correction-vector lens (#257): the user's taste as a geometric
    # direction, decomposed onto interpretable axes (+ leans toward the first
    # pole). Read-only; best-effort (needs the embedder). `coherence` is low by
    # nature (corrections scatter by topic); the axis loadings are the signal.
    try:
        from ..me.correction_lens import correction_signature
        sig = correction_signature()
        if sig.get("ready"):
            payload["correction_signature"] = sig
    except Exception:
        pass
    # #257 diachronic view: how the taste-axis loadings MOVED early→recent — the
    # asymmetric insight no within-session memory can see (not what your taste
    # is, but how it changed). Read-only, best-effort.
    try:
        from ..me.correction_lens import correction_drift
        drift = correction_drift()
        if drift.get("ready"):
            payload["correction_drift"] = drift
    except Exception:
        pass
    # #257 per-domain view: the same person steers differently by subject — the
    # ticket basin pushes for concrete/decisive, the admin basin for action. A
    # global signature averages that away; this keeps it. Read-only, best-effort.
    try:
        from ..me.correction_lens import correction_signature_by_basin
        by_basin = correction_signature_by_basin()
        if by_basin.get("ready"):
            payload["correction_by_basin"] = by_basin
    except Exception:
        pass
    # #257 prompt-space outliers: the asks farthest from every subject basin —
    # the user's most unusual one-offs (trivia, novel directions). Read-only.
    try:
        from ..me.geometric_insights import outlier_prompts
        outliers = outlier_prompts()
        if outliers.get("ready"):
            payload["prompt_outliers"] = outliers
    except Exception:
        pass
    print(json.dumps(payload, indent=2))
    if not acts:
        import sys
        sys.stderr.write(
            "\n→ No preference acts yet. Build the lens first:\n"
            "    trinity-local lens-build\n"
        )


def handle_lens_stop(args):
    """#242a — request cancellation of a running lens build."""
    import json
    from ..lens_progress import read_progress, request_cancel
    request_cancel()
    prog = read_progress()
    running = bool(prog and prog.status == "running")
    print(json.dumps({
        "ok": True,
        "canceled_requested": True,
        "was_running": running,
        "stage": prog.stage if prog else None,
    }, indent=2))
    if not running:
        import sys
        sys.stderr.write(
            "\n→ No lens build appears to be running; the cancel flag is set "
            "anyway and the next build start clears it.\n"
        )
    return 0


def handle_lens_resync(args):
    import sys
    path, summary = resync_lens_from_disk()
    print(json.dumps({"path": str(path), **summary}, indent=2))
    if not summary.get("ok"):
        sys.stderr.write(
            "\n→ Nothing to resync. Build a lens first:\n"
            "    trinity-local lens-build\n"
        )
        return
    sys.stderr.write(
        f"\n→ Registry seeded ({summary['active_tensions']} active tension(s)); "
        f"lens.md re-rendered with support. View it:\n"
        "    trinity-local lens-show\n"
    )
