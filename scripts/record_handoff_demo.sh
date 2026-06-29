#!/usr/bin/env bash
# record_handoff_demo.sh — automate the 60-second handoff demo for asciinema (#120)
#
# Closes the "shoot it" gate for the launch-arc 60-second hero demo
# without requiring a real video shoot. asciinema records pure terminal
# sessions; the .cast output embeds in README / launch.md / docs/index.html
# via `<asciinema-player>` with the same surface area a video would have,
# but it's text-based (small file size, scrollable, click-to-copy).
#
# What this script does:
#   1. Sanity-check asciinema is installed (cross-platform install hint)
#   2. Run `trinity-local handoff antigravity` after a short pause to let
#      the recording start
#   3. Output goes to docs/demo/handoff_60s.cast
#
# After running:
#   - Preview: asciinema play docs/demo/handoff_60s.cast
#   - Embed: add the <script src="https://asciinema.org/a/<ID>.js"></script>
#     tag to README hero after uploading via `asciinema upload`
#
# The Claude/Codex conversation that the handoff continues from is
# pre-staged in the prompt index — for a clean record, the user runs
# `trinity-local seed-from-taste-terminal` (or has captures from real
# usage) before invoking this script. Without prior context, handoff
# falls back to a "no recent turns" message, which doesn't tell the
# story.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO_DIR="${REPO_ROOT}/docs/demo"
CAST_FILE="${DEMO_DIR}/handoff_60s.cast"
TARGET_PROVIDER="${TARGET_PROVIDER:-antigravity}"

# ── Preflight ──────────────────────────────────────────────────────────
command -v asciinema >/dev/null 2>&1 || {
    echo "ERROR: asciinema not found." >&2
    echo "Install:" >&2
    echo "  macOS:   brew install asciinema" >&2
    echo "  Linux:   pip install asciinema" >&2
    echo "  Other:   https://docs.asciinema.org/getting-started/" >&2
    exit 2
}

command -v trinity-local >/dev/null 2>&1 || {
    echo "ERROR: trinity-local not on PATH." >&2
    echo "Did you run install.sh? See scripts/install.sh" >&2
    exit 2
}

mkdir -p "${DEMO_DIR}"

# ── Pre-recording context check ───────────────────────────────────────
prompt_count="$(trinity-local status --json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get("prompt_count", 0))
except Exception:
    print(0)
' 2>/dev/null || echo 0)"

if [[ "${prompt_count}" -lt 5 ]]; then
    echo "WARNING: prompt index has only ${prompt_count} entries." >&2
    echo "  handoff falls back to a 'no recent turns' message with thin context." >&2
    echo "  For a demo-worthy recording, first run:" >&2
    echo "    trinity-local seed-from-taste-terminal --path <your-exports>" >&2
    echo "  Or have a real Claude Code conversation that lands in the index" >&2
    echo "  via the MCP capture path." >&2
    echo "" >&2
    read -p "Continue anyway? [y/N] " -n 1 -r REPLY
    echo
    [[ ${REPLY} =~ ^[Yy]$ ]] || exit 1
fi

# ── Recording ──────────────────────────────────────────────────────────
echo "Recording handoff demo → ${CAST_FILE}"
echo "Target provider: ${TARGET_PROVIDER} (override via TARGET_PROVIDER env)"
echo
echo "When the prompt appears, paste/type:"
echo "  trinity-local handoff ${TARGET_PROVIDER}"
echo
echo "Then exit the recording with Ctrl-D when handoff completes (~30-45s)."
echo

# Use --command for a fully scripted demo, or omit to let user type live
if [[ "${SCRIPTED:-0}" == "1" ]]; then
    # Scripted variant: runs the command and exits as soon as handoff
    # returns. The video is then trimmed to ~30s of pure handoff output.
    asciinema rec \
        --overwrite \
        --command "trinity-local handoff ${TARGET_PROVIDER}" \
        --title "Trinity handoff — Claude → ${TARGET_PROVIDER} (60-second demo)" \
        "${CAST_FILE}"
else
    # Interactive variant: lets the recorder set up scene (e.g. cd, clear
    # screen, show intro text) before invoking handoff. Recommended for
    # the actual launch-day demo because it gives space for the "wait,
    # how did Gemini KNOW that?" beat to land.
    asciinema rec \
        --overwrite \
        --title "Trinity handoff — Claude → ${TARGET_PROVIDER} (60-second demo)" \
        "${CAST_FILE}"
fi

echo
echo "Done."
echo "Preview:  asciinema play ${CAST_FILE}"
echo "Upload:   asciinema upload ${CAST_FILE}"
echo "  → returns an asciinema.org URL like https://asciinema.org/a/abcdef"
echo "  → drop that ID into README hero block + docs/launch.md L286"
