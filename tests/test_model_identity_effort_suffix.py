"""A model recorded WITH its effort suffix must keep its version.

Councils store the member as "claude-opus-5 (high)". `_version`'s bare-integer
branch was anchored with `$`, so the suffix pushed the version out of reach and
every such model parsed as version "?".

Measured 2026-08-16 on the live disagreement ledger: all 197 `claude · opus · ?`
rows are dated on or after 2026-07-24 — the exact day Opus 5 became the default
claude member. That was the LARGEST Claude bucket in the ledger, and its identity
had been erased, which is why the product's headline names Opus 4.8 while the
model it actually dispatches has no measured rate. `claude · fable · ? · high`
(42 rows) was the same bug.

Mutation-proven: restoring the `$` anchor REDs test_effort_suffix_keeps_version.
"""
from __future__ import annotations

import pytest

from trinity_local.model_identity import parse_identity


@pytest.mark.parametrize("raw,version", [
    ("claude-opus-5 (high)", "5"),
    ("claude-opus-5(high)", "5"),
    ("claude-fable-5 (high)", "5"),
    ("claude-opus-5", "5"),
])
def test_effort_suffix_keeps_version(raw, version):
    assert parse_identity(raw).version == version, f"{raw} lost its version"


def test_the_suffix_still_parses_as_effort():
    """Recovering the version must not cost the effort leg."""
    ident = parse_identity("claude-opus-5 (high)")
    assert ident.version == "5"
    assert ident.effort == "high"
    assert (ident.family, ident.tier) == ("claude", "opus")


@pytest.mark.parametrize("raw,version", [
    ("claude-opus-4-8", "4.8"),          # dash form still wins
    ("gpt-5.6-sol", "5.6"),              # dot form still wins
    ("gpt-5.6-luna (medium)", "5.6"),
    ("claude-opus-4-8 (high)", "4.8"),
])
def test_dotted_and_dashed_forms_are_unaffected(raw, version):
    """These never hit the bug: a dotted or dashed version matches before the
    bare-integer branch is reached, which is why `gpt-5.6-sol (high)` was always
    fine and the damage was confined to Anthropic's single-digit slugs."""
    assert parse_identity(raw).version == version


@pytest.mark.parametrize("raw,version", [
    ("gpt-6 (high)", "6"),
    ("gpt-6", "6"),
])
def test_a_future_single_digit_openai_slug_would_have_hit_the_same_bug(raw, version):
    """Not hypothetical housekeeping. The bare-integer branch is reached by ANY
    vendor whose version has no dot and no dash, so the next single-digit OpenAI
    model would have lost its version exactly as Opus 5 did. This case fails
    against the old anchor."""
    assert parse_identity(raw).version == version


def test_a_genuinely_versionless_name_still_reports_unknown():
    """The fix must not invent a version where none exists — a '?' that becomes
    a number is worse than a '?' that stays one."""
    assert parse_identity("opus").version == "?"
    assert parse_identity("claude (high)").version == "?"


@pytest.mark.parametrize("raw,tier", [
    ("gpt-5.6-sol", "flagship-sol"),
    ("gpt-5.6-sol (high)", "flagship-sol"),
    ("gpt-5.6-luna", "flagship-luna"),
    ("gpt-5.6-luna (medium)", "flagship-luna"),
])
def test_named_variants_do_not_share_a_cell(raw, tier):
    """res_037. sol and luna share version 5.6 and are NOT the same model — luna
    is this repo's resolver/extraction seat, sol the general flagship. Pooled,
    their win rates would average into a number describing neither. Fixed while
    the collision was still latent (zero 5.6 rows in the ledger), because after
    5.6 lands it is a re-key of every per-model number."""
    assert parse_identity(raw).tier == tier


def test_sol_and_luna_land_in_different_cells():
    a, b = parse_identity("gpt-5.6-sol (high)"), parse_identity("gpt-5.6-luna (high)")
    assert (a.family, a.tier, a.version) != (b.family, b.tier, b.version)


@pytest.mark.parametrize("raw,tier", [
    ("gpt-5.5", "flagship"),          # the incumbent cell must not move
    ("gpt-5.5 (xhigh)", "flagship"),
    ("gpt-4o-mini", "mini"),
    ("gpt-6", "flagship"),
])
def test_unnamed_models_keep_the_plain_flagship_cell(raw, tier):
    """The variant split must not re-key models that have no variant — gpt-5.5
    carries n=107 in the live ledger and moving it would silently restate a
    published number."""
    assert parse_identity(raw).tier == tier
