"""#248: _is_user_facing_prompt catches the scaffolding classes that slipped
the corpus-purity floor and concentrated into near-pure junk basins —
AGENTS.md / <environment_context> dumps, Trinity's own third-person extraction
prompts, and slash-command skill bodies. Plus: basin clustering dedups exact
texts so a prompt repeated 100s of times isn't a pseudo-cluster.
"""
from __future__ import annotations

import pytest

from trinity_local.ingest import _is_user_facing_prompt
from trinity_local.session_schema import SessionMessage


def _filtered(text: str) -> bool:
    """True when the text is REJECTED as non-user (scaffolding)."""
    return not _is_user_facing_prompt(SessionMessage(role="user", text=text))


SCAFFOLDING = [
    "# AGENTS.md instructions for /Users/example/projects/trinity-local\n\n<INSTRUCTIONS>",
    "<environment_context>\n  <cwd>/Users/example</cwd>\n  <shell>zsh</shell>",
    "Find the idiosyncratic words and frames this human introduces.\n\nDO NOT include",
    "Find structural analogies the human draws BETWEEN UNRELATED DOMAINS.",
    "Find places where the human was WRONG, accepted the correction, and updated.",
    "Find OPEN LOOPS in this session — explicit forward-looking commitments the user made",
    "Compose a 4–6 paragraph TASTE PROFILE about this person, in third person.",
    "# /loop — schedule a recurring or self-paced prompt\n\nParse the input",
    "# /council — launch a council\n\nDispatch members",
    # Agent-ops / dispatch-test / automation control (#252) — not human taste.
    "respond with the word HELLO and nothing else",
    "respond with the single word OK and nothing else",
    "output only the word DONE",
    "continue with the plan if currently paused. do actual work",
    "continue from where you left off.",
    "proceed with the plan",
    # The autonomous-loop DRIVER prompt — a `/loop` cron fires this verbatim every
    # interval; it's harness automation, never the human's taste, yet lands
    # role=user once per firing (measured 2026-06-02: 14 corpus copies + 150
    # prompt turns across 15 recent loop sessions, growing each firing).
    "start new end to end flow to test the prod setup in various environments if "
    "currently paused. do actual work per what needs to be tested without skipping "
    "or doing trivial tasks and actually test all functionality in the browser. "
    "Improve usefulness.",
    # A harness echoing the user's lens back into its prompt preamble — the
    # "generator over generated" recursion. 40 such nodes were a live basin's
    # representative threads (2026-06-01 topology audit). Never human-authored.
    "The user has the following lens (cross-domain constraints they live by):\n\n  - infrastructure over features",
    # `you are …` / `you will …` system prompts — the highest-VOLUME scaffolding
    # on the antigravity source: `agy`'s transcript captures every council
    # member/chairman prompt Trinity dispatches to Gemini, and 93 of 238 sessions
    # (39%, measured 2026-06-01) are exactly these. If the `you are ` gate
    # regressed, Trinity's OWN council scaffolding would flood the founder's lens
    # as if they'd typed it (#268 self-pollution). The binding pattern was
    # untested until now.
    "You are one member of a multi-model council.\n\nTask:\nExplain the difference between a list and a tuple in Python.",
    "You are the primary council synthesizer for a SPECIFIC user. Your job is to pick the answer that best fits THIS user.",
    "You will act as a strict code reviewer. Flag every bug.",
    # Claude Code injects its own tool-use / session machinery as role=user —
    # these arrive in EVERY agent session (incl. the autonomous loop's own) and
    # would poison rejections.jsonl / vocabulary if indexed as the user's voice.
    "<task-notification>\n<task-id>abc123</task-id>\n<status>completed</status>\n</task-notification>",
    "<system-reminder>The task tools haven't been used recently.</system-reminder>",
    # Trinity's own dispatch/E2E sentinels reaching a provider CLI as role=user.
    "reply with exactly: TRINITY_AGY_E2E_OK",
    # gemini.google.com's adapter over-captures Gemini's internal batchexecute
    # config/telemetry RPCs as if they were chat. For some, the extracted
    # user_text is a `c_<hex>` conversation id; 10 reached the corpus as role=user
    # prompts (measured 2026-06-01). A hex conversation id is never a human
    # prompt — if this gate regressed, every config-RPC capture that slips the
    # empty-assistant-text skip would index its conversation hash as the founder's
    # voice. Untested until now.
    "c_087a73a78d0e878f",
    "c_cfa4ab2955280534",
    "C_FFC5FA2446DA491C",  # case-insensitive: capture casing varies
    # Claude Code's "[Request interrupted by user]" session marker AND its
    # "… for tool use]" variant — the closing-bracket-anchored form missed the
    # longer one, which leaked 94 corpus copies (2026-06-02).
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
    # Gemini's web adapter captures the page's ACTION-BUTTON labels as prompts
    # (same over-capture family as the c_<hex> ids). Top kept gemini-source
    # texts 2026-06-02: a human never types these exact UI strings standalone.
    "Used an Assistant feature",
    "Start research",
    "Generate Audio Overview",
    "Edit the research plan",
    # Per-SOURCE harness blocks the aggregate audit missed (2026-06-02): each
    # source has its own dialect, all captured role=user as pure blocks.
    "<image name=[Image #1]>\n</image>\n<image name=[Image #2]>\n</image>",  # Codex
    "<turn_aborted>\nThe user interrupted the previous turn on purpose.\n</turn_aborted>",  # Codex
    "<goal_context>\nContinue working toward the active thread goal.\n</goal_context>",  # Codex/loop
    '<scheduled-task name="morning-status" file="/x/SKILL.md">\ndo the thing\n</scheduled-task>',  # Cowork
    "<uploaded_files>\n<file><file_path>/Users/x/legal_doc.pdf</file_path></file>\n</uploaded_files>",  # Cowork (PII paths)
    "[Image: source: /var/folders/2j/0h80kj8566n993ygnbzq8gn00000/T/x.png]",  # Claude Code "[image:" form
    "continue until the goal is done",  # Codex goal-loop driver
]

REAL_USER = [
    "find the bug in this function that crashes on empty input",
    "find me a good standing desk under $400",
    "compose a birthday poem for my daughter about ice princesses",
    "list the steps to deploy a Next.js app to Vercel",
    "why is my smart bulb not responding to the switch?",
    "identify the load-bearing wall in this floor plan",
    "summarize this article about ocean warming in 3 bullets",
    # Real prompts that superficially resemble agent-ops but are genuine taste —
    # must survive (the filter matches the control SHAPE, not the lead word).
    "continue this story about the dragon",
    "respond to John about the meeting time",
    "continue",
    "ok",
    "output the json schema for the user model",
    "reply with your honest assessment of this plan",
    # "you are" must be START-anchored, not a substring — a genuine question that
    # merely mentions the phrase mid-sentence must survive (else the council-
    # dispatch guard above starts eating real prompts).
    "explain why you are seeing a 500 error on this endpoint",
    "the docs say you are supposed to call init() first — is that right?",
    # Precision for the autonomous-loop-driver filter: it requires BOTH the
    # "start new end to end flow" opener AND a "test the prod setup" co-occurrence,
    # so a genuine prompt that has only one must survive.
    "start a new end-to-end test flow for the checkout funnel",
    "can you test the prod setup for the new payments endpoint?",
    # Gemini UI-button filter is EXACT-match: a genuine prompt that begins with
    # the same words but carries an object must survive (the button label doesn't).
    "start research on quantum error correction",
    "edit the research plan to add a competitor analysis",
    "generate an audio overview of this paper for my commute",
    # startswith-precision for the per-source block filters: a genuine prompt that
    # merely MENTIONS the tag name mid-sentence must survive (only the literal
    # opening block is dropped).
    "what does the goal_context variable do in my codebase",
    "how do I handle uploaded_files in my flask route",
    "show me the image at line 50 of the render",
    "continue the migration until all tables are moved",
]


@pytest.mark.parametrize("text", SCAFFOLDING)
def test_scaffolding_is_rejected(text):
    assert _filtered(text), f"should reject scaffolding: {text[:60]!r}"


@pytest.mark.parametrize("text", REAL_USER)
def test_real_user_prompts_survive(text):
    # The imperative+third-person heuristic must not eat genuine "find/compose/
    # list/identify/summarize" user prompts that don't refer to the user in the
    # third person.
    assert not _filtered(text), f"should keep real user prompt: {text[:60]!r}"


def test_basin_clustering_dedups_identical_texts(monkeypatch):
    # 100 identical "continue" nodes must collapse to ONE clustering point so
    # they can't form a pseudo-cluster that dominates a basin (#248/#15).
    import trinity_local.me.basins as basins_mod

    class FakeNode:
        def __init__(self, nid, text, emb):
            self.id = nid
            self.text = text
            self.transcript_id = nid
            self.embedding = emb

    nodes = []
    # 100 identical-text nodes (same vector) + 30 distinct ones
    for i in range(100):
        nodes.append(FakeNode(f"dup{i}", "continue", [1.0, 0.0, 0.0]))
    for i in range(30):
        v = [0.0, float(i % 5), float(i)]
        nodes.append(FakeNode(f"uniq{i}", f"distinct prompt {i}", v))

    monkeypatch.setattr(basins_mod, "iter_prompt_nodes", lambda *a, **k: iter(nodes))
    monkeypatch.setattr(basins_mod, "is_finite_embedding", lambda e: bool(e))

    basins = basins_mod.compute_basins(k=5)
    total = sum(b.size for b in basins)
    # 100 "continue" dups → 1 point; 30 distinct → 30. Total clustering points
    # must be 31, NOT 130 — otherwise the dup mass dominates.
    assert total == 31, f"expected 31 deduped points, got {total}"


def test_basin_build_reapplies_user_facing_gate_at_read_time(monkeypatch):
    """#316: the basin build is a READ path over the append-only prompt index,
    which still holds nodes captured under an OLDER/weaker ingest filter. Stage 0
    (iter_turn_pairs) and agent search already re-apply `is_user_facing_text`, but
    compute_basins did NOT — so already-poisoned scaffolding (Trinity's own
    "The user has the following lens…" dispatch preamble, 40 such nodes on the
    real corpus) clustered into a near-pure scaffolding basin (b26, 71%), tripping
    the #248 concentration guard. compute_basins must now exclude it at read time.

    Mutation check: remove the `is_user_facing_text` gate in compute_basins and
    the poisoned ids reach a basin → this fails.
    """
    from types import SimpleNamespace

    from trinity_local.me.basins import compute_basins

    scaffold = (
        "The user has the following lens (cross-domain constraints they live by):\n"
        "  - infrastructure over interface"
    )
    real = [
        SimpleNamespace(
            id=f"real{i}", transcript_id=f"tr{i}",
            text=f"how should I structure the auth retry for case {i}",
            embedding=[1.0, 0.2, 0.0, 0.0],
        )
        for i in range(4)
    ]
    poisoned = [
        SimpleNamespace(
            id=f"pois{i}", transcript_id=f"tp{i}",
            text=scaffold,  # Trinity's own generated preamble, captured role=user
            embedding=[0.0, 0.0, 1.0, 0.3],
        )
        for i in range(4)
    ]
    monkeypatch.setattr(
        "trinity_local.me.basins.iter_prompt_nodes",
        lambda *a, **k: iter(real + poisoned),
    )
    basins = compute_basins(k=2)
    placed = {pid for b in basins for pid in (b.prompt_ids or [])}
    assert not (placed & {n.id for n in poisoned}), (
        "a 'The user has the following lens…' scaffolding node reached a basin — "
        "the basin build must re-apply is_user_facing_text (the b26 read-path leak)"
    )
    assert placed & {n.id for n in real}, "real prompts must still cluster"
