"""De-weight by BATCH-PROVENANCE, not raw repetition — the lens substrate cleaner.

Founder direction (2026-06-03): "de weight ... so the lens reflects you, not your loops"
— BUT "de-weight by batch-provenance, not raw repetition. Deliberately asking the same
question of Claude, GPT, and Gemini is also repetition — and it's the cross-provider signal
that is your entire moat, the thing [the engine] runs on. Raw-dedup would delete the asset.
Collapse batch-dispatched repeats to unit weight; keep human cross-provider repeats at full
weight. Step 1 must not eat what the engine eats."

The discriminator: a repeated prompt under ≥2 LABS is a deliberate cross-provider ask (kept
in full); a prompt repeated under a SINGLE lab at machine frequency is a dispatched loop
(collapsed to unit weight). The load-bearing test is `test_cross_provider_repeat_is_never_batch`
— the moat must survive no matter how often it repeats.
"""
from __future__ import annotations

from trinity_local.me.turn_pairs import (
    _BATCH_REPEAT_FLOOR,
    _BATCH_UNIT_WEIGHT,
    _dedup_key,
    cap_repeated_prompts,
    classify_batch_keys,
)


def _pair(user: str, i: int = 0):
    # (assistant_text, user_turn, prompt_id, next_user_text) — only the user turn is keyed.
    return (f"assistant {i}", user, f"p{i}", "")


# ── the provenance discriminator (pure) ──────────────────────────────────────


def test_single_lab_machine_loop_is_batch():
    """A prompt fired 704× under ONE lab is the autonomous /loop — batch-dispatched."""
    k = _dedup_key("is this floor-plan critique a correctness failure or aesthetic?")
    assert classify_batch_keys({k: {"claude"}}, {k: 704}) == {k}


def test_substantive_cross_provider_ask_is_never_batch():
    """THE MOAT. A real question (≥40c) put to Claude AND OpenAI AND Google is a deliberate
    cross-provider ask — it must NEVER be classified batch, no matter how often it repeats.
    Step 1 must not eat what the engine eats."""
    k = _dedup_key("which model gives the sharpest take on this tradeoff and why?")  # >40c
    assert len(k) >= 40
    assert classify_batch_keys({k: {"claude", "codex", "antigravity"}}, {k: 300}) == set()
    # even a two-lab substantive ask is the moat
    assert classify_batch_keys({k: {"claude", "codex"}}, {k: 50}) == set()


def test_short_cross_lab_driver_is_batch_not_moat():
    """The refinement the re-sample forced: "continue"/"ok"/"status" appear under all 3 labs
    because the founder types them everywhere — NOT a deliberate question. A ≥2-lab key that
    is too short to be a question is a driver, and collapses like any other machine repeat."""
    for driver in ("continue", "status", "ok", "commit and push"):
        k = _dedup_key(driver)
        assert len(k) < 40
        assert classify_batch_keys({k: {"claude", "codex", "antigravity"}}, {k: 278}) == {k}


def test_single_lab_low_count_is_a_genuine_reask_not_batch():
    """A handful of repeats under one lab is a human occasionally re-asking — kept at full
    weight; only machine frequency (≥ floor) trips the batch flag."""
    k = _dedup_key("fix the failing test")
    assert classify_batch_keys({k: {"claude"}}, {k: _BATCH_REPEAT_FLOOR - 1}) == set()
    assert classify_batch_keys({k: {"claude"}}, {k: _BATCH_REPEAT_FLOOR}) == {k}


def test_cowork_folds_to_claude_not_cross_provider():
    """cowork is Anthropic — claude+cowork is ONE lab, so a loop spanning both is still
    batch, not a (fake) cross-provider ask."""
    k = _dedup_key("review all project md files for consistency")
    assert classify_batch_keys({k: {"claude"}}, {k: 376}) == {k}  # both fold to 'claude' upstream


# ── the cap mechanism (pure) ─────────────────────────────────────────────────


def test_batch_key_collapses_to_unit_weight():
    """704 copies of a batch-dispatched prompt collapse to exactly unit weight."""
    k = _dedup_key("dispatched loop prompt")
    flood = [_pair("dispatched loop prompt", i) for i in range(704)]
    kept = list(cap_repeated_prompts(flood, batch_keys={k}))
    assert len(kept) == _BATCH_UNIT_WEIGHT


def test_cross_provider_repeats_pass_through_untouched():
    """A repeated prompt NOT in batch_keys (the cross-provider moat) is kept in full."""
    moat = [_pair("the same deliberate cross-provider question", i) for i in range(40)]
    kept = list(cap_repeated_prompts(moat, batch_keys=set()))  # empty → nothing is batch
    assert len(kept) == 40


def test_mixed_stream_collapses_only_the_batch_key():
    """Batch loop collapses to unit; the cross-provider asks and diverse turns all survive."""
    batch_k = _dedup_key("loop: status")
    batch = [_pair("loop: status", i) for i in range(50)]
    moat = [_pair("deliberate cross-provider ask", 100 + i) for i in range(3)]
    diverse = [_pair(f"unique turn {i}", 200 + i) for i in range(10)]
    kept = list(cap_repeated_prompts(batch + moat + diverse, batch_keys={batch_k}))
    assert len(kept) == _BATCH_UNIT_WEIGHT + 3 + 10
    # the survivors of the batch key are the FIRST occurrences, order preserved
    assert kept[0][2] == "p0"


def test_empty_stream_is_safe():
    assert list(cap_repeated_prompts([], batch_keys=set())) == []
