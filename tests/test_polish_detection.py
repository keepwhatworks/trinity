"""Tests for `is_polish_task` + its propagation through MCP `route()`.

A polish-shape task is one that asks for refinement of existing copy:
"make this better", "tighten this", "is this clearer?". The value of
detecting these is that consensus_round mode (iterative refinement)
shines on them — first pass catches the obvious, rounds 2-3 are where
the chairman crystallizes the line. Surfaced via `auto_iterate_recommended`
in route() output so harnesses can offer auto-iterate without us changing
default behavior.
"""
from __future__ import annotations

import json



class TestIsPolishTask:
    def test_literal_polish_phrases_trigger(self):
        from trinity_local.task_types import is_polish_task
        for prompt in (
            "Make this better. Does it make sense?",
            "polish this email",
            "tighten this paragraph",
            "any better?",
            "is this clearer?",
            "is this stronger?",
            "Rewrite this for HN.",
            "Refine this opener — too long.",
        ):
            assert is_polish_task(prompt), f"should detect polish in: {prompt!r}"

    def test_short_imperative_hints_trigger(self):
        """≤20 words + a hint word should fire."""
        from trinity_local.task_types import is_polish_task
        for prompt in (
            "Shorter please.",
            "make it crisper",
            "needs to be punchier",
            "this should be simpler",
            "stronger verb here?",
        ):
            assert is_polish_task(prompt), f"should detect short polish hint: {prompt!r}"

    def test_long_task_with_hint_word_does_not_trigger(self):
        """A long technical task that happens to contain 'simpler' shouldn't
        get false-flagged as polish."""
        from trinity_local.task_types import is_polish_task
        long_prompt = (
            "I need to refactor this 800-line Python module that handles "
            "authentication tokens, expiration, refresh, and the cache layer. "
            "It's grown organically over six months and there are multiple "
            "places where the same logic appears in subtly different forms. "
            "Could you suggest a simpler architecture that consolidates the "
            "shared logic while preserving the existing public API?"
        )
        assert not is_polish_task(long_prompt), "long technical task ≠ polish"

    def test_non_polish_tasks_do_not_trigger(self):
        from trinity_local.task_types import is_polish_task
        for prompt in (
            "Should I use SQLite or DuckDB for analytics?",
            "Debug this stack trace.",
            "Write a unit test for the routing function.",
            "Compare Claude vs GPT-4 for legal research.",
            "",
        ):
            assert not is_polish_task(prompt), f"should NOT detect polish in: {prompt!r}"


class TestLaunchpadPolishHint:
    """Pin the client-side polish hint UI: petite-vue getter must exist
    in the rendered launchpad HTML, and the in-card hint paragraph must
    be wired to it."""

    def test_polish_hint_renders_in_launchpad_html(self, tmp_path, monkeypatch):
        from trinity_local.launchpad_page import write_portal_html

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        path = write_portal_html(title="Test")
        html = path.read_text(encoding="utf-8")

        # JS-side detection must be defined.
        assert "get isPolishLike()" in html
        assert "get polishHintVisible()" in html
        # In-card hint paragraph must be present + bound to polishHintVisible.
        assert 'v-if="polishHintVisible"' in html
        # Both hint variants must render (auto-on AND auto-off paths).
        assert "Polish task detected" in html
        # One of the literal phrases the JS detector matches MUST be in
        # the source — proves the heuristic transferred from Python.
        assert "make this better" in html
        assert "tighten this" in html


# TestPolishAutoIterateSetting was removed 2026-05-17 with the auto-chain
# setting retirement. The is_polish_task heuristic still lives in
# task_types.py and gates `route()`'s `auto_iterate_recommended` field
# (covered by TestRouteSurfacesPolish below) — but there is no longer a
# settings-driven branch that auto-fires the chain. Users click
# auto-chain on the council review page when they want it.


class TestRouteSurfacesPolish:
    def test_route_includes_auto_iterate_recommended_true_for_polish(self, tmp_path, monkeypatch):
        """When route() is asked about a polish task, the payload's
        `auto_iterate_recommended` field must be True so harnesses + the
        launchpad can offer iteration."""
        import asyncio
        from trinity_local.mcp_server import _route

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        result = asyncio.run(_route({
            "task": "Make this tagline better. Any better?",
            "available_models": ["claude", "antigravity", "codex"],
        }))
        payload = json.loads(result[0]["text"])
        assert payload.get("auto_iterate_recommended") is True

    def test_route_includes_auto_iterate_recommended_false_for_non_polish(self, tmp_path, monkeypatch):
        import asyncio
        from trinity_local.mcp_server import _route

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        result = asyncio.run(_route({
            "task": "Which model is best for legal research workflows?",
            "available_models": ["claude", "antigravity", "codex"],
        }))
        payload = json.loads(result[0]["text"])
        assert payload.get("auto_iterate_recommended") is False
