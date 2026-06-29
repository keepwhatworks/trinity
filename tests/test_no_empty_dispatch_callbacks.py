"""Executable guard for principle #22 (empty callbacks swallow dispatch failures).

Earned 2026-05-26: `stopCouncil()` shipped as
`dispatcher.dispatch({..., onResult: () => {}})` — the empty arrow silently
consumed every failure (click Stop with no extension → kept polling, no banner,
no feedback). The original guard (test_council_review_stop_dispatch_failure)
covered only that ONE site. This is the generalized, principle-as-code version:
it renders every page that dispatches and fails if ANY `onResult:` handler is an
empty arrow, so a NEW dispatch site (or a refactor that empties an existing one)
is caught automatically — the #22 smell can't quietly recur.

Scanning the RENDERED HTML (single braces), not the f-string source (`{{ }}`
doubled), keeps the regex honest. Promoted from a one-site assertion to a
project-wide scanner per the founder's 2026-06-02 ask: "make the principles
executable where they aren't" (principles.md governance, tier 1).
"""
from __future__ import annotations

import re

# onResult: () => {}   /   onResult: (r) =>{}   — an EMPTY body is the #22 smell.
# (A NON-empty handler is fine even if it routes failure via its own status field
#  — there are four legit handlers here each using a different surface:
#  chainError, handleDispatchResult, refreshMemoryStatus, importProbeResult/
#  importStatus — so "must reference surface X" can't be a clean invariant; the
#  precise, encodable invariant is "the callback isn't empty".)
_EMPTY_ONRESULT = re.compile(r"onResult\s*:\s*\(\s*[a-zA-Z_]*\s*\)\s*=>\s*\{\s*\}")
_FAILURE_SURFACES = (
    "handleDispatchResult",
    "chainError",
    "dispatchErrorMessage",
    "launchError",
)


def _rendered_pages() -> dict[str, str]:
    """The two surfaces that dispatch via __TRINITY_DISPATCH__. Rendered (not
    source) so the JS braces are single — both render without a TRINITY_HOME."""
    from trinity_local.council_review import render_live_council_page
    from trinity_local.launchpad_template import render_launchpad_html

    return {
        "live_council.html": render_live_council_page(),
        "launchpad.html": render_launchpad_html(page_data={}),
    }


def test_no_empty_onresult_dispatch_callback():
    """No dispatch site may swallow its failure with an empty `onResult: () => {}`."""
    offenders = []
    for page, html in _rendered_pages().items():
        for m in _EMPTY_ONRESULT.finditer(html):
            ctx = html[max(0, m.start() - 10): m.end() + 5]
            offenders.append(f"{page}: ...{ctx!r}")
    assert not offenders, (
        "principle #22: an empty `onResult: () => {}` dispatch callback silently "
        "swallows the failure (no error banner, no optimistic-UI rollback). Route "
        "the failure into a user-visible surface "
        f"(e.g. {', '.join(_FAILURE_SURFACES)}, or the handler's own status field). "
        "Offenders:\n" + "\n".join(offenders)
    )
