"""Parser tests for transcript exports — claude.ai / ChatGPT export /
Gemini Takeout HTML. Originally landed in test_seed.py alongside the
seed-from-taste-terminal CLI round-trip; renamed to test_ingest_parsers.py
when that CLI retired 2026-05-27 (see retired_names.py). The parsers
themselves (in trinity_local.ingest) survive — they're shared by
import-export and the in-process ingest paths."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.ingest import (
    iter_prompt_turns,
    parse_chatgpt_export,
    parse_claude_ai_export,
    parse_gemini_takeout_html,
)


# ---------------------------------------------------------------------------
# Claude.ai webapp export parser
# ---------------------------------------------------------------------------

CLAUDE_AI_FIXTURE = [
    {
        "uuid": "conv-001",
        "name": "Routing thoughts",
        "summary": "",
        "created_at": "2025-03-13T20:36:46Z",
        "updated_at": "2025-03-13T22:03:49Z",
        "chat_messages": [
            {
                "uuid": "msg-001",
                "text": "list the s&p 100 stocks by their CAPE valuation",
                "content": [{"type": "text", "text": "list the s&p 100 stocks by their CAPE valuation"}],
                "sender": "human",
                "created_at": "2025-03-13T20:36:46Z",
            },
            {
                "uuid": "msg-002",
                "text": "Here are the top 10 ranked by Shiller CAPE...",
                "content": [{"type": "text", "text": "Here are the top 10 ranked by Shiller CAPE..."}],
                "sender": None,
                "created_at": "2025-03-13T20:36:55Z",
            },
        ],
    },
    {
        "uuid": "conv-002",
        "name": "",
        "summary": "Quick question",
        "created_at": "2025-04-01T00:00:00Z",
        "updated_at": "2025-04-01T00:00:00Z",
        "chat_messages": [
            {
                "uuid": "msg-101",
                "text": "what is 2+2",
                "content": [{"type": "text", "text": "what is 2+2"}],
                "sender": "human",
                "created_at": "2025-04-01T00:00:00Z",
            },
        ],
    },
]


@pytest.fixture
def claude_ai_export(tmp_path: Path) -> Path:
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(CLAUDE_AI_FIXTURE), encoding="utf-8")
    return path


class TestClaudeAIParser:
    def test_yields_one_session_per_conversation(self, claude_ai_export: Path):
        sessions = list(parse_claude_ai_export(claude_ai_export))
        assert len(sessions) == 2
        assert sessions[0].provider == "claude_ai"
        assert sessions[0].session_id == "conv-001"
        assert sessions[0].title == "Routing thoughts"

    def test_messages_parsed_correctly(self, claude_ai_export: Path):
        session = next(iter(parse_claude_ai_export(claude_ai_export)))
        assert len(session.messages) == 2
        assert session.messages[0].role == "user"
        assert session.messages[0].text == "list the s&p 100 stocks by their CAPE valuation"
        assert session.messages[1].role == "assistant"

    def test_iter_prompt_turns_yields_user_only(self, claude_ai_export: Path):
        sessions = list(parse_claude_ai_export(claude_ai_export))
        turns_for_first = list(iter_prompt_turns(sessions[0]))
        assert len(turns_for_first) == 1
        assert turns_for_first[0].text == "list the s&p 100 stocks by their CAPE valuation"
        assert turns_for_first[0].following_assistant_text.startswith("Here are")
        assert turns_for_first[0].provider == "claude_ai"


# ---------------------------------------------------------------------------
# ChatGPT webapp export parser
# ---------------------------------------------------------------------------

CHATGPT_FIXTURE = [
    {
        "id": "conv-gpt-1",
        "conversation_id": "conv-gpt-1",
        "title": "Stripe webhook for SaaS",
        "create_time": 1700000000.0,
        "update_time": 1700000100.0,
        "default_model_slug": "gpt-4",
        "current_node": "n3",
        "mapping": {
            "n0": {"id": "n0", "parent": None, "children": ["n1"], "message": None},
            "n1": {
                "id": "n1",
                "parent": "n0",
                "children": ["n2"],
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["I have a SaaS app and need a Stripe webhook"]},
                    "create_time": 1700000000.0,
                    "metadata": {"model_slug": "gpt-4"},
                },
            },
            "n2": {
                "id": "n2",
                "parent": "n1",
                "children": ["n3"],
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["Here's how to wire it up..."]},
                    "create_time": 1700000050.0,
                    "metadata": {"model_slug": "gpt-4"},
                },
            },
            "n3": {
                "id": "n3",
                "parent": "n2",
                "children": [],
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["thanks!"]},
                    "create_time": 1700000100.0,
                    "metadata": {"model_slug": "gpt-4"},
                },
            },
        },
    },
]


@pytest.fixture
def chatgpt_export(tmp_path: Path) -> Path:
    path = tmp_path / "conversations-000.json"
    path.write_text(json.dumps(CHATGPT_FIXTURE), encoding="utf-8")
    return path


class TestChatGPTParser:
    def test_walks_tree_to_linear_order(self, chatgpt_export: Path):
        sessions = list(parse_chatgpt_export(chatgpt_export))
        assert len(sessions) == 1
        s = sessions[0]
        assert s.provider == "chatgpt"
        assert s.title == "Stripe webhook for SaaS"
        assert s.model == "gpt-4"
        # n1 (user) → n2 (assistant) → n3 (user)
        assert len(s.messages) == 3
        assert [m.role for m in s.messages] == ["user", "assistant", "user"]
        assert s.messages[0].text.startswith("I have a SaaS app")

    def test_iter_prompt_turns_excludes_assistant(self, chatgpt_export: Path):
        sessions = list(parse_chatgpt_export(chatgpt_export))
        turns = list(iter_prompt_turns(sessions[0]))
        assert len(turns) == 2  # two user turns
        assert turns[0].text.startswith("I have a SaaS app")
        assert turns[0].following_assistant_text.startswith("Here's how to wire")

    def test_branch_walk_follows_current_node_excludes_abandoned(self, tmp_path: Path):
        """ChatGPT's `mapping` is a TREE, not a list: a regenerated response makes
        the user turn the parent of TWO assistant children, and `current_node`
        points at the active one. The parser must walk parent links from
        current_node (capturing only the live path), NOT iterate every node — or
        the abandoned regeneration leaks into the corpus as a phantom assistant
        turn (and switching to insertion-order on a fallback would double-count).
        The shipped fixture is linear, so this branch behaviour was unguarded;
        regenerating is one of the most common ChatGPT actions. Verified the walk
        excludes the abandoned branch 2026-06-06; this pins it."""
        conv = {
            "conversation_id": "c-branch",
            "title": "Regen",
            "create_time": 1700000000,
            "update_time": 1700000100,
            "current_node": "a_new",  # the regenerated (kept) answer
            "mapping": {
                "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
                "u1": {
                    "id": "u1", "parent": "root", "children": ["a_old", "a_new"],
                    "message": {"author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["my question"]},
                                "create_time": 1700000001},
                },
                "a_old": {
                    "id": "a_old", "parent": "u1", "children": [],
                    "message": {"author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["ABANDONED first answer"]},
                                "create_time": 1700000002},
                },
                "a_new": {
                    "id": "a_new", "parent": "u1", "children": [],
                    "message": {"author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["the regenerated answer"]},
                                "create_time": 1700000003},
                },
            },
        }
        path = tmp_path / "conversations.json"
        path.write_text(json.dumps([conv]), encoding="utf-8")
        sessions = list(parse_chatgpt_export(path))
        assert len(sessions) == 1
        texts = [m.text for m in sessions[0].messages]
        assert "the regenerated answer" in " ".join(texts), "the active (current_node) answer was dropped"
        assert all("ABANDONED" not in t for t in texts), (
            "the abandoned regeneration branch leaked into the corpus — the walk "
            "didn't follow current_node's parent chain"
        )
        # Exactly the live path: one user turn + one assistant turn, not three.
        assert [m.role for m in sessions[0].messages] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# Gemini Takeout HTML parser
# ---------------------------------------------------------------------------

GEMINI_FIXTURE_HTML = """<html><body>
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid"><div class="header-cell mdl-cell mdl-cell--12-col"><p class="mdl-typography--title">Gemini Apps<br></p></div><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Prompted <a href="https://gemini.google.com/app/abc123">Which version of Gemini live are you?</a><br>Apr 12, 2026, 3:34:31 PM EDT<p>I am Gemini 2.5 Pro, the latest preview release.</p></div><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div><div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption"><b>Products:</b><br>&emsp;Gemini Apps</div></div></div>
<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid"><div class="header-cell mdl-cell mdl-cell--12-col"><p class="mdl-typography--title">Gemini Apps<br></p></div><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">Prompted <a href="https://gemini.google.com/app/def456">summarize this article</a><br>Apr 13, 2026, 9:12:00 AM PDT<p>Here is a summary of the article...</p></div><div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 mdl-typography--text-right"></div><div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption"><b>Products:</b><br>&emsp;Gemini Apps</div></div></div>
</body></html>"""


@pytest.fixture
def gemini_takeout_html(tmp_path: Path) -> Path:
    path = tmp_path / "MyActivity.html"
    path.write_text(GEMINI_FIXTURE_HTML, encoding="utf-8")
    return path


class TestGeminiTakeoutParser:
    def test_yields_one_session_per_outer_cell(self, gemini_takeout_html: Path):
        sessions = list(parse_gemini_takeout_html(gemini_takeout_html))
        assert len(sessions) == 2
        assert all(s.provider == "gemini" for s in sessions)

    def test_extracts_prompt_response_and_timestamp(self, gemini_takeout_html: Path):
        sessions = list(parse_gemini_takeout_html(gemini_takeout_html))
        s = sessions[0]
        assert len(s.messages) == 2
        assert s.messages[0].role == "user"
        assert "Which version of Gemini" in s.messages[0].text
        assert s.messages[1].role == "assistant"
        assert "Gemini 2.5 Pro" in s.messages[1].text
        # Timestamp parsed from "Apr 12, 2026, 3:34:31 PM EDT"
        assert s.started_at is not None
        assert s.started_at.startswith("2026-04-12")

    def test_iter_prompt_turns_for_takeout(self, gemini_takeout_html: Path):
        sessions = list(parse_gemini_takeout_html(gemini_takeout_html))
        turns = list(iter_prompt_turns(sessions[0]))
        assert len(turns) == 1
        assert "Which version" in turns[0].text

    def test_multi_paragraph_response_is_not_truncated(self, tmp_path: Path):
        """Gemini answers are routinely multi-paragraph, and the activity cell
        carries each paragraph in its own <p>. A non-greedy `<p>(.*?)</p>`
        single-search captured ONLY the first paragraph, silently truncating
        the imported response (verified 2026-06-06: paras 2-3 dropped, prompt
        fine). The full model output must survive import — the parser joins all
        <p> blocks. Mutation: revert to a single `_re.search(<p>…</p>)` and this
        fails on paragraphs 2/3."""
        html = (
            '<html><body>'
            '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
            '<div class="mdl-grid">'
            '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
            'Prompted <a href="https://gemini.google.com/app/multi1">Explain entanglement</a><br>'
            'Apr 12, 2026, 3:34:31 PM EDT'
            '<p>Paragraph ONE of the answer.</p>'
            '<p>Paragraph TWO continues the thought.</p>'
            '<p>Paragraph THREE concludes.</p>'
            '</div>'
            '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
            '<b>Products:</b><br>&emsp;Gemini Apps</div>'
            '</div></div>'
            '</body></html>'
        )
        path = tmp_path / "MyActivity.html"
        path.write_text(html, encoding="utf-8")
        sessions = list(parse_gemini_takeout_html(path))
        assert len(sessions) == 1
        # messages[1] is the assistant response (messages[0] is the prompt).
        response = sessions[0].messages[1].text
        assert "Paragraph ONE" in response
        assert "Paragraph TWO" in response, "second paragraph dropped on import (truncation regression)"
        assert "Paragraph THREE" in response, "third paragraph dropped on import (truncation regression)"
        # The prompt must stay clean — not polluted by the response paragraphs.
        assert "Explain entanglement" in sessions[0].messages[0].text
        assert "Paragraph" not in sessions[0].messages[0].text

    def test_response_html_variants_dont_corrupt_the_prompt(self, tmp_path: Path):
        """The response is whatever the model RENDERED — attributed paragraphs
        (`<p class>`), `<ol>/<ul>` lists, or a `<p>`+list mix, not just bare
        `<p>`. A `<p>`-tag boundary missed all of these: with no bare `<p>`, the
        whole cell became the prompt, so the model's RESPONSE spilled into the
        user PROMPT the lens learns from (verified 2026-06-06 — prompt
        corruption, not just response loss). The timestamp-boundary split
        captures every form into the response and keeps the prompt clean.
        Mutation: revert to the `<p>(.*?)</p>` boundary and the list/`<p class>`
        response text reappears in the prompt → these fail."""

        def _cell(resp_html: str) -> str:
            return (
                '<html><body>'
                '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp"><div class="mdl-grid">'
                '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
                'Prompted <a href="https://gemini.google.com/app/v1">List the steps</a><br>'
                'Apr 12, 2026, 3:34:31 PM EDT' + resp_html +
                '</div>'
                '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
                '<b>Products:</b></div>'
                '</div></div></body></html>'
            )

        variants = {
            "attributed_p": ('<p class="response-text">The styled answer.</p>', ["The styled answer"]),
            "list_only": ('<ol><li>First step.</li><li>Second step.</li></ol>', ["First step", "Second step"]),
            "p_then_list": ('<p>Here are the steps:</p><ol><li>Alpha.</li><li>Beta.</li></ol>',
                            ["Here are the steps", "Alpha", "Beta"]),
        }
        for name, (resp_html, expected_fragments) in variants.items():
            path = tmp_path / f"MyActivity_{name}.html"
            path.write_text(_cell(resp_html), encoding="utf-8")
            sessions = list(parse_gemini_takeout_html(path))
            assert len(sessions) == 1, f"[{name}] expected one session"
            prompt = sessions[0].messages[0].text
            response = sessions[0].messages[1].text if len(sessions[0].messages) > 1 else ""
            assert prompt.strip() == "List the steps", (
                f"[{name}] the response leaked into the prompt: {prompt!r}"
            )
            for frag in expected_fragments:
                assert frag in response, f"[{name}] response dropped {frag!r}: {response!r}"


