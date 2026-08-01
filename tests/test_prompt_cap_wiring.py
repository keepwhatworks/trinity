"""The frequency cap must be WIRED, not merely present.

`tests/test_prompt_frequency_cap.py` proves the discriminator and the collapser
are correct as PURE FUNCTIONS. It passes with equal enthusiasm when the
production call site hands them an empty key set, because it never calls the
production entry point at all. That is the repo's documented recurring failure —
contract asserted at the producer end, never verified at the consumer — and it
is not hypothetical here: `iter_turn_pairs` computes `batch_keys` on one line and
a one-word edit on the next (`batch_keys=set()`) turns the whole defense into a
pass-through while every existing test stays green.

MEASURED 2026-07-31 (`internal/experiments/corpus_contamination_funnel.py`, run
against the real 40,416-node prompt index):
  - `cap_repeated_prompts` has exactly ONE production call site, and the cap is
    wired to 1 of 16 modules that read the prompt index.
  - On that corpus the cap decides the fate of 119 dedup-key families /
    7,232 nodes — i.e. an unwired cap silently changes Stage-0's input by ~20%
    of its user-facing mass.
  - The moat it must NOT eat is real and adjacent: a naive frequency cap at the
    same floor catches 16 cross-lab substantive families (11.9% of everything it
    catches), which the provenance-aware classifier is specifically built to
    exclude. So "fix the wiring" and "fix it by deduping harder" are different
    changes and only the first is correct.

These tests therefore drive the PRODUCTION entry point (`iter_turn_pairs`) end to
end over a synthetic corpus, and every assertion about a collapse is preceded by
an assertion that the uncapped stream actually contained a flood to collapse —
so none of them can pass over degenerate input.
"""
from __future__ import annotations

import pytest

from trinity_local.me.turn_pairs import (
    _BATCH_REPEAT_FLOOR,
    _BATCH_UNIT_WEIGHT,
    _corpus_batch_keys,
    _dedup_key,
    _iter_turn_pairs_raw,
    iter_turn_pairs,
)
from trinity_local.memory import PromptNode, upsert_prompt_node

# A single-lab machine loop: substantive enough to clear the driver threshold,
# fired far above the shipped floor. This is the shape `classify_batch_keys`
# exists to collapse.
LOOP_TEXT = (
    "Review the floor-plan critique and decide whether it is a correctness "
    "failure or an aesthetic preference, then patch the renderer accordingly."
)
# The moat: the SAME substantive question asked deliberately of two labs. This
# must survive the wired path at full weight no matter how often it repeats.
MOAT_TEXT = (
    "What is the go-to-market for this app, and should it launch as a desktop "
    "product for non-coders or stay a terminal-first developer tool?"
)

FLOOD = _BATCH_REPEAT_FLOOR * 3  # comfortably above the floor, not near it


def _write(text: str, *, provider: str, n: int, tag: str) -> None:
    """Write `n` PromptNodes carrying `text`, each in its OWN transcript.

    Distinct transcript_ids matter: the node id is
    stable_id("pnode", transcript_id, turn_index, text[:200]), so N firings in
    one transcript would collapse to ONE node by upsert and the fixture would be
    degenerate. Each node carries preceding_assistant_text because
    `_iter_turn_pairs_raw` drops any turn it cannot pair with assistant text.
    """
    for i in range(n):
        upsert_prompt_node(PromptNode(
            id=f"{tag}_{provider}_{i}",
            transcript_id=f"{tag}_{provider}_t{i}",
            provider=provider,
            source_path=f"/fake/{tag}/{provider}/{i}",
            turn_index=0,
            text=text,
            embedding=[],
            created_at="2026-07-31T00:00:00Z",
            preceding_assistant_text=f"assistant reply {i}",
            following_assistant_text="",
        ))


def _count(pairs, text: str) -> int:
    key = _dedup_key(text)
    return sum(1 for p in pairs if _dedup_key(p[1]) == key)


@pytest.fixture
def _home(patch_trinity_home):
    """Isolated TRINITY_HOME. Named so the dependency is explicit at each use."""
    return patch_trinity_home


def test_single_lab_flood_is_collapsed_through_the_production_entry_point(_home):
    """The wiring guard. Drives `iter_turn_pairs`, not the pure helpers.

    Fails if the production call site passes an empty/literal key set — the exact
    edit that leaves every test in test_prompt_frequency_cap.py green.
    """
    _write(LOOP_TEXT, provider="claude", n=FLOOD, tag="loop")

    # PRECONDITION — the fixture must actually contain a flood. Without this the
    # collapse assertion below would pass over an empty corpus, which is the
    # degenerate-pass this whole file exists to prevent.
    raw = _count(list(_iter_turn_pairs_raw()), LOOP_TEXT)
    assert raw == FLOOD, (
        f"fixture degenerate: uncapped stream yielded {raw} occurrences, expected "
        f"{FLOOD}. The collapse assertion below would be vacuous — fix the fixture, "
        f"do not relax the assertion."
    )
    assert raw > _BATCH_UNIT_WEIGHT, "nothing to collapse; assertion would be vacuous"

    # The classifier must actually mark it, or we are testing the wrong corpus.
    assert _dedup_key(LOOP_TEXT) in _corpus_batch_keys(), (
        "the single-lab flood was not classified as batch — the corpus fixture no "
        "longer exercises the discriminator this guard is about"
    )

    capped = _count(list(iter_turn_pairs()), LOOP_TEXT)
    assert capped == _BATCH_UNIT_WEIGHT, (
        f"CAP NOT WIRED: {raw} occurrences reached Stage 0 as {capped}, expected "
        f"{_BATCH_UNIT_WEIGHT}. `iter_turn_pairs` computes batch_keys but the value "
        f"is not reaching `cap_repeated_prompts`."
    )


def test_cross_provider_moat_survives_the_wired_path(_home):
    """Wiring the cap must not become deduping harder.

    The measured false-positive exposure of a naive frequency cap at this floor
    is 11.9% cross-lab substantive families. This asserts the wired path keeps
    them at FULL weight — the founder direction of 2026-06-03 ("raw-dedup would
    delete the asset").
    """
    # Same question, two LABS — that is what makes it the moat rather than a loop.
    _write(MOAT_TEXT, provider="claude", n=FLOOD, tag="moat")
    _write(MOAT_TEXT, provider="codex", n=FLOOD, tag="moat")
    total = FLOOD * 2

    raw = _count(list(_iter_turn_pairs_raw()), MOAT_TEXT)
    assert raw == total, f"fixture degenerate: expected {total} occurrences, got {raw}"
    assert raw >= _BATCH_REPEAT_FLOOR, (
        "the moat fixture repeats below the floor, so the cap would ignore it for "
        "the wrong reason and this guard would pass vacuously"
    )
    assert _dedup_key(MOAT_TEXT) not in _corpus_batch_keys(), (
        "a substantive cross-lab ask was classified as batch-dispatched"
    )

    kept = _count(list(iter_turn_pairs()), MOAT_TEXT)
    assert kept == total, (
        f"MOAT EATEN: {raw} cross-provider asks reduced to {kept}. The cap must "
        f"collapse single-lab loops only."
    )


def test_cap_refuses_to_claim_a_collapse_on_a_degenerate_corpus(_home):
    """The degenerate-data case: nothing written, so nothing may be claimed.

    An empty corpus must produce an EMPTY batch-key set — not a set that happens
    to be empty for the same reason a broken classifier would be. The guard is
    that `iter_turn_pairs` also yields nothing, so the collapse assertion in the
    first test is provably measuring the cap rather than an empty pipe.
    """
    assert _corpus_batch_keys() == set(), (
        "an empty corpus classified some key as batch-dispatched — the "
        "discriminator is reading something that is not there"
    )
    assert list(iter_turn_pairs()) == []
    # And the precondition style used above genuinely discriminates: on this
    # degenerate corpus the flood-precondition would FAIL rather than sail
    # through to a green collapse assertion.
    assert _count(list(_iter_turn_pairs_raw()), LOOP_TEXT) == 0


def test_sub_floor_repeats_are_never_capped_through_the_wired_path(_home):
    """A genuine occasional re-ask stays at full weight.

    Guards the other direction: a wiring "fix" that capped everything repeated
    would pass the first test and destroy the corpus. Below the floor, every
    occurrence must survive.
    """
    n = _BATCH_REPEAT_FLOOR - 1
    _write(LOOP_TEXT, provider="claude", n=n, tag="occasional")

    raw = _count(list(_iter_turn_pairs_raw()), LOOP_TEXT)
    assert raw == n, f"fixture degenerate: expected {n} occurrences, got {raw}"
    assert _dedup_key(LOOP_TEXT) not in _corpus_batch_keys()

    kept = _count(list(iter_turn_pairs()), LOOP_TEXT)
    assert kept == n, (
        f"sub-floor re-ask was capped: {raw} occurrences reduced to {kept}. The cap "
        f"is over-firing — genuine repeated prompts are being deleted from Stage 0."
    )
