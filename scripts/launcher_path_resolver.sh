#!/usr/bin/env bash
# launcher_path_resolver.sh — resolve the Trinity Python source dir.
#
# The Chrome auto-update story (loop area #2): when the Web Store
# version of the Trinity extension ships, the Python source rides
# inside the extension package. Chrome auto-updates the extension
# every ~5 hours; Trinity's launcher at ~/.local/bin/trinity-local
# resolves the latest extension version dir at runtime via this
# script, so new Python lands without `git pull`.
#
# Probe order:
#   1. Chrome / Brave / Edge / Arc extension dirs — sorted -V, latest wins.
#      First browser with the extension installed wins.
#   2. ~/.trinity/code/ — fallback for sideloaded / dev / pre-Web-Store
#      installs where the source lives at the canonical location and
#      gets updated via `trinity-local update` (git pull).
#   3. ~/.claude/skills/trinity/ — legacy back-compat for users who
#      installed before the 2026-05-19 MCP-first pivot.
#
# Outputs (one of):
#   - Absolute path to a dir containing src/trinity_local/ → stdout, exit 0.
#   - "" + exit 1 if nothing usable was found.
#
# Usage:
#   ./launcher_path_resolver.sh <extension-id>
#
# Args:
#   $1: extension ID (chrome-extension://<id>) of the Trinity extension.
#       Defaults to the canonical ID baked in below.
#
# Env override:
#   TRINITY_FORCE_SOURCE: if set + valid, skips all probing and uses
#     that path directly. Useful for dev installs and tests.

set -eu

EXTENSION_ID="${1:-caaojjhagginmgobdaheincllmblcjoi}"

# Explicit override wins. Path must contain src/trinity_local/ to count
# as a valid source dir.
if [[ -n "${TRINITY_FORCE_SOURCE:-}" ]] && [[ -d "${TRINITY_FORCE_SOURCE}/src/trinity_local" ]]; then
  echo "${TRINITY_FORCE_SOURCE}"
  exit 0
fi

# Browser extension dir candidates. macOS and Linux paths checked in
# the same loop — non-existent paths are silently skipped, so a Linux
# user's macOS paths just no-op and vice versa. Order = preference:
# Chrome first, then Chromium derivatives, finally Arc.
declare -a BROWSER_EXT_ROOTS=(
  # macOS
  "$HOME/Library/Application Support/Google/Chrome/Default/Extensions"
  "$HOME/Library/Application Support/BraveSoftware/Brave-Browser/Default/Extensions"
  "$HOME/Library/Application Support/Microsoft Edge/Default/Extensions"
  "$HOME/Library/Application Support/Arc/User Data/Default/Extensions"
  # Linux
  "$HOME/.config/google-chrome/Default/Extensions"
  "$HOME/.config/BraveSoftware/Brave-Browser/Default/Extensions"
  "$HOME/.config/microsoft-edge/Default/Extensions"
)

# Each browser may host the extension under a version-named subdir.
# Chrome rewrites that subdir on every update — pick the highest via
# sort -V (version sort). Marker file check confirms the Trinity
# Python ships in that version. Without this, an extension version
# that PREDATES the Python-in-package change would silently win.
SOURCE_MARKER="trinity/src/trinity_local/__init__.py"

for root in "${BROWSER_EXT_ROOTS[@]}"; do
  ext_dir="$root/$EXTENSION_ID"
  if [[ ! -d "$ext_dir" ]]; then
    continue
  fi
  # Versions: ls the extension dir, sort by semver, take the highest.
  # `2>/dev/null` swallows EACCES on dirs the user can't read.
  latest=$(ls -1 "$ext_dir" 2>/dev/null | sort -V | tail -1)
  if [[ -z "$latest" ]]; then
    continue
  fi
  source_path="$ext_dir/$latest/trinity"
  if [[ -f "$ext_dir/$latest/$SOURCE_MARKER" ]]; then
    echo "$source_path"
    exit 0
  fi
done

# Fall back to canonical sideload / git-clone location. Established
# 2026-05-19 as part of the MCP-first pivot; the install.sh updates
# that location via `trinity-local update`.
if [[ -d "$HOME/.trinity/code/src/trinity_local" ]]; then
  echo "$HOME/.trinity/code"
  exit 0
fi

# Legacy: pre-pivot users had the source at ~/.claude/skills/trinity/.
# Keep this as the FINAL fallback so existing installs keep working.
if [[ -d "$HOME/.claude/skills/trinity/src/trinity_local" ]]; then
  echo "$HOME/.claude/skills/trinity"
  exit 0
fi

# Nothing usable found.
exit 1
