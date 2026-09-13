"""A reviewer's vote must survive anything it writes around the JSON.

Found 2026-09-09 mid-experiment. The parser matched votes with a GREEDY span,
r'\\{.*"votes".*\\}' under re.S, which runs from the first brace in the output to
the last one anywhere in it. A reviewer that emitted its JSON and then one more
sentence containing a brace produced a span that was not valid JSON, and the
vote was silently dropped as unparseable.

Measured on hq_107: 17% of one lab's reads lost, and ASYMMETRICALLY — five of
the six dropped arms were the treatment arm, because a reviewer voting FAIL
writes a longer, more discursive `why` than one voting PASS. A parse failure
correlated with the condition under test is a measurement bug, not lost data:
it removes exactly the reads that carry the signal.
"""
from __future__ import annotations

import pytest

from trinity_local.verify import _parse_votes

GOOD = '{"votes": {"c": "FAIL"}, "why": "b is unguarded."}'


class TestItSurvivesWhatModelsActuallyWrite:
    @pytest.mark.parametrize("label,text", [
        ("clean", GOOD),
        ("trailing prose with a brace", GOOD + "\n\nNote: the dict {a: 1} is unaffected."),
        ("markdown fenced", "```json\n" + GOOD + "\n```"),
        ("preamble", "Here is my verdict:\n" + GOOD),
        ("a brace inside why", '{"votes": {"c": "FAIL"}, "why": "the literal {} is empty."}'),
        ("another object first", '{"note": "thinking"}\n' + GOOD),
        ("both sides", 'Let me think. {"scratch": 1}\n' + GOOD + "\nDone {}."),
    ])
    def test_the_vote_is_read(self, label, text):
        assert _parse_votes(text, ["c"]) == {"c": False}, f"lost the vote: {label}"

    def test_a_pass_is_read_too(self):
        assert _parse_votes('{"votes": {"c": "PASS"}, "why": ""}', ["c"]) == {"c": True}

    def test_multiple_criteria(self):
        t = '{"votes": {"a": "PASS", "b": "FAIL"}, "why": "b is wrong."} trailing {}'
        assert _parse_votes(t, ["a", "b"]) == {"a": True, "b": False}


class TestItStillRefuses:
    """Robustness must not become credulity: an unreadable vote is still None,
    because a fabricated vote is worse than a missing one."""

    @pytest.mark.parametrize("label,text", [
        ("no json at all", "I cannot judge this change."),
        ("empty", ""),
        ("a word that is not a vote", '{"votes": {"c": "MAYBE"}}'),
        ("votes is not an object", '{"votes": "PASS"}'),
        ("criterion missing", '{"votes": {"other": "PASS"}}'),
        ("json but no votes key", '{"why": "I refuse."}'),
        ("truncated json", '{"votes": {"c": "FA'),
    ])
    def test_returns_none(self, label, text):
        assert _parse_votes(text, ["c"]) is None, f"accepted garbage: {label}"

    def test_a_non_object_json_does_not_crash(self):
        assert _parse_votes("[1, 2, 3]", ["c"]) is None
        assert _parse_votes('"votes"', ["c"]) is None


class TestTruncatedResponses:
    """A vote that arrived must not be lost because the sentence after it didn't.

    Diagnosed 2026-09-09 from a banked raw_tail: a reviewer wrote a long `why`,
    the response was cut off mid-sentence, and the outer JSON object never
    closed — so the envelope failed to parse even though the vote was complete
    and sat at the front of it. Four reads were lost to this and read only as
    "unparseable vote"; the cause was invisible until the raw text was banked,
    and I guessed wrong at it twice before that.

    `votes` is first in the requested shape, so it survives a truncation that
    kills the envelope.
    """

    def test_truncated_after_the_vote_still_reads(self):
        t = '{"votes": {"c": "FAIL"}, "why": "The loop returns path when data.get('
        assert _parse_votes(t, ["c"]) == {"c": False}

    def test_truncated_with_escaped_quotes_in_why(self):
        t = '{"votes": {"c": "FAIL"}, "why": "it returns \\"x\\" instead of'
        assert _parse_votes(t, ["c"]) == {"c": False}

    def test_a_pass_survives_truncation_too(self):
        assert _parse_votes('{"votes": {"c": "PASS"}, "why": "looks right becau', ["c"]) == {"c": True}

    @pytest.mark.parametrize("label,text", [
        ("truncated INSIDE the vote object", '{"votes": {"c": "FA'),
        ("truncated before votes appears", '{"why": "a long preamble that never rea'),
        ("votes key present but value truncated", '{"votes": {'),
    ])
    def test_an_incomplete_vote_is_still_refused(self, label, text):
        assert _parse_votes(text, ["c"]) is None, f"invented a vote: {label}"
