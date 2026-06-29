"""Vendored-asset supply-chain + packaging guard.

The launchpad / memory-viewer / council pages serve vendored JS libraries AND
self-hosted webfonts (Hanken Grotesk + JetBrains Mono woff2) from
``src/trinity_local/data/vendor/`` (so the "never leaves your machine" privacy
claim doesn't depend on a CDN being an honest broker at render time).
``scripts/vendor-sha256.txt`` pins the expected SHA-256 of each vendored file.

Two guards live here:
- ``test_vendored_js_matches_sha256_manifest`` fails if a committed vendored
  file's bytes drift from its pinned hash — catching a tampered/swapped
  library, or a version bump that updated the bytes but not the manifest.
- ``test_vendored_fonts_are_covered_by_package_data`` fails if a shipped font
  asset isn't matched by a ``[tool.setuptools.package-data]`` glob — catching
  the v1.7.310 latent bug where fonts shipped only via editable installs and
  were absent from real ``pip install``s (silent system-font fallback).

On a DELIBERATE bump: edit the URL in ``scripts/refresh-vendor.sh``, re-run it
(``scripts/refresh-vendor.sh --update-manifest`` regenerates the manifest), and
commit the URL + new bytes + new manifest line together so the diff is auditable.
"""
from __future__ import annotations

import fnmatch
import hashlib
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
PKG = REPO / "src" / "trinity_local"
VENDOR = PKG / "data" / "vendor"
FONTS = PKG / "data" / "fonts"
MANIFEST = REPO / "scripts" / "vendor-sha256.txt"
PYPROJECT = REPO / "pyproject.toml"


def _manifest() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        sha, name = line.split()
        out[name] = sha
    return out


def test_vendored_js_matches_sha256_manifest():
    manifest = _manifest()
    actual = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (*VENDOR.glob("*.js"), *VENDOR.glob("*.woff2"))
    }
    assert set(actual) == set(manifest), (
        "vendor/ files vs manifest mismatch: "
        f"only-in-dir={sorted(set(actual) - set(manifest))}, "
        f"only-in-manifest={sorted(set(manifest) - set(actual))}. "
        "Regenerate scripts/vendor-sha256.txt."
    )
    drift = {n: (manifest[n], actual[n]) for n in actual if actual[n] != manifest[n]}
    assert not drift, (
        "Vendored JS bytes drifted from the pinned SHA-256 (tampering, or a "
        f"version bump that didn't update the manifest): {drift}. If intentional, "
        "re-run `scripts/refresh-vendor.sh --update-manifest` and commit the new "
        "bytes + manifest together."
    )


def _package_data_globs() -> list[str]:
    """Extract the ``trinity_local = [ ... ]`` glob list from the
    ``[tool.setuptools.package-data]`` table of pyproject.toml. Text-parsed
    (no tomllib — that's 3.11+, and the package supports 3.10) and
    comment-stripped so the strings are exactly the shipped wildcards."""
    text = PYPROJECT.read_text(encoding="utf-8")
    block = text.split("[tool.setuptools.package-data]", 1)[1]
    # the trinity_local list runs from its `[` to the first standalone `]`
    body = block.split("trinity_local = [", 1)[1].split("]", 1)[0]
    return re.findall(r'"([^"]+)"', body)


def _glob_covers(glob: str, relpath: str) -> bool:
    """True if ``glob`` matches ``relpath`` under *setuptools* single-level
    semantics: ``*`` does NOT cross ``/`` (unlike fnmatch on the whole path).
    A coverage check that ignored ``/`` could green a ``data/*.woff2`` glob
    that the real wheel build would never expand — the exact false-pass we're
    guarding against. ``**`` globs (skills) are out of scope here."""
    g, p = glob.split("/"), relpath.split("/")
    if "**" in g or len(g) != len(p):
        return False
    return all(fnmatch.fnmatch(seg, pat) for seg, pat in zip(p, g))


def test_vendored_fonts_are_covered_by_package_data():
    """Latent-bug guard (v1.7.310): the brand fonts (woff2 webfonts + the PIL
    share-card TTFs) only render if they're actually IN the built wheel, which
    means a ``[tool.setuptools.package-data]`` glob has to match each one.

    Before v1.7.310 the table listed only ``data/vendor/*.js`` — so every
    vendored ``.woff2`` (and later the ``data/fonts/*.ttf``) shipped ONLY via
    the editable dev install and would have been absent from a real
    ``pip install``, silently falling back to system fonts on every user
    machine. Editable installs mask this forever, so a render test never
    catches it. This source-level check does: every shipped font asset must be
    covered by a package-data glob, evaluated with the wheel's own ``*``
    semantics. Mutating away the ``*.woff2`` / ``*.ttf`` glob fails here."""
    globs = _package_data_globs()
    assert globs, "could not parse package-data globs from pyproject.toml"

    # The runtime brand assets that MUST ship for the launchpad + cards to
    # render in-brand: the self-hosted webfonts and their PIL TTF counterparts
    # (the .txt are the OFL licenses that travel with the fonts).
    must_ship = [
        *VENDOR.glob("*.woff2"),
        *VENDOR.glob("*.txt"),
        *FONTS.glob("*.ttf"),
        *FONTS.glob("*.txt"),
    ]
    assert must_ship, "no vendored font assets found — did the data/ layout move?"

    uncovered = []
    for path in must_ship:
        rel = path.relative_to(PKG).as_posix()  # e.g. data/vendor/HankenGrotesk-400.woff2
        if not any(_glob_covers(g, rel) for g in globs):
            uncovered.append(rel)
    assert not uncovered, (
        "These vendored font assets are NOT matched by any "
        "[tool.setuptools.package-data] glob, so a pip/wheel install ships "
        f"without them (silent system-font fallback): {sorted(uncovered)}. "
        f"Current globs: {globs}. Add the missing wildcard to pyproject.toml."
    )


def test_vendored_js_is_covered_by_package_data():
    """Sibling of the font guard, for the HYDRATION-critical JS. The launchpad /
    memory viewer / council pages are petite-vue apps — they render as a blank,
    non-reactive page unless ``petite-vue.iife.js`` (and the charts via
    ``chart.umd.min.js``) actually ship in the wheel, i.e. a package-data glob
    matches them. The editable dev install reads them from the source tree, and the
    SHA-256 test above only checks the source bytes — so a pyproject edit that dropped
    the ``data/vendor/*.js`` glob would leave EVERY pip-installed launchpad blank and
    no render test would catch it (editable installs mask it forever). Found worth
    guarding 2026-06-08 by a clean-venv ``pip install .`` dogfood (the JS DID ship;
    this keeps it that way). Mutating away the ``*.js`` glob fails here."""
    globs = _package_data_globs()
    assert globs, "could not parse package-data globs from pyproject.toml"
    must_ship = list(VENDOR.glob("*.js"))
    assert must_ship, "no vendored .js found — did the data/vendor layout move?"

    uncovered = []
    for path in must_ship:
        rel = path.relative_to(PKG).as_posix()  # e.g. data/vendor/petite-vue.iife.js
        if not any(_glob_covers(g, rel) for g in globs):
            uncovered.append(rel)
    assert not uncovered, (
        "These vendored JS files are NOT matched by any "
        "[tool.setuptools.package-data] glob, so a pip/wheel install ships without "
        f"them and the launchpad won't hydrate (blank reactive page): {sorted(uncovered)}. "
        f"Current globs: {globs}. Add the missing wildcard to pyproject.toml."
    )


def test_every_vendored_file_is_covered_by_package_data():
    """Widen the v1.7.310 font guard above from the FONT assets to EVERY
    runtime-published vendored file. ``vendor.VENDORED_FILES`` is the canonical
    list ``publish_vendor_files`` copies into ``portal_pages/vendor/`` on each
    render: the JS libraries (petite-vue / chart.js / 9 d3 modules) AND the woff2
    webfonts AND the OFL licenses. ALL must be in the built wheel or a ``pip
    install`` ships a broken launchpad. Missing fonts fall back silently — but a
    missing ``data/vendor/*.js`` glob is worse: the launchpad loses petite-vue/d3
    and renders raw ``{{ }}`` templates with no interpolation, on every pip-install
    machine. The fonts got a package-data guard after v1.7.310; the JS — the
    ORIGINAL reason ``data/vendor`` exists (13 CDN files, see vendor.py docstring) —
    never did. This closes that gap: every ``VENDORED_FILES`` entry must exist on
    disk AND match a package-data glob under the wheel's single-level ``*``
    semantics. Verified end-to-end 2026-06-03 by building a real wheel and resolving
    each asset from a fresh non-editable install. Mutation: drop the
    ``data/vendor/*.js`` glob -> this fails (the fonts-only test does not)."""
    from trinity_local.vendor import VENDORED_FILES

    globs = _package_data_globs()
    assert globs, "could not parse package-data globs from pyproject.toml"

    missing_on_disk = [n for n in VENDORED_FILES if not (VENDOR / n).exists()]
    assert not missing_on_disk, (
        "vendor.VENDORED_FILES names files absent from "
        f"{VENDOR}: {missing_on_disk}. publish_vendor_files skips missing files, "
        "so the page 404s on ./vendor/<name>."
    )

    uncovered = []
    for name in VENDORED_FILES:
        rel = f"data/vendor/{name}"  # e.g. data/vendor/petite-vue.es.js
        if not any(_glob_covers(g, rel) for g in globs):
            uncovered.append(rel)
    assert not uncovered, (
        "These runtime-vendored assets (vendor.VENDORED_FILES) are NOT matched by "
        "any [tool.setuptools.package-data] glob, so a pip/wheel install ships "
        f"without them: {sorted(uncovered)}. The JS ones break the launchpad "
        f"entirely (no petite-vue/d3 -> raw {{ }} templates). Current globs: {globs}. "
        "Add the missing wildcard to pyproject.toml."
    )
