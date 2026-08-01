"""Shared test fixtures for trinity-local."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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


# ───────────────────────────────────────────────────────────────────────
# Measured-run snapshot (2026-07-31)
#
# The published test count used to be FABRICATED: scripts/render_docs.py
# ran `pytest --collect-only -q`, scraped "N tests collected", then
# subtracted a hardcoded 4 for "skipped". `--collect-only` never emits a
# skip summary (skips are a RUNTIME outcome), so the fallback constant
# always fired and every doc surface published `collected - 4` as
# "N tests passing + 4 skipped" — two numbers nothing had observed.
#
# Fix: the only place that KNOWS the run outcome is the run itself.
# This hook writes what the terminal summary actually counted; render_docs
# reads that file and REFUSES (raises) when it is missing, stale, or from
# a red run. No fallback constants — an unmeasured number is an error,
# not a default.
# ───────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = _REPO_ROOT / "tests"
RUN_SNAPSHOT_PATH = _REPO_ROOT / "test-run-snapshot.json"


def _is_whole_suite_run(config) -> bool:
    """True only when this invocation selects the DEFAULT whole suite.

    A partial run (``pytest tests/test_foo.py``, ``-k``, ``-m``, ``--lf``)
    must never overwrite the snapshot, or the published headline silently
    becomes "the 12 tests I happened to run". Explicit paths are allowed
    only when they name the repo root or ``tests/`` itself, because
    ``pytest tests/ -v`` is the documented dev command.
    """
    if getattr(config.option, "collectonly", False):
        return False  # nothing ran; there is no outcome to record
    for opt in ("keyword", "markexpr", "last_failed", "failed_first"):
        if getattr(config.option, opt, None):
            return False
    # Use config.args (pytest's PARSED positional paths), never
    # invocation_params.args (the raw argv). Walking raw argv and skipping
    # anything that starts with "-" misreads an OPTION'S VALUE as a test path:
    # `-p no:cacheprovider` made this function resolve "no:cacheprovider" as a
    # path, find it outside the repo root, and return False. That silently
    # disqualified the exact command the trinity-discipline skill documents as
    # the gate, so a red snapshot could never be cleared by running the
    # prescribed fix — the advice-closure failure this repo has a guard class
    # for. Any option taking a value (-p/-k/-m/-n/-c/-o/--rootdir) hit it.
    base = Path(getattr(config.invocation_params, "dir", _REPO_ROOT))
    for arg in getattr(config, "args", []) or []:
        candidate = Path(str(arg).split("::", 1)[0])
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return False
        if resolved not in (_REPO_ROOT, _TESTS_DIR):
            return False
    return True


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Record the OBSERVED outcome of a whole-suite run to disk.

    Written on green AND red runs — the file carries ``exit_status`` and
    ``failed``/``errors`` so the consumer can refuse a red run rather
    than the run being unable to report itself (which would deadlock:
    a stale-snapshot guard could never be cleared).
    """
    if not _is_whole_suite_run(config):
        return
    stats = terminalreporter.stats

    def _n(key: str) -> int:
        return len(stats.get(key, []))

    selected = int(getattr(terminalreporter, "_numcollected", 0))
    deselected = _n("deselected")
    snapshot = {
        # How the numbers were produced — a count is only meaningful
        # alongside the invocation that produced it.
        "invocation": "pytest -q (default shard)"
        if os.environ.get("TRINITY_SLOW") != "1"
        else "TRINITY_SLOW=1 pytest -q (full shard)",
        "trinity_slow": os.environ.get("TRINITY_SLOW") == "1",
        "exit_status": int(exitstatus),
        # What the terminal summary actually counted.
        "collected": selected + deselected,
        "selected": selected,
        "deselected": deselected,
        "passed": _n("passed"),
        "failed": _n("failed"),
        "errors": _n("error"),
        "skipped": _n("skipped"),
        "xfailed": _n("xfailed"),
        "xpassed": _n("xpassed"),
        "measured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    try:
        previous = json.loads(RUN_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception:
        previous = None
    if isinstance(previous, dict):
        # Keep the diff quiet: only rewrite when a NUMBER moved, so a
        # no-op re-run doesn't churn a tracked file with a new timestamp.
        comparable = {k: v for k, v in snapshot.items() if k != "measured_at"}
        if all(previous.get(k) == v for k, v in comparable.items()):
            return
    try:
        RUN_SNAPSHOT_PATH.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError:
        pass  # a read-only checkout must not fail the suite over bookkeeping


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
