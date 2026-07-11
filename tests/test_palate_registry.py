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


class TestChoiceOracle:
    """The choice oracle (task #11): rank on the SAME frozen direction the
    registry scores, same abstain floor, live accuracy traveling with every
    answer. The SELECTION half of the stand-in claim, productized."""

    def _snap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _write_acts(tmp_path, FIT_ROWS)
        from trinity_local.me.palate_registry import record_direction_snapshot
        assert record_direction_snapshot(embed_fn=_embed)["ok"]

    def test_ranks_by_lens_fit(self, tmp_path, monkeypatch):
        self._snap(tmp_path, monkeypatch)
        from trinity_local.me.palate_registry import rank_options
        r = rank_options(["a verbose elaborate treatment", "a terse direct fix"],
                         embed_fn=_embed)
        assert r["ready"] and not r["abstain"]
        assert r["ranked"][0]["option"] == "a terse direct fix"
        assert r["ranked"][0]["score"] > r["ranked"][1]["score"]
        assert "live_accuracy" in r and "decided_trials" in r

    def test_near_tie_abstains(self, tmp_path, monkeypatch):
        """A gap under the pre-registered floor is a coin flip the oracle
        must not dress up — abstain: true means ask the human."""
        self._snap(tmp_path, monkeypatch)
        from trinity_local.me.palate_registry import rank_options
        r = rank_options(["neutral thing one", "neutral thing two"], embed_fn=_embed)
        assert r["ready"] and r["abstain"] is True

    def test_no_snapshot_not_ready(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me.palate_registry import rank_options
        r = rank_options(["a", "b"], embed_fn=_embed)
        assert not r["ready"]

    def test_single_option_refused(self, tmp_path, monkeypatch):
        self._snap(tmp_path, monkeypatch)
        from trinity_local.me.palate_registry import rank_options
        assert not rank_options(["only one"], embed_fn=_embed)["ready"]

    def test_low_live_accuracy_stamps_advisory(self, tmp_path, monkeypatch):
        """Kill-condition coupling: at n>=10 decided trials under 60%, every
        answer self-demotes to advisory_only — the oracle cannot outrun the
        registry that measures it."""
        import json as _json
        self._snap(tmp_path, monkeypatch)
        rows = [{"act_id": f"t{i}", "verdict": "correct" if i < 4 else "incorrect",
                 "gap": 0.1, "scored_at": "x", "snapshot_built_at": "y"} for i in range(10)]
        (tmp_path / "me" / "palate_trials.jsonl").write_text(
            "\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        from trinity_local.me.palate_registry import rank_options
        r = rank_options(["a verbose elaborate treatment", "a terse direct fix"],
                         embed_fn=_embed)
        assert r["ready"] and r["advisory_only"] is True and r["live_accuracy"] == 0.4


class TestChooseCli:
    """CLI mirror of the choose MCP tool — same core, same honesty banners."""

    def test_ready_path_ranks_and_reports_trust(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import me as me_cmd
        monkeypatch.setattr("trinity_local.me.palate_registry.rank_options",
            lambda opts, embed_fn=None: {"ready": True,
                "ranked": [{"option": "terse fix", "score": 0.4},
                           {"option": "verbose tour", "score": -0.2}],
                "confidence_gap": 0.6, "abstain": False, "advisory_only": False,
                "live_accuracy": 0.73, "decided_trials": 22})
        from types import SimpleNamespace
        rc = me_cmd.handle_choose(SimpleNamespace(options=["terse fix", "verbose tour"], as_json=False))
        out = capsys.readouterr().out
        assert rc == 0 and "1. [+0.4000] terse fix" in out and "73%" in out and "22" in out

    def test_not_ready_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import me as me_cmd
        from types import SimpleNamespace
        rc = me_cmd.handle_choose(SimpleNamespace(options=["a", "b"], as_json=False))
        assert rc == 1 and "can't rank" in capsys.readouterr().out

    def test_abstain_banner(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.commands import me as me_cmd
        monkeypatch.setattr("trinity_local.me.palate_registry.rank_options",
            lambda opts, embed_fn=None: {"ready": True,
                "ranked": [{"option": "a", "score": 0.01}, {"option": "b", "score": 0.0}],
                "confidence_gap": 0.01, "abstain": True, "advisory_only": False,
                "live_accuracy": 0.73, "decided_trials": 22})
        from types import SimpleNamespace
        me_cmd.handle_choose(SimpleNamespace(options=["a", "b"], as_json=False))
        assert "ABSTAIN" in capsys.readouterr().out


import pytest


class TestCorruptSnapshotResilience:
    """Corrupt-state resilience (#304 vein) on the palate files — found by the
    hour-6 flight audit: a wrong-typed palate_snapshot.json leaked a raw
    AttributeError through `choose` and stamped it as score_prospective's
    `reason`. The read boundary (_load_snapshot) now shape-guards; every
    caller degrades to the honest rebuild hint. Mutation: bypass
    _load_snapshot (restore the bare json.loads) → these red."""

    def _corrupt_home(self, tmp_path, monkeypatch, snapshot_text: str):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        me = tmp_path / "me"
        me.mkdir(parents=True, exist_ok=True)
        (me / "palate_snapshot.json").write_text(snapshot_text, encoding="utf-8")

    @pytest.mark.parametrize("bad", [
        '["not","a","dict"]',       # wrong type
        '{"direction": [0.1',        # truncated JSON
        '42',                        # scalar
    ])
    def test_rank_options_degrades_honestly(self, tmp_path, monkeypatch, bad):
        from trinity_local.me import palate_registry as pr
        self._corrupt_home(tmp_path, monkeypatch, bad)
        out = pr.rank_options(["a", "b"], embed_fn=lambda ts: [[0.0] * 8 for _ in ts])
        assert out["ready"] is False
        assert "AttributeError" not in str(out.get("reason", ""))
        assert "unreadable" in out["reason"] or "snapshot" in out["reason"]

    def test_score_prospective_degrades_honestly(self, tmp_path, monkeypatch):
        from trinity_local.me import palate_registry as pr
        self._corrupt_home(tmp_path, monkeypatch, '["not","a","dict"]')
        out = pr.score_prospective(embed_fn=lambda ts: [[0.0] * 8 for _ in ts])
        assert out["ready"] is False
        assert "AttributeError" not in str(out.get("reason", ""))

    def test_corrupt_trials_lines_are_skipped_not_fatal(self, tmp_path, monkeypatch):
        from trinity_local.me import palate_registry as pr
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        me = tmp_path / "me"
        me.mkdir(parents=True, exist_ok=True)
        (me / "palate_trials.jsonl").write_text(
            'not json\n{"trial": 42}\n{"verdict": "correct", "act_id": "r_1"}\n',
            encoding="utf-8",
        )
        s = pr.summarize_trials()
        assert s["trials"] >= 0  # no raise is the contract; corrupt rows don't count
