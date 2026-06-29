"""The popup's council-loading messages are a hand-mirror of the Python source
(`launchpad_data.COUNCIL_LOADING_MESSAGES`) — the popup has no page-data to read
them from, unlike the launchpad (which gets them via `pageData`). That mirror had
SILENTLY DRIFTED: popup.js carried only the first 8 generic lines and was missing
the 8 Trinity-specific ones, so the popup showed "Pushing pixels..." while a
council ran instead of "Weighing three opinions against your taste...". This guard
parses popup.js's array and pins it byte-for-byte to the Python source so it can't
drift again.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from trinity_local.launchpad_data import COUNCIL_LOADING_MESSAGES

POPUP_JS = Path(__file__).resolve().parents[1] / "browser-extension" / "popup.js"


def _popup_messages() -> list[str]:
    text = POPUP_JS.read_text(encoding="utf-8")
    m = re.search(r"const COUNCIL_LOADING_MESSAGES = \[(.*?)\];", text, re.DOTALL)
    assert m, "couldn't find COUNCIL_LOADING_MESSAGES array in popup.js"
    # Each entry is a JS double-quoted string literal; pull them in order.
    return [json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(1))]


def test_popup_loading_messages_match_python_source():
    assert _popup_messages() == list(COUNCIL_LOADING_MESSAGES), (
        "popup.js COUNCIL_LOADING_MESSAGES drifted from "
        "launchpad_data.COUNCIL_LOADING_MESSAGES — copy the Python list into "
        "browser-extension/popup.js verbatim (the launchpad reads them from "
        "pageData; the popup can't, so it mirrors them)."
    )


def test_council_review_reuses_the_single_python_source():
    """The live council page imports the SAME list (no third hand-copy)."""
    from trinity_local.council_review import LIVE_COUNCIL_LOADING_MESSAGES

    assert LIVE_COUNCIL_LOADING_MESSAGES is COUNCIL_LOADING_MESSAGES
