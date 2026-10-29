"""Tests for the SSOT name registry (#129).

Two guard families:
1. Set-membership invariants (provider slugs across the four groupings).
2. Drift guards — MCP_TOOL_NAMES must equal the live tool list emitted
   by ``mcp_server.handle_list_tools()``. If a new tool ships without
   updating the registry, this test catches it.
"""
from __future__ import annotations

import asyncio

from trinity_local.registry import (
    CANONICAL_COUNCIL_PROVIDERS,
    CAPTURE_PROVIDERS,
    MCP_TOOL_NAMES,
)


class TestProviderGroupings:
    def test_council_providers_are_three(self):
        assert len(CANONICAL_COUNCIL_PROVIDERS) == 3
        assert set(CANONICAL_COUNCIL_PROVIDERS) == {"claude", "codex", "antigravity"}

    def test_capture_providers_are_web_chat_surfaces(self):
        """The browser-extension capture set is intentionally distinct
        from the lab provider set — chatgpt (consumer app) ≠ codex (CLI),
        even though they're both OpenAI."""
        assert set(CAPTURE_PROVIDERS) == {"claude", "chatgpt", "gemini"}

    def test_chatgpt_is_capture_not_lab(self):
        """Codified the distinction: chatgpt is the consumer-app slug,
        codex is the CLI sibling. They are siblings, not aliases."""
        assert "chatgpt" in CAPTURE_PROVIDERS
        assert "chatgpt" not in CANONICAL_COUNCIL_PROVIDERS
        assert "codex" in CANONICAL_COUNCIL_PROVIDERS
        assert "codex" not in CAPTURE_PROVIDERS


class TestMcpToolDrift:
    """Guard: any tool registered in mcp_server.handle_list_tools()
    MUST appear in MCP_TOOL_NAMES, and vice versa. Catches "new tool
    shipped without registry update" + "stale tool name in registry."""

    def test_registry_matches_live_mcp_tools(self):
        from trinity_local.mcp_server import handle_list_tools

        # handle_list_tools is async — call it via asyncio.run.
        tools = asyncio.run(handle_list_tools())
        live_names = {t.name for t in tools}
        registry_names = set(MCP_TOOL_NAMES)

        missing_from_registry = live_names - registry_names
        stale_in_registry = registry_names - live_names

        assert not missing_from_registry, (
            f"mcp_server registers {missing_from_registry} but they're "
            f"missing from registry.MCP_TOOL_NAMES. Add them so other "
            f"callers can import the canonical list."
        )
        assert not stale_in_registry, (
            f"registry.MCP_TOOL_NAMES lists {stale_in_registry} but "
            f"mcp_server doesn't register them. Remove from registry "
            f"or restore the tool."
        )

    def test_count_matches_canonical_mcp_tool_count(self):
        """The canonical ``mcp_tool_count`` placeholder in claude.md (8)
        must equal len(MCP_TOOL_NAMES). render_docs.py reads the count
        from mcp_server's registration, so this is the trust chain:
        mcp_server == registry == render_docs == claude.md prose.
        (Was 9 after `lens_generators` landed 2026-06-05; back to 8 when
        `mark_pick_wrong` retired with the user-pick layer 2026-06-05;
        down to 7 when `route` was cut in the loop-primitive pass 2026-06-08;
        back to 8 when `run_eval` added in-session eval-judging 2026-06-11;
        up to 10 when `trust` landed 2026-07-18; back to 7 the same day when the
        eval-harness/palate/generators soft-demote pulled `run_eval` / `choose` /
        `lens_generators` off the MCP surface — CLI verbs + engines stay.)"""
        # get_picks removed 2026-08-11 with the router it read (7 -> 6); `verify`, the
        # pre-deploy gate, landed 2026-09-06 (6 -> 7). ask / get_persona /
        # import_provider_memory are scheduled off per amd_0205 once hq_105 reads.
        assert len(MCP_TOOL_NAMES) == 4


class TestRegistryAdoption:
    """Smoke-check: callers we updated in this slice actually import
    from registry (not from a local literal). Catches regressions
    where someone re-inlines the duplicated set."""

    def test_capture_host_imports_capture_providers(self):
        import trinity_local.capture_host as capture_host_mod

        assert hasattr(capture_host_mod, "CAPTURE_PROVIDERS")

    def test_extension_repair_imports_capture_providers(self):
        from trinity_local.commands import extension_repair

        assert hasattr(extension_repair, "CAPTURE_PROVIDERS")


class TestPersonalRoutingJoinCoverage:
    """res_018 — the join that never fired.

    `compute_personal_routing_table` keys buckets on the CHAIRMAN's free-form
    emitted task_type; `chairman_picker` reads them with `guess_task_type()`, a
    five-label heuristic. The key spaces do not intersect, so the personal blend
    has never engaged and every "X% personalized" claim downstream was reporting
    something that could not occur.

    Both halves work perfectly in isolation. That is exactly why no test caught
    it: the contract was asserted at the producer and never checked at the
    consumer. This is that check.
    """

    def test_the_two_key_spaces_are_disjoint_and_that_is_recorded(self):
        """Asserts the DEFECT, deliberately. If this ever fails, the join
        started working and res_018 plus chairman_picker's docstring must be
        updated together — a silently-fixed defect is as bad as a silent one."""
        from trinity_local.task_types import guess_task_type

        read_side = {guess_task_type(t) for t in (
            "fix this failing test", "research the literature on X",
            "write a blog post", "why is this crashing", "what should I do")}
        # The write side's labels are the chairman's own, e.g.
        # 'advisor_bottleneck_triage'. A five-label heuristic cannot produce one.
        assert read_side <= {"general", "research", "coding", "debugging", "writing"}, (
            f"guess_task_type now emits {read_side - {'general','research','coding','debugging','writing'}} "
            "— if the read side gained chairman-style labels the join may fire; "
            "re-measure coverage and update res_018")

    def test_the_chair_pick_no_longer_reads_the_table(self):
        """The consumer of that never-joined table was the chairman blend, removed
        2026-10-15 for a fixed chair order. If the picker reads the personal
        routing table again, the key contract above needs an executable check."""
        import inspect

        from trinity_local.ranker import chairman_picker
        src = inspect.getsource(chairman_picker)
        assert "compute_personal_routing_table" not in src and "personal_routing import" not in src
