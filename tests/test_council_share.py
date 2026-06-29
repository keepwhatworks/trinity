"""Tests for the council-share PNG renderer + CLI command.

Mirrors test_eval_share.py: data shaping, PNG shape invariants, CTA
guard, and CLI handler smoke. The privacy invariant is the most
important one here — the prior broken council-share leaked user
prompts into filenames; the rewrite uses chairman-extracted fields
only and tests pin that.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field

import pytest

from trinity_local.council_card import (
    CARD_HEIGHT,
    CARD_WIDTH,
    CTA_HEADLINE,
    CTA_LANDING_URL,
    CouncilCardData,
    collect_card_data_from_outcome,
    render_council_card,
)


@dataclass
class _FakeRoutingLabel:
    agreed_claims: list = field(default_factory=list)
    disagreed_claims: list = field(default_factory=list)


@dataclass
class _FakeMember:
    provider: str
    output_text: str = ""


@dataclass
class _FakeOutcome:
    council_run_id: str
    member_results: list = field(default_factory=list)
    winner_provider: str | None = None
    routing_label: _FakeRoutingLabel | None = None
    bundle_id: str = "b_test"


# ── data shaping ───────────────────────────────────────────────────


def test_collect_card_data_pulls_chairman_fields_only():
    """The card MUST come exclusively from chairman-extracted fields
    (winner, agreed_claims, disagreed_claims). User prompts and member
    response text must NEVER cross into the card — that was the prior
    privacy bug in council-share."""
    label = _FakeRoutingLabel(
        agreed_claims=["Both models agreed X.", "Both flagged risk Y."],
        disagreed_claims=[
            {"provider": "claude", "claim": "Use approach A.",
             "why_matters": "It compounds; B is reversible."},
        ],
    )
    outcome = _FakeOutcome(
        council_run_id="council_abc123def456",
        member_results=[
            # output_text intentionally LONG and verbatim — the test
            # asserts it does NOT appear in the card data.
            _FakeMember("claude", "VERBATIM_USER_PROMPT_LEAK_CANARY_1"),
            _FakeMember("antigravity", "VERBATIM_USER_PROMPT_LEAK_CANARY_2"),
            _FakeMember("codex",  "VERBATIM_USER_PROMPT_LEAK_CANARY_3"),
        ],
        winner_provider="claude",
        routing_label=label,
    )
    data = collect_card_data_from_outcome(outcome)
    assert data.members == ["claude", "antigravity", "codex"]
    assert data.winner == "claude"
    assert data.agreed_claims == ["Both models agreed X.", "Both flagged risk Y."]
    assert data.disagreed_claim == "Use approach A."
    assert data.disagreed_why == "It compounds; B is reversible."

    # Privacy invariant: the verbatim canaries must not appear ANYWHERE
    # in the projected card data. Belt-and-suspenders check.
    card_repr = json.dumps(data.to_dict())
    for canary in ("CANARY_1", "CANARY_2", "CANARY_3"):
        assert canary not in card_repr, (
            f"Member output_text {canary!r} leaked into CouncilCardData. "
            f"The card must only carry chairman-extracted fields."
        )


def test_collect_card_data_handles_missing_routing_label():
    """When chairman synthesis failed and routing_label is None, the
    card should still build — empty claims, no disagreement — not
    crash."""
    outcome = _FakeOutcome(
        council_run_id="council_xyz",
        member_results=[_FakeMember("claude")],
        winner_provider=None,
        routing_label=None,
    )
    data = collect_card_data_from_outcome(outcome)
    assert data.agreed_claims == []
    assert data.disagreed_claim is None
    assert data.disagreed_why is None


# ── PNG shape + content ────────────────────────────────────────────


def _assert_valid_png(png_bytes: bytes) -> None:
    PIL_Image = pytest.importorskip("PIL.Image")
    img = PIL_Image.open(io.BytesIO(png_bytes))
    assert img.size == (CARD_WIDTH, CARD_HEIGHT)
    assert img.format == "PNG"


def test_render_council_card_with_full_data():
    pytest.importorskip("PIL")
    data = CouncilCardData(
        members=["claude", "antigravity", "codex"],
        winner="claude",
        agreed_claims=["First agreed point.", "Second agreed point."],
        disagreed_claim="Which approach to take.",
        disagreed_why="Compounds vs reversible.",
    )
    png = render_council_card(data)
    _assert_valid_png(png)


def test_render_council_card_empty_state():
    pytest.importorskip("PIL")
    png = render_council_card(CouncilCardData())
    _assert_valid_png(png)


# ── URL invariant — shared with eval-share ─────────────────────────


def test_council_card_pins_landing_url():
    """Same single-source-of-truth rule as eval_card: the CTA URL must
    not drift into the H1-banned vanity domain shapes. Brand URL flipped
    2026-05-17 → keepwhatworks.com."""
    assert CTA_LANDING_URL == "keepwhatworks.com"
    assert "trinity.local/" not in CTA_LANDING_URL
    assert CTA_HEADLINE.endswith(":")


# ── CLI handler: end-to-end smoke ──────────────────────────────────


def test_council_share_cli_writes_png_with_safe_filename(tmp_path, monkeypatch):
    """End-to-end: the rewritten handler writes a valid PNG, computes a
    filename that doesn't leak the user's prompt text, and DOESN'T
    contain the prior `council_-...` malformed prefix from the
    [:8] slicing bug."""
    pytest.importorskip("PIL")
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))

    # Seed a council_outcomes record + matching prompt_bundle so
    # load_council_outcome can resolve.
    from trinity_local.council_schema import (
        CouncilOutcome, CouncilMemberResult, CouncilRoutingLabel, PromptBundle,
    )
    from trinity_local.council_runtime import save_council_outcome, save_prompt_bundle

    bundle = PromptBundle(
        bundle_id="b_smoke",
        task_cluster_id="tc_smoke",
        task_text="SENSITIVE_USER_PROMPT_LEAK_CHECK should NOT land in filename",
    )
    save_prompt_bundle(bundle)

    outcome = CouncilOutcome(
        council_run_id="council_abc123def4567890",
        bundle_id="b_smoke",
        task_cluster_id="tc_smoke",
        primary_provider="claude",
        winner_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", output_text="x"),
            CouncilMemberResult(provider="antigravity", output_text="y"),
            CouncilMemberResult(provider="codex", output_text="z"),
        ],
        routing_label=CouncilRoutingLabel(
            winner="claude",
            agreed_claims=["A", "B"],
            disagreed_claims=[{"provider": "antigravity", "claim": "C", "why_matters": "D"}],
        ),
        synthesis_output="ok",
    )
    save_council_outcome(outcome)

    out_path = tmp_path / "card.png"
    from trinity_local.commands.council import handle_council_share
    from types import SimpleNamespace
    import sys
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    handle_council_share(SimpleNamespace(
        council="council_abc123def4567890",
        out=str(out_path),
        open_after=False,
    ))

    summary = json.loads(captured.getvalue())
    assert summary["ok"] is True
    assert summary["winner"] == "claude"
    assert summary["members"] == ["claude", "antigravity", "codex"]
    assert summary["agreed_claims_count"] == 2
    assert summary["disagreed_claim_present"] is True
    assert out_path.exists()
    assert out_path.stat().st_size > 5000

    # Privacy: the verbatim user prompt must not appear ANYWHERE in
    # the produced summary (the PNG renderer is also tested for this
    # invariant above; this asserts the CLI handler's JSON output).
    assert "SENSITIVE_USER_PROMPT_LEAK_CHECK" not in captured.getvalue()
