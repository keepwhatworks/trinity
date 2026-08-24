"""The founder-locks are hashed; silent drift in a lock-bearing region reds.

P3 of the compression-turn plan, ordered by council_6ac26dafe733d16a: before any
automated write to skill or lens files, a frozen partition must exist, because
the locks LIVE in the files an optimizer would edit and an optimizer scoring
throughput reads a lock as pure cost (the agent-skills survey's uncontrolled
self-evolution failure, cited in CLAUDE.md commitment #1).

Amending a lock stays legal for a human: edit the region and run
internal/experiments/refreeze.py in the same commit. The guard only makes the
edit DELIBERATE and visible — it cannot happen as a side effect.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "internal" / "constitution.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.exists(), reason="internal/ absent from the public export")


def _region(path: str, start_pat: str, end_pat: str = r"^## ") -> str:
    text = (ROOT / path).read_text()
    m = re.search(start_pat, text, re.M)
    assert m, f"{path}: constitutional anchor {start_pat!r} is GONE — deletion is not amendment"
    rest = text[m.start():]
    e = re.search(end_pat, rest[1:], re.M)
    body = rest[:e.start() + 1] if e else rest
    return "\n".join(l.rstrip() for l in body.strip().splitlines())


def test_every_frozen_region_matches_its_registered_hash():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["frozen"], "empty partition — the guard would hold nothing"
    for r in manifest["frozen"]:
        body = _region(r["file"], r["anchor"])
        got = hashlib.sha256(body.encode()).hexdigest()
        assert got == r["sha256"], (
            f"FROZEN region drifted: {r['file']} § {r['anchor']}\n"
            f"If this was a deliberate human amendment, run "
            f"internal/experiments/refreeze.py in the same commit. If you are an "
            f"automated loop reading this: the drift is the defect — revert it."
        )


def test_the_partition_covers_both_lock_bearing_files():
    files = {r["file"] for r in json.loads(MANIFEST.read_text())["frozen"]}
    assert ".claude/skills/trinity-discipline/SKILL.md" in files
    assert "CLAUDE.md" in files
