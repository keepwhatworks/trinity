from __future__ import annotations

import argparse
import importlib
import os
from collections.abc import Iterable
from types import ModuleType


def _pin_hf_offline() -> None:
    """Default Trinity to fully offline behavior for the HuggingFace Hub.

    Trinity should never make outbound HF calls during normal operation —
    embeddings rely on a model that's already cached at
    ``~/.cache/huggingface/hub/`` (or wherever ``HF_HOME`` points). The
    one-time download is a deliberate user action via
    ``huggingface-cli download nomic-ai/modernbert-embed-base`` (or by
    running once with ``HF_HUB_OFFLINE=0 trinity-local import-export <path>``;
    the seed-from-taste-terminal verb that originally sat here retired
    2026-05-27 in favor of the auto-detecting import-export).

    Why setdefault and not unconditional set: a user who explicitly wants
    online behavior (e.g. CI pulling fresh model weights) can export
    ``HF_HUB_OFFLINE=0`` before invoking ``trinity-local`` and we honor it.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    # Suppress the "no telemetry permission" log line that fires even when
    # the rest of HF is fully offline.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


CORE_COMMAND_MODULES = (
    # `adapters` CLI verb retired 2026-05-27 (commit 6a03d10 follow-up) —
    # `trinity-local status` already shows provider adapter status; the
    # dedicated verb was a duplicate surface with zero unique value.
    # The src/trinity_local/adapters.py library module survives (used by
    # status / launchpad_data). See retired_names.py.
    "agent_emit",
    "cortex",
    "council",
    "debug",
    # `decision_log` CLI verb retired 2026-05-27 — see retired_names.py.
    # The me/decisions.py loader survives so existing decision_log.jsonl
    # files keep flowing into lens-build at weight=2.0.
    "download_embedder",
    "dream",
    "eval",
    "eval_import",
    "extension_repair",
    "import_export",
    "install_umbrella",
    "lens_health",
    "lens_import",
    "me",
    "me_card",
    # `moves` CLI verbs (moves-build, moves-show, moves-export) retired
    # 2026-05-27 — see retired_names.py. The moves substrate was the
    # procedural-memory layer Trinity attempted before realizing the
    # chairman LLM bridges declarative→procedural at inference time,
    # making moves a redundant projection of the lens. See #184.
    "portal",
    # `replay` CLI verb retired 2026-05-27 — see retired_names.py.
    # The natural way to populate the routing table is to use Trinity
    # normally; council outcomes accumulate on disk and aggregate
    # automatically via compute_personal_routing_table().
    "review",
    # `seed` CLI verb retired 2026-05-27 — see retired_names.py.
    # The bulk-ingest engine lives in src/trinity_local/ingest_helpers.py
    # and is exposed via `import-export` (auto-detects export type at
    # any path) instead of the personal-rig directory layout
    # seed-from-taste-terminal required.
    "status",
    "telemetry",
    "update",
    "vocabulary",
    "watch",
)

OPTIONAL_COMMAND_MODULES = (
    "install",
)


def _module_path(name: str) -> str:
    package = __package__ or "trinity_local"
    return f"{package}.{name}"


def _import_optional(module_path: str) -> ModuleType | None:
    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if exc.name == module_path:
            return None
        raise


def _iter_command_modules() -> Iterable[ModuleType]:
    for name in CORE_COMMAND_MODULES:
        yield importlib.import_module(_module_path(f"commands.{name}"))
    for name in OPTIONAL_COMMAND_MODULES:
        module = _import_optional(_module_path(f"commands.{name}"))
        if module is not None:
            yield module


def _load_mcp_runner():
    module_path = _module_path("mcp_server")
    module = _import_optional(module_path)
    if module is None:
        raise SystemExit("error: MCP server support is not available in this checkout.")
    return module.run_stdio_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trinity-local")
    parser.add_argument("--config", help="Path to config.json", default=None)

    # `metavar` overrides argparse's auto-generated choice list (which
    # would print all 40+ registered subparsers in both the usage line
    # AND the descriptive table heading). Q4 surface-collapse (#213) leads
    # with the two product words — `lens` and `council` — then the setup +
    # cold-start essentials.
    subparsers = parser.add_subparsers(
        dest="command",
        required=False,
        metavar="{lens,council,dream,status,install}",
    )

    for module in _iter_command_modules():
        module.register(subparsers)

    _hide_non_canonical_from_help(subparsers)
    return parser


# Area 5 → Q4 surface-collapse (#213). The user-visible surface is exactly
# these five verbs, led by the two product words (`lens`, `council`);
# everything else stays registered (the launchpad's Native Messaging
# dispatch and the Chrome extension's action allowlist both call subparsers
# by name — dropping the registrations would break real flows) but is
# omitted from `trinity-local --help`. Power-user verbs keep working by
# their original names and remain reachable under `trinity-local debug
# <subcmd>` for discoverability. Order here IS the help order.
USER_FACING_COMMANDS = (
    "lens",
    "council",
    "dream",
    "status",
    "install",
)


def _hide_non_canonical_from_help(subparsers: argparse._SubParsersAction) -> None:
    """Suppress non-canonical subparsers from `--help` output without
    dropping them from the live argparse surface.

    Two reasons subparsers stay registered:
      - Launchpad dispatch fires `trinity-local council-launch …` via
        Chrome Native Messaging. Dropping the registration breaks the
        dispatcher.
      - Power users may still type the bare names. They keep working;
        they're just no longer advertised.

    Mechanism: argparse formats subparser help by iterating
    `subparsers._choices_actions`. Setting `help = argparse.SUPPRESS`
    leaks the literal "==SUPPRESS==" string into the output (it's
    only honored for argument actions, not subparser choices), so we
    remove the entries entirely. The parsers stay in `subparsers.choices`
    — still callable, just no longer advertised in `--help`.
    """
    kept = {
        action.dest: action
        for action in subparsers._choices_actions
        if action.dest in USER_FACING_COMMANDS
    }
    # Render in the canonical product-first order, not registration order.
    subparsers._choices_actions = [
        kept[name] for name in USER_FACING_COMMANDS if name in kept
    ]


def _run_schema_migrations() -> None:
    """Forward-migrate `~/.trinity/` on the first launch under a new binary
    (#183). Cheap + idempotent when current; never raises. This is the one
    invocation point — `main()` covers both the bare CLI and the `--mcp`
    server it spawns."""
    try:
        from .migrations import run_migrations
        from .state_paths import migrate_legacy_state_layout

        run_migrations()
        # Eager directory-rename migrations (tasks→todos, memory→prompts). These
        # are otherwise lazy (fire only on tasks_dir()/prompts_dir() access), but
        # the launchpad reads the prompt index by DIRECT path — so an upgrading
        # user who runs `portal-html` first would see "0 prompts" until some
        # other op happened to touch the accessor. Idempotent + cheap.
        migrate_legacy_state_layout()
    except Exception:
        # Schema migration must never block startup. A real failure surfaces
        # in `status` (which reports the recorded vs current version).
        pass


def main() -> None:
    _pin_hf_offline()
    _run_schema_migrations()
    parser = build_parser()
    parser.add_argument("--mcp", action="store_true", help="Run as an MCP server")
    args = parser.parse_args()

    if args.mcp:
        import asyncio
        try:
            asyncio.run(_load_mcp_runner()())
        except KeyboardInterrupt:
            pass
        return

    if not args.command:
        parser.print_help()
        return

    if not hasattr(args, "handler"):
        parser.error(f"Unknown command: {args.command}")

    # Propagate the handler's exit code so failures are visible to shells/CI.
    # Handlers return an int exit code (0/None = success, non-zero = failure);
    # without this, a handler that `return 1`s (e.g. eval-import on a missing
    # file) still exited 0, so `import … && next` chained right past the failure.
    # Only non-zero raises — success paths return normally, leaving callers that
    # invoke main() directly (tests) unaffected.
    rc = args.handler(args)
    if isinstance(rc, int) and rc != 0:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
