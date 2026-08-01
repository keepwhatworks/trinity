"""The (model, effort) pair survives all the way to the STORED PromptNode.

This is the consumer end of the contract PromptTurn asserts at the producer
end, and it is a separate file because the producer-asserted /
consumer-unverified split is this repo's most repeated defect shape: a field
the parser fills, the prompt asks for, or the UI dispatches, that the thing on
the other side silently drops. `PromptTurn.model` being right proves nothing
about what lands in `~/.trinity/prompts/prompt_nodes.jsonl` — there are TWO
independent PromptNode write sites (`ingest_helpers.flush_chunk` for the batch
lens build, `incremental_ingest.ingest_recent` for the tool-triggered pass) and
each has to be wired separately.

Mutation-proven 2026-07-31: deleting `model=turn.model, effort=turn.effort`
from either write site turns the matching test RED; before this file existed,
deleting it from ingest_helpers left the whole suite GREEN.

Every test here reads the node back through the real store, not the in-memory
object, so a to_dict()/from_dict() that dropped the field would also fail.
"""
from __future__ import annotations

import pytest

from trinity_local.incremental_ingest import ingest_recent
from trinity_local.ingest_helpers import flush_chunk, stage_session
from trinity_local.memory.store import iter_prompt_nodes, iter_prompt_nodes_no_embedding
from trinity_local.session_schema import PromptTurn, SessionMessage, SessionRecord


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
    return tmp_path


def _session_with_identity():
    """A claude-code-shaped session whose answer carries an explicit pair."""
    return SessionRecord(
        provider="claude",
        session_id="sess-identity-1",
        source_path="/fake/sess-identity-1.jsonl",
        native_id="sess-identity-1",
        model="claude-opus-5",
        effort="xhigh",
        messages=[
            SessionMessage(
                role="user",
                text="Should the ledger key on effort as well as version?",
                timestamp="2026-07-30T10:00:00Z",
            ),
            SessionMessage(
                role="assistant",
                text="Yes — effort-splitting is what separated the two GPT numbers.",
                timestamp="2026-07-30T10:00:20Z",
                model="claude-opus-5",
                effort="xhigh",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Write site 1 — ingest_helpers.stage_session + flush_chunk (batch lens build)
# ---------------------------------------------------------------------------

def test_flush_chunk_persists_model_and_effort(isolated_home):
    staged = stage_session(_session_with_identity(), set())
    assert staged is not None and not staged["already_indexed"]

    written, _, _ = flush_chunk([staged], set(), dim=16, batch_size=8)
    assert written == 1, "fixture must actually write a node or this test is vacuous"

    nodes = list(iter_prompt_nodes())
    assert len(nodes) == 1
    assert nodes[0].model == "claude-opus-5"
    assert nodes[0].effort == "xhigh"


def test_flush_chunk_leaves_an_unrecorded_identity_as_none(isolated_home):
    """The gemini case: a real node, no model, no invented default."""
    session = SessionRecord(
        provider="gemini",
        session_id="sess-gemini-1",
        source_path="/fake/gemini.json",
        native_id="sess-gemini-1",
        messages=[
            SessionMessage(role="user", text="What does the chairman decide?",
                           timestamp="2026-07-30T12:00:00Z"),
            SessionMessage(role="assistant", text="Which side survives.",
                           timestamp="2026-07-30T12:00:10Z"),
        ],
    )
    staged = stage_session(session, set())
    assert staged is not None
    written, _, _ = flush_chunk([staged], set(), dim=16, batch_size=8)
    assert written == 1

    nodes = list(iter_prompt_nodes())
    assert len(nodes) == 1
    assert nodes[0].model is None
    assert nodes[0].effort is None


def test_skinny_read_path_keeps_the_pair(isolated_home):
    """The SECOND read path, which does not go through json.loads intact.

    `iter_prompt_nodes_no_embedding` regex-substitutes the 768-float
    `"embedding":[...]` array out of each raw line before parsing it, for
    speed. That regex runs over the same line the model/effort keys live on,
    so a greedier pattern would silently eat every field written after the
    embedding — and the node would come back with model=None from a record
    that has one. Search / autofill read through this path, so it has to be
    asserted separately from `iter_prompt_nodes`.
    """
    staged = stage_session(_session_with_identity(), set())
    written, _, _ = flush_chunk([staged], set(), dim=16, batch_size=8)
    assert written == 1

    skinny = list(iter_prompt_nodes_no_embedding())
    assert len(skinny) == 1
    assert skinny[0].embedding == [], "fixture must exercise the strip, not bypass it"
    assert skinny[0].model == "claude-opus-5"
    assert skinny[0].effort == "xhigh"


# ---------------------------------------------------------------------------
# Write site 2 — incremental_ingest.ingest_recent (tool-triggered pass)
# ---------------------------------------------------------------------------

def test_ingest_recent_persists_model_and_effort(isolated_home, monkeypatch, tmp_path):
    path = tmp_path / "sess.jsonl"
    path.write_text("{}", encoding="utf-8")
    session = _session_with_identity()

    turn = PromptTurn(
        transcript_id="sess-identity-1",
        provider="claude",
        source_path=str(path),
        turn_index=0,
        text="Should the ledger key on effort as well as version?",
        timestamp="2026-07-30T10:00:00Z",
        model="claude-opus-5",
        effort="xhigh",
    )

    monkeypatch.setattr(
        "trinity_local.watch_runtime._iter_recent_paths",
        lambda source, since: iter([path]) if source == "claude" else iter([]),
    )
    monkeypatch.setattr(
        "trinity_local.watch_runtime._parse_source_path",
        lambda source, p: session if p == path else None,
    )
    monkeypatch.setattr(
        "trinity_local.incremental_ingest.iter_prompt_turns",
        lambda s: iter([turn]),
    )

    result = ingest_recent(sources=["claude"])
    assert result.added == 1, "fixture must write a node or this test is vacuous"

    nodes = list(iter_prompt_nodes())
    assert len(nodes) == 1
    assert nodes[0].model == "claude-opus-5"
    assert nodes[0].effort == "xhigh"


def test_ingest_recent_leaves_an_unrecorded_identity_as_none(
    isolated_home, monkeypatch, tmp_path
):
    path = tmp_path / "sess2.jsonl"
    path.write_text("{}", encoding="utf-8")
    session = _session_with_identity()
    turn = PromptTurn(
        transcript_id="sess-none-1",
        provider="gemini",
        source_path=str(path),
        turn_index=0,
        text="What does the chairman decide?",
        timestamp="2026-07-30T12:00:00Z",
    )

    monkeypatch.setattr(
        "trinity_local.watch_runtime._iter_recent_paths",
        lambda source, since: iter([path]) if source == "claude" else iter([]),
    )
    monkeypatch.setattr(
        "trinity_local.watch_runtime._parse_source_path",
        lambda source, p: session if p == path else None,
    )
    monkeypatch.setattr(
        "trinity_local.incremental_ingest.iter_prompt_turns",
        lambda s: iter([turn]),
    )

    assert ingest_recent(sources=["claude"]).added == 1
    nodes = list(iter_prompt_nodes())
    assert len(nodes) == 1
    assert nodes[0].model is None
    assert nodes[0].effort is None
