"""Phase 3 — REFERENCE ROT: a list item whose citations died is flagged, not trusted.

gstack decays its taste memory 5% a week. That is wrong for rules. A rule does
not become less true with time; it becomes IRRELEVANT when the thing it guards
stops existing. The `consolidate` rules would have rotted the day that verb was
retired, and no clock would have noticed.

So the decay signal here is REFERENCE ROT: every citation a list item leans on
must still resolve.

  hq_ ids        must appear in internal/experiments/hypothesis_queue.jsonl
  res_ ids       must appear in internal/experiments/residual_ledger.jsonl —
                 a DIFFERENT registry. The first version of this guard checked
                 both against the hypothesis queue and reported res_015 as dead
                 on its first run. It was not dead; the guard was wrong. Nearly
                 "fixed" a correct citation, which is how a rot signal becomes
                 a corruption signal.
  amd_ ids       must appear in internal/amendment-ledger.jsonl
  r_<16hex>      preference-act ids — NOT checked here. They live in the
                 founder's ~/.trinity, which tests must never read (the
                 isolated-TRINITY_HOME invariant), and a guard that passes
                 vacuously under an isolated home is worse than no guard.
                 The loop's own grounding gate checks these at proposal time,
                 which is the right place.
  file paths     must exist in the repo

FLAG, NEVER AUTO-REMOVE. A dead citation means "a human should look at this",
not "delete it". Auto-removal on a mechanical signal is how a safety rule
disappears because the incident that motivated it got archived — the exact
failure the constitutional partition exists to prevent, arriving by a different
door.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "council-review-plan" / "SKILL.md"

_HQ = re.compile(r"\b(hq_\d{3}|res_\d{3})\b")
_AMD = re.compile(r"\bamd_\d{4}\b")
# A repo-relative path mentioned in prose. Deliberately narrow: it must carry a
# directory separator and a known extension, so ordinary words with dots
# ("i.e.", "0.70") are not mistaken for files.
_PATH = re.compile(r"\b((?:src|tests|docs|internal|scripts|\.claude)/[\w./-]+\.\w{2,4})\b")


def _list_sections() -> str:
    """Only the two MINED lists — the rest of the skill is prose about method
    and cites freely."""
    text = SKILL.read_text(encoding="utf-8")
    start = text.find("## The known-failure-modes list")
    end = text.find("## The receipts")
    assert start != -1 and end != -1 and end > start, (
        "the mined-list sections moved or were renamed; this guard reads them by heading"
    )
    return text[start:end]


@pytest.fixture(scope="module")
def sections() -> str:
    return _list_sections()


def test_the_guard_has_something_to_check(sections):
    """A citation guard over zero citations passes vacuously — this repo's
    signature failure. Assert the scan actually finds references."""
    n = len(_HQ.findall(sections)) + len(_AMD.findall(sections)) + len(_PATH.findall(sections))
    assert n >= 10, (
        f"only {n} citations found across both mined lists — either the lists lost their "
        "provenance or the patterns stopped matching. Both are defects."
    )


def _ids_in(path: Path) -> set[str]:
    out = set()
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rid = json.loads(line).get("id")
            except json.JSONDecodeError:
                continue
            if rid:
                out.add(rid)
    return out


def test_every_hypothesis_id_still_resolves(sections):
    """An item citing a hypothesis that no longer exists is leaning on a fact
    nobody can check.

    hq_ and res_ live in SEPARATE registries and this guard's first version
    conflated them — see the module docstring.
    """
    exp = REPO / "internal" / "experiments"
    known = _ids_in(exp / "hypothesis_queue.jsonl") | _ids_in(exp / "residual_ledger.jsonl")
    cited = set(_HQ.findall(sections))
    dead = sorted(cited - known)
    assert not dead, (
        f"list item(s) cite hypotheses that are NOT in the registry: {dead}. "
        "FLAG for review — decide whether the item still earns its place, then either "
        "re-cite it or merge it. Do NOT delete the item on this signal alone."
    )


def test_every_amendment_id_still_resolves(sections):
    ledger = REPO / "internal" / "amendment-ledger.jsonl"
    if not ledger.exists():
        pytest.skip("no amendment ledger on disk")
    dead = sorted(set(_AMD.findall(sections)) - _ids_in(ledger))
    assert not dead, f"list item(s) cite amendments not in the ledger: {dead}. FLAG for review."


def test_every_cited_path_still_exists(sections):
    """The rot signal with real teeth: a rule that names a file guards that
    file, and the file is gone."""
    dead = sorted({p for p in _PATH.findall(sections) if not (REPO / p).exists()})
    assert not dead, (
        f"list item(s) cite paths that no longer exist: {dead}. This is REFERENCE ROT — "
        "the thing the rule guarded has moved or been retired, so the rule may no longer "
        "apply. FLAG for merge; never auto-remove."
    )
