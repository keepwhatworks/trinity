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
        assert (_parse_votes(text, ["c"]) or (None, ""))[0] == {"c": False}, f"lost the vote: {label}"

    def test_a_pass_is_read_too(self):
        assert (_parse_votes('{"votes": {"c": "PASS"}, "why": ""}', ["c"]) or (None, ""))[0] == {"c": True}

    def test_multiple_criteria(self):
        t = '{"votes": {"a": "PASS", "b": "FAIL"}, "why": "b is wrong."} trailing {}'
        assert (_parse_votes(t, ["a", "b"]) or (None, ""))[0] == {"a": True, "b": False}


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
        assert (_parse_votes(t, ["c"]) or (None, ""))[0] == {"c": False}

    def test_truncated_with_escaped_quotes_in_why(self):
        t = '{"votes": {"c": "FAIL"}, "why": "it returns \\"x\\" instead of'
        assert (_parse_votes(t, ["c"]) or (None, ""))[0] == {"c": False}

    def test_a_pass_survives_truncation_too(self):
        assert (_parse_votes('{"votes": {"c": "PASS"}, "why": "looks right becau', ["c"]) or (None, ""))[0] == {"c": True}

    @pytest.mark.parametrize("label,text", [
        ("truncated INSIDE the vote object", '{"votes": {"c": "FA'),
        ("truncated before votes appears", '{"why": "a long preamble that never rea'),
        ("votes key present but value truncated", '{"votes": {'),
    ])
    def test_an_incomplete_vote_is_still_refused(self, label, text):
        assert _parse_votes(text, ["c"]) is None, f"invented a vote: {label}"


class TestTheReviewerReasoningIsPreserved:
    """The `why` was parsed and thrown away for the life of this module.

    The prompt has always asked for {"votes": ..., "why": ...}, every reviewer
    has always sent one, and the parser returned only the votes — so the
    concerns survived solely as `raw_tail`, an accidental last-300-characters
    slice of the raw response. Council 595c6e34, all three members: "Generic
    READ output does not justify the review burden" and "READ should surface
    concrete concerns tied to code."

    Preservation, not generation: the concerns already exist and already
    arrive. Discarding them and then asking a human to review blind is the
    worst trade this product makes.
    """

    def test_the_why_comes_back_with_the_votes(self):
        got = _parse_votes('{"votes": {"c": "FAIL"}, "why": "b is unguarded."}', ["c"])
        assert got is not None
        votes, why = got
        assert votes == {"c": False}
        assert why == "b is unguarded."

    def test_an_empty_why_is_empty_not_missing(self):
        votes, why = _parse_votes('{"votes": {"c": "PASS"}, "why": ""}', ["c"])
        assert votes == {"c": True} and why == ""

    def test_a_why_survives_trailing_prose(self):
        t = '{"votes": {"c": "FAIL"}, "why": "the bound is off by one."}\n\nNote: {x}'
        votes, why = _parse_votes(t, ["c"])
        assert why == "the bound is off by one."

    def test_a_truncated_envelope_still_yields_the_vote_even_without_a_why(self):
        """The vote is what the RULE needs; the why is what the HUMAN needs.
        Losing the second must never cost the first."""
        votes, why = _parse_votes('{"votes": {"c": "FAIL"}, "why": "it was cut off mid-sen', ["c"])
        assert votes == {"c": False}
        assert why == ""

    def test_a_non_string_why_is_ignored_rather_than_stringified(self):
        votes, why = _parse_votes('{"votes": {"c": "FAIL"}, "why": {"a": 1}}', ["c"])
        assert votes == {"c": False} and why == ""
