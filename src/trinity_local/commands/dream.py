"""`trinity-local dream` — compatibility alias for `trinity-local lens`.

`dream` folded into `lens --deep` on 2026-07-04. On 2026-10-15 `--deep` lost the phase that
made it different (council_6a9187cd799419e7): it clustered similar past prompts and spent one
flagship chairman call per cluster synthesizing a "virtual council" -- about 430 calls and three
hours on a real history -- read only by tallies that the lens, the trust ledger and councils all
excluded. What remained (the lens build, vocabulary, the core.md distill) is what `lens` runs, so
both names run `lens`. The launchpad's "Refresh memory" button dispatches `dream`.
"""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace


def register(subparsers):
    _ALIAS = ("(Compatibility alias for `lens`, which `lens --deep` also runs since 2026-10-15.) "
              "Rebuild your lens, vocabulary and core.md from your own prompts.")
    sp = subparsers.add_parser(
        "dream",
        help=_ALIAS,
        # `dream` is hidden from the top-level --help (main.USER_FACING_COMMANDS), so the
        # description is where a user typing `dream --help` learns it is `lens`.
        description=_ALIAS,
    )
    sp.add_argument(
        "--only-distill",
        action="store_true",
        help="Only re-distill core.md from the existing memories (one flagship call; none "
             "when core.md is kept as first written).",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="Cluster your topics and print their summary; no model calls.",
    )
    sp.add_argument(
        "--primary-provider",
        default=None,
        help="Chairman for the core.md distillation (default: claude).",
    )
    sp.set_defaults(handler=handle_dream)


def handle_dream(args):
    if getattr(args, "only_distill", False):
        return only_distill(getattr(args, "primary_provider", None) or "claude")
    from ..me_builder import ME_SAMPLE_SIZE
    from .me import handle_me_build
    me_args = SimpleNamespace(
        sample_size=ME_SAMPLE_SIZE, k_basins=None, dry_run=bool(getattr(args, "dry_run", False)),
        deep=False, only_distill=False, force=False, legacy=False,
    )
    return handle_me_build(me_args)


def only_distill(provider: str) -> int:
    """Re-distill core.md from the memories already on disk: no embedder, no build."""
    from ..distill import distill_via_chairman
    from ..lens_addon import enable_lens
    enable_lens()  # an explicit distill opts into the lens add-on, as a build does
    started = time.monotonic()
    print("distilling memories → core.md…", file=sys.stderr)
    report = distill_via_chairman(provider=provider)
    print(json.dumps({
        "ok": bool(report.get("ok")),
        "phases": {"distill": report},
        "total_ms": int((time.monotonic() - started) * 1000),
        "mode": "only-distill",
    }, indent=2))
    return 0
