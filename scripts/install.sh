#!/usr/bin/env bash
# Trinity Local installer — curl|sh entry point.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
#   bash scripts/install.sh
#
# Idempotent: clones the repo if missing, updates if present. Writes thin
# shell wrappers to ~/.local/bin/ so `trinity-local <cmd>` works without
# pip-installing the wheel. Detects the harnesses you have (Claude Code,
# Codex CLI, Antigravity, Cursor) and registers Trinity's MCP server in
# each. Verifies with `trinity-local status`.
#
# Architecture ratified by council_37eca30b6e7010df (see
# docs/three-tier-architecture.md). MCP is primary per the 2026-05-19
# pivot (docs/three-tier-architecture.md L15-22); the skill at
# ~/.claude/skills/trinity/ is kept as a back-compat alias for users
# who already type `/trinity` in Claude Code. This script is the
# user-facing install path. No PyPI, no npm — just a git clone + a
# couple of shell wrappers.

set -euo pipefail

TRINITY_REPO_URL="${TRINITY_REPO_URL:-https://github.com/keepwhatworks/trinity}"
# Post-2026-05-19 pivot: ~/.trinity/code/ is the canonical install
# location. Old default ~/.claude/skills/trinity/ becomes a back-compat
# symlink (created below) so /trinity in Claude Code keeps working.
# Override via $TRINITY_SKILL_DIR if needed — name kept for back-compat
# with users / docs that already reference the env var.
TRINITY_SKILL_DIR="${TRINITY_SKILL_DIR:-$HOME/.trinity/code}"
TRINITY_SKILL_LEGACY="$HOME/.claude/skills/trinity"
TRINITY_BIN_DIR="${TRINITY_BIN_DIR:-$HOME/.local/bin}"
TRINITY_BRANCH="${TRINITY_BRANCH:-main}"
# Export so `trinity-local install-mcp` (a child python process) can find the
# wrapper at a CUSTOM bin dir even before it's on PATH — _resolve_mcp_command
# probes $TRINITY_BIN_DIR to avoid baking a bare interpreter that ModuleNotFounds.
export TRINITY_BIN_DIR

# ─── Output helpers ────────────────────────────────────────────────

# Colors only when stdout is a real terminal — clean output under
# curl|sh OR redirection.
if [[ -t 1 ]]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
  C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_DIM=''; C_BOLD=''; C_RESET=''
fi

step()  { printf "%s→%s %s\n" "$C_BOLD" "$C_RESET" "$1"; }
ok()    { printf "  %s✓%s %s\n" "$C_GREEN" "$C_RESET" "$1"; }
warn()  { printf "  %s⚠%s %s\n" "$C_YELLOW" "$C_RESET" "$1" >&2; }
fail()  { printf "  %s✗%s %s\n" "$C_RED" "$C_RESET" "$1" >&2; exit 1; }

# ─── 1. Prerequisites ──────────────────────────────────────────────

step "Checking prerequisites"

# #272: Windows is supported via WSL2 (or Git Bash, best-effort) only — this
# script is bash + UNIX paths + symlinks + a POSIX capture-host wrapper, none of
# which work under native Windows. Detect the MSYS/Cygwin/Git-Bash shells and
# point the user at WSL2 rather than failing halfway with a cryptic ln/path
# error. (Native cmd/PowerShell can't run this script at all — they hit "bash
# not recognized", which is already honest.)
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    warn "Detected a Windows shell ($(uname -s)). Trinity's installer is UNIX-only."
    warn "  Recommended: install + run Trinity inside WSL2 (Ubuntu) — full support."
    warn "  Git Bash is best-effort and may break on symlinks / the capture host."
    warn "  Continuing best-effort; if it errors, switch to WSL2."
    ;;
esac

command -v git >/dev/null 2>&1 || fail "git not found. Install git and re-run."
ok "git $(git --version | awk '{print $3}')"

# Trinity needs Python 3.10+. Most polyharness users have it (Claude Code
# itself needs Node + Python for tools; Codex CLI needs Node). If missing,
# tell them; don't try to install Python ourselves — the system Python
# story is too varied (pyenv, asdf, system, brew, uv, mise).
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY_VER=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")
    PY_MAJOR=${PY_VER%%.*}
    PY_MINOR=${PY_VER##*.}
    if [[ "$PY_MAJOR" == "3" ]] && (( PY_MINOR >= 10 )); then
      # Bake the ABSOLUTE interpreter path, not the bare name. The
      # capture-host wrapper is launched by Chrome/Edge as a native-
      # messaging host with a SANITIZED PATH (typically /usr/bin:/bin,
      # without Homebrew's /opt/homebrew/bin). A bare `python3.12` then
      # fails to resolve and capture silently dies. The absolute path
      # works regardless of the launcher's PATH (Chrome, launchd, cron).
      PYTHON_BIN="$(command -v "$candidate")"
      ok "$candidate ($PY_VER) → $PYTHON_BIN"
      break
    fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  fail "Python 3.10+ not found. Install Python 3.10 or newer and re-run.
       macOS: brew install python@3.12   (or use pyenv / uv / mise)
       Linux: apt install python3.12      (or your distro's equivalent)"
fi

# ─── 1b. Dedicated venv for Trinity's runtime deps (PEP 668) ────────
# System pythons (Homebrew, Debian/Ubuntu 23.04+, Fedora) are PEP 668
# "externally managed": `pip install --user` there FAILS with
# `error: externally-managed-environment`, so Pillow/mcp/numpy never
# install. The CLI's `status` still looks fine (it doesn't import mcp),
# but the MCP SERVER can't `import mcp` and never starts — Trinity in
# Claude Code / Cursor is silently dead. (Caught 2026-06-02 booting the
# installed wrapper under a sanitized env.) A dedicated venv sidesteps
# PEP 668 AND isolates Trinity's deps from system/user site. We bake the
# venv interpreter into the wrappers below so every harness spawn — incl.
# GUI apps launched with a minimal /usr/bin:/bin PATH — uses it.
TRINITY_VENV="$HOME/.trinity/venv"
step "Creating dependency venv at $TRINITY_VENV"
if "$PYTHON_BIN" -m venv "$TRINITY_VENV" >/dev/null 2>&1 \
   && [[ -x "$TRINITY_VENV/bin/python3" ]]; then
  PYTHON_BIN="$TRINITY_VENV/bin/python3"
  ok "venv → $PYTHON_BIN"
else
  warn "Could not create a venv (the python 'venv' module may be missing)."
  warn "Falling back to the system interpreter. If it is PEP 668 'externally"
  warn "managed' (Homebrew / Debian / Ubuntu), the dep install below will fail"
  warn "and the MCP server won't start. Fix: install python3-venv (Linux) and re-run,"
  warn "  or: $PYTHON_BIN -m venv $TRINITY_VENV && that venv's pip install Pillow mcp numpy"
fi

# ─── 2. Clone or update the repo ───────────────────────────────────

step "Installing skill to $TRINITY_SKILL_DIR"

mkdir -p "$(dirname "$TRINITY_SKILL_DIR")"

if [[ -d "$TRINITY_SKILL_DIR/.git" ]]; then
  # Already cloned — fetch + fast-forward.
  cd "$TRINITY_SKILL_DIR"
  if git remote get-url origin >/dev/null 2>&1; then
    REMOTE_URL=$(git remote get-url origin)
    if [[ "$REMOTE_URL" != "$TRINITY_REPO_URL" ]] \
        && [[ "$REMOTE_URL" != "${TRINITY_REPO_URL}.git" ]]; then
      warn "Existing checkout has remote: $REMOTE_URL"
      warn "Expected:                       $TRINITY_REPO_URL"
      warn "Skipping git update (preserving user's customizations)."
    else
      git fetch --quiet origin "$TRINITY_BRANCH"
      BEHIND=$(git rev-list --count "HEAD..origin/$TRINITY_BRANCH")
      if (( BEHIND > 0 )); then
        git merge --quiet --ff-only "origin/$TRINITY_BRANCH"
        ok "Updated ($BEHIND new commits)"
      else
        ok "Already up to date"
      fi
    fi
  else
    warn "Existing checkout has no 'origin' remote; skipping update"
  fi
else
  # First-time clone — single shallow clone for fast install. Use the
  # full history if the user wants `git log` exploration; default to
  # depth 1 to keep curl|sh installs snappy.
  git clone --quiet --depth 1 --branch "$TRINITY_BRANCH" \
      "$TRINITY_REPO_URL" "$TRINITY_SKILL_DIR"
  ok "Cloned to $TRINITY_SKILL_DIR"
fi

# ─── 2b. Skill back-compat symlink ─────────────────────────────────
# Keep /trinity in Claude Code working without forcing two copies of
# the repo on disk. If the legacy skill location at
# ~/.claude/skills/trinity/ doesn't exist (or is already a symlink we
# manage), create a symlink to the canonical install. If it's a real
# directory (existing pre-pivot install), leave it alone — overwriting
# would silently lose the user's own git checkout.
if [[ "$TRINITY_SKILL_DIR" != "$TRINITY_SKILL_LEGACY" ]]; then
  if [[ -L "$TRINITY_SKILL_LEGACY" ]] || [[ ! -e "$TRINITY_SKILL_LEGACY" ]]; then
    mkdir -p "$(dirname "$TRINITY_SKILL_LEGACY")"
    rm -f "$TRINITY_SKILL_LEGACY"
    # #272: symlinks fail under Git Bash on Windows without Developer Mode. With
    # `set -e` an un-guarded `ln -s` would abort the whole install mid-way; fall
    # back to a copy so the rest of the install completes.
    if ln -s "$TRINITY_SKILL_DIR" "$TRINITY_SKILL_LEGACY" 2>/dev/null; then
      ok "Symlinked $TRINITY_SKILL_LEGACY → $TRINITY_SKILL_DIR (Claude Code /trinity alias)"
    elif cp -R "$TRINITY_SKILL_DIR" "$TRINITY_SKILL_LEGACY" 2>/dev/null; then
      warn "Symlink unsupported here — copied to $TRINITY_SKILL_LEGACY instead (re-run 'update' to refresh)."
    else
      warn "Could not link or copy the /trinity alias — Claude Code's /trinity may be unavailable (MCP still works)."
    fi
  else
    warn "$TRINITY_SKILL_LEGACY exists as a real directory — leaving it alone."
    warn "Migrate manually if you want a single canonical install:"
    warn "  rm -rf $TRINITY_SKILL_LEGACY && ln -s $TRINITY_SKILL_DIR $TRINITY_SKILL_LEGACY"
  fi
fi

# ─── 3. Install CLI wrappers in ~/.local/bin/ ──────────────────────

step "Installing CLI wrappers to $TRINITY_BIN_DIR"

mkdir -p "$TRINITY_BIN_DIR"

# Path resolver — used by both wrappers below. Copying it to
# ~/.local/bin/ instead of leaving it inside the skill dir avoids a
# chicken-and-egg lookup (the wrappers need it to find the skill dir).
# The resolver script is small + self-contained; this copy gets
# overwritten on every install so it tracks the source.
RESOLVER_SRC="$TRINITY_SKILL_DIR/scripts/launcher_path_resolver.sh"
RESOLVER_DST="$TRINITY_BIN_DIR/trinity-path-resolver.sh"
if [[ -f "$RESOLVER_SRC" ]]; then
  cp "$RESOLVER_SRC" "$RESOLVER_DST"
  chmod +x "$RESOLVER_DST"
  ok "trinity-path-resolver.sh"
else
  warn "launcher_path_resolver.sh not in skill repo — wrappers will use legacy direct-path mode"
  RESOLVER_DST=""
fi

# Write the trinity-local wrapper. With the resolver in place, the
# wrapper probes Chrome / Brave / Edge / Arc extension dirs first
# (Web Store auto-update path), then ~/.trinity/code/ (sideload /
# `trinity-local update`), then ~/.claude/skills/trinity/ (legacy).
# Without the resolver (defensive fallback for partial installs),
# uses the legacy direct-path approach.
if [[ -n "$RESOLVER_DST" ]]; then
  cat > "$TRINITY_BIN_DIR/trinity-local" <<WRAPPER_EOF
#!/usr/bin/env bash
# Trinity Local CLI wrapper — installed by scripts/install.sh.
# Resolves the Python source dir via launcher_path_resolver.sh so
# Chrome Web Store auto-updates flow through to the CLI.
set -e
RESOLVER="$RESOLVER_DST"
SOURCE_DIR=\$("\$RESOLVER" 2>/dev/null) || {
  echo "error: Trinity source not found in any browser extension dir," >&2
  echo "  ~/.trinity/code/, or ~/.claude/skills/trinity/." >&2
  echo "Re-install: curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash" >&2
  exit 1
}
export PYTHONPATH="\$SOURCE_DIR/src:\${PYTHONPATH:-}"
# #274: prefer the baked absolute interpreter (v1.7.56 — Chrome's sanitized
# native-messaging PATH can't resolve a bare python3); fall back to a PATH
# lookup if it moved (pyenv/asdf/brew relocate). TRINITY_PYTHON overrides.
TRINITY_PY="\${TRINITY_PYTHON:-$PYTHON_BIN}"
[ -x "\$TRINITY_PY" ] || TRINITY_PY="\$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "$PYTHON_BIN")"
exec "\$TRINITY_PY" -m trinity_local.main "\$@"
WRAPPER_EOF
else
  # Legacy fallback wrapper (partial install / resolver missing).
  cat > "$TRINITY_BIN_DIR/trinity-local" <<LEGACY_EOF
#!/usr/bin/env bash
TRINITY_SKILL_DIR="\${TRINITY_SKILL_DIR:-\$HOME/.claude/skills/trinity}"
if [[ ! -d "\$TRINITY_SKILL_DIR/src/trinity_local" ]]; then
  echo "error: Trinity skill not found at \$TRINITY_SKILL_DIR" >&2
  exit 1
fi
export PYTHONPATH="\$TRINITY_SKILL_DIR/src:\${PYTHONPATH:-}"
# #274: prefer the baked absolute interpreter (v1.7.56 — Chrome's sanitized
# native-messaging PATH can't resolve a bare python3); fall back to a PATH
# lookup if it moved (pyenv/asdf/brew relocate). TRINITY_PYTHON overrides.
TRINITY_PY="\${TRINITY_PYTHON:-$PYTHON_BIN}"
[ -x "\$TRINITY_PY" ] || TRINITY_PY="\$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "$PYTHON_BIN")"
exec "\$TRINITY_PY" -m trinity_local.main "\$@"
LEGACY_EOF
fi
chmod +x "$TRINITY_BIN_DIR/trinity-local"
ok "trinity-local"

# Native Messaging host wrapper — same resolver pattern.
if [[ -n "$RESOLVER_DST" ]]; then
  cat > "$TRINITY_BIN_DIR/trinity-local-capture-host" <<CAPTURE_EOF
#!/usr/bin/env bash
# Trinity Local Native Messaging host wrapper — installed by
# scripts/install.sh. Chrome / Edge talk to this via stdio.
set -e
SOURCE_DIR=\$("$RESOLVER_DST" 2>/dev/null) || {
  echo "error: Trinity source not found" >&2
  exit 1
}
export PYTHONPATH="\$SOURCE_DIR/src:\${PYTHONPATH:-}"
# Run as a MODULE, not a script file: capture_host.py uses relative
# imports (from .registry import …) which only resolve under -m. Running
# the file directly raises "attempted relative import with no known
# parent package" the moment Chrome launches the host.
TRINITY_PY="\${TRINITY_PYTHON:-$PYTHON_BIN}"
[ -x "\$TRINITY_PY" ] || TRINITY_PY="\$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "$PYTHON_BIN")"
exec "\$TRINITY_PY" -m trinity_local.capture_host "\$@"
CAPTURE_EOF
else
  cat > "$TRINITY_BIN_DIR/trinity-local-capture-host" <<CAPTURE_LEGACY_EOF
#!/usr/bin/env bash
TRINITY_SKILL_DIR="\${TRINITY_SKILL_DIR:-\$HOME/.claude/skills/trinity}"
export PYTHONPATH="\$TRINITY_SKILL_DIR/src:\${PYTHONPATH:-}"
# Module mode — capture_host.py uses relative imports (see above).
TRINITY_PY="\${TRINITY_PYTHON:-$PYTHON_BIN}"
[ -x "\$TRINITY_PY" ] || TRINITY_PY="\$(command -v python3.12 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "$PYTHON_BIN")"
exec "\$TRINITY_PY" -m trinity_local.capture_host "\$@"
CAPTURE_LEGACY_EOF
fi
chmod +x "$TRINITY_BIN_DIR/trinity-local-capture-host"
ok "trinity-local-capture-host"

# Sanity-check PATH. If ~/.local/bin isn't on PATH, the user has to fix
# their shell config — we surface this loudly rather than silently
# failing later.
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$TRINITY_BIN_DIR"; then
  warn "$TRINITY_BIN_DIR is not on your PATH."
  warn "Add this line to your shell config (~/.zshrc, ~/.bashrc, etc.):"
  warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  warn "Then re-open your terminal."
fi

# ─── 3.5. Install Python runtime deps (Pillow, mcp, numpy) ─────────

# Trinity's runtime deps are declared in pyproject.toml: Pillow>=10 (for
# me-card PNG rendering, loaded lazily), mcp>=1.0 (for the MCP server
# the harness spawns), and numpy>=1.26 (embedding matmul fast-path +
# k-means + vocabulary stats — used across embeddings/, memory/, me/,
# knn_analytics, vocabulary). We don't pip-install trinity-local itself
# — the wrapper points at the cloned repo via PYTHONPATH. But the
# runtime deps still have to be available to the system python; without
# them, the status health check's first run flags failures the user has to fix manually.
#
# --user installs into ~/.local (or the venv if active) without touching
# system site-packages. If pip is missing we surface a warning rather
# than failing: dispatch + lens-build don't need Pillow/mcp; only
# me-card and the MCP server do.

step "Installing Python runtime deps"

# pip refuses --user when run inside an active virtualenv. Detect the
# venv first; if we're inside one, install into the venv directly (no
# --user). Otherwise prefer --user so we don't touch system site-
# packages without explicit consent.
if "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  if "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)' 2>/dev/null; then
    PIP_INSTALL_ARGS=(install --quiet --upgrade)
    PIP_MODE_LABEL="venv $PYTHON_BIN"
  else
    PIP_INSTALL_ARGS=(install --quiet --user --upgrade)
    PIP_MODE_LABEL="user site-packages"
  fi
  # #274: do NOT silence pip stderr — a read-only ~/.local (corporate Linux) or
  # a missing wheel must show the REAL error, not a vague "reported issues" that
  # sends the user into a no-heal loop.
  if "$PYTHON_BIN" -m pip "${PIP_INSTALL_ARGS[@]}" \
       'Pillow>=10' 'mcp>=1.0' 'numpy>=1.26'; then
    ok "Pillow + mcp + numpy installed ($PIP_MODE_LABEL)"
  else
    warn "pip install failed (see the error above). If it's a permissions error on"
    warn "  ~/.local, retry in a venv: python3 -m venv ~/.trinity/venv && source it."
  fi
  # Apple Silicon: auto-install the NATIVE MLX embedder RUNTIME (mlx +
  # mlx-embeddings, #244). It's small (no 800MB torch) and IS the real, fast
  # embedding path — but the ~600MB model WEIGHTS are a separate, optional pull
  # (Trinity pins HF_HUB_OFFLINE=1, so nothing auto-downloads), so the lens runs
  # on the TF-IDF fallback until `download-embedder` is run (#242 cold-start
  # decouple). Non-Apple installs skip this (torch is heavy) and opt in later via
  # the deeper-memory note below.
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    if "$PYTHON_BIN" -m pip "${PIP_INSTALL_ARGS[@]}" \
         'mlx>=0.20' 'mlx-embeddings>=0.1' 2>/dev/null; then
      ok "MLX embedder runtime installed (Apple Silicon)"
    else
      warn "MLX embedder install hit issues — lens uses the TF-IDF fallback until you run:"
      warn "    $PYTHON_BIN -m pip install 'mlx>=0.20' 'mlx-embeddings>=0.1'"
    fi
  fi
else
  warn "pip not available for $PYTHON_BIN — install pip and re-run, or:"
  warn "  $PYTHON_BIN -m ensurepip --user && $PYTHON_BIN -m pip install --user 'Pillow>=10' 'mcp>=1.0' 'numpy>=1.26'"
fi

# ─── 4. Register MCP server in installed harnesses ─────────────────

step "Registering MCP server"

# Delegate to install-mcp, which knows how to detect Claude Code /
# Codex CLI / Antigravity / Cursor configs and write the right entries.
if "$TRINITY_BIN_DIR/trinity-local" install-mcp 2>&1 | sed 's/^/  /'; then
  ok "MCP server registered"
else
  warn "install-mcp reported issues — run 'trinity-local install-mcp' to retry"
fi

# ─── 4b. Pre-wire the browser-capture native host ──────────────────
#
# Register the Native Messaging host for the CANONICAL extension id NOW,
# before the user installs the extension. The host is inert until an
# extension with that exact id connects, so this is safe to do ahead of
# time — and it means when the user later adds the published Trinity
# extension, capture "just works" with zero further setup (no copying the
# 32-char id, no second command). Best-effort: a failure here never fails
# the install (browser capture is optional; councils work without it).
step "Pre-wiring browser-capture host (optional)"
if "$TRINITY_BIN_DIR/trinity-local" install-extension >/dev/null 2>&1; then
  ok "capture host pre-wired (canonical extension id)"
else
  warn "could not pre-wire capture host — run 'trinity-local install-extension' after installing the extension"
fi

# ─── 5. Verify ─────────────────────────────────────────────────────

step "Running health check"
"$TRINITY_BIN_DIR/trinity-local" status 2>&1 | sed 's/^/  /' || \
  warn "health check reported issues — fix what it surfaces, then 'trinity-local status' again"

# ─── 6. Done ───────────────────────────────────────────────────────

echo ""
printf "%sTrinity Local installed.%s\n" "$C_BOLD$C_GREEN" "$C_RESET"
echo ""
echo "Type /trinity in Claude Code to start. Or run 'trinity-local status'"
echo "to verify. Updates: 'trinity-local update' pulls the latest."
echo ""
echo "Source: $TRINITY_SKILL_DIR"
echo "CLI:    $TRINITY_BIN_DIR/trinity-local"
echo "Data:   $HOME/.trinity/ (prompts, council outcomes, lens)"
echo "Docs:   $TRINITY_SKILL_DIR/docs/INSTALL-skill.md"
echo ""
# Is the extension actually published to the Chrome Web Store yet? The
# canonical source of truth is registry.CHROME_WEB_STORE_URL — empty until
# publish. Don't promise "Add to Chrome / auto-updates" before there's a
# listing; that's the kind of claim that erodes trust on a fresh install.
WEB_STORE_URL=""
if [[ -d "$TRINITY_SKILL_DIR/src/trinity_local" ]]; then
  WEB_STORE_URL=$(PYTHONPATH="$TRINITY_SKILL_DIR/src" "$PYTHON_BIN" -c \
    'from trinity_local.registry import CHROME_WEB_STORE_URL as u; print(u)' \
    2>/dev/null || echo "")
fi

printf "%sOptional next step — install the Chrome extension:%s\n" "$C_BOLD" "$C_RESET"
echo "  - Captures conversations from claude.ai / chatgpt.com /"
echo "    gemini.google.com into ~/.trinity/conversations/"
if [[ -n "$WEB_STORE_URL" ]]; then
  echo "  - One-click install + auto-updates via the Chrome Web Store:"
  echo "      $WEB_STORE_URL"
else
  echo "  - Not yet on the Chrome Web Store — load it unpacked: open"
  echo "    chrome://extensions, enable Developer mode, 'Load unpacked',"
  echo "    and pick $TRINITY_SKILL_DIR/browser-extension/."
  echo "  - No auto-update yet; 'trinity-local update' refreshes the source."
fi
echo "  - See $TRINITY_SKILL_DIR/docs/INSTALL-extension.md"
echo ""
# Honest embedder-readiness check (#273): the "real embeddings" claim must gate on
# the SAME invariant `trinity-local status` reports — can the embedder actually
# PRODUCE a vector — not merely on the runtime importing. The old check probed only
# `import mlx_embeddings`, so it said "real embeddings available" even when the model
# WEIGHTS aren't downloaded yet (the common fresh-install case — install.sh never
# pulls the ~600MB model), directly contradicting status, which correctly reports the
# TF-IDF fallback. mlx_actually_loaded() runs a real probe-embed, so the two agree.
if PYTHONPATH="$TRINITY_SKILL_DIR/src" TRINITY_AUTOSCAN_DISABLED=1 HF_HUB_OFFLINE=1 \
     "$PYTHON_BIN" -c "from trinity_local.embeddings import mlx_actually_loaded; import sys; sys.exit(0 if mlx_actually_loaded() else 1)" >/dev/null 2>&1; then
  ok "Real embeddings live — the model is downloaded and producing 768d vectors"
elif "$PYTHON_BIN" -c "import mlx_embeddings" >/dev/null 2>&1 \
     || "$PYTHON_BIN" -c "import sentence_transformers" >/dev/null 2>&1; then
  printf "%s⚠  Embedding runtime installed, but the model isn't downloaded yet%s — your\n" "$C_BOLD" "$C_RESET"
  printf "   lens / cold-open use the lexical TF-IDF fallback until you pull it (next step).\n"
else
  printf "%s⚠  Real embeddings are NOT enabled%s — your lens, cold-open, and topic\n" "$C_BOLD" "$C_RESET"
  printf "   map will be LIMITED (lexical anchors only) until you install the embedder.\n"
fi
printf "%sOptional — enable real embeddings by pulling the model (~600MB, one-time):%s\n" "$C_BOLD" "$C_RESET"
echo "  - Councils + the launchpad work WITHOUT this; it's the lens/cold-open layer."
echo "  - lens / dream / vocabulary use the modernbert-embed model for MEANING."
echo "  - Without real embeddings they fall back to the SHA-1 TF-IDF projection,"
echo "    which groups by word-overlap, not meaning — the lens/cold-open abstain."
echo "  - The model pull opts back in past Trinity's offline pin:"
echo "      HF_HUB_OFFLINE=0 trinity-local download-embedder"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  echo "  - The MLX embedder was auto-installed above (Apple Silicon) — just run"
  echo "    the pull line and your full lens builds on first connect."
else
  echo "  - Non-Apple: also install the embedder runtime first (heavier, torch):"
  echo "      $PYTHON_BIN -m pip install 'sentence-transformers>=2.2' einops 'torch>=2.0'"
  echo "    A bare 'trinity-local download-embedder' WILL fail without it."
fi
