#!/usr/bin/env bash
# Regenerate docs/launchpad_example.png from a SYNTHETIC cold-start TRINITY_HOME.
#
# NEVER point this at ~/.trinity. On 2026-05-31 the committed launchpad
# screenshot leaked the founder's real 51k-prompt corpus (topic basins, a
# datable timeline, routing reasoning) onto the public marketing site. The
# README caption is "what a brand-new install opens to on first run", so an
# EMPTY home is both the correct visual state and the only leak-free one.
# Guarded by TestNoPersonalDataInPublicDocs.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SYNTH="$(mktemp -d)"
PORT="${LAUNCHPAD_REGEN_PORT:-8197}"
PY="$REPO/.venv/bin/trinity-local"
[ -x "$PY" ] || PY="trinity-local"

TRINITY_HOME="$SYNTH" "$PY" portal-html >/dev/null
# Kill any STALE server squatting the port. A previous interrupted run can leave a
# `python3 -m http.server` bound to $PORT serving a since-deleted temp dir; the new
# server then fails to bind (silently, in the background subshell) and Chrome
# screenshots the stale server's 404 — which is exactly how docs/launchpad_example.png
# became a "404 File not found" hero on the public README (2026-06-11). Clear it first.
lsof -ti ":$PORT" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
# Serve the synth HOME as root (not portal_pages/) so the shared-CSS @font-face
# `../portal_pages/vendor/SpaceGrotesk-*.woff2` resolves exactly as it does in
# production (~/.trinity is the root, the launchpad lives at portal_pages/). Serving
# portal_pages/ as root made that path escape above root and 404'd the display font,
# leaving the screenshot on a system-grotesk fallback.
( cd "$SYNTH" && python3 -m http.server "$PORT" >/dev/null 2>&1 ) &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$SYNTH"' EXIT
sleep 1

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="$(command -v google-chrome chromium chromium-browser 2>/dev/null | head -1)"
[ -x "$CHROME" ] || { echo "No Chrome/Chromium found for headless screenshot." >&2; exit 1; }

"$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1380,2200 --screenshot="$REPO/docs/launchpad_example.png" \
  "http://127.0.0.1:$PORT/portal_pages/launchpad.html"

echo "Regenerated docs/launchpad_example.png from a synthetic cold-start home."
