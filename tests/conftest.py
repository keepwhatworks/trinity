"""Shared test fixtures for trinity-local."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest


def pytest_collection_modifyitems(config, items):
    """Two-tier test split (gstack pattern, 2026-05-27).

    Tests marked ``@pytest.mark.slow`` are skipped by default so
    ``pytest -q`` stays under a minute. Run the slow shard explicitly:

      TRINITY_SLOW=1 pytest -q       # run everything
      pytest -m slow                  # run only the slow shard
      pytest -m "not slow"            # explicit fast-only

    Without the env or an explicit ``-m`` selector, slow tests are
    deselected at collection time — same behavior as gstack's
    ``EVALS=1`` gate. Treats retries as a non-determinism budget,
    not cover for real bugs (see gstack pattern #7 in the audit).
    """
    if os.environ.get("TRINITY_SLOW") == "1":
        return
    selected_marker = config.getoption("-m", default="") or ""
    if selected_marker:
        return
    skip_slow = pytest.mark.skip(
        reason="slow test (real Chrome / MLX / provider subprocess). "
        "Run with TRINITY_SLOW=1 or `pytest -m slow`."
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


_NM_MANIFEST_NAME = "local.trinity.capture.json"


def _real_native_messaging_manifests() -> list[Path]:
    """The developer's REAL Chrome/Edge native-messaging host manifests, from
    the actual home dir (Path.home() is still real at session-collection time).
    Best-effort — returns [] if the install module can't be imported."""
    try:
        from trinity_local.commands.install import _native_messaging_dirs

        return [
            d / _NM_MANIFEST_NAME
            for _label, d in _native_messaging_dirs(["chrome", "edge"])
            if not str(d).startswith("registry:")
        ]
    except Exception:
        return []


@pytest.fixture(autouse=True, scope="session")
def _protect_real_native_messaging_manifests():
    """Snapshot the user's real native-messaging host manifests before the test
    session and restore any that a test clobbers.

    Without this, a test that registers the capture host without isolating
    Path.home() overwrites the user's real Chrome manifest — pointing `path`
    at a now-deleted pytest tmp dir and `allowed_origins` at a fake extension
    id — silently killing browser capture until a reinstall (#265, found
    2026-05-30 when a stale May-29 test run had broken live capture). This
    makes `pytest` safe to run on a machine with Trinity installed.
    """
    snapshots: dict[Path, bytes] = {}
    for p in _real_native_messaging_manifests():
        try:
            if p.exists():
                snapshots[p] = p.read_bytes()
        except Exception:
            pass
    yield
    for p, data in snapshots.items():
        try:
            if not p.exists() or p.read_bytes() != data:
                p.write_bytes(data)  # a test clobbered it — heal silently
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _disable_cold_start_autoscan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block the MCP cold-start auto-scan from firing during tests.

    Without this, ``cold_start.is_cold_start()`` reads ``Path.home()`` to
    look for ``~/.claude``, ``~/.codex`` etc — i.e., the developer's
    real corpus — and would either scan it (slow + unexpected) or skip
    based on whatever happens to be on the machine (flaky). Tests opt
    in to cold-start behavior by clearing this var inside their own
    monkeypatch.
    """
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Create a temporary state directory mimicking trinity-local's ~/.trinity/ layout."""
    state = tmp_path / "trinity_home"
    # `watcher` was here historically but watcher_dir() retired
    # 2026-05-17 (see state_paths.py L217). The fixture kept creating
    # the dir long after — harmless but dead, and the post-launch
    # consistency loop caught it 2026-05-23.
    for sub in [
        "todos", "actions", "prompt_bundles", "council_outcomes",
        "task_sync", "portal_pages", "review_pages",
    ]:
        (state / sub).mkdir(parents=True)
    return state


@pytest.fixture
def patch_trinity_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch TRINITY_HOME env var so all state goes to temp."""
    home = tmp_path / "trinity_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("TRINITY_HOME", str(home))
    return home


def write_prompt_node(
    home: Path, prompt_id: str, text: str, *, provider: str = "claude"
) -> None:
    """Seed the real REJECTION transcript shape into the prompts index: a QUESTION
    turn (turn 0) followed by the REACTION turn ``prompt_id`` points at (turn 1),
    in one transcript.

    A rejection is [question] → [model answer] → [user reaction]. The lens
    extraction stores ``prompt_id`` = the REACTION node; the eval PROMPT is the
    prior user turn (the question, #316). So ``text`` goes on turn 0 — keeping
    ``item.prompt == text`` for callers — and turn 1 holds the reaction node that
    ``prompt_id`` resolves to (its text is irrelevant to the recovered prompt; it
    carries the provider attribution).

    Unifies the two byte-divergent copies that used to live in
    ``test_evals_builder.py`` and ``test_output_shape_smoke.py`` — the divergence
    that bit the #316 eval-unification work (the smoke copy lacked the
    ``provider`` kwarg). Module-level so both import it as
    ``from tests.conftest import write_prompt_node``.
    """
    from trinity_local.memory.schemas import PromptNode
    from trinity_local.memory.store import upsert_prompt_node

    tid = f"t_{prompt_id}"

    def _node(node_id: str, turn_index: int, node_text: str) -> PromptNode:
        return PromptNode(
            id=node_id,
            transcript_id=tid,
            provider=provider,
            source_path=f"/fake/{provider}.json",
            turn_index=turn_index,
            text=node_text,
            embedding=None,
            created_at="2026-05-01T10:00:00",
            timestamp="2026-05-01T10:00:00",
            preceding_assistant_text="",
            following_assistant_text="",
            themes=[],
        )

    upsert_prompt_node(_node(f"{prompt_id}__q", 0, text))  # turn 0 — the QUESTION
    upsert_prompt_node(  # turn 1 — the REACTION node prompt_id points at
        _node(prompt_id, 1, f"[reaction turn for {prompt_id}]")
    )


# ---------------------------------------------------------------------------
# Gemini CLI session fixtures
# ---------------------------------------------------------------------------

GEMINI_SESSION_MINIMAL: dict[str, Any] = {
    "sessionId": "gemini-test-001",
    "kind": "INTERACTIVE",
    "startTime": "2026-04-01T12:00:00Z",
    "lastUpdated": "2026-04-01T12:05:00Z",
    "messages": [
        {"type": "user", "content": "Explain Python generators", "timestamp": "2026-04-01T12:00:01Z"},
        {
            "type": "gemini",
            "content": "A generator is a special type of iterator...",
            "model": "gemini-2.5-pro",
            "timestamp": "2026-04-01T12:00:02Z",
            "tokens": {"input": 8, "output": 120},
        },
    ],
}

GEMINI_SESSION_WITH_TOOLS: dict[str, Any] = {
    "sessionId": "gemini-test-002",
    "kind": "INTERACTIVE",
    "startTime": "2026-04-01T13:00:00Z",
    "lastUpdated": "2026-04-01T13:10:00Z",
    "messages": [
        {"type": "user", "content": "List files in /tmp", "timestamp": "2026-04-01T13:00:01Z"},
        {
            "type": "gemini",
            "content": "Let me list the files for you.",
            "model": "gemini-2.5-flash",
            "timestamp": "2026-04-01T13:00:02Z",
            "toolCalls": [
                {"id": "tc1", "name": "list_directory", "args": {"path": "/tmp"}, "result": ["/tmp/foo"]},
            ],
        },
        {"type": "user", "content": "Thanks", "timestamp": "2026-04-01T13:00:03Z"},
        {
            "type": "gemini",
            "content": "You're welcome!",
            "model": "gemini-2.5-flash",
            "timestamp": "2026-04-01T13:00:04Z",
        },
    ],
}


@pytest.fixture
def gemini_session_file(tmp_path: Path) -> Path:
    """Write a minimal Gemini CLI session JSON file."""
    path = tmp_path / "session-gemini-001.json"
    path.write_text(json.dumps(GEMINI_SESSION_MINIMAL), encoding="utf-8")
    return path


@pytest.fixture
def gemini_session_dir(tmp_path: Path) -> Path:
    """Set up a Gemini CLI session directory tree."""
    project = tmp_path / "my-project" / "chats"
    project.mkdir(parents=True)
    (project / "session-001.json").write_text(
        json.dumps(GEMINI_SESSION_MINIMAL), encoding="utf-8"
    )
    (project / "session-002.json").write_text(
        json.dumps(GEMINI_SESSION_WITH_TOOLS), encoding="utf-8"
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Claude Code session fixtures
# ---------------------------------------------------------------------------

CLAUDE_SESSION_LINES: list[dict[str, Any]] = [
    {
        "type": "user",
        "timestamp": "2026-04-02T10:00:00Z",
        "cwd": "/Users/test/project",
        "version": "1.0.30",
        "gitBranch": "main",
        "permissionMode": "auto",
        "message": {"content": "Fix the authentication bug"},
    },
    {
        "type": "assistant",
        "timestamp": "2026-04-02T10:00:05Z",
        "message": {
            "model": "claude-sonnet-4-20250514",
            "content": [
                {"type": "text", "text": "I'll fix the authentication bug."},
                {"type": "tool_use", "id": "tu1", "name": "write_file", "input": {"path": "auth.py", "content": "fixed"}},
            ],
            "usage": {
                "input_tokens": 150,
                "output_tokens": 80,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 0,
            },
        },
    },
    {
        "type": "assistant",
        "timestamp": "2026-04-02T10:00:10Z",
        "message": {
            "model": "claude-sonnet-4-20250514",
            "content": "The authentication bug has been fixed.",
            "usage": {"input_tokens": 200, "output_tokens": 30},
        },
    },
]


@pytest.fixture
def claude_session_file(tmp_path: Path) -> Path:
    """Write a minimal Claude Code JSONL session file."""
    path = tmp_path / "test-session-123.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for entry in CLAUDE_SESSION_LINES:
            f.write(json.dumps(entry) + "\n")
    return path


# ---------------------------------------------------------------------------
# Codex session fixtures
# ---------------------------------------------------------------------------

CODEX_SESSION_LINES: list[dict[str, Any]] = [
    {
        "type": "session_meta",
        "timestamp": "2026-04-03T14:00:00Z",
        "payload": {
            "id": "codex-session-001",
            "cwd": "/Users/test/codex-project",
            "cli_version": "0.3.2",
            "model_provider": "openai",
        },
    },
    {
        "type": "turn_context",
        "timestamp": "2026-04-03T14:00:01Z",
        "payload": {"model": "o3"},
    },
    {
        "type": "response_item",
        "timestamp": "2026-04-03T14:00:02Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": "Write a test for the auth module",
        },
    },
    {
        "type": "response_item",
        "timestamp": "2026-04-03T14:00:05Z",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": "Here's a test for the auth module...",
        },
    },
    {
        "type": "response_item",
        "timestamp": "2026-04-03T14:00:06Z",
        "payload": {
            "type": "function_call",
            "call_id": "fc1",
            "name": "write_file",
            "arguments": '{"path": "test_auth.py", "content": "def test_login(): pass"}',
        },
    },
]


@pytest.fixture
def codex_session_file(tmp_path: Path) -> Path:
    """Write a minimal Codex JSONL session file."""
    path = tmp_path / "rollout-codex-001.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for entry in CODEX_SESSION_LINES:
            f.write(json.dumps(entry) + "\n")
    return path


# ---------------------------------------------------------------------------
# Cowork session fixtures
# ---------------------------------------------------------------------------

COWORK_META: dict[str, Any] = {
    "sessionId": "cowork-session-001",
    "model": "claude-sonnet-4-20250514",
    "cwd": "/Users/test/cowork-project",
    "title": "Research quantum computing",
    "hostLoopMode": "agent",
    "processName": "Claude Desktop",
    "slashCommands": ["/code", "/search"],
    "remoteMcpServersConfig": [{"name": "puppeteer"}],
}

COWORK_AUDIT_LINES: list[dict[str, Any]] = [
    {
        "type": "user",
        "timestamp": "2026-04-04T09:00:00Z",
        "message": {"content": "Research quantum computing basics"},
    },
    {
        "type": "assistant",
        "timestamp": "2026-04-04T09:00:10Z",
        "message": {
            "model": "claude-sonnet-4-20250514",
            "content": "Quantum computing uses qubits...",
            "usage": {"input_tokens": 50, "output_tokens": 200},
        },
    },
]


@pytest.fixture
def cowork_session_dir(tmp_path: Path) -> Path:
    """Create a cowork session with metadata JSON and audit JSONL."""
    meta_path = tmp_path / "local_cowork-session-001.json"
    meta_path.write_text(json.dumps(COWORK_META), encoding="utf-8")
    session_dir = tmp_path / "local_cowork-session-001"
    session_dir.mkdir()
    audit_path = session_dir / "audit.jsonl"
    with audit_path.open("w", encoding="utf-8") as f:
        for entry in COWORK_AUDIT_LINES:
            f.write(json.dumps(entry) + "\n")
    return meta_path
