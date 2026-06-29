#!/usr/bin/env bash
# Vendor the trinity_local ENGINE into the Claude Code plugin, so a one-command
# marketplace install ships the whole engine — no separate `curl|bash`. The engine
# is pure Python (~3.4M); the 3 runtime deps (numpy/mcp/Pillow) are installed once
# by the launcher's first-boot bootstrap (they're compiled / per-platform, so they
# can't be vendored). The embedder (torch/mlx + the model) stays a separate, opt-in
# download — see the wizard.
#
# Regenerate on every release. `tests/test_bundled_engine_in_sync.py` fails the
# build if the committed bundle drifts from src/, so a release can't ship a stale
# engine to the marketplace.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/src/trinity_local"
DST="$REPO/plugins/trinity-local/engine/trinity_local"

[ -d "$SRC" ] || { echo "src/trinity_local not found at $SRC" >&2; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "rsync required" >&2; exit 1; }

mkdir -p "$(dirname "$DST")"
# --delete so a removed source file disappears from the bundle. Exclude build cruft
# and the test-only / dev-only bits that have no business shipping to a user.
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$SRC/" "$DST/"

echo "bundled engine → plugins/trinity-local/engine/trinity_local ($(du -sh "$DST" | cut -f1))"
