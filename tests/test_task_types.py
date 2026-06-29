"""guess_task_type heuristic — keyword-collision regressions.

The classifier feeds routing: its label picks which provider track record
the router votes on. A misclassification routes the question on the wrong
history. Live 2026-05-31 (via ask mode=route MCP call): "Postgres or
DynamoDB for a write-heavy event log" classified as `writing` because the
bare substring "write" matched "write-heavy" — so a database/architecture
question voted on the prose-writing track record.

These pin that "write" is only `writing` for genuine prose authoring, not
for the database/systems sense or "write a <code-thing>".
"""
from __future__ import annotations

import pytest

from trinity_local.task_types import guess_task_type


class TestWriteKeywordCollision:
    @pytest.mark.parametrize("text", [
        "Should we use Postgres or DynamoDB for a write-heavy event log?",
        "Which database is best for high write throughput?",
        "Design a read/write split across our replicas",
        "explain write-ahead logging in Postgres",
        "tune write IOPS on the volume",
        "reduce write amplification in the LSM tree",
    ])
    def test_systems_write_is_not_writing(self, text):
        """The database/systems sense of 'write' must NOT classify as prose
        writing. It falls through to `general` (no architecture task_type
        exists in the coarse heuristic — the chairman refines downstream)."""
        got = guess_task_type(text)
        assert got != "writing", f"{text!r} misclassified as writing"
        assert got == "general", f"{text!r} → {got!r}, expected general"

    @pytest.mark.parametrize("text", [
        "Write a function to parse JSON",
        "write a python script to dedupe rows",
        "write code to refactor this module",
    ])
    def test_code_write_is_coding_not_writing(self, text):
        assert guess_task_type(text) == "coding", f"{text!r} not coding"

    @pytest.mark.parametrize("text", [
        "Help me write a blog post about our launch",
        "draft an email to the team",
        "write a memo summarizing Q3",
        "rewrite this doc into a one-pager",
    ])
    def test_prose_write_still_classifies_as_writing(self, text):
        """The fix must not over-correct — genuine prose authoring is still
        `writing`."""
        assert guess_task_type(text) == "writing", f"{text!r} not writing"

    def test_other_categories_unaffected(self):
        assert guess_task_type("compare Redis vs Memcached") == "research"
        assert guess_task_type("debug this traceback") == "debugging"
        assert guess_task_type("refactor the repo") == "coding"
        assert guess_task_type("what's the weather like") == "general"
