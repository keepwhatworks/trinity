"""Cross-provider-paste provenance filter (the #296 provenance work, layer 1).

The founder pastes one model's answer in to compare across providers / get feedback
("Gemini:", "Gemini says this:", "feedback for ChatGPT:", "Claude's response"). That's
another model's VOICE, not the founder's authored taste, so it must not feed the lens.

The 8-experiment provenance investigation (2026-06-03) established: the structural #262
`pasted_fraction` filter is BLIND to pasted flowing prose (no code/markup), and a learned
embedding classifier can't separate a thoughtful pasted analysis from the founder's own
authoring. But the founder ANNOTATES these pastes with the model as speaker — a precise,
reward-free signal. This filter catches exactly that, as the high-precision layer of the
layered filter (attribution hard-drop + classifier + soft-weight).

PRECISION is the whole point: a genuine QUESTION about a model ("new releases from
ChatGPT's keynote?", "what does Gemini think?") is the founder's authored taste and MUST
survive. The speaker-form + paste-sized-body requirement is what separates the two.
"""
from __future__ import annotations

import pytest

from trinity_local.ingest import is_cross_provider_paste, is_user_facing_text

# Real shapes from the founder's corpus (2026-06-03 spot-check) — pasted agent responses.
PASTES = [
    "Gemini: who is Right? That statement from ChatGPT is a dangerous oversimplification of the build-vs-buy tradeoff and ignores the real constraints here",
    "Gemini says: No, that proposed language is not sufficient and contains several gaps a careful reviewer would flag immediately on first read",
    "Gemini says this: This is an excellent example of how complex and frankly misleading these comparisons can get when the framing is loaded",
    "Any feedback for Gemini: Vishi, thank you for sharing this. This exchange is a masterclass in how two strong models can disagree productively",
    "Send me feedback the chatGPT: Gemini's response is a tour de force, sharp and assertive, but it overstates the case in two specific places",
    "Here's what Gemini said: Let's cut straight to the chase. None of the architecture firms you listed actually do prefab at scale today",
    "ChatGPT's response to the margin question was solid but it missed the second-order effect on gross margin that compounds across quarters",
    "Claude's critique of the plan is sharp: the bounded-edit budget is the load-bearing idea and the gate is secondary until you have a reward",
]

# Genuine founder turns that mention/ask-about a model — authored taste, MUST survive.
KEEP = [
    "What are some new releases from ChatGPT's keynote yesterday?",
    "what does Gemini think about the prefab market and where it's heading",
    "i like sending the models response to other models to see if they converge",
    "fix the bug in this function that crashes on empty input",
    "Which companies do prefab floor wall and roof cassettes for residential builds?",
    "claude code keeps crashing when i run the loop, can you help me debug it",
    "should i use gpt-5.5 or gemini for this kind of strategic question?",
]


@pytest.mark.parametrize("text", PASTES)
def test_pasted_cross_provider_response_is_detected_and_dropped(text):
    assert is_cross_provider_paste(text), f"missed a pasted agent response: {text[:60]!r}"
    # and it must be dropped from the lens-bearing read paths
    assert not is_user_facing_text(text), f"pasted response leaked into the lens: {text[:60]!r}"


@pytest.mark.parametrize("text", KEEP)
def test_genuine_turn_about_a_model_survives(text):
    assert not is_cross_provider_paste(text), (
        f"FALSE POSITIVE — ate the founder's authored taste: {text[:60]!r}"
    )
    assert is_user_facing_text(text), f"genuine turn wrongly filtered: {text[:60]!r}"


def test_speaker_form_vs_topic_is_the_discriminator():
    """The exact distinction that makes it precise: a model as SPEAKER (paste) vs the
    same model as a TOPIC (authored question). Mutation: if the 's-form broadened to
    any possessive, "ChatGPT's keynote" would false-positive."""
    assert is_cross_provider_paste("Claude's response was: " + "x" * 60)      # speaker → drop
    assert not is_cross_provider_paste("What did you think of ChatGPT's keynote and roadmap announcement today?")  # topic → keep


def test_short_mention_is_not_a_paste():
    """A paste-sized body is required, so a terse mention can't be mistaken for a paste."""
    assert not is_cross_provider_paste("Gemini: yes")   # too short to be a pasted response
    assert not is_cross_provider_paste("ask gemini")


# ── video/audio transcript outros captured as user turns (#246 artifact family) ──
import pytest as _pytest

_OUTROS = [
    "Thank you for watching.",
    "Thanks for watching!",
    "Thank you for watching. Don't forget to subscribe.",
    "Please subscribe for more content like this.",
    "Thanks for listening.",
    "See you in the next video.",
]
_OUTRO_SURVIVORS = [
    "thanks for watching the demo — now wire the auth flow and run the tests",  # genuine, long
    "subscribe to the webhook events and log each one",                          # 'subscribe' as a verb
]


@_pytest.mark.parametrize("text", _OUTROS)
def test_video_outro_artifact_is_dropped(text):
    assert not is_user_facing_text(text), f"A/V outro leaked into the lens: {text!r}"


@_pytest.mark.parametrize("text", _OUTRO_SURVIVORS)
def test_genuine_turn_resembling_an_outro_survives(text):
    assert is_user_facing_text(text), f"false positive on a genuine turn: {text!r}"
