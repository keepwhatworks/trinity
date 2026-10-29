#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────
#  Trinity Local — release check
#
#  Runs the six programmatic gates every release must pass. Single
#  command, green/red verdict per step, non-zero exit on any failure.
#  (Named for the 2026 launch, when it guarded the public-repo flip.)
#
#  Usage:
#    bash scripts/launch-check.sh
#
#  After it passes, commit the docs step 1 re-rendered with the release.
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
            sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
    esac
done

# Test the tree this script lives in. The venv's editable install points at whichever checkout
# installed it, so from a worktree every gate imported the primary checkout's code: the 1.7.406
# release check compared one tree's version with the other's (coherence review 2026-10-16).
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

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
run_step "Step 1/6: pytest (full suite, ~2 min)" \
    $PYTEST -q

# A green step 1 wrote a fresh measurement (test-run-snapshot.json). Render the docs that quote it
# now, so steps 2 and 5 check the docs the release will carry. Before this, every release that
# added a test failed both steps until someone re-rendered by hand (1.7.406 and 1.7.407, coherence
# review 2026-10-16). After a red step 1 nothing is rendered: render_docs refuses an unmeasured
# run, and step 5 reports it. The re-rendered docs belong in the release commit.
if [ ${#failed_steps[@]} -eq 0 ]; then
    .venv/bin/python scripts/render_docs.py | grep -E "^Rendered [0-9]+ doc" \
        | sed 's/$/ (from the fresh measurement; commit them with the release)/' || true
fi

# Step 2: doc-consistency guards
run_step "Step 2/6: doc-consistency guards" \
    $PYTEST tests/test_doc_count_consistency.py -q

# Step 3: install.sh bash syntax check + the install-sh guards
run_step "Step 3/6: install.sh syntax + structural guards" \
    $PYTEST tests/test_install_sh_and_update.py -q

# Step 4: bash-n the installer end-to-end (one more sanity check that
# the curl|sh entry point parses cleanly — the actual fresh-machine
# smoke runs on a real VM).
run_step "Step 4/6: bash -n scripts/install.sh" \
    bash -n scripts/install.sh

# Step 5: doc-to-REALITY gate. The doc-consistency guards in step 2
# compare doc-to-doc / doc-to-generator, so they stay green when the
# GENERATOR is wrong — that is exactly how "4389 tests passing +
# 4 skipped" shipped past 113 of them (the skip count was a hardcoded
# fallback that `pytest --collect-only` could never override, 2026-07-31).
# `render_docs --check` compares the published values to the canonical
# extractors, and the test counts now read `test-run-snapshot.json` —
# the terminal summary of the run step 1 just finished. Order matters:
# this must come AFTER step 1 so it checks against a fresh measurement.
run_step "Step 5/6: render_docs --check (published values match measured reality)" \
    .venv/bin/python scripts/render_docs.py --check

# Step 6: the wheel a release ships, installed into an empty venv with an empty HOME. Every
# step above runs from a source checkout, so a command that fails only when installed passes
# all of them: v1.7.403 shipped three (council_a68965439ef6ad3e).
run_step "Step 6/6: clean-wheel smoke (install the wheel, run what a new user is shown)" \
    .venv/bin/python scripts/wheel_smoke.py

# Verdict
printf "${BOLD}═══════════════════════════════════════════════════════${NC}\n"
if [ ${#failed_steps[@]} -eq 0 ]; then
    printf "${GREEN}${BOLD}✓ All gates passed — ready to release.${NC}\n"
    # A green here attests every test that RAN. Browser-marked guards skip without
    # Chrome, and measured on this repo's own history (hq_099b/hq_099c) they carry the
    # entire guard for ~30% of fix commits. Say so rather than let the tick imply it.
    printf "${DIM}"; .venv/bin/python scripts/coverage_disclosure.py; printf "${NC}"
    exit 0
else
    printf "${RED}${BOLD}✗ %d gate(s) failed:${NC}\n" "${#failed_steps[@]}"
    for step in "${failed_steps[@]}"; do
        printf "${RED}  - %s${NC}\n" "$step"
    done
    printf "${DIM}Fix before releasing.${NC}\n"
    exit 1
fi
