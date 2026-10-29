"""Trinity's LLM calls ride councils (AGENTS.md, architecture commitment 1, with its documented bends). Only a regex
for two retired local-model names guarded that (internal/rule-enforcement.md, 2026-10-26). This freezes today's
call sites: every module that builds a provider or asks the MCP client to sample. The list is an INVENTORY, not a
sanction. Whether each site outside a council is within the commitment is an open question for the founder. A new
site fails here until someone reviews it and lists it."""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
_DISPATCH = {"make_provider", "CLIProvider", "CodexProvider", "create_message"}
INVENTORY = {                                   # reviewed 2026-10-26; not a ruling on which are sanctioned
    "providers.py": "defines the providers",
    "council_runner.py": "council members and the chairman",
    "build_mode.py": "build mode's patch-writing members",
    "verify.py": "verify's reader panel",
    "distill.py": "core.md distillation",
    "me_builder.py": "the lens build's Stage-3 namer",
    "me/generators.py": "the lens build's Stage-3 namer",
    "evals/runner.py": "eval dispatch",
    "evals/scorer.py": "the eval judge",
    "evals/judge_alignment.py": "the eval judge's alignment check",
    "mcp_sampling.py": "MCP sampling through the user's own client",
}


def _dispatch_refs(source: str) -> set[str]:
    """Any reference counts, not only a direct call: build_mode binds make_provider to a local factory first."""
    found = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Name):
            names = [n.id]
        elif isinstance(n, ast.Attribute):
            names = [n.attr]
        elif isinstance(n, ast.ImportFrom):
            names = [a.name for a in n.names]
        else:
            continue
        found |= {m for m in names if m in _DISPATCH}
    return found


def test_the_detector_sees_a_dispatch():
    assert _dispatch_refs("from .providers import make_provider\n") == {"make_provider"}
    assert _dispatch_refs("factory = provider_factory or P.make_provider\n") == {"make_provider"}
    assert _dispatch_refs("await session.create_message(messages=m)\n") == {"create_message"}
    assert _dispatch_refs("import json\nprovider = config.providers['claude']\n") == set()


def test_no_new_llm_call_site():
    scanned, found = 0, set()
    for p in sorted(SRC.rglob("*.py")):
        scanned += 1
        if _dispatch_refs(p.read_text(encoding="utf-8")):
            found.add(p.relative_to(SRC).as_posix())
    assert scanned > 100, scanned
    assert not found - set(INVENTORY), (f"a new LLM call site: {sorted(found - set(INVENTORY))}. LLM calls ride "
                                        "councils (AGENTS.md); review it and list it in INVENTORY with its reason.")
    assert set(INVENTORY) <= found, f"listed but no longer calling a model, drop them: {set(INVENTORY) - found}"
