"""Routing aggregation + ingest role gate.

The routing table trains purely on the chairman's pick — the user-pick/verdict
layer was retired so the user just chats while the chairman decides. The ingest
role gate keeps every assistant/tool token out of the user's PromptNode corpus
("do not train on self-authored tokens as external evidence").
"""
from __future__ import annotations

from trinity_local.personal_routing import aggregate_routing_table


def _council(task_type, chairman, scores=None):
    # Provider scores are required for the task_type to register in the table
    # (the output loop iterates by_task_scores). Real councils always carry them.
    if scores is None:
        scores = {
            "claude": {"overall": 0.8},
            "codex": {"overall": 0.7},
            "antigravity": {"overall": 0.6},
        }
    return {
        "task_type": task_type,
        "routing_label": {"task_type": task_type, "provider_scores": scores},
        "chairman_winner": chairman,
    }


class TestChairmanPickDrivesRouting:
    def test_chairman_pick_counts_as_one_win(self):
        table = aggregate_routing_table([_council("code", chairman="codex")])
        wins = table["wins_per_task_type"]["code"]
        assert wins.get("codex") == 1

    def test_best_per_task_type_is_most_chairman_wins(self):
        councils = [
            _council("code", chairman="codex"),
            _council("code", chairman="codex"),
            _council("code", chairman="claude"),
        ]
        table = aggregate_routing_table(councils)
        assert table["best_per_task_type"]["code"] == "codex"
        wins = table["wins_per_task_type"]["code"]
        assert wins["codex"] == 2 and wins["claude"] == 1

    def test_chairman_pick_used_for_every_council(self):
        councils = [_council("code", chairman="codex") for _ in range(3)]
        table = aggregate_routing_table(councils)
        assert table["best_per_task_type"]["code"] == "codex"
        assert table["wins_per_task_type"]["code"]["codex"] == 3


class TestAssistantTokensNeverIngested:
    def test_assistant_and_tool_roles_rejected(self):
        from trinity_local.ingest import SessionMessage, _is_user_facing_prompt

        for role in ("assistant", "tool", "system"):
            msg = SessionMessage(role=role, text="a long substantive message " * 20)
            assert _is_user_facing_prompt(msg) is False, role

    def test_user_role_with_real_text_kept(self):
        from trinity_local.ingest import SessionMessage, _is_user_facing_prompt

        msg = SessionMessage(role="user", text="what should I do about the tax gain?")
        assert _is_user_facing_prompt(msg) is True

    def test_role_gate_is_the_first_check(self):
        """The role gate must be the FIRST line of the guard — defense in depth
        so no later branch can resurrect assistant text."""
        import inspect

        from trinity_local.ingest import _is_user_facing_prompt

        src = inspect.getsource(_is_user_facing_prompt)
        body = src.split(":", 1)[1]  # after the signature
        assert 'message.role != "user"' in body
        # It appears before any text-content inspection.
        assert body.index('message.role != "user"') < body.index("lowered")
