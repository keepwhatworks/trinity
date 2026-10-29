"""No goals/OKR layer (founder lock 4; docs/architecture.md, "deliberately absent", declined 2026-07-04).
Nothing enforced it (internal/rule-enforcement.md, 2026-10-26). This pins the structure such a layer would need:
no module, CLI verb, state path or MCP tool named for goals or OKRs. run_council's `goal` argument frames one
council's question; it is not a layer, and this does not touch it."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
_LAYER = re.compile(r"(^|[_-])(goals?|okrs?|objectives)([_-]|$)", re.I)


def test_the_detector_sees_a_goals_layer():
    """The positive control: a detector that matches nothing would pass every test below."""
    assert all(_LAYER.search(n) for n in ("goals", "goal_store", "okr", "user-okrs", "team_objectives"))
    assert not any(_LAYER.search(n) for n in ("goalkeeper", "verify", "council_runner"))


def test_no_module_is_named_for_goals():
    bad = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if _LAYER.search(p.stem)]
    assert not bad, f"a goals layer is deliberately absent (docs/architecture.md): {bad}"


def test_no_cli_verb_is_named_for_goals():
    from trinity_local import main

    parser = main.build_parser()
    verbs = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert len(verbs) > 10                       # the scan saw the real CLI
    assert not [v for v in verbs if _LAYER.search(v)]


def test_no_state_path_is_named_for_goals():
    from trinity_local import state_paths

    names = [n for n, f in inspect.getmembers(state_paths, inspect.isfunction) if f.__module__ == state_paths.__name__]
    assert any(n.endswith("_path") or n.endswith("_dir") for n in names)
    assert not [n for n in names if _LAYER.search(n)]


def test_no_mcp_tool_is_named_for_goals():
    from trinity_local import mcp_server

    tools = asyncio.run(mcp_server.handle_list_tools())
    assert tools and not [t.name for t in tools if _LAYER.search(t.name)]
