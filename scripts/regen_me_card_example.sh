#!/usr/bin/env bash
# Regenerate docs/me_card_example.png from a SYNTHETIC cold-start TRINITY_HOME.
#
# NEVER point this at ~/.trinity. The me-card renders the user's LENS TENSIONS —
# the single most personal artifact Trinity holds (how they think, distilled) —
# so a real-home render would publish the founder's taste profile to the public
# marketing site. This is the me-card sibling of regen_launchpad_example.sh,
# which was hardened to a synthetic home after the 2026-05-31 corpus leak; the
# me-card recipe (a bare `trinity-local me-card` against ~/.trinity) was missed.
# An empty synthetic home renders the honest first-run "Run trinity-local lens"
# state — the same cold-start framing as the launchpad example, and the only
# leak-free one. Guarded by TestRegenScriptsAreLeakSafe.
#
# Whether to PUBLISH a populated (non-empty) example me-card — and with what
# example lens content — is a founder/brand call; this script only guarantees
# the render is leak-safe.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SYNTH="$(mktemp -d)"
PY="$REPO/.venv/bin/trinity-local"
[ -x "$PY" ] || PY="trinity-local"
trap 'rm -rf "$SYNTH"' EXIT

# TRINITY_AUTOSCAN_DISABLED keeps an empty home from kicking a cold-start scan.
TRINITY_AUTOSCAN_DISABLED=1 TRINITY_HOME="$SYNTH" \
  "$PY" me-card --out "$REPO/docs/me_card_example.png"

echo "Regenerated docs/me_card_example.png from a synthetic cold-start home."
