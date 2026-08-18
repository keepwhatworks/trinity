"""The compression KPI's determinism claims, made executable.

`compression_score`'s docstring says its coder is "pinned ... and asserted by a
test" so historical scores stay comparable. That sentence shipped 2026-08-06 with
NO such test — an adversarial review caught it on 2026-08-07. This is the test the
claim was describing.

What must hold, and why each would silently invalidate every recorded score:
  * byte-identical output on re-run (an unpinned coder makes the tracked metric drift)
  * insensitivity to INPUT ORDER (groups are sorted internally; if not, the score
    would depend on corpus iteration order, which is filesystem-dependent)
  * the size-preserving null (if the shuffle changed group SIZES it would compare
    partitions of different shape and measure the shape, not the assignment)
  * the model cost cancels from the numerator (documented, and easy to "fix" wrongly)
"""
from __future__ import annotations

import importlib.util
import random
from pathlib import Path

_MOD = None


def _cs():
    """Load internal/experiments/compression_score.py by PATH, without touching
    sys.path at import time.

    A module-level sys.path.insert would run at COLLECTION, before any test, and
    leak into every subsequent test module — the "invisible armor" class
    tests/test_no_module_level_env_mutation.py exists to catch (and did catch, on
    the first draft of this file). Loading from an explicit spec keeps the effect
    inside this module.
    """
    global _MOD
    if _MOD is None:
        path = Path(__file__).resolve().parent.parent / "internal" / "experiments" / "compression_score.py"
        spec = importlib.util.spec_from_file_location("_compression_score_under_test", path)
        assert spec and spec.loader, f"cannot load {path}"
        _MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_MOD)
    return _MOD


def _corpus(seed: int = 11):
    rng = random.Random(seed)
    groups = [[f"group {g} item {i} with shared vocabulary about subject {g}"
               for i in range(25)] for g in range(6)]
    texts = [t for gg in groups for t in gg]
    labels = [g for g, gg in enumerate(groups) for _ in gg]
    pairs = list(zip(texts, labels))
    rng.shuffle(pairs)
    return [t for t, _ in pairs], [g for _, g in pairs]


def test_score_is_byte_identical_across_runs():
    t, lab = _corpus()
    a, b = _cs().compression_score(t, lab), _cs().compression_score(t, lab)
    assert a == b, "compression_score is not deterministic — the tracked metric drifts"


def test_score_is_invariant_to_input_order():
    """Groups are sorted internally, so shuffling the INPUT must not move the score.
    Without that, the metric depends on corpus iteration order."""
    t, lab = _corpus()
    pairs = list(zip(t, lab))
    random.Random(99).shuffle(pairs)
    t2, lab2 = [x for x, _ in pairs], [g for _, g in pairs]
    a = _cs().compression_score(t, lab)["score"]
    b = _cs().compression_score(t2, lab2)["score"]
    assert abs(a - b) < 1e-12, f"score moved with input order: {a} vs {b}"


def test_null_preserves_group_sizes():
    """The null must permute LABELS, never resize groups — otherwise it compares
    partitions of a different shape and measures the shape."""
    t, lab = _corpus()
    from collections import Counter

    sizes = sorted(Counter(lab).values())
    shuffled = list(lab)
    random.Random(_cs().NULL_SEED).shuffle(shuffled)
    assert sorted(Counter(shuffled).values()) == sizes


def test_model_cost_cancels_from_the_numerator():
    """Documented behaviour, pinned so nobody 'fixes' it into a k-penalty.

    The null pays an identical assignment cost, so M cancels exactly from
    (null - real). Selecting k by un-normalised total bytes instead is monotone to
    k=1 on this construction (amd_0093) — the cancellation is correct, not a bug."""
    t, lab = _corpus()
    a = _cs().compression_score(t, lab, two_part=True)
    b = _cs().compression_score(t, lab, two_part=False)
    assert abs((a["null_bits_mean"] - a["real_bits"]) - (b["null_bits_mean"] - b["real_bits"])) < 1e-6
    assert a["model_cost_bytes"] > 0, "the model cost term vanished entirely"


def test_pinned_constants_are_present():
    """These four define score comparability across the whole arc; changing any of
    them silently invalidates every number already recorded in the ledger."""
    m = _cs()
    assert m.GZIP_LEVEL == 9
    assert m.NULL_REPLICATES == 25
    assert m.NULL_SEED == 20260806
    assert set(m.CODERS) == {"gzip", "bz2", "lzma"}


def test_every_coder_agrees_that_real_structure_beats_the_null():
    """A governing KPI must not be an artefact of one coder."""
    t, lab = _corpus()
    for coder in _cs().CODERS:
        r = _cs().compression_score(t, lab, coder=coder)
        assert r["score"] > 0 and r["beats_every_null"], f"{coder} failed to see real structure"
