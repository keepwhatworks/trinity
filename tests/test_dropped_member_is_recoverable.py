"""A dropped member keeps its answer, so a wrong drop is repairable.

The substitution guard drops a member whose echoed model disagrees with the one
argv pinned. The first version set `output_text=""` and kept only the reason —
so the answer, which had been dispatched, generated and paid for, was gone.

Then the echo itself turned out to be wrong: claude's `modelUsage` lists every
model that ran in a turn, including a small background helper, and the
derivation took whichever appeared first. Twenty-one councils between
2026-09-12 19:48 and 2026-09-16 08:08 dropped a HEALTHY Fable member, and there
was nothing left to re-synthesize from. The only repair available was
re-dispatching every member of every council.

Keeping the text costs nothing and converts that class of incident from lost to
repairable.

The second class here is the one that must not regress: the preserved answer
may NOT reach the chairman or the vote count. A dropped member is dropped. The
text is for a human, or for a later re-synthesis that has decided the drop was
wrong — never for the council that dropped it.
"""
from __future__ import annotations

import inspect

from trinity_local import council_runner as CR


class TestTheAnswerIsPreserved:
    def test_the_failure_payload_carries_the_answer(self):
        src = inspect.getsource(CR)
        assert '"answer_text": _text' in src, (
            "a dropped member's answer must be preserved, or a wrong drop can only "
            "be repaired by re-dispatching every member")

    def test_it_also_records_how_much_was_preserved(self):
        src = inspect.getsource(CR)
        assert '"answer_chars"' in src, (
            "an empty preserved answer and a missing one must be distinguishable")

    def test_the_detail_says_it_is_recoverable(self):
        src = inspect.getsource(CR)
        assert "recoverable by" in src and "re-synthesis" in src, (
            "whoever reads this payload months later needs to know the text is there")


class TestTheDroppedAnswerStaysOutOfTheCouncil:
    """Preservation must not become participation."""

    def test_output_text_is_still_emptied(self):
        """AST, not a string slice. The first version of this test sliced source
        text around the substitution marker and matched an `output_text=""` from
        a DIFFERENT error path — so letting the dropped answer through to the
        chairman passed it. A test that can match the wrong line is not a guard.

        This finds the return statement that builds the substitution payload and
        asserts on ITS keyword."""
        import ast
        tree = ast.parse(inspect.getsource(CR))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
                continue
            kws = {k.arg: k.value for k in node.value.keywords if k.arg}
            payload = kws.get("error_payload")
            if not isinstance(payload, ast.Dict):
                continue
            reasons = [v.value for k, v in zip(payload.keys, payload.values)
                       if isinstance(k, ast.Constant) and k.value == "reason"
                       and isinstance(v, ast.Constant)]
            if "model_substitution" not in reasons:
                continue
            found.append(kws.get("output_text"))
        assert found, "could not locate the substitution return; the branch moved"
        for ot in found:
            assert isinstance(ot, ast.Constant) and ot.value == "", (
                "the substitution branch must pass output_text='' — a dropped member "
                "that reaches the chairman is not dropped, it is laundered")

    def test_the_member_is_still_recorded_as_failed(self):
        src = inspect.getsource(CR)
        assert "member_failures.append(execution.error_payload)" in src, (
            "the council must still report itself degraded")


class TestTheGuardStillFiresOnlyWhenItShould:
    def test_a_real_substitution_is_caught(self):
        from trinity_local.providers import is_model_substitution
        assert is_model_substitution("claude-fable-5-1", "claude-haiku-4-5") is True

    def test_a_background_helper_no_longer_trips_it(self):
        """The 2026-09-16 incident: the echo is now the model that produced the
        output tokens, so a helper listed first cannot masquerade as the
        answering model."""
        import json
        from trinity_local.providers import is_model_substitution, parse_claude_json
        payload = json.dumps({"result": "ok", "usage": {}, "modelUsage": {
            "claude-haiku-4-5": {"outputTokens": 0, "canonicalModel": "claude-haiku-4-5"},
            "claude-fable-5-1": {"outputTokens": 120, "canonicalModel": "claude-fable-5-1"}}})
        echo = parse_claude_json(payload)["model"]
        assert is_model_substitution("claude-fable-5-1", echo) is False
