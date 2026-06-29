"""Tests for tool-triggered incremental ingest.

The module's job is small but its bounds matter: it must persist a cursor
across calls so a second pass doesn't re-walk the same files, it must
respect the deadline so it can't block MCP latency, and a parser
breakage on one file must not poison the whole batch.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trinity_local import incremental_ingest
from trinity_local.incremental_ingest import (
    IngestResult,
    _load_cursors,
    _save_cursors,
    ingest_recent,
)


class _FakeTurn:
    def __init__(
        self,
        transcript_id: str,
        turn_index: int,
        text: str,
        provider: str = "claude",
    ):
        self.transcript_id = transcript_id
        self.turn_index = turn_index
        self.text = text
        self.provider = provider
        self.source_path = f"/fake/{transcript_id}"
        self.timestamp = "2026-05-11T12:00:00Z"
        self.preceding_assistant_text = ""
        self.following_assistant_text = ""


class _FakeSession:
    """Minimal session shape the real parsers return."""


def _stub_iter_prompt_turns_factory(turns_per_path: dict[Path, list]):
    """Return a stub for `iter_prompt_turns(session)` that uses session-id
    threading to look up turns. We tag each fake session with the path so
    the stub can route correctly."""

    def _stub(session):
        path = getattr(session, "_path", None)
        return iter(turns_per_path.get(path, []))

    return _stub


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


class TestCursorPersistence:
    def test_load_empty_when_no_file(self, isolated_state):
        assert _load_cursors() == {}

    def test_round_trip(self, isolated_state):
        _save_cursors({"claude": 1234.5, "codex": 2345.6})
        loaded = _load_cursors()
        assert loaded == {"claude": 1234.5, "codex": 2345.6}

    def test_legacy_scalar_form_tolerated(self, isolated_state):
        # Older code may have written `{source: mtime}` directly.
        from trinity_local.state_paths import ingest_cursors_path

        path = ingest_cursors_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"claude": 1500.0}), encoding="utf-8")
        loaded = _load_cursors()
        assert loaded == {"claude": 1500.0}

    def test_atomic_replace_via_tmp(self, isolated_state):
        from trinity_local.state_paths import ingest_cursors_path

        _save_cursors({"claude": 100.0})
        # The .tmp file should be gone after the atomic rename completes
        path = ingest_cursors_path()
        tmp = path.with_suffix(path.suffix + ".tmp")
        assert path.exists()
        assert not tmp.exists()

    @pytest.mark.parametrize("bad", ["[1, 2, 3]", "42", '"corrupt"', "null"])
    def test_non_dict_cursors_json_degrades_not_crash(self, isolated_state, bad):
        """guard_shape_not_just_parse: a valid-JSON-but-NON-DICT cursors.json (a
        partial/concurrent write or a hand-edit leaving a list/scalar/null) must
        degrade to a fresh cursor — NOT crash `.items()` with an uncaught
        AttributeError. _load_cursors / _load_drained run on EVERY MCP tool call via
        ingest_recent; a crash here silently breaks ALL future incremental ingest
        until the file is hand-fixed. Mutation: drop the `isinstance(raw, dict)`
        guard in either loader → `.items()` raises → this reds."""
        from trinity_local.incremental_ingest import _load_drained
        from trinity_local.state_paths import ingest_cursors_path

        path = ingest_cursors_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bad, encoding="utf-8")
        # Both readers degrade to empty; neither raises.
        assert _load_cursors() == {}
        assert _load_drained() == {}
        # And the end-to-end prod path (runs on every MCP call) doesn't crash.
        ingest_recent(sources=["claude"], deadline_s=1.0)  # must not raise


class TestIngestRecent:
    def test_no_sources_no_errors(self, isolated_state, monkeypatch):
        # _iter_recent_paths returns nothing for an empty source.
        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            lambda source, since: iter([]),
        )
        result = ingest_recent(sources=["claude"])
        assert isinstance(result, IngestResult)
        assert result.scanned == 0
        assert result.added == 0
        # Cursor should still be written so we don't re-scan from zero.
        assert _load_cursors() == {"claude": 0.0}

    def test_appends_prompt_nodes_and_updates_cursor(self, isolated_state, monkeypatch, tmp_path):
        # Two fake transcripts with one turn each.
        path_a = tmp_path / "a.jsonl"
        path_b = tmp_path / "b.jsonl"
        path_a.write_text("{}", encoding="utf-8")
        path_b.write_text("{}", encoding="utf-8")

        session_a = _FakeSession()
        session_a._path = path_a
        session_b = _FakeSession()
        session_b._path = path_b

        turns_by_path = {
            path_a: [_FakeTurn("ta", 0, "How do I configure pytest?")],
            path_b: [_FakeTurn("tb", 0, "Why is my React state stale?")],
        }

        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            lambda source, since: iter([path_a, path_b]) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(
            "trinity_local.watch_runtime._parse_source_path",
            lambda source, path: (
                session_a if path == path_a
                else session_b if path == path_b
                else None
            ),
        )
        monkeypatch.setattr(
            "trinity_local.incremental_ingest.iter_prompt_turns",
            _stub_iter_prompt_turns_factory(turns_by_path),
        )

        result = ingest_recent(sources=["claude"])
        assert result.scanned == 2
        assert result.added == 2
        # Cursor updated to the max mtime of the scanned files
        cursors = _load_cursors()
        assert "claude" in cursors
        assert cursors["claude"] > 0

        # Second call should skip everything (cursor + de-dupe both gate)
        result2 = ingest_recent(sources=["claude"])
        # Cursor is now >= file mtimes so no paths are returned
        assert result2.added == 0

    def test_parser_breakage_isolated(self, isolated_state, monkeypatch, tmp_path):
        """A single broken parser must not stop the whole batch."""
        good_path = tmp_path / "good.jsonl"
        bad_path = tmp_path / "bad.jsonl"
        good_path.write_text("{}", encoding="utf-8")
        bad_path.write_text("garbage", encoding="utf-8")

        good_session = _FakeSession()
        good_session._path = good_path
        turns_by_path = {good_path: [_FakeTurn("good", 0, "Working turn")]}

        def _parse(source, path):
            if path == bad_path:
                raise ValueError("parser blew up")
            return good_session

        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            lambda source, since: iter([bad_path, good_path]) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(
            "trinity_local.watch_runtime._parse_source_path",
            _parse,
        )
        monkeypatch.setattr(
            "trinity_local.incremental_ingest.iter_prompt_turns",
            _stub_iter_prompt_turns_factory(turns_by_path),
        )

        result = ingest_recent(sources=["claude"])
        assert result.skipped_parse >= 1
        assert result.added == 1  # the good path still produced its node

    def test_deadline_respected(self, isolated_state, monkeypatch, tmp_path):
        """When we blow the deadline, we stop and persist what we have."""
        # Build many paths so the loop has more work than the deadline allows
        paths = [tmp_path / f"p{i}.jsonl" for i in range(50)]
        for p in paths:
            p.write_text("{}", encoding="utf-8")
        sessions = []
        turns_by_path = {}
        for i, p in enumerate(paths):
            s = _FakeSession()
            s._path = p
            sessions.append(s)
            turns_by_path[p] = [_FakeTurn(f"t{i}", 0, f"Turn {i} text")]

        def _slow_parse(source, path):
            time.sleep(0.05)  # 50ms per file × 50 files = 2.5s
            return sessions[paths.index(path)]

        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            lambda source, since: iter(paths) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(
            "trinity_local.watch_runtime._parse_source_path",
            _slow_parse,
        )
        monkeypatch.setattr(
            "trinity_local.incremental_ingest.iter_prompt_turns",
            _stub_iter_prompt_turns_factory(turns_by_path),
        )

        result = ingest_recent(sources=["claude"], deadline_s=0.2)
        assert result.deadline_hit is True
        assert result.scanned < len(paths)  # didn't get through them all
        assert result.took_ms <= 1500  # Some leeway but bounded

    def test_default_sources_when_none_given(self, isolated_state, monkeypatch):
        seen_sources = []

        def _stub(source, since):
            seen_sources.append(source)
            return iter([])

        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            _stub,
        )
        ingest_recent()
        # Should walk all four default sources
        assert set(seen_sources) == set(incremental_ingest.DEFAULT_SOURCES)

    def test_cli_handler_threads_args_and_prints_result(self, isolated_state, monkeypatch, capsys):
        """`trinity-local ingest-recent` is a thin wrapper around ingest_recent()
        but its job is concrete: thread --source / --deadline through, print the
        IngestResult shape as JSON. Without this test, a refactor to
        ingest_recent's kwargs could break the CLI silently."""
        import json
        from types import SimpleNamespace
        from trinity_local import incremental_ingest
        from trinity_local.commands.watch import handle_ingest_recent

        captured_args: dict = {}

        def stub_ingest(*, sources, deadline_s):
            captured_args["sources"] = sources
            captured_args["deadline_s"] = deadline_s
            return incremental_ingest.IngestResult(
                scanned=5,
                added=2,
                skipped_existing=3,
                sources=list(sources),
                took_ms=42,
            )

        monkeypatch.setattr(incremental_ingest, "ingest_recent", stub_ingest)
        # Also patch the bound reference in commands/watch.py (it does a
        # local `from ..incremental_ingest import ingest_recent` inside the
        # handler, so monkeypatch on the module is the canonical surface).

        handle_ingest_recent(SimpleNamespace(sources=["claude", "codex"], deadline=3.5))
        out = json.loads(capsys.readouterr().out)

        # Args were honored — not silently dropped via positional-kwarg drift.
        assert captured_args["sources"] == ["claude", "codex"]
        assert captured_args["deadline_s"] == 3.5
        # IngestResult.to_dict() shape is what the JSON output looks like.
        assert out["scanned"] == 5
        assert out["added"] == 2
        assert out["sources"] == ["claude", "codex"]

    def test_cli_handler_defaults_to_all_sources_when_empty(self, isolated_state, monkeypatch, capsys):
        """args.sources defaults to [] from argparse (action="append"). The
        handler must fan out to DEFAULT_SOURCES, not pass an empty list to
        ingest_recent (which would walk zero sources)."""
        from types import SimpleNamespace
        from trinity_local import incremental_ingest
        from trinity_local.commands.watch import handle_ingest_recent

        captured_sources = []

        def stub_ingest(*, sources, deadline_s):
            captured_sources.extend(sources)
            return incremental_ingest.IngestResult(sources=list(sources))

        monkeypatch.setattr(incremental_ingest, "ingest_recent", stub_ingest)
        handle_ingest_recent(SimpleNamespace(sources=[], deadline=10.0))
        capsys.readouterr()

        assert set(captured_sources) == set(incremental_ingest.DEFAULT_SOURCES)

    def test_dedupe_by_node_id(self, isolated_state, monkeypatch, tmp_path):
        """Calling twice with the same turn must not insert duplicates."""
        path = tmp_path / "p.jsonl"
        path.write_text("{}", encoding="utf-8")
        session = _FakeSession()
        session._path = path
        turn = _FakeTurn("same_transcript", 0, "stable text content")
        turns_by_path = {path: [turn]}

        # First call: cursor is 0 so file is returned. Make sure the file
        # mtime stays the same so the second call also returns it (we want
        # to test dedupe, not cursor gating).
        monkeypatch.setattr(
            "trinity_local.watch_runtime._iter_recent_paths",
            lambda source, since: iter([path]) if source == "claude" else iter([]),
        )
        monkeypatch.setattr(
            "trinity_local.watch_runtime._parse_source_path",
            lambda source, p: session,
        )
        monkeypatch.setattr(
            "trinity_local.incremental_ingest.iter_prompt_turns",
            _stub_iter_prompt_turns_factory(turns_by_path),
        )

        # Force cursor stays at zero so file is re-walked
        monkeypatch.setattr(
            "trinity_local.incremental_ingest._load_cursors",
            lambda: {},
        )
        # Disable the #216 drained-skip so the file is actually re-parsed and
        # the node-id dedupe path (not the cheaper drained-skip) is exercised.
        monkeypatch.setattr(
            "trinity_local.incremental_ingest._load_drained",
            lambda: {},
        )

        r1 = ingest_recent(sources=["claude"])
        r2 = ingest_recent(sources=["claude"])
        assert r1.added == 1
        # Second pass walks the same file, same turn → stable node_id → skip
        assert r2.added == 0
        assert r2.skipped_existing >= 1


def test_iter_recent_paths_includes_boundary_mtime(tmp_path, monkeypatch):
    """Review HIGH#4: the cursor boundary must be inclusive (>=). Batch-written
    files share an mtime; a strict `>` drops every sibling at the cursor's exact
    mtime after a deadline commits there. id-dedup makes the re-scan safe."""
    from trinity_local import watch_runtime

    root = tmp_path / "claude"
    root.mkdir()
    f = root / "a.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    import os
    os.utime(f, (1000.0, 1000.0))

    monkeypatch.setattr(watch_runtime, "_source_root", lambda source: root)
    # since_mtime == the file's exact mtime: must still be yielded (inclusive).
    paths = list(watch_runtime._iter_recent_paths("claude", 1000.0))
    assert f in paths, "boundary-mtime file dropped — strict > would lose it"


def test_empty_embedding_does_not_shadow_real(tmp_path, monkeypatch):
    """Review HIGH#5: an empty-embedding record must not shadow an earlier
    fully-embedded record with the same id (append-upsert latest-wins)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.memory.store import _iter_jsonl_latest_by_id, prompt_nodes_path

    path = prompt_nodes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": "n1", "embedding": [0.1, 0.2, 0.3], "text": "real"}) + "\n"
        + json.dumps({"id": "n1", "embedding": [], "text": "cheap"}) + "\n",
        encoding="utf-8",
    )
    records = {r["id"]: r for r in _iter_jsonl_latest_by_id(path, protect_field="embedding")}
    assert records["n1"]["embedding"] == [0.1, 0.2, 0.3], "empty embedding shadowed the real one"
    # Without the guard, latest-wins would keep the empty embedding.
    plain = {r["id"]: r for r in _iter_jsonl_latest_by_id(path)}
    assert plain["n1"]["embedding"] == []  # confirms the guard is what protects it


def test_drained_boundary_file_skipped_when_unchanged(tmp_path, monkeypatch):
    """#216: a fully-drained boundary file at the cursor mtime must not be
    re-parsed on the next call while it's unchanged (the cost of the inclusive
    `>=` boundary). A size change re-includes it."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))  # isolate cursors.json + store
    from trinity_local import incremental_ingest as ii
    from trinity_local import watch_runtime

    f = tmp_path / "t.jsonl"
    f.write_text("{}\n", encoding="utf-8")

    # _iter_recent_paths + _parse_source_path are imported into watch_runtime;
    # iter_prompt_turns is a module attr of incremental_ingest.
    monkeypatch.setattr(watch_runtime, "_iter_recent_paths",
                        lambda source, since: iter([f]) if source == "claude" else iter([]))
    monkeypatch.setattr(watch_runtime, "_parse_source_path", lambda source, p: _FakeSession())
    parse_calls = {"n": 0}
    def _count_turns(session):
        parse_calls["n"] += 1
        return []
    monkeypatch.setattr(ii, "iter_prompt_turns", _count_turns)

    ii.ingest_recent(sources=["claude"], deadline_s=5.0)
    first = parse_calls["n"]
    assert first == 1, "first run should parse the file"

    # Second call, file unchanged → drained-skip kicks in, no re-parse.
    ii.ingest_recent(sources=["claude"], deadline_s=5.0)
    assert parse_calls["n"] == first, "unchanged boundary file was re-parsed"

    # Grow the file → size changes → re-parsed.
    f.write_text("{}\n{}\n", encoding="utf-8")
    ii.ingest_recent(sources=["claude"], deadline_s=5.0)
    assert parse_calls["n"] == first + 1, "grown file should be re-parsed"


def test_review_command_injects_reconciled_model(tmp_path, monkeypatch):
    """#217: review.py must inject the authoritative config.model (which the
    v1.7.40 loader strips out of command/args) so the reviewer doesn't run on
    the CLI default."""
    import json as _json
    from trinity_local.commands.review import _reviewer_command_for

    cfg = tmp_path / "config.json"
    cfg.write_text(_json.dumps({
        "providers": {
            "codex": {"type": "codex", "command": ["codex", "--quiet"],
                      "args": ["--model", "gpt-5.3-codex"]},
            "antigravity": {"type": "antigravity", "command": ["agy", "-p"],
                            "model": "Gemini 3.1 Pro (high)"},
        },
    }), encoding="utf-8")

    codex_cmd = _reviewer_command_for(reviewer="codex", config_path=str(cfg))
    assert "--model" in codex_cmd and "gpt-5.3-codex" in codex_cmd
    assert codex_cmd[0] == "codex"  # binary stays first

    # antigravity has no --model flag → never injected.
    agy_cmd = _reviewer_command_for(reviewer="antigravity", config_path=str(cfg))
    assert "--model" not in agy_cmd


def test_real_end_to_end_claude_session_ingest(tmp_path, monkeypatch):
    """Integration guard (2026-06-01): every test above MOCKS _parse_source_path /
    _iter_recent_paths, so the REAL pipeline — the `~/.claude/projects/**/*.jsonl`
    glob, parse_claude_code_session, cursor persistence, and stable_id dedup — is
    never exercised end to end. This drives it against a real synthetic session via
    HOME isolation (ingest_recent appends WITHOUT embeddings, so no embedder).

    Covers the three states a live install hits: a fresh ingest indexes the user
    turn; a re-run adds nothing (idempotent); a GROWN session (an ongoing
    conversation that gains a turn) ingests only the new turn and dedups the old.
    A regression in the real glob/parser/cursor stays green in the mocked units
    but silently breaks corpus freshness on every MCP `ask`."""
    import os

    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "proj"
    proj.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRINITY_HOME", str(home / ".trinity"))

    def _jsonl(*entries):
        return "".join(json.dumps(e) + "\n" for e in entries)

    sess = proj / "s1.jsonl"
    sess.write_text(
        _jsonl(
            {"type": "user", "sessionId": "s1", "timestamp": "2024-06-01T10:00:00Z",
             "message": {"role": "user", "content": "structure a floor-plan engine"}},
            {"type": "assistant", "sessionId": "s1", "timestamp": "2024-06-01T10:00:05Z",
             "message": {"role": "assistant", "model": "claude-opus-4",
                         "content": [{"type": "text", "text": "Start with RoomZones."}]}},
        ),
        encoding="utf-8",
    )

    from trinity_local.state_paths import prompts_dir

    def node_texts():
        p = prompts_dir() / "prompt_nodes.jsonl"
        return [json.loads(l)["text"] for l in p.read_text().splitlines()] if p.exists() else []

    ingest_recent(sources=["claude"])
    assert node_texts() == ["structure a floor-plan engine"], "fresh ingest must index the user turn"

    ingest_recent(sources=["claude"])
    assert node_texts() == ["structure a floor-plan engine"], "re-run must not duplicate"

    # Grow the session (ongoing conversation gains a turn). Bump mtime well past
    # the cursor so the test is deterministic on coarse-resolution filesystems.
    with sess.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "user", "sessionId": "s1", "timestamp": "2024-06-01T10:10:00Z",
            "message": {"role": "user", "content": "add a Deck zone"},
        }) + "\n")
    future = time.time() + 60
    os.utime(sess, (future, future))

    ingest_recent(sources=["claude"])
    assert node_texts() == ["structure a floor-plan engine", "add a Deck zone"], (
        "a grown session must ingest only the new turn and dedup the old"
    )
