"""install-reflex — the activation layer (council_de2451dca3203cf1, 2026-07-04).

MCP docstrings alone don't make agents use the tools (observed live: an
instructed agent ignored them for a week until a CLAUDE.md-level rule forced
the reflex). The command writes a versioned managed block into the
user-global CLAUDE.md; these tests pin the council's consent constraints:
surgical markers, idempotent re-runs, byte-preserved user content, clean
removal."""
from __future__ import annotations

import json
from types import SimpleNamespace

from trinity_local.commands.install import (
    REFLEX_BEGIN,
    REFLEX_END,
    handle_install_reflex,
)


def _args(tmp_path, **kw):
    return SimpleNamespace(path=str(tmp_path / "CLAUDE.md"),
                           remove=kw.get("remove", False))


def test_creates_file_with_block_when_missing(tmp_path, capsys):
    rc = handle_install_reflex(_args(tmp_path))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["targets"][0]["action"] == "created"
    body = (tmp_path / "CLAUDE.md").read_text()
    assert REFLEX_BEGIN in body and REFLEX_END in body
    # the two live gates, named: a plan gate and a pre-deploy gate
    assert "`run_council`" in body and "`verify`" in body
    assert "agreed_claims" in body


def test_appends_and_preserves_user_content(tmp_path, capsys):
    p = tmp_path / "CLAUDE.md"
    p.write_text("# My rules\n\nNever use tabs.\n", encoding="utf-8")
    handle_install_reflex(_args(tmp_path))
    body = p.read_text()
    assert body.startswith("# My rules")
    assert "Never use tabs." in body
    assert REFLEX_BEGIN in body


def test_rerun_is_idempotent_single_block(tmp_path, capsys):
    handle_install_reflex(_args(tmp_path))
    handle_install_reflex(_args(tmp_path))
    handle_install_reflex(_args(tmp_path))
    body = (tmp_path / "CLAUDE.md").read_text()
    assert body.count(REFLEX_BEGIN) == 1 and body.count(REFLEX_END) == 1


def test_remove_restores_user_content_exactly(tmp_path, capsys):
    p = tmp_path / "CLAUDE.md"
    p.write_text("# Mine\n\nkeep this.\n", encoding="utf-8")
    handle_install_reflex(_args(tmp_path))
    handle_install_reflex(_args(tmp_path, remove=True))
    body = p.read_text()
    assert REFLEX_BEGIN not in body and REFLEX_END not in body
    assert "keep this." in body and body.startswith("# Mine")


def test_remove_when_absent_is_a_clean_noop(tmp_path, capsys):
    rc = handle_install_reflex(_args(tmp_path, remove=True))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["targets"][0]["removed"] is False


def test_reflex_text_stays_within_the_council_word_budget():
    """The council's ≤80-word constraint on the reflex body — a bloated
    reflex block is context tax on EVERY session of every install."""
    from trinity_local.commands.install import REFLEX_TEXT
    words = len(REFLEX_TEXT.replace("## Trinity reflex", "").split())
    assert words <= 80, f"reflex text is {words} words (council budget: 80)"


def test_multi_harness_detection_and_the_live_gates(tmp_path, monkeypatch, capsys):
    """Cross-harness parity (council_8b5c845792aa1d1e): the reflex writes to
    every DETECTED harness (config dir exists) — Claude Code, Codex, Gemini —
    and never creates files for harnesses the user doesn't run. It used to
    teach `ask` and `choose`; both left the MCP surface, and the text kept
    teaching them to every harness on every reinstall."""
    import json
    from types import SimpleNamespace
    from trinity_local.commands.install import handle_install_reflex
    monkeypatch.setenv("HOME", str(tmp_path))
    import pathlib as _pl
    monkeypatch.setattr(_pl.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()   # detected
    # NO .gemini dir → must not be created
    rc = handle_install_reflex(SimpleNamespace(path=None, remove=False))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and len(out["targets"]) == 2
    claude_f = tmp_path / ".claude" / "CLAUDE.md"
    codex_f = tmp_path / ".codex" / "AGENTS.md"
    assert claude_f.exists() and codex_f.exists()
    assert not (tmp_path / ".gemini").exists(), "undetected harness must not be created"
    body = codex_f.read_text()
    assert "`verify`" in body and "`choose`" not in body
    assert body.count("trinity-local reflex") >= 1



def test_the_reflex_names_only_live_tools():
    """The reflex is written into three harnesses' instruction files. It named
    three retired tools for weeks, and the founder's CLAUDE.md grew a paragraph
    telling agents to ignore it -- the fix applied to the output, not the
    source. Every backticked name here must be registered in mcp_server and
    absent from the retirement registry."""
    import re
    from pathlib import Path
    from trinity_local import retired_names
    from trinity_local.commands.install import REFLEX_TEXT
    src = (Path(__file__).resolve().parent.parent
           / "src/trinity_local/mcp_server.py").read_text(encoding="utf-8")
    live = set(re.findall(r'name="([a-z_]+)"', src))
    retired = {n.split(":")[-1] for n in retired_names.names_by_kind("mcp_tool")}
    named = set(re.findall(r"`([a-z_]+)(?:\(|`)", REFLEX_TEXT))
    tools = named & (live | retired | {"ask", "choose"})
    assert tools, "the reflex names no tool at all"
    assert not (tools & retired), f"reflex names retired tools: {sorted(tools & retired)}"
    assert "choose" not in named, "choose is not an MCP tool"
    assert {"run_council", "verify"} <= tools <= live
