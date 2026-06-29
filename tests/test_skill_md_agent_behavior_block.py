"""SKILL.md agent-behavior block: trigger conditions for import_provider_memory.

Council 3e4564e9 (run 2026-05-25) ruled the highest-leverage post-launch ship
is teaching agents WHEN to call the newly-shipped import_provider_memory MCP
tool — the tool exists but no agent will think to use it without a prompt-
level hint. This test pins the trigger conditions so a future SKILL.md
refactor doesn't quietly drop them.

The two SKILL.md files are mirrored (data/skills/trinity/SKILL.md is the
canonical package-data file; skills/trinity/SKILL.md is the externally
published copy). Both must carry the same content — otherwise an agent
loading the skill from one location sees different behavior guidance
than from the other.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL_PATHS = [
    REPO / "src" / "trinity_local" / "data" / "skills" / "trinity" / "SKILL.md",
    REPO / "skills" / "trinity" / "SKILL.md",
]


@pytest.fixture
def skills():
    return [(p, p.read_text(encoding="utf-8")) for p in SKILL_PATHS]


class TestImportProviderMemoryListedInTools:
    """Tool catalog block must mention the new tool so a casual reader
    counting tools by reading the list doesn't end at 8."""

    def test_both_skill_md_mention_import_provider_memory_in_tool_list(self, skills):
        for path, text in skills:
            assert "import_provider_memory" in text, (
                f"{path} missing import_provider_memory mention. "
                "The MCP tool shipped 2026-05-25 (commit 8deab36); the "
                "SKILL must list it so agents reading the tool catalog "
                "know it exists."
            )


class TestAgentBehaviorBlockPresent:
    """Pin the 4-trigger spec the council ruled was the load-bearing
    teaching. Each trigger is identifiable by a stable keyword."""

    def test_all_four_trigger_conditions_named(self, skills):
        # Trigger keywords from the council-ratified spec. If a future
        # cleanup edits the wording, this test fires and the editor
        # has to make a deliberate decision about which trigger is
        # being dropped (or renamed).
        required_triggers = [
            "REFRAME",          # axis name; trigger #1
            "tension",          # paired-tension trigger (#2)
            "Post-council",     # trigger #3
            "dry_run",          # trigger #4 — and the safety valve
        ]
        for path, text in skills:
            missing = [t for t in required_triggers if t not in text]
            assert not missing, (
                f"{path} agent-behavior block missing trigger keywords: "
                f"{missing}. Council 3e4564e9 ratified the 4-trigger "
                "spec; dropping any of them collapses the write-back "
                "path back to 0% activation."
            )

    def test_verifiable_test_named(self, skills):
        """Council eval_seed required a verifiable test for whether the
        ship worked. The block names both — lens-acts for the unified
        preference ledger, jq .lenses for lenses."""
        for path, text in skills:
            assert "preference_acts.jsonl" in text, f"{path}: missing preference_acts.jsonl test target"
            assert "trinity-local lens-acts" in text, f"{path}: missing lens-acts verification command"
            assert "lenses.json" in text, f"{path}: missing lenses.json test target"


class TestCursorIsNotAReadsSource:
    """The 'Trinity reads transcripts' sentence must NEVER list Cursor as an
    INGEST source. Cursor is an install target only — there is no parse_cursor
    reader (it keeps chats in SQLite state.vscdb, not the JSONL the parsers
    read). v1.7.292 swept this overclaim from CLAUDE.md + how-trinity-works but
    MISSED all three SKILL.md copies — the highest-traffic surface (loaded on
    every /trinity). This pins the fix so the overclaim can't creep back.

    Naming Cursor as an install-target CAVEAT (after the em-dash) is fine and
    on-message; naming it inside the comma-separated SOURCE list is the bug.
    """

    def _reads_sentence(self, text: str) -> str:
        for line in text.splitlines():
            if line.startswith("Trinity reads transcripts"):
                return line
        return ""

    def test_reads_line_does_not_list_cursor_as_source(self, skills):
        for path, text in skills:
            sentence = self._reads_sentence(text)
            assert sentence, f"{path}: no 'Trinity reads transcripts' sentence found"
            # the parenthetical source list, up to the em-dash caveat (if any)
            paren = sentence.split("(", 1)[1].split(")", 1)[0] if "(" in sentence else sentence
            source_list = paren.split("—")[0]  # everything before the install-target caveat
            assert "cursor" not in source_list.lower(), (
                f"{path}: Cursor is listed as a reads/ingest SOURCE "
                f"({source_list!r}). Cursor is an INSTALL TARGET only — no "
                "parse_cursor reader exists (state.vscdb). Move it behind the "
                "em-dash install-target caveat or drop it; never list it as a source."
            )

    def test_reads_line_keeps_the_install_target_caveat(self, skills):
        # If Cursor is mentioned at all in the reads sentence, it must be framed
        # as an install target (the honest scoping), not silently dropped-then-readded.
        for path, text in skills:
            sentence = self._reads_sentence(text)
            if "cursor" in sentence.lower():
                assert "install target" in sentence.lower(), (
                    f"{path}: the reads sentence mentions Cursor without the "
                    "'install target' framing — keep it honest (install target, "
                    "not yet an ingest source) or remove the mention entirely."
                )


class TestBothSkillMdInSync:
    """The two SKILL.md files must stay byte-identical — drift between
    them means an agent loading the skill from one location sees
    different guidance than from the other."""

    def test_skill_md_files_byte_identical(self, skills):
        if len(skills) < 2:
            pytest.skip("Need both SKILL.md files for sync check")
        canonical_text = skills[0][1]
        for path, text in skills[1:]:
            assert text == canonical_text, (
                f"{path} drifted from canonical "
                f"{skills[0][0]}. Mirror with: "
                f"cp {skills[0][0]} {path}"
            )
