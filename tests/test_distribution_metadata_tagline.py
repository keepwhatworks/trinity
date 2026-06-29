"""Class guard: distribution METADATA must carry the current brand tagline and
never a retired one.

The bug class (found 2026-06-17 during the brand re-lead sweep): the flip to
"Ask all three. Keep what works." reached the markdown surfaces (README,
CLAUDE.md, the launch docs) and the live launchpad, but the STRUCTURED install
metadata still led with the retired "Own your taste." — the Chrome-extension
`manifest.json`, the plugin `marketplace.json`, and the plugin `plugin.json`.
Those `description` strings are exactly what a user reads in the Chrome Web Store
listing, the Claude Code plugin marketplace, and `gh repo view`, so a stale one
ships the OLD positioning to the install surface while every doc says the new
one.

`test_doc_count_consistency.py`'s brand guard pins the hero/sub in the MARKDOWN
surfaces; nothing pinned the JSON metadata. This is the missing half — the
root-cause guard for the whole class, not a fix for one file:

  • FORBID — no user-facing metadata description contains a retired tagline.
  • REQUIRE — every brand-carrying description contains the current tagline.

Mutation-provable in both directions: revert any description to "Own your taste."
and the forbid-check fails; strip the tagline and the require-check fails. The
checks are token-based + case-insensitive so natural in-sentence phrasing
("Trinity Local — ask all three, keep what works.") still passes, while the
retired brand is caught regardless of capitalization.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Retired brand taglines that must NEVER appear in shipped install metadata.
# (Markdown CHANGELOG / docs/historical keep them as the pivot record — those are
# NOT install metadata and are out of scope here.)
RETIRED_TAGLINES = ("own your taste",)

# The current tagline. Both tokens must be present (case-insensitive) so the
# guard accepts the exact "Ask all three. Keep what works." AND the natural
# in-sentence "ask all three, keep what works." variant.
CURRENT_TAGLINE_TOKENS = ("ask all three", "keep what works")


def _json_field(rel: str, *keys):
    """Load REPO/rel and walk the key/index path to a string field."""
    node = json.loads((REPO / rel).read_text(encoding="utf-8"))
    for k in keys:
        node = node[k]
    return node


# (human label, accessor) for every user-facing description string. Every one is
# FORBIDDEN from carrying a retired tagline.
def _all_description_fields():
    return [
        (
            "browser-extension/manifest.json :: description",
            lambda: _json_field("browser-extension/manifest.json", "description"),
        ),
        (
            ".claude-plugin/marketplace.json :: description",
            lambda: _json_field(".claude-plugin/marketplace.json", "description"),
        ),
        (
            ".claude-plugin/marketplace.json :: plugins[0].description",
            lambda: _json_field(".claude-plugin/marketplace.json", "plugins", 0, "description"),
        ),
        (
            "plugins/trinity-local/.claude-plugin/plugin.json :: description",
            lambda: _json_field(
                "plugins/trinity-local/.claude-plugin/plugin.json", "description"
            ),
        ),
    ]


# The subset that carries the brand tagline (the headline descriptions a user
# reads first). The nested marketplace plugin entry is a terse feature line, so
# it's forbid-only — it must not REGRESS to the old brand, but isn't required to
# repeat the tagline.
BRAND_CARRYING = {
    "browser-extension/manifest.json :: description",
    ".claude-plugin/marketplace.json :: description",
    "plugins/trinity-local/.claude-plugin/plugin.json :: description",
}


@pytest.mark.parametrize("label,accessor", _all_description_fields())
def test_no_retired_tagline_in_metadata(label, accessor):
    text = accessor()
    low = text.lower()
    hit = [t for t in RETIRED_TAGLINES if t in low]
    assert not hit, (
        f"{label} still carries a RETIRED brand tagline {hit!r}: {text!r}. "
        "The brand re-lead is 'Ask all three. Keep what works.' — distribution "
        "metadata (Web Store listing, plugin marketplace, gh repo view) must not "
        "ship the old positioning while the docs say the new one."
    )


@pytest.mark.parametrize(
    "label,accessor",
    [f for f in _all_description_fields() if f[0] in BRAND_CARRYING],
)
def test_current_tagline_present_in_brand_metadata(label, accessor):
    text = accessor()
    low = text.lower()
    missing = [t for t in CURRENT_TAGLINE_TOKENS if t not in low]
    assert not missing, (
        f"{label} is missing the current tagline token(s) {missing!r}: {text!r}. "
        "Brand-carrying install metadata must lead with 'Ask all three. Keep "
        "what works.' (both halves present, any capitalization/punctuation)."
    )


def test_brand_carrying_subset_is_a_real_subset():
    """Cheap structural guard: every BRAND_CARRYING label must be a real
    description field — a typo'd label would make the require-check silently
    cover nothing."""
    known = {label for label, _ in _all_description_fields()}
    unknown = BRAND_CARRYING - known
    assert not unknown, f"BRAND_CARRYING names non-existent fields: {unknown}"
