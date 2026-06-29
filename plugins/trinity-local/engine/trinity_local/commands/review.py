"""Handler for the review command — post-hoc review (Council-lite)."""
from __future__ import annotations

import json

from ..config import load_config
from ..notifications import open_path
from ..review import render_review_html, run_review, save_review
from ..task_runtime import load_task_record, load_task_record_or_exit


DEFAULT_REVIEWER_COMMANDS = {
    "claude": ["claude", "-p"],
    "antigravity": ["agy", "-p"],
    "codex": ["codex", "--quiet"],
}


def _reviewer_command_for(*, reviewer: str, config_path: str | None) -> list[str]:
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        return DEFAULT_REVIEWER_COMMANDS.get(reviewer, [reviewer])
    provider_config = config.providers.get(reviewer)
    if provider_config:
        command = list(provider_config.command)
        # Inject the authoritative model. Since v1.7.40 the config loader
        # strips any inline `--model` from command/args into config.model, so
        # the raw command no longer carries it — without this the reviewer
        # runs on the CLI's default model, not the configured one. Insert
        # right after the binary so it lands before any prompt-consuming flag
        # (e.g. claude `-p`). Skip antigravity: agy's CLI has no --model flag.
        model = getattr(provider_config, "model", None)
        if model and reviewer != "antigravity" and "--model" not in command:
            command[1:1] = ["--model", model]
        return command
    return DEFAULT_REVIEWER_COMMANDS.get(reviewer, [reviewer])


def register(subparsers):
    parser = subparsers.add_parser("review", help="Run a post-hoc review of a completed task")
    parser.add_argument("--task", required=True, help="Task ID to review")
    parser.add_argument("--reviewer", required=True, help="Provider to use as reviewer (e.g. antigravity, codex)")
    parser.add_argument("--cwd", default=".", help="Working directory for the reviewer")
    parser.add_argument("--open-browser", action="store_true", help="Open review in browser")
    parser.set_defaults(handler=handle_review)


def handle_review(args):
    task = load_task_record_or_exit(args.task)

    task_text = task.task_text or task.title or ""
    output_text = task.metadata.get("final_text", "") if task.metadata else ""
    if not output_text:
        output_text = task_text

    if not task_text:
        print(json.dumps({"error": "Task has no text to review"}))
        raise SystemExit(1)

    reviewer_command = _reviewer_command_for(reviewer=args.reviewer, config_path=args.config)

    print(f"Running post-hoc review with {args.reviewer}...")
    result = run_review(
        task_id=task.task_id,
        task_text=task_text,
        output_text=output_text,
        original_provider=task.source_provider or "unknown",
        reviewer_provider=args.reviewer,
        reviewer_command=reviewer_command,
        cwd=args.cwd,
    )

    review_path = save_review(result)
    html_path = render_review_html(result)

    output = {
        "review_id": result.review_id,
        "task_id": result.task_id,
        "reviewer": result.reviewer_provider,
        "verdict": result.verdict,
        "issues_count": len(result.issues),
        "suggestions_count": len(result.suggestions),
        "elapsed_seconds": result.elapsed_seconds,
        "review_path": str(review_path),
        "html_path": str(html_path),
    }
    print(json.dumps(output, indent=2))

    if args.open_browser:
        open_path(html_path)
