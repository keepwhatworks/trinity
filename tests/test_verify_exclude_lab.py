"""exclude_lab must accept what a caller naturally passes, and refuse garbage.

It compared the raw value against LAB names only, so exclude_lab="claude" --
the natural call from a Claude session -- matched nothing and was silently
ignored. The author's own lab then reviewed its own change: the exact thing the
flag exists to prevent. Reported 2026-09-28 by a peer session.
"""
from __future__ import annotations

import pytest

from trinity_local.verify import _resolve_lab


@pytest.mark.parametrize("given,lab", [
    ("anthropic", "anthropic"), ("claude", "anthropic"), ("Claude", "anthropic"),
    ("codex", "openai"), ("openai", "openai"), ("antigravity", "google"),
    ("google", "google"), (None, None), ("", None),
])
def test_labs_and_providers_both_resolve(given, lab):
    assert _resolve_lab(given) == lab


@pytest.mark.parametrize("bad", ["gpt", "gemini-3.8", "claud", "all"])
def test_an_unknown_value_is_an_error_never_a_no_op(bad):
    with pytest.raises(ValueError):
        _resolve_lab(bad)


def test_the_provider_name_actually_excludes_that_lab(monkeypatch, tmp_path):
    """Through read_panel itself: no read is dispatched to the excluded lab."""
    from trinity_local import verify
    from trinity_local.verify import Criterion
    dispatched = []

    def fake(name, prompt, cwd, config, effort):
        dispatched.append(name)
        raise RuntimeError("stop after recording")
    monkeypatch.setattr(verify, "_dispatch", fake)
    crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s", "blocking": True})]
    verify.read_panel(crit, "diff", "ctx", tmp_path, exclude_lab="claude")
    assert "claude" not in dispatched and dispatched, dispatched
