"""`trinity-local lens-health` — run the degeneracy self-test on your own lens.

The user-facing version of the checks the maintainers run by hand: it tells you
whether the lens Trinity built from your transcripts is TRUSTWORTHY or degenerate
(TF-IDF fallback, collapsed topology, template pollution, empty tensions) and
abstains honestly rather than greening junk. Read-only.

Exit code is the contract for scripting / benchmark-gating: 0 when no degeneracy
blocks trust (clean or merely thin), 1 when the lens is degenerate or unbuilt.
"""
from __future__ import annotations

import json

from ..lens_health import format_human, run_lens_health


def register(subparsers):
    parser = subparsers.add_parser(
        "lens-health",
        help="Self-test your lens for degeneracy (the checks we run, run by you)",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    parser.set_defaults(handler=handle_lens_health)


def handle_lens_health(args) -> int:
    report = run_lens_health()
    if getattr(args, "as_json", False):
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_human(report))
    # 0 = no degeneracy blocks trust (clean or thin); 1 = degenerate / unbuilt.
    return 0 if not report.blocking else 1
