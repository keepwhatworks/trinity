"""New palate trials carry regen-proof audit keys (res_077).

The corpus regen of 2026-08 re-minted every r_* act id and orphaned 198 of 354
recorded trials — 56% of the canary's audit trail pointed at nothing. Trials now
carry a content hash (rejoins the same text under any future id) and the
prompt_id (stable across regens, and the provider join the per-environment
invariance slice reads). The file's ids+numbers-only privacy property is
unchanged: no raw text may enter a trial row.
"""
from __future__ import annotations

import json

from trinity_local.me import palate_registry as pr


class _Act:
    def __init__(self, i, priv, sac, prompt_id="pnode_x"):
        self.id, self.privileged, self.sacrificed, self.prompt_id = i, priv, sac, prompt_id


class TestAuditKeys:
    def test_content_hash_is_stable_across_id_regens(self):
        a = _Act("r_old", "keep this", "drop that")
        b = _Act("r_new_minted", "keep this", "drop that")
        assert pr._act_sha256(a) == pr._act_sha256(b)

    def test_content_hash_separates_different_acts(self):
        assert pr._act_sha256(_Act("x", "keep this", "drop that")) != \
               pr._act_sha256(_Act("x", "keep this", "drop other"))

    def test_trial_rows_carry_hash_and_prompt_id_but_never_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        (tmp_path / "me").mkdir(parents=True)
        (tmp_path / "me" / "palate_snapshot.json").write_text(json.dumps({
            "built_at": "2026-08-23T00:00:00+00:00",
            "direction": [1.0, 0.0], "fit_act_ids": []}))
        acts = [_Act("r_1", "privileged text here", "sacrificed text here", "pnode_77")]
        monkeypatch.setattr(pr, "_fit_acts", lambda _a: acts)
        import trinity_local.me.preference_acts as pa
        monkeypatch.setattr(pa, "load_preference_acts", lambda: acts)
        out = pr.score_prospective(embed_fn=lambda texts: [[1.0, 0.0], [0.0, 1.0]])
        assert out.get("ready"), out
        rows = [json.loads(l) for l in
                (tmp_path / "me" / "palate_trials.jsonl").read_text().splitlines()]
        assert rows, "no trial written — the assertions below would be vacuous"
        r = rows[0]
        assert r["act_sha256"] == pr._act_sha256(acts[0])
        assert r["prompt_id"] == "pnode_77"
        blob = json.dumps(r)
        assert "privileged text" not in blob and "sacrificed text" not in blob, \
            "raw act text leaked into a trial row — the no-text property is a privacy commitment"
