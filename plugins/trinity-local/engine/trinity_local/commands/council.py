"""Council command handlers."""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from types import SimpleNamespace

from ..config import load_config
from ..council_review import write_live_council_page
from ..council_runner import run_council
from ..council_runtime import (
    create_prompt_bundle,
    load_council_outcome,
    load_council_outcome_or_exit,
    load_prompt_bundle,
    load_prompt_bundle_or_exit,
    save_prompt_bundle,
)
from ..council_status import (
    council_status_json_path,
    init_council_run_state,
    load_council_status,
    write_council_status,
)
from ..notifications import open_path
from ..refresh import refresh_launchpad
from ..task_runtime import ensure_task_record, save_sync_record, save_task_record
from ..utils import stable_id
from .helpers import read_text_file


def _status_metadata(*, members: list[str], cwd: Path, pid: int | None = None, process_group_id: int | None = None) -> dict[str, object]:
    metadata: dict[str, object] = {
        "kind": "council",
        "members": members,
        "cwd": str(cwd),
    }
    if pid is not None:
        metadata["pid"] = pid
    if process_group_id is not None:
        metadata["process_group_id"] = process_group_id
    return metadata


def register(subparsers):
    # Internal council subcommands (council-prompt / -run / -outcome) were
    # retired from the public CLI on 2026-05-17. They were programmatic
    # helpers used only by `council-launch` internally and had no skill /
    # launchpad / Native-Messaging-dispatch callers. Their library shapes
    # (run_council, create_council_outcome, render_*_prompt) remain
    # importable from trinity_local.council_runner / council_runtime —
    # nothing about the wire shape changed, only the user-facing CLI.

    council_start_parser = subparsers.add_parser("council-start", help="Create/update task, run council, and optionally open the review page")
    council_start_parser.add_argument("--bundle", required=True, help="Bundle id or path")
    council_start_parser.add_argument("--members", nargs="+", required=True, help="Member providers to query")
    council_start_parser.add_argument(
        "--primary-provider",
        default=None,
        help="Chairman/synthesizer provider. If omitted, auto-selected as the strongest predicted model for the task.",
    )
    council_start_parser.add_argument("--cwd", default=".", help="Working directory for provider runs")
    council_start_parser.add_argument("--status-token", default=None, help="Launchpad status token for same-tab council tracking")
    council_start_parser.add_argument("--open-browser", action="store_true")
    council_start_parser.set_defaults(handler=handle_council_start)

    # Q4 surface-collapse (#213): `council` is the user-facing product word
    # for "run all three providers on this and synthesize". `council-launch`
    # stays as an alias — launchpad Native-Messaging dispatch + the Chrome
    # extension action allowlist both call it by that name.
    council_launch_parser = subparsers.add_parser(
        "council",
        aliases=["council-launch"],
        help="Run a council: all three providers answer, the chairman synthesizes one verdict in your voice.",
    )
    council_launch_parser.add_argument("--task", required=True, help="Task text to compare across providers")
    council_launch_parser.add_argument("--goal", default="Find the strongest answer.")
    council_launch_parser.add_argument("--instructions", default="Prefer the strongest answer for the user's current task.")
    council_launch_parser.add_argument("--context-file", default=None)
    council_launch_parser.add_argument("--project-hint", default="")
    # Default = enabled subset of canonical providers, not hardcoded 3-up.
    # Codex-only / claude-only / gemini-only users no longer fire a broken
    # 3-column council (persona audit P89). User can still pass --members
    # claude gemini codex to force the full lineup.
    from ..config import default_council_members
    council_launch_parser.add_argument("--members", nargs="+", default=default_council_members())
    council_launch_parser.add_argument(
        "--primary-provider",
        default=None,
        help="Chairman/synthesizer provider. If omitted, auto-selected as the strongest predicted model for the task.",
    )
    council_launch_parser.add_argument("--cwd", default=".")
    council_launch_parser.add_argument("--status-token", default=None)
    council_launch_parser.add_argument("--open-browser", action="store_true")
    council_launch_parser.set_defaults(handler=handle_council_launch)

    council_stop_parser = subparsers.add_parser(
        "council-stop",
        help="Stop a running council launched from the launchpad",
    )
    council_stop_parser.add_argument("--status-token", required=True)
    council_stop_parser.set_defaults(handler=handle_council_stop)

    council_share_parser = subparsers.add_parser(
        "council-share",
        help="Render a council outcome as a 1200×630 PNG share card (privacy-safe — no user prompts inlined)",
    )
    council_share_parser.add_argument("--council", required=True, help="Council ID")
    council_share_parser.add_argument(
        "--out", default=None,
        help="Output PNG path. Defaults to ~/.trinity/share/council_<id8>.png.",
    )
    council_share_parser.add_argument(
        "--open", dest="open_after", action="store_true",
        help="Open the produced PNG with the OS default handler.",
    )
    council_share_parser.set_defaults(handler=handle_council_share)

    # Single unified iteration command. Replaces the prior trio of
    # council-continue / council-refine / council-auto-chain — they all
    # called the same engine (run_consensus_round / auto_chain_council)
    # with one flag varied. One command keyed by (rounds, prompt) collapses
    # the surface without losing any behavior:
    #   council-iterate --rounds 1               → former "continue"
    #   council-iterate --rounds 1 --prompt P    → former "refine"
    #   council-iterate --rounds N               → former "auto-chain"
    council_iterate_parser = subparsers.add_parser(
        "council-iterate",
        help="Iterate an existing council. With --prompt, run one round under that "
             "directive. Without --prompt, auto-iterate up to --rounds, stopping when "
             "the chairman declares convergence (agreed_claims rich, disagreed_claims "
             "empty, confidence high).",
    )
    council_iterate_parser.add_argument("--council", required=True, help="Parent council_run_id")
    council_iterate_parser.add_argument(
        "--rounds", type=int, default=3,
        help="Max rounds when auto-iterating; ignored when --prompt is set (always 1 round).",
    )
    council_iterate_parser.add_argument(
        "--prompt", default=None,
        help="New user directive. If set, runs one round under this directive instead of auto-iterating.",
    )
    council_iterate_parser.add_argument("--cwd", default=".")
    council_iterate_parser.add_argument(
        "--status-token", default=None,
        help="Reuse this status token so iteration rounds overwrite the same live page.",
    )
    council_iterate_parser.add_argument("--open-browser", action="store_true")
    council_iterate_parser.set_defaults(handler=handle_council_iterate)


def handle_council_start(args):
    config = load_config(args.config)
    bundle = load_prompt_bundle_or_exit(args.bundle)
    cwd = Path(args.cwd).expanduser().resolve()
    status_token = getattr(args, "status_token", None)
    if not args.primary_provider:
        from ..ranker import predict_strongest_chairman

        args.primary_provider = predict_strongest_chairman(
            bundle.task_text,
            available_providers=list(args.members),
        )
    process_id = os.getpid()
    process_group_id = os.getpgid(0)
    status_metadata = _status_metadata(
        members=list(args.members),
        cwd=cwd,
        pid=process_id,
        process_group_id=process_group_id,
    )
    completed_metadata = _status_metadata(members=list(args.members), cwd=cwd)
    task = ensure_task_record(
        bundle=bundle,
        status="running",
        tags=["council"],
        metadata={"cwd": str(cwd)},
    )
    task_path = save_task_record(task)
    sync_path = save_sync_record(task)
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def _mark_canceled(message: str) -> None:
        task.status = "canceled"
        save_task_record(task)
        save_sync_record(task)
        if status_token:
            write_council_status(
                status_token,
                status="canceled",
                task_text=bundle.task_text,
                bundle_id=bundle.bundle_id,
                council_id=bundle.bundle_id,
                error=message,
                metadata=status_metadata,
            )
        refresh_launchpad()

    def _termination_handler(signum, _frame):  # type: ignore[no-untyped-def]
        sig_name = signal.Signals(signum).name
        _mark_canceled(f"Council stopped ({sig_name}).")
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _termination_handler)
    signal.signal(signal.SIGINT, _termination_handler)
    if status_token:
        init_council_run_state(
            status_token,
            task_text=bundle.task_text,
            bundle_id=bundle.bundle_id,
            council_id=bundle.bundle_id,
            members=list(args.members),
            metadata=status_metadata,
            runner_pid=process_id,
            runner_pgid=process_group_id,
        )
        refresh_launchpad()
    # Skip the "Council running" start notification by default. The live
    # council page already opens to a "Council running" status view with
    # streaming member responses — a system notification on top of that
    # is redundant and uses Script Editor's generic icon (no Trinity
    # branding) plus a "Show" button that opens an unhelpful preview pane.
    # Completion notifications still fire because they tell you something
    # the page can't (the run finished while you weren't looking).
    try:
        result = run_council(
            config=config,
            bundle=bundle,
            member_providers=args.members,
            primary_provider=args.primary_provider,
            cwd=cwd,
            run_state_token=status_token or bundle.bundle_id,
            # Honor chain mode end-to-end. Without these, the runner ignored
            # the caller's intent and dispatched parallel — silent drift
            # between the reported mode and the actual execution.
            mode=getattr(args, "mode", "parallel") or "parallel",
            sequence=getattr(args, "sequence", None),
        )
    except Exception as exc:
        if status_token:
            write_council_status(
                status_token,
                status="failed",
                task_text=bundle.task_text,
                bundle_id=bundle.bundle_id,
                error=str(exc),
                metadata=status_metadata,
            )
        refresh_launchpad()
        raise
    finally:
        signal.signal(signal.SIGTERM, original_sigterm)
        signal.signal(signal.SIGINT, original_sigint)
    # (The per-council "review_ready" action record retired 2026-07-02, #332:
    # its completion path died 2026-05-18 with the action-complete CLI, so the
    # store only ever GREW — 18k pending records, 2.3s status scans, zero
    # readers. The review page itself is the artifact; see retired_names.py.)
    if status_token and load_council_status(status_token) is None:
        write_council_status(
            status_token,
            status="completed",
            task_text=bundle.task_text,
            bundle_id=bundle.bundle_id,
            council_id=result.outcome.council_run_id,
            review_path=str(result.review_path),
            metadata=completed_metadata,
        )
    refresh_launchpad()

    # Auto-chain settings retired 2026-05-17: every-council auto-iterate
    # was a power-user setting that hid behavior from new users. Users now
    # click the auto-chain button on the council review page when they
    # want sequential refinement — the `council_auto_chain` dispatch
    # action wires that click to `council-iterate` programmatically.

    opened = open_path(result.review_path) if args.open_browser else False
    # Interactive terminal users get offered the review page. Gated on a TTY
    # so the launchpad's Native-Messaging dispatcher (non-TTY, consumes the
    # JSON below) is never blocked on input.
    if not opened:
        import sys as _sys
        if _sys.stdin.isatty() and _sys.stdout.isatty():
            try:
                ans = input("\nOpen the result in your browser? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans in ("", "y", "yes"):
                opened = open_path(result.review_path)
    payload = {
        "task_path": str(result.task_path or task_path),
        "sync_path": str(result.sync_path or sync_path),
        "review_path": str(result.review_path),
        "opened": opened,
        "council_run_id": result.outcome.council_run_id,
    }
    print(json.dumps(payload, indent=2))


def handle_council_launch(args):
    status_token = getattr(args, "status_token", None)
    metadata = {
        "launch_source": "launchpad",
        "members": list(args.members),
    }
    if status_token:
        metadata["status_token"] = status_token
    if args.project_hint:
        metadata["project_hint"] = args.project_hint
    bundle = create_prompt_bundle(
        task_cluster_id=stable_id("cluster", args.project_hint, args.task[:400]),
        task_text=args.task,
        context_excerpt=read_text_file(args.context_file),
        goal=args.goal,
        comparison_instructions=args.instructions,
        origin_provider="launchpad",
        origin_session_id=status_token,
        metadata=metadata,
    )
    save_prompt_bundle(bundle)
    if status_token:
        write_live_council_page()
    launch_args = SimpleNamespace(
        config=args.config,
        bundle=bundle.bundle_id,
        members=args.members,
        primary_provider=args.primary_provider,
        cwd=args.cwd,
        status_token=status_token,
        open_browser=args.open_browser,
        # Thread chain-mode params through to run_council. Before this fix,
        # handle_council_start dropped them and silently ran parallel even
        # when the caller (MCP `run_council(mode='chain')`) reported chain.
        mode=getattr(args, "mode", "parallel"),
        sequence=getattr(args, "sequence", None),
    )
    handle_council_start(launch_args)


def handle_council_stop(args):
    status_token = args.status_token
    status_path = council_status_json_path(status_token)
    if not status_path.exists():
        print(json.dumps({"stopped": False, "reason": "status_not_found", "status_token": status_token}, indent=2))
        return

    raw = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}
    metadata = dict(raw.get("metadata") or {})
    task_text = raw.get("task_text")
    bundle_id = raw.get("bundle_id")
    council_id = raw.get("council_id")
    pid = raw.get("runner_pid") or metadata.get("pid")
    pgid = raw.get("runner_pgid") or metadata.get("process_group_id")

    write_council_status(
        status_token,
        status="canceled",
        task_text=task_text,
        bundle_id=bundle_id,
        council_id=council_id,
        error="Council stopped by user.",
        metadata=metadata,
    )
    refresh_launchpad()

    killed = False
    kill_error = None
    try:
        if pgid:
            os.killpg(int(pgid), signal.SIGTERM)
            killed = True
        elif pid:
            os.kill(int(pid), signal.SIGTERM)
            killed = True
    except OSError as exc:
        kill_error = str(exc)
        if "No such process" in kill_error:
            killed = True

    print(
        json.dumps(
            {
                "stopped": killed,
                "status_token": status_token,
                "pid": pid,
                "process_group_id": pgid,
                "error": kill_error,
            },
            indent=2,
        )
    )


def handle_council_share(args):
    """Render a council outcome as a 1200×630 PNG share card.

    Rewritten 2026-05-17 (iteration 5 of the share-workflow audit).
    Prior implementation copied write_unified_council_page's redirect
    HTML (379 bytes pointing at a relative `live_council.html?...` path)
    to Desktop — which was useless to any recipient because the
    relative path resolved only on the author's filesystem.

    New shape: PNG card matching eval_card + me_card. Three share
    surfaces, one visual language. Privacy-safe by construction —
    only the chairman-extracted agreed_claims / disagreed_claims /
    winner cross to the card. The user's verbatim prompt + members'
    full response text never touch the artifact, AND the prompt no
    longer leaks into the output filename (former bug).
    """
    from ..council_card import collect_card_data_from_outcome, render_council_card
    from ..state_paths import share_dir

    outcome = load_council_outcome_or_exit(args.council)
    # Strip the "council_" prefix when slicing the id for the filename —
    # the prior code used outcome.council_run_id[:8] which (since the
    # id starts with literal "council_") produced filenames like
    # "trinity-council-council_-..." with a useless prefix.
    id_short = outcome.council_run_id.removeprefix("council_")[:8] or "anon"

    card_data = collect_card_data_from_outcome(outcome)
    png_bytes = render_council_card(card_data)

    out = (Path(args.out) if args.out
           else share_dir() / f"council_{id_short}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes)

    opened = False
    if args.open_after:
        opened = open_path(out)

    print(json.dumps({
        "ok": True,
        "path": str(out),
        "bytes": len(png_bytes),
        "council_id": outcome.council_run_id,
        "winner": card_data.winner,
        "members": list(card_data.members),
        "agreed_claims_count": len(card_data.agreed_claims),
        "disagreed_claim_present": card_data.disagreed_claim is not None,
        "opened": opened,
    }, indent=2))


# ---------------------------------------------------------------------------
# Consensus-iteration chain handlers
# ---------------------------------------------------------------------------


def _print_round_summary(result, round_label: str) -> None:
    outcome = result.outcome
    label = outcome.routing_label
    summary = {
        "round": round_label,
        "council_run_id": outcome.council_run_id,
        "review_path": str(result.review_path),
        "primary_provider": outcome.primary_provider,
        "winner_provider": outcome.winner_provider,
        "parent_council_id": outcome.metadata.get("parent_council_id"),
        "round_number": outcome.metadata.get("round_number"),
    }
    if label is not None:
        summary["winner"] = label.winner
        summary["confidence"] = label.confidence
        summary["agreed_claims"] = len(label.agreed_claims)
        summary["disagreed_claims"] = len(label.disagreed_claims)
        from ..council_runtime import chairman_says_converged
        summary["converged"] = chairman_says_converged(label)
    print(json.dumps(summary, indent=2))


def handle_council_iterate(args):
    """Unified iteration: rounds + optional --prompt directive. Replaces the
    prior trio of continue/refine/auto-chain handlers; they were all variants
    of the same `run_consensus_round` / `auto_chain_council` engine."""
    from ..council_runner import auto_chain_council, run_consensus_round

    config = load_config(args.config)
    parent = load_council_outcome_or_exit(args.council)
    cwd = Path(args.cwd).expanduser().resolve()
    status_token = getattr(args, "status_token", None)

    # When the user asks for a single round (with or without --prompt), they
    # want a forced continuation — the launchpad's "continue" button must
    # work even when the parent already converged. Skip auto_chain_council's
    # convergence gate; run exactly one round.
    if args.prompt or args.rounds == 1:
        result = run_consensus_round(
            config=config,
            parent_outcome=parent,
            user_refinement=args.prompt,
            cwd=cwd,
            run_state_token=status_token,
        )
        if args.open_browser:
            open_path(result.review_path)
        _print_round_summary(result, round_label="refine" if args.prompt else "continue")
        return

    # rounds > 1, no prompt → auto-iterate, stop on convergence.
    results = auto_chain_council(
        config=config,
        initial_outcome=parent,
        max_rounds=args.rounds,
        cwd=cwd,
        run_state_token=status_token,
    )
    if results and args.open_browser:
        open_path(results[-1].review_path)

    # Per-round summary
    rounds = []
    for i, r in enumerate(results):
        outcome = r.outcome
        label = outcome.routing_label
        rounds.append({
            "round_number": outcome.metadata.get("round_number"),
            "council_run_id": outcome.council_run_id,
            "review_path": str(r.review_path),
            "winner": label.winner if label else None,
            "confidence": label.confidence if label else None,
            "agreed_claims": len(label.agreed_claims) if label else 0,
            "disagreed_claims": len(label.disagreed_claims) if label else 0,
        })

    from ..council_runtime import chairman_says_converged
    final = results[-1].outcome if results else parent
    print(json.dumps({
        "ok": True,
        "initial_council_run_id": parent.council_run_id,
        "rounds_run": len(results),
        "max_rounds": args.rounds,
        "converged": chairman_says_converged(final.routing_label),
        "final_council_run_id": final.council_run_id,
        "rounds": rounds,
    }, indent=2))
