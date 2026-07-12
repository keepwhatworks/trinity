#!/usr/bin/env bash
# Fast pre-commit gates — deterministic AUTOFIX, not flagging (founder list #5).
#
# Scope: only checks that are (a) sub-second and (b) have a deterministic fix.
# The full suite stays the PUSH gate (exit-code discipline); this hook kills the
# one incident class that slipped through THREE times the week of 2026-07-11:
# byte-mirror drift — an edit landing on one copy of a mirrored file.
#   * schemas/*.json            → skills/trinity/schemas/*.json   (2 incidents)
#   * config.example.json       → src/trinity_local/data/config.example.json
#     (1 incident — shipped red because the push chained ahead of the gate)
# Canonical direction is left→right: the top-level file is the one you edit;
# the bundled copy is what installs read. The hook copies canonical→mirror and
# re-stages, so the commit that would have drifted simply ships synced.
#
# Install (once):  git config core.hooksPath scripts/githooks
# (scripts/githooks/pre-commit execs this file; committed so the hook rides
# the repo instead of living in untracked .git/hooks.)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

fixed=0

sync_pair() {
  local canonical="$1" mirror="$2"
  [ -f "$canonical" ] || return 0
  if ! cmp -s "$canonical" "$mirror" 2>/dev/null; then
    cp "$canonical" "$mirror"
    git add "$mirror"
    echo "pre-commit: synced $mirror ← $canonical"
    fixed=1
  fi
}

# config.example mirror
sync_pair config.example.json src/trinity_local/data/config.example.json

# schemas mirror (every canonical schema file)
for f in schemas/*.json; do
  [ -e "$f" ] || continue
  sync_pair "$f" "skills/trinity/schemas/$(basename "$f")"
done

if [ "$fixed" -eq 1 ]; then
  echo "pre-commit: mirror drift auto-fixed and re-staged (nothing to do manually)."
fi
exit 0
