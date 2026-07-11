"""The seam closure (council_5ab2854092bcf68f, 2026-07-07).

The council's eval seed, verbatim: "unverified imports never affect
preference_acts, lenses, orderings, routing, or lens-build until transcript
anchors verify." These tests ARE that sentence. The old gate proved an anchor
was PRESENT; the seam was that nothing proved it TRUE — an in-harness agent
could fabricate "the user asked X". Now: verify-at-import against the local
prompt index, quarantine-until-verified in a sidecar outside every canonical
store, promotion on ingest, fail-closed on malformed."""
from __future__ import annotations

import json
from types import SimpleNamespace


def _seed_corpus(home, prompts):
    d = home / "prompts"
    d.mkdir(parents=True, exist_ok=True)
    rows = [json.dumps({"id": f"n{i}", "text": t}) for i, t in enumerate(prompts)]
    (d / "prompt_nodes.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


REAL_PROMPT = "compare fixed versus variable mortgage for a fifteen year horizon"


def _eval_payload(prompt):
    return {
        "source_provider": "chatgpt",
        "rejections": [{
            "type": "REFRAME",
            "original_prompt": prompt,
            "model_quote": "a long generic answer the user rejected",
            "user_substitute": "the reframe the user actually wanted",
            "why_signal": "user pivoted the frame",
        }],
    }


def _run_eval_import(home, payload):
    from trinity_local.commands.eval_import import handle_eval_import
    f = home / "payload.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    args = SimpleNamespace(path=str(f), dry_run=False, as_json=True,
                           provider=None, stdin=False)
    return handle_eval_import(args)


def _ledger_ids(home):
    p = home / "me" / "preference_acts.jsonl"
    if not p.exists():
        return []
    return [json.loads(l).get("id") for l in p.read_text().splitlines() if l.strip()]


class TestEvalKindSeam:
    def test_fabricated_prompt_quarantines_and_ledger_stays_untouched(self, tmp_path, monkeypatch, capsys):
        """THE seam: a well-formed import whose claimed prompt does NOT exist
        in the user's real corpus must not enter the ledger — it waits in the
        sidecar. This is the council's eval seed made executable."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed_corpus(tmp_path, [REAL_PROMPT])
        rc = _run_eval_import(tmp_path, _eval_payload(
            "a fabricated prompt the user never actually typed anywhere"))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert out["rejections"]["quarantined_unverified"] == 1
        assert out["rejections"]["new"] == 0 or _ledger_ids(tmp_path) == [], \
            "fabricated import reached the canonical ledger"
        assert _ledger_ids(tmp_path) == []
        q = tmp_path / "me" / "quarantine_acts.jsonl"
        assert q.exists() and len(q.read_text().splitlines()) == 1

    def test_real_prompt_verifies_and_enters_ledger(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed_corpus(tmp_path, [REAL_PROMPT])
        rc = _run_eval_import(tmp_path, _eval_payload(REAL_PROMPT))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["rejections"]["quarantined_unverified"] == 0
        assert len(_ledger_ids(tmp_path)) == 1

    def test_promotion_when_transcript_lands_later(self, tmp_path, monkeypatch, capsys):
        """quarantine-until-verified, then the ingest that finds the
        transcript promotes the row through the same admission path."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed_corpus(tmp_path, [REAL_PROMPT])
        claimed = "the session prompt that has not been ingested quite yet"
        _run_eval_import(tmp_path, _eval_payload(claimed))
        capsys.readouterr()
        assert _ledger_ids(tmp_path) == []
        # the transcript lands (corpus grows), promotion runs (the ingest hook)
        _seed_corpus(tmp_path, [REAL_PROMPT, claimed])
        from trinity_local.me.import_verification import promote_quarantined
        r = promote_quarantined()
        assert r["eval"]["promoted"] == 1 and r["eval"]["pending"] == 0
        assert len(_ledger_ids(tmp_path)) == 1
        # idempotent: nothing left to promote, ledger unchanged
        r2 = promote_quarantined()
        assert r2["eval"]["promoted"] == 0
        assert len(_ledger_ids(tmp_path)) == 1


class TestLensKindSeam:
    def _lens_payload(self, evidence):
        return {
            "source_provider": "chatgpt",
            "tensions": [{
                "pole_a": "terse execution", "pole_b": "thorough coverage",
                "failure_a": "cargo cult", "failure_b": "theater",
                "evidence": evidence, "confidence": "high",
            }],
            "orderings": [],
        }

    def _run(self, home, payload):
        from trinity_local.commands.lens_import import handle_lens_import
        f = home / "lens_payload.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        args = SimpleNamespace(path=str(f), dry_run=False, as_json=True,
                               provider=None, stdin=False)
        return handle_lens_import(args)

    def test_unresolvable_evidence_quarantines_registry_untouched(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed_corpus(tmp_path, [REAL_PROMPT])
        rc = self._run(tmp_path, self._lens_payload(
            ["an invented moment that appears nowhere in the corpus"]))
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["tensions"]["quarantined_unverified"] == 1
        assert out["tensions"]["new"] == 0
        lenses = tmp_path / "me" / "lenses.json"
        assert not lenses.exists() or "terse execution" not in lenses.read_text()

    def test_resolving_evidence_admits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        _seed_corpus(tmp_path, [REAL_PROMPT])
        rc = self._run(tmp_path, self._lens_payload(
            ["fixed versus variable mortgage for a fifteen year"]))  # fragment of a real prompt
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["tensions"]["quarantined_unverified"] == 0
        assert out["tensions"]["new"] == 1


class TestFloors:
    def test_short_anchor_never_verifies(self, tmp_path, monkeypatch):
        """Below the pre-registered floor a match is coincidence — a 5-char
        'anchor' must not verify even if it appears in the corpus."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me.import_verification import MIN_ANCHOR_CHARS, anchor_resolves
        corpus = ["the fix"]
        assert not anchor_resolves("the fix", corpus, min_chars=MIN_ANCHOR_CHARS)


class TestQuarantineCountsHonesty:
    """quarantine_counts is the black-hole meter (the council's pre-registered
    falsifier surface) — it must count PARSEABLE rows, the same universe
    promote_quarantined can ever act on. A corrupt line counted as 'pending'
    would inflate the meter forever while being invisible to promotion.
    Mutation: revert counts to raw line-counting → reds."""

    def test_unparseable_lines_do_not_count_as_pending(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        from trinity_local.me.import_verification import quarantine_counts
        me = tmp_path / "me"
        me.mkdir(parents=True, exist_ok=True)
        (me / "quarantine_acts.jsonl").write_text(
            'garbage\n{"kind": 123}\n[]\n', encoding="utf-8",
        )
        counts = quarantine_counts()
        # 'garbage' unparseable, '[]' non-dict → only {"kind":123} is a row
        assert counts["eval"] == 1
