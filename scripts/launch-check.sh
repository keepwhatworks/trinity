#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────
#  Trinity Local — Launch Verification (T-0 Step 1)
#
#  Runs the four programmatic gates that close the public-repo-flip
#  risk. Single command, green/red verdict per step, non-zero exit
#  on any failure.
#
#  Usage:
#    bash scripts/launch-check.sh
#
#  After this passes, run the remaining T-0 steps
#  (the ones requiring credentials and external services):
#    - gh repo edit --visibility public ...
#    - gh repo edit --description ... --add-topic ...
#    - upload social card via Settings UI
#    - pin starter issues
# ───────────────────────────────────────────────────────────
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Colors
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    DIM='\033[2m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    GREEN='' RED='' DIM='' BOLD='' NC=''
fi

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

PYTEST="${PYTEST:-.venv/bin/python -m pytest}"

failed_steps=()

run_step() {
    local label="$1"; shift
    printf "${BOLD}─── %s ───${NC}\n" "$label"
    if "$@"; then
        printf "${GREEN}✓${NC} %s\n\n" "$label"
    else
        printf "${RED}✗${NC} %s\n\n" "$label"
        failed_steps+=("$label")
    fi
}

# Step 1: full pytest suite
run_step "Step 1/4: pytest (full suite, ~2 min)" \
    $PYTEST -q

# Step 2: doc-consistency guards (the launch-credibility checks)
run_step "Step 2/4: doc-consistency guards (launch-credibility checks)" \
    $PYTEST tests/test_doc_count_consistency.py -q

# Step 3: install.sh bash syntax check + the install-sh guards
run_step "Step 3/4: install.sh syntax + structural guards" \
    $PYTEST tests/test_install_sh_and_update.py -q

# Step 4: bash-n the installer end-to-end (one more sanity check that
# the curl|sh entry point parses cleanly — the actual fresh-machine
# smoke runs on a real VM).
run_step "Step 4/4: bash -n scripts/install.sh" \
    bash -n scripts/install.sh

# Verdict
printf "${BOLD}═══════════════════════════════════════════════════════${NC}\n"
if [ ${#failed_steps[@]} -eq 0 ]; then
    printf "${GREEN}${BOLD}✓ All gates passed — ready for the public flip.${NC}\n"
    printf "${DIM}Next manual steps (need credentials + GitHub): make the repo public + set description/topics via gh.${NC}\n"
    exit 0
else
    printf "${RED}${BOLD}✗ %d gate(s) failed:${NC}\n" "${#failed_steps[@]}"
    for step in "${failed_steps[@]}"; do
        printf "${RED}  - %s${NC}\n" "$step"
    done
    printf "${DIM}Fix before flipping the repo public.${NC}\n"
    exit 1
fi
