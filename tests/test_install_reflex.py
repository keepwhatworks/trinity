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
    assert rc == 0 and out["action"] == "created"
    body = (tmp_path / "CLAUDE.md").read_text()
    assert REFLEX_BEGIN in body and REFLEX_END in body
    # the cheap-first ladder is the whole lesson — all three rungs present
    assert 'ask(mode="route")' in body and "run_council" in body
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
    assert rc == 0 and out["removed"] is False


def test_reflex_text_stays_within_the_council_word_budget():
    """The council's ≤80-word constraint on the reflex body — a bloated
    reflex block is context tax on EVERY session of every install."""
    from trinity_local.commands.install import REFLEX_TEXT
    words = len(REFLEX_TEXT.replace("## Trinity reflex", "").split())
    assert words <= 80, f"reflex text is {words} words (council budget: 80)"
