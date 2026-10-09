#!/bin/sh
# Trinity: one line to set it up for Claude Code, Codex and agy.
#
#   curl -LsSf https://keepwhatworks.com/install.sh | sh
#
# 1. uv, if you don't have it (it brings its own Python, so a stock Mac works).
# 2. `uv tool install` Trinity from the public repo's newest release tarball,
#    which puts `trinity-local` on your PATH. No git needed: on a Mac without the
#    developer tools, `git` is a stub that opens an install dialog.
# 3. `trinity-local install`: registers Trinity's MCP server in every CLI it finds,
#    then prints a read-only check of what is set up.
# Your transcripts stay on your machine. Nothing here needs an API key.
set -eu

REPO="keepwhatworks/trinity"

if ! command -v uv >/dev/null 2>&1; then
  echo "-> installing uv (Python tool manager from astral.sh)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_PRINT_QUIET=1 sh   # its own PATH notes would read as a to-do; we set PATH below
  PATH="$HOME/.local/bin:$PATH"; export PATH
fi

if [ -n "${TRINITY_SOURCE:-}" ]; then
  SOURCE="$TRINITY_SOURCE"                       # a local checkout, for testing this script
else
  REF="${TRINITY_REF:-}"
  if [ -z "$REF" ]; then
    # Fetch first, parse second: in a pipeline a failed curl would be masked and
    # reported as "no tag" instead of the real network / rate-limit error.
    TAGS="$(curl -fsSL "https://api.github.com/repos/$REPO/tags")" \
      || { echo "could not reach GitHub to find the newest Trinity release (see the error above); set TRINITY_REF=vX.Y.Z to skip the lookup" >&2; exit 1; }
    # Newest vMAJOR.MINOR.PATCH by numeric fields (plain POSIX sort; no `sort -V`).
    REF="$(printf '%s\n' "$TAGS" | sed -n 's/.*"name": *"v\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' \
      | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)"
    [ -z "$REF" ] || REF="v$REF"
  fi
  [ -n "$REF" ] || { echo "no Trinity release tag found at github.com/$REPO" >&2; exit 1; }
  SOURCE="https://github.com/$REPO/archive/refs/tags/$REF.tar.gz"
fi

echo "-> installing trinity-local (${REF:-local checkout})"
uv tool install --quiet --force --python ">=3.10" "$SOURCE"

BIN="$(uv tool dir --bin)"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) PATH="$BIN:$PATH"; export PATH; uv tool update-shell >/dev/null 2>&1 || true ;;
esac

trinity-local install
