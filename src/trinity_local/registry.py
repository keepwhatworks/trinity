"""Single-source-of-truth registry for provider slugs + MCP tool names.

The motivation (council_76e5aef79bb9f241 #1): names that recur across
modules drifted multiple times during development — provider slug
sets were inlined as tuples in 4+ places, MCP tool names lived only
inside ``mcp_server.py`` string literals, and the canonical counts in
``claude.md`` drifted from the source of truth more than once. This
module consolidates the canonical groupings as importable symbols so
new call sites adopt them by reference rather than by copy-paste.

Four distinct groupings — they share members but serve different
purposes, and conflating them is a real drift source. Each name below
is the set used by a specific Trinity subsystem:

- ``CANONICAL_COUNCIL_PROVIDERS`` — the 3 frontier-lab CLIs that play
  council member roles. Re-exported from ``config`` for back-compat.
- ``CANONICAL_LAB_PROVIDERS`` — 4 entries: council trio + ``gemini``
  (the consumer Gemini app via takeout/capture, distinct from the
  ``antigravity`` CLI). Used by cortex frontmatter filters that need
  to know "did this field name a provider we know about?"
- ``CAPTURE_PROVIDERS`` — the 3 web-chat surfaces with browser-
  extension capture adapters (``claude``, ``chatgpt``, ``gemini``).
  Different from CANONICAL_LAB_PROVIDERS — ``chatgpt`` here maps to
  the OpenAI consumer app; ``codex`` is the CLI sibling and lives in
  the CANONICAL_LAB_PROVIDERS set.
- ``MCP_TOOL_NAMES`` — the 8 tools registered in ``mcp_server.py``.
  Tested for drift against the actual ``handle_list_tools()`` output.
"""
from __future__ import annotations

from .config import CANONICAL_COUNCIL_PROVIDERS

__all__ = [
    "CANONICAL_COUNCIL_PROVIDERS",
    "CANONICAL_LAB_PROVIDERS",
    "CAPTURE_PROVIDERS",
    "MCP_TOOL_NAMES",
]


# All four frontier-lab provider slugs Trinity recognizes in code +
# config + transcript metadata. Cortex frontmatter parsers use this
# set to separate "known provider field" from arbitrary other
# metadata. The order here matches the order CANONICAL_COUNCIL_PROVIDERS
# plus gemini at the end (consumer Gemini, not the antigravity CLI).
CANONICAL_LAB_PROVIDERS: tuple[str, ...] = tuple(
    list(CANONICAL_COUNCIL_PROVIDERS) + ["gemini"]
)


# Web-chat surfaces the browser extension captures from. NOT the same
# as lab providers — "chatgpt" here is the consumer chatgpt.com app
# (OpenAI), distinct from "codex" which is the CLI sibling. Trinity's
# native-messaging adapters live at browser-extension/adapters/<slug>.js
# for each of these.
CAPTURE_PROVIDERS: tuple[str, ...] = ("claude", "chatgpt", "gemini")


# The canonical Chrome extension ID — the SINGLE source of truth.
#
# This is what makes "the extension auto-wires itself to Trinity" possible:
# install.sh pre-registers the native-messaging host for THIS id, so when
# the user installs the published extension (which has this fixed id) the
# host is already there and the extension connects on first run. A bare
# native-messaging host only accepts connections whose origin is in its
# allowed_origins, so the id MUST match the installed extension.
#
# Today this is the id Chrome assigned to the locally-loaded unpacked build.
# On Web Store publish, replace it with the assigned store id (the store id
# is fixed forever once published). The bash resolver
# (scripts/launcher_path_resolver.sh) hard-codes the same value as its
# default — `test_extension_id_sync` keeps the two in lockstep.
CANONICAL_EXTENSION_ID: str = "caaojjhagginmgobdaheincllmblcjoi"


# The Chrome Web Store listing URL. EMPTY until published — when empty,
# install CTAs fall back to the sideload (Load unpacked) instructions;
# once set, they flip to a one-click "Add to Chrome" button. This is the
# single switch that turns the non-coder funnel on.
CHROME_WEB_STORE_URL: str = ""


# The 8 MCP tools registered in mcp_server.py's handle_list_tools().
# Order matches the registration order. Tested for drift against the
# live tool list in tests/test_registry.py — adding/removing/renaming
# a tool MUST keep both surfaces in sync.
MCP_TOOL_NAMES: tuple[str, ...] = (
    "ask",
    "run_council",
    "run_eval",
    "get_persona",
    "get_picks",
    "get_council_status",
    "import_provider_memory",
    "lens_generators",
)
