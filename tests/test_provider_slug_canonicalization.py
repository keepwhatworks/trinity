"""normalize_provider_slug must canonicalize the WEB-CAPTURE brand names,
not just the legacy 'gemini' alias.

Web-capture councils (Chrome extension on claude.ai / chatgpt.com /
gemini.google.com) recorded brand names on disk (chatgpt, claude_ai,
gemini) while CLI councils recorded slugs (codex, claude, antigravity).
That fragmented the same lab across two names in the 547 council
outcomes — the winner-distribution stat showed chatgpt 44% AND codex 5%
as if they were different providers. The load boundary already applies
normalize_provider_slug to winner/primary/member providers; the bug was
that _LEGACY_PROVIDER_ALIASES only knew 'gemini'. These guards pin the
brand→slug coverage so the fragmentation can't silently return.
"""
from __future__ import annotations

import pytest

from trinity_local.council_schema import normalize_provider_slug


@pytest.mark.parametrize("brand,slug", [
    ("chatgpt", "codex"),
    ("openai", "codex"),
    ("gpt", "codex"),
    ("claude_ai", "claude"),
    ("claude.ai", "claude"),
    ("anthropic", "claude"),
    ("gemini", "antigravity"),
    ("google", "antigravity"),
    ("bard", "antigravity"),
])
def test_brand_names_canonicalize_to_trio_slug(brand, slug):
    assert normalize_provider_slug(brand) == slug


@pytest.mark.parametrize("slug", ["claude", "codex", "antigravity"])
def test_canonical_slugs_pass_through(slug):
    assert normalize_provider_slug(slug) == slug


def test_unknown_and_nonstr_pass_through():
    assert normalize_provider_slug("ollama:llama3") == "ollama:llama3"
    assert normalize_provider_slug(None) is None


def test_no_lab_fragmentation_remains():
    """After normalization the only provider slugs that can appear are the
    canonical trio (plus pass-through locals) — never both a brand name and
    its slug for the same lab."""
    canonical = {normalize_provider_slug(n) for n in
                 ("chatgpt", "codex", "claude_ai", "claude", "gemini", "antigravity")}
    assert canonical == {"codex", "claude", "antigravity"}
