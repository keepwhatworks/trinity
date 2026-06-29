"""Eager legacy-directory migration for upgrading users (found 2026-06-06).

The `tasks/`→`todos/` and `memory/`→`prompts/` directory renames were LAZY — they
fired only when something happened to call `tasks_dir()` / `prompts_dir()`. But
the launchpad reads the prompt index by DIRECT path
(`trinity_home()/"prompts"/prompt_nodes.jsonl`, anti-ghost-dir so the empty new
dir isn't created on every render), and `build_page_data` never calls those
accessors. The schema-migration runner (`run_migrations`) only migrated the
preference-acts ledger, NOT these dir renames. So an upgrading user who ran
`portal-html` first saw "0 prompts indexed" — their corpus stuck in the old
`memory/` dir, invisible until some other op touched the accessor.

Fix: `migrate_legacy_state_layout()` fires both renames eagerly, called from
`main._run_schema_migrations` on every CLI/MCP dispatch (before any render).
"""
from __future__ import annotations

from pathlib import Path


def test_migrate_legacy_state_layout_renames_both_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / ".trinity"))
    t = tmp_path / ".trinity"
    (t / "tasks").mkdir(parents=True)
    (t / "tasks" / "t1.json").write_text('{"id":"t1"}')
    (t / "memory").mkdir(parents=True)
    (t / "memory" / "prompt_nodes.jsonl").write_text('{"node_id":"p1","text":"x"}\n')

    from trinity_local.state_paths import migrate_legacy_state_layout, state_dir

    migrate_legacy_state_layout()
    assert (state_dir() / "todos" / "t1.json").exists(), "tasks/ did not migrate to todos/"
    assert (state_dir() / "prompts" / "prompt_nodes.jsonl").exists(), "memory/ did not migrate to prompts/"
    assert not (state_dir() / "tasks").exists()
    assert not (state_dir() / "memory").exists()


def test_eager_migration_makes_prompts_visible_to_launchpad(tmp_path, monkeypatch):
    """The user-facing symptom: the launchpad reads the prompt index by DIRECT
    path, so before the eager migration an upgraded home (corpus in legacy
    memory/) reads as promptsIndexed=False. After it, the corpus is visible.
    Mutation: drop migrate_legacy_state_layout from _run_schema_migrations →
    an upgrade + portal-html-first stays at "0 prompts indexed"."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / ".trinity"))
    t = tmp_path / ".trinity"
    (t / "memory").mkdir(parents=True)
    (t / "memory" / "prompt_nodes.jsonl").write_text(
        "\n".join(
            f'{{"node_id":"p{i}","text":"old corpus entry {i} here words"}}'
            for i in range(6)
        )
    )
    from trinity_local.launchpad_data import _embedder_status
    from trinity_local.state_paths import migrate_legacy_state_layout

    # Before: the direct-path read sees the (absent) new prompts/ dir.
    assert _embedder_status()["promptsIndexed"] is False
    migrate_legacy_state_layout()  # what _run_schema_migrations fires on dispatch
    assert _embedder_status()["promptsIndexed"] is True, (
        "after the eager migration an upgrading user's corpus must be visible "
        "on the launchpad"
    )


def test_idempotent_and_no_ghost_dirs_on_fresh_home(tmp_path, monkeypatch):
    """Fresh home with nothing legacy → no-op, and crucially NO empty ghost
    todos/ or prompts/ dirs created (the reason the launchpad read stays direct).
    Re-running is a no-op."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / ".trinity"))
    from trinity_local.state_paths import migrate_legacy_state_layout, state_dir

    migrate_legacy_state_layout()
    assert not (state_dir() / "todos").exists(), "must not mkdir an empty ghost todos/"
    assert not (state_dir() / "prompts").exists(), "must not mkdir an empty ghost prompts/"
    migrate_legacy_state_layout()  # second run — still a clean no-op


def test_startup_runner_fires_the_eager_migration():
    """Wiring guard: main._run_schema_migrations must call
    migrate_legacy_state_layout, else the eager fix silently regresses to lazy."""
    src = (
        Path(__file__).resolve().parent.parent
        / "src" / "trinity_local" / "main.py"
    ).read_text()
    body = src[src.find("def _run_schema_migrations"):src.find("def main(")]
    assert "migrate_legacy_state_layout()" in body, (
        "_run_schema_migrations must call migrate_legacy_state_layout() so the "
        "dir renames fire on startup, not lazily"
    )
