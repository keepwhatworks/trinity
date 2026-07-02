"""Class-level guard: no rendered Trinity surface may reference an undefined CSS
custom property.

The recurring defect (UX sweep iter 45 → iter 46): an f-string HTML module writes
`var(--muted, #888)` / `var(--accent-warm)` / `var(--divider)` / `var(--text)` —
a token that is NOT declared in that surface's `:root`. CSS then silently resolves
the reference to the fallback (often an off-palette hex like #888 = 3.42:1, below
the WCAG-AA body floor) or, with no fallback, to the property's inherited/initial
value (a teal-grey border where a #dde1e6 hairline was intended; an invalid color).
The green check (page renders) passes while the paint is wrong.

Iter 45 fixed two such refs in launchpad_template.py but only swept that module.
This guard closes the CLASS across ALL THREE HTML-generating f-string modules —
council_review.py (live council + unified review), memory_viewer.py, and
launchpad_template.py — by parsing each module's REAL rendered output, extracting
every `var(--token)` reference and every `--token:` declaration, and asserting that
every referenced token is defined within that same rendered surface. A FUTURE
undefined var on any of these surfaces reds this test naming the offending token.

Pure-string parse of the rendered HTML (no browser needed) so it rides the default
`pytest -q` suite and bites cheaply on every change.
"""
from __future__ import annotations

import re

# A declaration: "--x:" (the name immediately followed by a colon). References use
# "var(--x" so they never match this — the colon disambiguates def from ref.
_DEF_RE = re.compile(r"(--[a-zA-Z0-9_-]+)\s*:")
# A var() reference. We capture the FULL argument list of each var(...) so we can
# also catch tokens inside a nested fallback, e.g. var(--a, var(--b)).
_VAR_CALL_RE = re.compile(r"var\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
# Inside a captured var() argument list, every --token used as a *reference* (the
# first token is the primary; any further --token is a nested fallback reference).
_TOKEN_RE = re.compile(r"--[a-zA-Z0-9_-]+")


def _defined_tokens(html: str) -> set[str]:
    """Every CSS custom property DECLARED in the rendered surface (any `:root`/rule)."""
    return set(_DEF_RE.findall(html))


def _referenced_tokens(html: str) -> set[str]:
    """Every CSS custom property REFERENCED via var(...) in the rendered surface,
    including tokens used inside a nested fallback."""
    refs: set[str] = set()
    for arg in _VAR_CALL_RE.findall(html):
        refs.update(_TOKEN_RE.findall(arg))
    return refs


def _rendered_surfaces() -> dict[str, str]:
    """name -> rendered HTML for each f-string HTML-generating module.

    Covers the no-arg render surfaces directly via a minimal
    fixture so the SHARED_CSS / page-local `:root` tokens are in the string.
    """
    from trinity_local import council_review, memory_viewer
    from trinity_local.launchpad_template import render_launchpad_html

    return {
        "live_council (council_review.render_live_council_page)": (
            council_review.render_live_council_page()
        ),
        "memory_viewer (memory_viewer.render_memory_viewer_html)": (
            memory_viewer.render_memory_viewer_html()
        ),
        "launchpad (launchpad_template.render_launchpad_html)": (
            render_launchpad_html(page_data={"version": "0.0.0-test"})
        ),
    }
    # (unified_review surface removed with render_unified_council_page, #311/#8;
    # the live council page carries the same CSS vars and is still checked above.)


def test_no_surface_references_an_undefined_css_var():
    """Every var(--token) on every rendered surface must resolve to a token DECLARED
    on that same surface — closing the iter-45/46 undefined-var class across
    council_review.py, memory_viewer.py, and launchpad_template.py."""
    surfaces = _rendered_surfaces()
    failures: list[str] = []
    for name, html in surfaces.items():
        defined = _defined_tokens(html)
        referenced = _referenced_tokens(html)
        undefined = sorted(referenced - defined)
        if undefined:
            failures.append(
                f"{name}: references undefined CSS var(s) {undefined} — "
                f"the iter-45 class (e.g. var(--muted,#888) → 3.42:1, "
                f"var(--divider) → currentColor). Define the token in this "
                f"surface's :root or point the ref at the correct existing token "
                f"(defined here: {sorted(defined)[:8]}…)."
            )
    assert not failures, "Undefined CSS custom property reference(s):\n" + "\n".join(
        failures
    )


def test_guard_extraction_distinguishes_def_from_ref_and_catches_nested():
    """Sanity-pin the extractor so the guard itself can't silently rot: a `--x:`
    declaration is DEFINED not referenced; a `var(--y)` is referenced; a nested
    `var(--a, var(--b))` references BOTH --a and --b."""
    sample = (
        ":root { --x: #fff; --a: red; --b: blue; }"
        " .q { color: var(--y); border: 1px solid var(--a, var(--b)); }"
    )
    assert _defined_tokens(sample) == {"--x", "--a", "--b"}
    assert _referenced_tokens(sample) == {"--y", "--a", "--b"}
    # --y is referenced but not defined -> would be flagged.
    assert (_referenced_tokens(sample) - _defined_tokens(sample)) == {"--y"}
