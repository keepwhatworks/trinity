"""The screen guarding corpus text that leaves this machine must see CONTENT.

Written 2026-08-15 after a measured leak: hq_087's re-run was the first
experiment to dispatch raw transcript text rather than LLM-extracted act
fields, it carried the usual inline four-regex screen (email/money/url/phone),
and 4 of 48 dispatched items turned out to contain medical or financial
narrative that no identifier pattern can see.

Mutation-proven: deleting the CONTENT block REDs
`test_narrative_sensitive_content_is_blocked`; making `screen_reason` return
None on empty REDs `test_fails_closed_on_empty`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "internal" / "experiments" / "corpus_screen.py"


def _screen():
    """Load the module by PATH rather than mutating sys.path at import time —
    module-level sys.path mutation in a test leaks into every test that runs
    after it, which tests/test_no_module_level_env_mutation.py exists to catch
    (and did catch, on this file)."""
    spec = importlib.util.spec_from_file_location("corpus_screen", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_narrative_sensitive_content_is_blocked():
    """The exact gap: no @, no $, no URL, no phone — and it must still block."""
    cs = _screen()
    for text in (
        "The patient presented with recurring symptoms after the treatment plan changed.",
        "I pulled the numbers off my tax return and the mortgage is the bigger line.",
        "Dear Margaret, sincerely, it has been a long year.",
        "He asked for the passport number and date of birth before booking.",
    ):
        assert not cs.is_safe(text), f"content screen missed: {text[:40]}"
        assert cs.screen_reason(text) is not None


def test_identifier_screen_still_applies():
    cs = _screen()
    assert cs.screen_reason("ping me at a@b.com") == "email"
    assert cs.screen_reason("see https://example.com/x") == "url"
    assert cs.screen_reason("that came to $1,200.00") == "money"


def test_ordinary_engineering_text_passes():
    """A screen that blocks everything protects nothing — it just empties the
    sample while looking careful."""
    cs = _screen()
    for text in (
        "The retry loop skipped null keys, so the failures never re-dispatched.",
        "Rank the basins by rate of change rather than by count.",
        "That assertion survives its own deletion, so it is decoration.",
    ):
        assert cs.is_safe(text), f"false positive on: {text[:40]}"


def test_fails_closed_on_empty():
    cs = _screen()
    for text in (None, "", "   "):
        assert not cs.is_safe(text)
        assert cs.screen_reason(text) == "empty"


def test_partition_reports_what_it_dropped():
    """A shrinking sample must be visible, never silent."""
    cs = _screen()
    safe, blocked = cs.partition({
        "a": "Rebuild the index from the checkpoint.",
        "b": "The patient chart lists the medication.",
        "c": "",
    })
    assert list(safe) == ["a"]
    assert blocked == {"b": "medical", "c": "empty"}
