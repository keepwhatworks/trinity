"""Two copy ratchets for per-model / per-instrument numbers.

WHY THIS EXISTS (2026-07-31). Both halves of this file guard the SAME failure:
a number in prose outliving the artifact it was read off, because prose is
written in four places and only one gets updated.

1. THE DEAD EFFORT PAIR. CLAUDE.md carried "GPT-5.5 xhigh at 69% vs 48%" as the
   payoff line for keying the trust tally on model x version x effort. It is a
   PRE-CONTAMINATION-FIX number: `trust --build` used to dispatch its resolver
   through `claude -p`, that transcript was ingested role=user, and the next
   build read it back as "what the user did next" (188 verdicts invalidated,
   re-resolved 2026-07-26). The live artifact
   `~/.trinity/disagreement_ledger/summary.json` reads

       effort_breakdown["openai . flagship . 5.5"]["xhigh"]
         = 17W-12L = 0.586, ci [0.407, 0.745], ci_excludes_half FALSE

   — 58.6%, indistinguishable from chance. There is no sibling cell a "vs"
   could point at, but NOT because that is the only cell above MIN_TALLY_N:
   THREE clear it (Fable high 15W-7L, Gemini 3.1 high 30W-57L, GPT-5.5 xhigh
   17W-12L). The reason is that no model x version has a SECOND effort level on
   file, so no cell has a sibling. The first fix of this file asserted "the only
   effort sub-cell", having read the single key the sentence was about — the
   same one-key-generalised-to-a-shape move it was written to correct, repeated
   inside the correction. The count and the per-model level max are now planted
   into CLAUDE.md from `effort_breakdown` (evidence_claims.py:
   `ledger_effort_cells_n`, `ledger_effort_max_levels_per_model`), so a shape
   claim is machine-checked rather than restated in four prose copies.
   CLAUDE.md's own rule ("Never requote a per-model number taken before that
   fix") had been violated in four places: CLAUDE.md, two mcp_server.py comment
   blocks, and a test docstring. A prose rule that four copies can violate needs
   an executable wall.

2. THE PALATE NUMBER AGREES ACROSS ITS COPIES. The prospective palate figure
   lives in three live-copy sites (CLAUDE.md, the trinity-discipline skill's
   measured-claims table, docs/architecture.md). It sat at "73% over 22" in all
   three for three weeks while the artifact walked to 301 decided trials — an
   UNDERSTATEMENT, which no banned-string rule can catch because the stale value
   is not wrong, only old. What IS mechanically checkable is that the copies do
   not disagree with each other: the moment one is refreshed and the others are
   not, this fires and names the laggards. (The value itself is checked against
   `~/.trinity` by hand — CI has no home directory, so this file deliberately
   does NOT claim to validate the number, only its consistency.)

DEGENERATE-DATA DISCIPLINE: every scan here asserts it actually scanned
something and that its patterns still match a known control. A repo-text guard
whose glob rots matches nothing and passes forever — this repo's #1 bug shape,
in the guard layer itself.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The dead pairing, tolerant of the separators the four copies actually used
# ("69% vs 48%", "69 % vs. 48%"). Deliberately narrow: "69%" and "48%" on their
# own are legitimate elsewhere (graft-when-lost, TF-IDF coverage notes, a CSS
# bar width), so only the PAIRING is banned.
DEAD_EFFORT_PAIR = re.compile(r"69\s*%\s*vs\.?\s*48\s*%", re.IGNORECASE)

# Timestamped/archival surfaces may quote a dead number as history.
EXEMPT = {
    "CHANGELOG.md",
    "tests/test_precontamination_ledger_numbers.py",
}

# The dead OPUS 4.8 record. The clean artifact reads 28W-13L = 68.3%; the
# pre-contamination run read 37W-11L = 77%. Unlike the effort pair, this one
# has a LEGITIMATE use — disclosing the correction ("came down from 77% to
# 68%") is exactly what CLAUDE.md and the public blog post do, and banning that
# would push the repo toward silently swapping numbers, which is the worse
# failure. So the ban fires only when the dead record appears WITHOUT a
# correction marker on the same line.
DEAD_OPUS_RECORD = re.compile(
    r"37\s*W?\s*[-–]\s*11\s*L?\b|37\s+wins,\s*11\s+losses", re.IGNORECASE
)
CORRECTION_MARKER = re.compile(
    r"pre-?contamin|invalidat|re-?resolv|previous|earlier|was\s+comput|dead|"
    r"corrected|came\s+down|no\s+longer|stale|do\s+not\s+requote|never\s+requote",
    re.IGNORECASE,
)

SCAN_GLOBS = (
    "*.md",
    "docs/**/*.md",
    "internal/**/*.md",
    ".claude/skills/**/*.md",
    "src/**/*.py",
    "tests/**/*.py",
    "plugins/**/*.py",
    # internal/**/*.py was MISSING until 2026-08-01, and that gap shipped a real
    # leak: cold_start_model_backtest.py's banner justified its own KILL verdict
    # with "Opus 4.8 sides with this user on 77% ... (37W-11L)" — a dead number
    # load-bearing in an instrument's reasoning, in the one tree the scan could
    # not see. Experiment docstrings are exactly where numbers get quoted as
    # justification, so they need the wall most.
    "internal/**/*.py",
)


def _scan_files() -> list[Path]:
    seen: set[Path] = set()
    for pattern in SCAN_GLOBS:
        for path in REPO.glob(pattern):
            if path.is_file() and path.relative_to(REPO).as_posix() not in EXEMPT:
                seen.add(path)
    return sorted(seen)


def test_scan_is_not_vacuous():
    """The guard below is a text scan; if its globs rot it matches nothing and
    passes forever. Pin both the breadth of the scan and the liveness of the
    pattern."""
    files = _scan_files()
    assert len(files) > 300, (
        f"the scan only found {len(files)} files — SCAN_GLOBS have rotted and "
        "the ban below would pass vacuously"
    )
    rel = {p.relative_to(REPO).as_posix() for p in files}
    for must in ("CLAUDE.md", "src/trinity_local/mcp_server.py",
                 ".claude/skills/trinity-discipline/SKILL.md"):
        assert must in rel, f"{must} dropped out of the scan set"
    # positive control: the pattern still matches the string it was written for
    assert DEAD_EFFORT_PAIR.search("GPT-5.5·xhigh at 69% vs 48%")
    assert DEAD_EFFORT_PAIR.search("revealed GPT-5.5 xhigh at 69 % vs. 48 %")
    # negative control: the live number must NOT trip it
    assert not DEAD_EFFORT_PAIR.search("GPT-5.5 xhigh 17W-12L = 58.6%")
    # same discipline for the dead Opus record, in both directions
    assert DEAD_OPUS_RECORD.search("Opus 4.8 (37W-11L, CI excludes chance)")
    assert DEAD_OPUS_RECORD.search("| Claude Opus 4.8 | 37 wins, 11 losses |")
    assert not DEAD_OPUS_RECORD.search("Opus 4.8 28W-13L = 68.3%")
    # and the disclosure carve-out must actually carve
    assert CORRECTION_MARKER.search("the previous 77%/37-11 was computed on")
    assert not CORRECTION_MARKER.search("Opus 4.8 sides with this user (37W-11L)")


def test_no_precontamination_effort_pair_in_repo():
    """CLAUDE.md: 'Never requote a per-model number taken before that fix.'"""
    leaks: list[str] = []
    for path in _scan_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if DEAD_EFFORT_PAIR.search(line):
                leaks.append(f"  {path.relative_to(REPO).as_posix()}:{lineno}")
    assert not leaks, (
        "Pre-contamination-fix effort figure '69% vs 48%' found:\n"
        + "\n".join(leaks)
        + "\n\nThe live artifact (~/.trinity/disagreement_ledger/summary.json) "
        "reads GPT-5.5 xhigh 17W-12L = 58.6%, CI [0.407, 0.745], "
        "ci_excludes_half FALSE — not distinguishable from chance. Three effort "
        "sub-cells clear MIN_TALLY_N, but no model x version has a SECOND level, "
        "so no cell has a sibling and there is nothing a 'vs' could compare. "
        "Effort stays a gated secondary because it is part of the identity unit "
        "(model x size x effort), not because it produced a split."
    )


# ── The palate figure must agree across its copies ──────────────────────────

PALATE_SITES = (
    "CLAUDE.md",
    ".claude/skills/trinity-discipline/SKILL.md",
    "docs/architecture.md",
)

# "80.7% over 301 DECIDED trials" / "... 301 decided live trials"
PALATE_CLAIM = re.compile(r"(\d{1,3}(?:\.\d)?)%\s+over\s+(\d+)\s+decided", re.IGNORECASE)


def test_palate_claim_agrees_across_its_copies():
    """The prospective-palate figure is written in three places. When only one
    gets refreshed, the other two silently understate the instrument — which is
    exactly what happened between 2026-07-10 and 2026-07-31 (73%/22 held while
    the artifact walked to 80.7%/301)."""
    found: dict[str, set[tuple[str, str]]] = {}
    for rel in PALATE_SITES:
        text = (REPO / rel).read_text(encoding="utf-8")
        # ALL matches, not the first: a file that restates the claim twice and
        # refreshes only one of them is the same drift, one scope smaller.
        hits = {(m.group(1), m.group(2)) for m in PALATE_CLAIM.finditer(text)}
        if hits:
            found[rel] = hits
    missing = [rel for rel in PALATE_SITES if rel not in found]
    assert not missing, (
        "No '<pct>% over <n> decided' palate claim found in: "
        + ", ".join(missing)
        + " — either the claim was dropped or the phrasing drifted out of the "
        "guard's reach. Both are drift; restate the claim in the canonical "
        "shape or update PALATE_CLAIM."
    )
    distinct = {hit for hits in found.values() for hit in hits}
    assert len(distinct) == 1, (
        "The palate claim disagrees across (or within) its copies — refresh "
        "them together:\n"
        + "\n".join(
            f"  {rel}: " + ", ".join(f"{pct}% over {n} decided"
                                     for pct, n in sorted(hits))
            for rel, hits in sorted(found.items())
        )
        + "\n\nRecompute with palate_registry.summarize_trials() over "
        "~/.trinity/me/palate_trials.jsonl and update every site."
    )


def test_no_dead_opus_record_asserted_as_live():
    """The dead 37W-11L / 77% Opus 4.8 record may be DISCLOSED but not ASSERTED.

    Found 2026-08-01 in two places at once: a public blog post whose entire
    results table was the invalidated run, and an experiment banner that used
    the dead record to justify its own verdict. Neither was reachable by the
    existing scan.

    The carve-out is deliberate. A rule that banned the number outright would
    push every surface toward silently swapping its table, and a benchmark that
    quietly revises itself has told a reader nothing about whether to trust the
    next revision. So: quote it while correcting it, or do not quote it.
    """
    leaks: list[str] = []
    for path in _scan_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if DEAD_OPUS_RECORD.search(line) and not CORRECTION_MARKER.search(line):
                leaks.append(f"  {path.relative_to(REPO).as_posix()}:{lineno}: {line.strip()[:90]}")
    assert not leaks, (
        "the pre-contamination Opus 4.8 record (37W-11L / 37 wins, 11 losses) is asserted "
        "as live here. The clean artifact reads 28W-13L = 68.3%. Either use the clean "
        "number, or keep the dead one and say on the SAME LINE that it was corrected "
        "(invalidated / re-resolved / previous / came down from ...):\n" + "\n".join(leaks)
    )
