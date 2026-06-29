"""The launchpad copy must not promise a user-pick / override / rating interaction.

The entire user-pick / veto / rating layer was removed (v1.7.342, founder direction:
"users never pick the model they like — they just chat without changing any of their
behavior"). The routing table now trains on the CHAIRMAN's pick automatically (the
do-operator fix, #260), NOT on a user click. So any UI copy that tells the user to
"override" the winner or that "that click trains the router" is describing a feature
that no longer exists — a credibility bug a new user hits on the cold-start launchpad
(found 2026-06-08 by a real-browser dogfood of the cold-start page).

This guard scans the launchpad template for the removed-mechanism language and asserts
the corrected, automatic-training copy is present instead — so a partial revert (drop
the fix but leave the phrase) is caught, not just a clean re-add.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "src" / "trinity_local" / "launchpad_template.py"

# User-ACTION phrasings of the removed pick/override/rating mechanism. Tightly scoped
# so legitimate "the router learns automatically" copy is fine — only the *user does
# the training by clicking/rating/overriding* framing is forbidden.
_FORBIDDEN = [
    r"you override",
    r"that click trains",
    r"your override",
    r"you rate\b",
    r"your rating",
    r"click to override",
    r"pick the winner you",  # "...pick the winner you prefer" style user-pick CTA
]


def test_launchpad_has_no_removed_user_pick_copy():
    text = TEMPLATE.read_text(encoding="utf-8")
    low = text.lower()
    hits = [pat for pat in _FORBIDDEN if re.search(pat, low)]
    assert not hits, (
        "launchpad_template.py contains removed user-pick/override/rating copy "
        f"{hits!r}. The user-pick layer was removed v1.7.342 — the router trains on "
        "the CHAIRMAN's pick automatically (#260). Describe automatic training, not a "
        "user click/override/rating."
    )


def test_council_card_describes_automatic_router_training():
    """Positive half (anti-vacuous, per the mutation-testing lesson): the corrected
    copy — the router sharpens AUTOMATICALLY, no clicks/ratings — must be present, so a
    revert that drops the fix but leaves the card is caught."""
    text = TEMPLATE.read_text(encoding="utf-8").lower()
    assert "sharpens the local router automatically" in text, (
        "the council card lost the corrected 'router sharpens automatically' copy"
    )
    assert "no clicks" in text and "you just chat" in text, (
        "the council card must say the training is automatic — no clicks/ratings, you just chat"
    )
