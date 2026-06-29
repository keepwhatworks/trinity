#!/usr/bin/env bash
#
# refresh-vendor.sh — re-download the 12 vendored JS files from
# their upstream CDNs.
#
# This is a maintainer ritual, NOT runtime code. Users NEVER pull
# from a CDN at render time — they read the bundled bytes shipped in
# the wheel under src/trinity_local/data/vendor/. This script exists
# so that "bump d3-force from v3.0.0 to v3.0.1" is a single command
# instead of 12 hand-curls.
#
# Pinning is exact: every URL carries a version. If you want a
# different version, edit the URL in this file, then commit the
# diff with both the URL bump AND the resulting binary change.
# Treat the version bumps as security-sensitive — read the
# changelog before bumping, and consider whether the new bytes
# need a re-audit. The whole reason we vendor is that the user's
# privacy claim ("never leaves your machine") shouldn't depend on
# unpkg/jsdelivr being honest brokers.
#
# Verification: after running this script, `pytest
# tests/test_no_cdn_in_rendered_html.py` MUST still pass — that
# guard re-renders launchpad + memory-viewer and checks they
# contain zero unpkg/jsdelivr references in the rendered HTML.
#
# Usage:
#   ./scripts/refresh-vendor.sh           # download to data/vendor/
#   ./scripts/refresh-vendor.sh --check   # dry-run: print URLs only

set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "$0")/.." && pwd)/src/trinity_local/data/vendor"
MANIFEST="$(cd "$(dirname "$0")" && pwd)/vendor-sha256.txt"
DRY_RUN=0
UPDATE_MANIFEST=0
case "${1-}" in
    --check) DRY_RUN=1 ;;
    --update-manifest) UPDATE_MANIFEST=1 ;;
esac

_sha256() { shasum -a 256 "$1" | awk '{print $1}'; }

# (target_filename, source_url) pairs. Pin every version.
URLS=(
    "petite-vue.es.js https://unpkg.com/petite-vue@0.4.1/dist/petite-vue.es.js"
    "chart.umd.min.js https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"
    "marked.min.js https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"
    "d3-selection.min.js https://cdn.jsdelivr.net/npm/d3-selection@3.0.0/dist/d3-selection.min.js"
    "d3-dispatch.min.js https://cdn.jsdelivr.net/npm/d3-dispatch@3.0.1/dist/d3-dispatch.min.js"
    "d3-timer.min.js https://cdn.jsdelivr.net/npm/d3-timer@3.0.1/dist/d3-timer.min.js"
    "d3-quadtree.min.js https://cdn.jsdelivr.net/npm/d3-quadtree@3.0.1/dist/d3-quadtree.min.js"
    "d3-drag.min.js https://cdn.jsdelivr.net/npm/d3-drag@3.0.0/dist/d3-drag.min.js"
    "d3-force.min.js https://cdn.jsdelivr.net/npm/d3-force@3.0.0/dist/d3-force.min.js"
    "d3-zoom.min.js https://cdn.jsdelivr.net/npm/d3-zoom@3.0.0/dist/d3-zoom.min.js"
    "d3-interpolate.min.js https://cdn.jsdelivr.net/npm/d3-interpolate@3.0.1/dist/d3-interpolate.min.js"
    "d3-color.min.js https://cdn.jsdelivr.net/npm/d3-color@3.1.0/dist/d3-color.min.js"
    # Fonts — Hanken Grotesk (everything) + JetBrains Mono (commands), OFL, Calm/Teal
    # design language. Latin subsets from fontsource; the licenses travel with them.
    "HankenGrotesk-400.woff2 https://cdn.jsdelivr.net/npm/@fontsource/hanken-grotesk@5/files/hanken-grotesk-latin-400-normal.woff2"
    "HankenGrotesk-500.woff2 https://cdn.jsdelivr.net/npm/@fontsource/hanken-grotesk@5/files/hanken-grotesk-latin-500-normal.woff2"
    "HankenGrotesk-600.woff2 https://cdn.jsdelivr.net/npm/@fontsource/hanken-grotesk@5/files/hanken-grotesk-latin-600-normal.woff2"
    "HankenGrotesk-700.woff2 https://cdn.jsdelivr.net/npm/@fontsource/hanken-grotesk@5/files/hanken-grotesk-latin-700-normal.woff2"
    "JetBrainsMono-400.woff2 https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5/files/jetbrains-mono-latin-400-normal.woff2"
    "JetBrainsMono-500.woff2 https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5/files/jetbrains-mono-latin-500-normal.woff2"
    "HankenGrotesk-OFL.txt https://cdn.jsdelivr.net/npm/@fontsource/hanken-grotesk@5/LICENSE"
    "JetBrainsMono-OFL.txt https://cdn.jsdelivr.net/npm/@fontsource/jetbrains-mono@5/LICENSE"
)

mkdir -p "$VENDOR_DIR"

for pair in "${URLS[@]}"; do
    name="${pair%% *}"
    url="${pair#* }"
    target="$VENDOR_DIR/$name"
    if [[ $DRY_RUN -eq 1 ]]; then
        printf '%-30s <- %s\n' "$name" "$url"
        continue
    fi
    printf 'fetching %s\n' "$name"
    curl -fsSL "$url" -o "$target.tmp"
    if [[ $UPDATE_MANIFEST -eq 0 ]]; then
        expected="$(awk -v n="$name" '$2==n {print $1}' "$MANIFEST")"
        actual="$(_sha256 "$target.tmp")"
        if [[ -z "$expected" ]]; then
            printf 'ERROR: %s is not pinned in %s. Re-run with --update-manifest after a deliberate add/bump.\n' "$name" "$MANIFEST" >&2
            rm -f "$target.tmp"; exit 1
        fi
        if [[ "$expected" != "$actual" ]]; then
            printf 'ERROR: SHA-256 mismatch for %s\n  expected %s\n  got      %s\nRefusing to overwrite. If this is an intentional version bump, re-run with --update-manifest.\n' "$name" "$expected" "$actual" >&2
            rm -f "$target.tmp"; exit 1
        fi
    fi
    mv "$target.tmp" "$target"
done

if [[ $UPDATE_MANIFEST -eq 1 ]]; then
    ( cd "$VENDOR_DIR" && shasum -a 256 *.js | sort -k2 ) > "$MANIFEST"
    printf '\nRegenerated %s\n' "$MANIFEST"
fi

if [[ $DRY_RUN -eq 0 ]]; then
    printf '\nDone. Now run: pytest tests/test_no_cdn_in_rendered_html.py tests/test_vendor_integrity.py\n'
fi
