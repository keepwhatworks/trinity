"""Recurrence guard: PII-bearing render PNGs must never be committable.

The 2026-05/06 corpus-leak incident (purged in commit e4e0d64d) found machine-
rendered screenshots of the REAL ``~/.trinity`` served *and committed to git
history* — a privacy breach that needed a ``filter-repo`` purge. The fix was a
``.gitignore`` ``*.png`` rule with a SHORT allow-list of synthetic/brand example
PNGs, and the comment there records that the smoke/me-card re-includes were
DELIBERATELY removed so those real-home renders stay ignored.

Nothing TESTED that contract, though. A future ``!docs/smoke/*.png`` re-include, a
weakened ``*.png`` rule, or an accidental ``git add -f`` of a real-home screenshot
would silently re-expose the founder's data — and the existing enforcement (the
``docs/**`` PII *text* grep) scans text, so it can't see PII inside a binary PNG.

This pins the gitignore side of the privacy invariant from BOTH directions
([[test_the_boundary_and_the_action]]): the synthetic/brand PNGs stay TRACKED, and
the real-home render dirs stay IGNORED. Skips cleanly when git isn't available
(e.g. an sdist build tree), so it never falsely fails off a checkout.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The ONLY PNGs that may be committed: synthetic launchpad example (the freshness
# canary), the brand favicon, the synthetic eval-card launch assets, and the
# extension icons. Everything else — anything rendered from ~/.trinity — must be
# ignored. A match is exact-path OR directory-prefix.
ALLOWED_TRACKED_PNGS = (
    "docs/launchpad_example.png",
    "docs/favicon.png",
    "docs/og-card.png",           # social-share OG card — brand art, no user data
    "docs/launch_assets/",        # synthetic eval-card brand examples
    "browser-extension/icons/",   # extension icons (no user data)
)

# Render directories that hold screenshots of the REAL ~/.trinity — must stay
# ignored. docs/smoke/ is the browser-smoke gate's full-page-screenshot sink.
PII_RENDER_PROBES = (
    "docs/smoke/1-launchpad.png",
    "docs/smoke/14-memory-viewer.png",
    "docs/smoke/any-future-surface.png",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(REPO), capture_output=True, text=True, timeout=20
    )


def _git_available() -> bool:
    try:
        return _git("rev-parse", "--is-inside-work-tree").returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError):
        return False


def _allowed(png: str) -> bool:
    return any(png == a or png.startswith(a) for a in ALLOWED_TRACKED_PNGS)


def test_no_pii_render_png_is_tracked():
    """Every committed .png is on the synthetic/brand allow-list — a tracked PNG
    outside it is a real-home render that leaked into git (the e4e0d64d class)."""
    if not _git_available():
        pytest.skip("not a git work tree")
    tracked = [p for p in _git("ls-files", "*.png").stdout.splitlines() if p.strip()]
    assert tracked, "no tracked PNGs found — expected the brand/example allow-list"
    leaked = [p for p in tracked if not _allowed(p)]
    assert not leaked, (
        f"PNG(s) committed but not on the synthetic/brand allow-list: {leaked}. "
        "These may be screenshots rendered from the REAL ~/.trinity — the corpus-"
        "leak class (commit e4e0d64d). Regenerate from a SYNTHETIC fixture, or add "
        "to ALLOWED_TRACKED_PNGS only if the PNG provably contains no user data."
    )


def test_real_home_render_dirs_stay_gitignored():
    """The browser-smoke screenshot sink (docs/smoke/*.png) renders the real home —
    it must stay ignored so a smoke run can't commit the founder's data."""
    if not _git_available():
        pytest.skip("not a git work tree")
    for probe in PII_RENDER_PROBES:
        rc = _git("check-ignore", probe).returncode
        assert rc == 0, (
            f"{probe} is NOT gitignored — a real-home screenshot in docs/smoke/ "
            "could be committed. The `*.png` rule or its allow-list regressed; do "
            "NOT add a `!docs/smoke/*.png` re-include (that re-opens the leak)."
        )


def test_allow_list_pngs_are_actually_committable():
    """Inverse direction: the synthetic/brand example PNGs must NOT be swept by an
    over-broad ignore rule — the launchpad freshness canary depends on its example
    PNG being tracked, and the brand assets must ship."""
    if not _git_available():
        pytest.skip("not a git work tree")
    for keep in ("docs/launchpad_example.png", "docs/favicon.png"):
        rc = _git("check-ignore", keep).returncode
        assert rc == 1, (
            f"{keep} is gitignored but must stay TRACKED (the *.png rule lost its "
            f"`!{keep}` re-include). The screenshot-freshness canary + brand assets "
            "break if these can't be committed."
        )
