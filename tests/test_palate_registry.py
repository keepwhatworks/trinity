"""Prospective palate registry — the stand-in claim scored on live choices.

The walls under test: (1) train-on-test — an act the direction was fit on can
NEVER become a trial; (2) honest abstain — a below-noise gap is recorded as
abstain, never correct; (3) idempotency — an act is scored at most once;
(4) score-then-replace — a rebuild settles pending trials against the OUTGOING
direction before freezing a new one; (5) accuracy is over decided trials only.
Synthetic injectable embedder throughout (no MLX needed)."""
from __future__ import annotations

import json


def _embed(texts):
    """1-D synthetic space: 'terse'→+1, 'verbose'→−1, 'neutral'→~0.
    Padded to 3 dims so norms behave."""
    out = []
    for t in texts:
        if "terse" in t:
            out.append([1.0, 0.1, 0.0])
        elif "verbose" in t:
            out.append([-1.0, 0.1, 0.0])
        else:
            out.append([0.005, 0.1, 0.0])  # near-orthogonal → tiny gap
    return out


def _write_acts(home, rows):
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({
        "id": rid, "trigger": "MODEL_MISS", "privileged": priv, "sacrificed": sac,
    }) for rid, priv, sac in rows]
    (me / "preference_acts.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


FIT_ROWS = [(f"fit{i}", "terse answer please", "verbose answer offered") for i in range(8)]


def test_snapshot_freezes_direction_and_fit_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS)
    from trinity_local.me.palate_registry import record_direction_snapshot, _snapshot_path
    r = record_direction_snapshot(embed_fn=_embed)
    assert r["ok"] and r["fit_n"] == 8
    snap = json.loads(_snapshot_path().read_text())
    assert len(snap["fit_act_ids"]) == 8 and snap["direction"]


def test_thin_ledger_refuses_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS[:5])
    from trinity_local.me.palate_registry import record_direction_snapshot
    r = record_direction_snapshot(embed_fn=_embed)
    assert not r["ok"] and "thin" in r["reason"]


def test_prospective_scores_only_post_snapshot_acts(tmp_path, monkeypatch):
    """THE train-on-test wall: fit-set acts never become trials; new acts do,
    and the lens-consistent one scores correct."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS)
    from trinity_local.me.palate_registry import record_direction_snapshot, score_prospective
    record_direction_snapshot(embed_fn=_embed)
    # user makes two NEW choices: one lens-consistent, one contrarian
    _write_acts(tmp_path, FIT_ROWS + [
        ("new1", "terse answer chosen", "verbose answer rejected"),   # consistent
        ("new2", "verbose answer chosen", "terse answer rejected"),   # contrarian
    ])
    s = score_prospective(embed_fn=_embed)
    assert s["ready"] and s["newly_scored"] == 2
    assert s["correct"] == 1 and s["incorrect"] == 1
    assert s["trials"] == 2, "fit-set acts leaked into the trial registry"


def test_scoring_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS)
    from trinity_local.me.palate_registry import record_direction_snapshot, score_prospective
    record_direction_snapshot(embed_fn=_embed)
    _write_acts(tmp_path, FIT_ROWS + [("new1", "terse pick", "verbose pick")])
    s1 = score_prospective(embed_fn=_embed)
    s2 = score_prospective(embed_fn=_embed)
    assert s1["newly_scored"] == 1 and s2["newly_scored"] == 0
    assert s2["trials"] == 1


def test_below_noise_gap_records_honest_abstain(tmp_path, monkeypatch):
    """A near-zero projection gap must record as abstain — 'ask the human' —
    and abstains never count toward accuracy."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS)
    from trinity_local.me.palate_registry import record_direction_snapshot, score_prospective
    record_direction_snapshot(embed_fn=_embed)
    _write_acts(tmp_path, FIT_ROWS + [("new1", "neutral thing a", "neutral thing b")])
    s = score_prospective(embed_fn=_embed)
    assert s["abstained"] == 1 and s["decided"] == 0
    assert s["accuracy"] is None, "abstains must never manufacture an accuracy"


def test_rebuild_settles_pending_trials_first(tmp_path, monkeypatch):
    """score-then-replace: choices that arrived under the old direction are
    scored against IT before the new snapshot swallows them into the fit set."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _write_acts(tmp_path, FIT_ROWS)
    from trinity_local.me.palate_registry import record_direction_snapshot, summarize_trials
    record_direction_snapshot(embed_fn=_embed)
    _write_acts(tmp_path, FIT_ROWS + [("new1", "terse pick", "verbose pick")])
    r = record_direction_snapshot(embed_fn=_embed)  # rebuild
    assert r["ok"] and r["settled_pending"] == 1
    assert summarize_trials()["correct"] == 1


def test_no_snapshot_reads_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.me.palate_registry import score_prospective
    s = score_prospective(embed_fn=_embed)
    assert not s.get("ready")


class TestLensHealthSurface:
    def test_early_counts_read_as_accumulating(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _write_acts(tmp_path, FIT_ROWS)
        from trinity_local.me import palate_registry as pr
        pr.record_direction_snapshot(embed_fn=_embed)
        _write_acts(tmp_path, FIT_ROWS + [("new1", "terse pick", "verbose pick")])
        pr.score_prospective(embed_fn=_embed)
        monkeypatch.setattr(pr, "score_prospective",
                            lambda embed_fn=None: {**pr.summarize_trials(), "ready": True})
        from trinity_local.lens_health import _palate_prospective, OK
        c = _palate_prospective(True)
        assert c.status == OK and "accumulating" in c.summary

    def test_low_live_accuracy_fires_weak_at_n(self, tmp_path, monkeypatch):
        """Degenerate case: 10+ decided trials at 40% must NOT read green —
        that green would attest 'the lens stands in for you' while it's
        measurably failing on live choices."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        me = tmp_path / "me"; me.mkdir(parents=True)
        rows = [{"act_id": f"t{i}", "verdict": "correct" if i < 4 else "incorrect",
                 "gap": 0.1, "scored_at": "x", "snapshot_built_at": "y"} for i in range(10)]
        (me / "palate_trials.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        from trinity_local.me import palate_registry as pr
        monkeypatch.setattr(pr, "score_prospective",
                            lambda embed_fn=None: {**pr.summarize_trials(), "ready": True})
        from trinity_local.lens_health import _palate_prospective, WEAK
        c = _palate_prospective(True)
        assert c.status == WEAK, (c.status, c.summary)
        assert c.fix, "a WEAK must carry its lever"

    def test_tfidf_backend_abstains(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.lens_health import _palate_prospective, ABSTAIN
        assert _palate_prospective(False).status == ABSTAIN
