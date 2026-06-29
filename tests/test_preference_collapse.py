"""The lens preference-collapse meter — held-out, data-isolated, meter-only.

`evaluate_split` fits the lens taste DIRECTION on TRAIN and scores held-out VALIDATION: a
"false-accept" is a held-out correction the direction ranks the REJECTED output above the
user's substitute. A one-sided sign test asks whether the direction predicts held-out signs
at all; if not, the single-direction lens is collapsing (blind where taste reverses). It
ABSTAINS under thin data / the TF-IDF fallback — never a false collapse verdict.
"""
from __future__ import annotations

from trinity_local.me.preference_acts import MODEL_MISS, PreferenceAct
from trinity_local.me.preference_collapse import (
    MIN_TRAIN,
    evaluate_split,
    lens_collapse_signal,
)

# 1-axis deterministic embedder: dim0 = terse(+) / verbose(-).
_TERSE = ("short", "one line", "just the answer", "keep it")
_VERBOSE = ("detailed", "thorough", "explanation", "caveat", "full context")


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        tl = t.lower()
        v = sum(1.0 for k in _TERSE if k in tl) - sum(1.0 for k in _VERBOSE if k in tl)
        out.append([v, 0.0])
    return out


def _terse_act(i):  # the user privileged the terse form over the verbose one
    return PreferenceAct(id=f"t{i}", trigger=MODEL_MISS,
                         privileged="just the answer keep it short",
                         sacrificed="a detailed thorough explanation with full context",
                         kind="REFRAME", why="w")


def _verbose_act(i):  # the OPPOSITE steer — the lens (fit on terse) is blind here
    return PreferenceAct(id=f"v{i}", trigger=MODEL_MISS,
                         privileged="a detailed thorough explanation with full context",
                         sacrificed="just the answer keep it short",
                         kind="REFRAME", why="w")


def test_consistent_taste_reads_ok():
    train = [_terse_act(i) for i in range(10)]
    val = [_terse_act(100 + i) for i in range(10)]
    r = evaluate_split(train, val, embed_fn=_fake_embed)
    assert r["ready"] and r["verdict"] == "ok"
    assert r["false_accept_rate"] == 0.0 and r["p"] < 0.05
    assert r["false_accept_ids"] == []


def test_held_out_reversal_reads_collapse():
    # The direction fits on terse-steering corrections, but every held-out correction steers
    # the OTHER way — the lens ranks the rejected output higher on all of them.
    train = [_terse_act(i) for i in range(10)]
    val = [_verbose_act(100 + i) for i in range(10)]
    r = evaluate_split(train, val, embed_fn=_fake_embed)
    assert r["ready"] and r["verdict"] == "collapse"
    assert r["false_accept_rate"] == 1.0
    # the held-out false-accepts ARE the de-biasing adversarial samples
    assert len(r["false_accept_ids"]) == 10 and all(i.startswith("v") for i in r["false_accept_ids"])


def test_abstains_on_thin_splits():
    train = [_terse_act(i) for i in range(MIN_TRAIN - 1)]
    val = [_terse_act(100 + i) for i in range(10)]
    r = evaluate_split(train, val, embed_fn=_fake_embed)
    assert not r["ready"] and "thin" in r["reason"]


def test_public_signal_abstains_without_real_embeddings(monkeypatch):
    # Simulate the TF-IDF / no-embedder box deterministically (regardless of whether the real
    # embedder is installed in this env): _default_embed returns None → abstain, never a
    # collapse verdict on word-overlap geometry.
    import trinity_local.me.preference_collapse as pc

    monkeypatch.setattr(pc, "_default_embed", lambda: None)
    r = pc.lens_collapse_signal([_terse_act(i) for i in range(40)], embed_fn=None)
    assert not r["ready"] and "embedding" in r["reason"].lower()


def test_public_signal_splits_and_evaluates():
    # 40 consistent terse corrections → the id-hash split yields usable train+val and a
    # generalizing direction.
    acts = [_terse_act(i) for i in range(40)]
    r = lens_collapse_signal(acts, embed_fn=_fake_embed)
    assert r["ready"] and r["verdict"] == "ok" and r["train_n"] >= MIN_TRAIN


def _neutral_act(i):  # no terse/verbose keywords → embeds to [0,0] → does NOT load on the direction
    return PreferenceAct(id=f"n{i}", trigger=MODEL_MISS,
                         privileged="apple banana", sacrificed="cherry date",
                         kind="REFRAME", why="w")


def test_abstains_when_too_few_loaded_underpowered():
    """len(val) clears MIN_VALIDATION, but only 4 acts LOAD on the direction (the rest
    score within the axis-noise band) → the sign test is underpowered: even a PERFECT
    split gives min p = 0.0625 >= 0.05, so it can never reject. Must ABSTAIN, not emit a
    false 'collapse'. (2026-06-29 fresh-lens false caution: n=4, 0 false-accepts, but
    mislabeled 'collapse' because min p >= 0.05.)"""
    train = [_terse_act(i) for i in range(10)]
    val = [_terse_act(100 + i) for i in range(4)] + [_neutral_act(i) for i in range(4)]
    r = evaluate_split(train, val, embed_fn=_fake_embed)
    assert not r["ready"], r                       # abstain, not a verdict
    assert "underpowered" in r["reason"], r["reason"]
    assert r.get("verdict") != "collapse"          # the false alarm we're preventing
