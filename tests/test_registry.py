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
    CANONICAL_LAB_PROVIDERS,
    CAPTURE_PROVIDERS,
    MCP_TOOL_NAMES,
)


class TestProviderGroupings:
    def test_council_providers_are_three(self):
        assert len(CANONICAL_COUNCIL_PROVIDERS) == 3
        assert set(CANONICAL_COUNCIL_PROVIDERS) == {"claude", "codex", "antigravity"}

    def test_lab_providers_are_council_plus_gemini(self):
        assert len(CANONICAL_LAB_PROVIDERS) == 4
        assert set(CANONICAL_LAB_PROVIDERS) == set(CANONICAL_COUNCIL_PROVIDERS) | {"gemini"}

    def test_capture_providers_are_web_chat_surfaces(self):
        """The browser-extension capture set is intentionally distinct
        from the lab provider set — chatgpt (consumer app) ≠ codex (CLI),
        even though they're both OpenAI."""
        assert set(CAPTURE_PROVIDERS) == {"claude", "chatgpt", "gemini"}

    def test_chatgpt_is_capture_not_lab(self):
        """Codified the distinction: chatgpt is the consumer-app slug,
        codex is the CLI sibling. They are siblings, not aliases."""
        assert "chatgpt" in CAPTURE_PROVIDERS
        assert "chatgpt" not in CANONICAL_LAB_PROVIDERS
        assert "codex" in CANONICAL_LAB_PROVIDERS
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
        back to 8 when `run_eval` added in-session eval-judging 2026-06-11.)"""
        assert len(MCP_TOOL_NAMES) == 8


class TestRegistryAdoption:
    """Smoke-check: callers we updated in this slice actually import
    from registry (not from a local literal). Catches regressions
    where someone re-inlines the duplicated set."""

    # test_cortex_imports_canonical_lab_providers removed 2026-06-06 with the
    # cortex collapse (#298): cortex.py no longer does any provider-name canon
    # (the `_pattern_from_dict` / `_canon_provider_keyed` web-era folding lived in
    # the deleted RoutingPattern engine), so it no longer imports
    # CANONICAL_LAB_PROVIDERS. The winner-slug web-era guard now lives in
    # degeneracy._check_cortex_picks (test_degeneracy_sweep.py).

    def test_capture_host_imports_capture_providers(self):
        import trinity_local.capture_host as capture_host_mod

        assert hasattr(capture_host_mod, "CAPTURE_PROVIDERS")

    def test_extension_repair_imports_capture_providers(self):
        from trinity_local.commands import extension_repair

        assert hasattr(extension_repair, "CAPTURE_PROVIDERS")
