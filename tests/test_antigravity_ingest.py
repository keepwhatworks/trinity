"""#268 — ingest antigravity (agy) CLI transcripts.

agy writes per-conversation JSONL at
`~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`.
The parser must pull the user's `<USER_REQUEST>` text (not the injected
metadata tags), and the corpus filter must drop Trinity's own council-dispatch
and E2E prompts that land in agy's transcript because Trinity dispatches TO it.
"""
from __future__ import annotations

import json
from pathlib import Path


def _write_transcript(root: Path, conv_id: str, lines: list[dict]) -> Path:
    d = root / conv_id / ".system_generated" / "logs"
    d.mkdir(parents=True)
    p = d / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines))
    return p


class TestAntigravityParser:
    def test_extracts_user_request_strips_metadata(self, tmp_path):
        from trinity_local.ingest import parse_antigravity_session

        brain = tmp_path / "brain"
        p = _write_transcript(
            brain,
            "conv-aaaa",
            [
                {
                    "type": "USER_INPUT",
                    "created_at": "2026-05-30T18:00:00Z",
                    "content": "<USER_REQUEST>\nremove selenium, use stagehand\n</USER_REQUEST>\n"
                    "<ADDITIONAL_METADATA>\nThe current local time is: 2026-05-30T14:00:00-04:00.\n</ADDITIONAL_METADATA>",
                },
                {
                    "type": "PLANNER_RESPONSE",
                    "created_at": "2026-05-30T18:00:05Z",
                    "content": "Sure, swapping selenium for stagehand.",
                },
            ],
        )
        rec = parse_antigravity_session(p)
        assert rec is not None
        assert rec.provider == "antigravity"
        assert rec.session_id == "conv-aaaa"
        users = [m for m in rec.messages if m.role == "user"]
        assert len(users) == 1
        # The metadata tags are stripped — only the request body remains.
        assert users[0].text == "remove selenium, use stagehand"
        assert "ADDITIONAL_METADATA" not in users[0].text

    def test_no_user_turns_returns_none(self, tmp_path):
        from trinity_local.ingest import parse_antigravity_session

        brain = tmp_path / "brain"
        p = _write_transcript(
            brain, "conv-empty",
            [{"type": "CONVERSATION_HISTORY", "created_at": "2026-05-30T18:00:00Z"}],
        )
        assert parse_antigravity_session(p) is None

    def test_iter_finds_transcripts(self, tmp_path):
        from trinity_local.ingest import iter_antigravity_sessions

        brain = tmp_path / "brain"
        _write_transcript(brain, "c1", [
            {"type": "USER_INPUT", "content": "<USER_REQUEST>hi</USER_REQUEST>"}])
        _write_transcript(brain, "c2", [
            {"type": "USER_INPUT", "content": "<USER_REQUEST>bye</USER_REQUEST>"}])
        sessions = list(iter_antigravity_sessions(root=brain))
        assert {s.session_id for s in sessions} == {"c1", "c2"}


class TestCouncilAndE2ENoiseFiltered:
    def test_council_dispatch_and_e2e_dropped_real_kept(self):
        from trinity_local.ingest import SessionMessage, _is_user_facing_prompt

        drop = [
            "You are one member of a multi-model council.\n\nTask:\nExplain X",
            "Reply with exactly: TRINITY_AGY_E2E_OK",
            "respond with the word HELLO and nothing else",
        ]
        keep = [
            "what should i use gemini 3.5 flash vs opus 3.8?",
            "remove selenium. howabout using stagehand?",
            "make it better so it looks like a monkey not a blob",
        ]
        for t in drop:
            assert _is_user_facing_prompt(SessionMessage(role="user", text=t)) is False, t
        for t in keep:
            assert _is_user_facing_prompt(SessionMessage(role="user", text=t)) is True, t


class TestAntigravityInDefaultSources:
    def test_antigravity_is_a_default_source(self):
        from trinity_local.incremental_ingest import DEFAULT_SOURCES

        assert "antigravity" in DEFAULT_SOURCES

    def test_dispatch_wired(self):
        from trinity_local.watch_runtime import _parse_source_path, _source_root

        assert _source_root("antigravity").name == "brain"
        # _parse_source_path routes antigravity to the parser (no raise).
        import inspect

        src = inspect.getsource(_parse_source_path)
        assert "antigravity" in src and "parse_antigravity_session" in src
