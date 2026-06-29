"""eval-import: provider JSON → unified preference-act ledger merge.

Pins schema mapping (REFRAME/REDIRECT/SHARPENING/COMPRESSION axis
validation), dedup-by-stable-id (same input → same id, second import
no-ops), and append-only preference_acts.jsonl semantics.
"""
from __future__ import annotations

import json
from argparse import Namespace

import pytest

from trinity_local.commands.eval_import import (
    _provider_dict_to_rejection_signal,
    _read_existing_ids,
    handle_eval_import,
    handle_eval_prompt,
)
from trinity_local.me.preference_acts import preference_acts_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def _good_rejection(axis: str = "REFRAME") -> dict:
    return {
        "type": axis,
        "model_quote": "Let me explain why X is hard before showing the answer",
        "user_substitute": "skip the why, just give me the SQL",
        "why_signal": "user wants the answer first, justification second",
        "confidence": "high",
    }


def _payload(rejections: list[dict], provider: str = "claude") -> dict:
    return {
        "source_provider": provider,
        "extracted_at": "2026-05-25T08:00:00Z",
        "horizon_window_days": 30,
        "rejections": rejections,
    }


class TestProviderDictMapping:
    def test_canonical_rejection_maps_cleanly(self):
        sig = _provider_dict_to_rejection_signal(_good_rejection(), "claude", 0)
        assert sig is not None
        assert sig.type == "REFRAME"
        assert "skip the why" in sig.user_substitute
        # source_provider + confidence get folded into why_signal so eval-run
        # downstream sees the provenance.
        assert "[claude/high]" in sig.why_signal
        assert sig.id.startswith("r_")  # matches schemas/rejection_signal.schema.json ^r_

    def test_invalid_axis_rejected(self):
        bad = _good_rejection(axis="EXPLAIN")  # not one of the 4 valid axes
        assert _provider_dict_to_rejection_signal(bad, "claude", 0) is None

    def test_missing_quote_or_substitute_rejected(self):
        for missing in ("model_quote", "user_substitute"):
            bad = _good_rejection()
            del bad[missing]
            assert _provider_dict_to_rejection_signal(bad, "claude", 0) is None

    def test_axis_normalized_to_uppercase(self):
        bad = _good_rejection(axis="reframe")  # lowercase
        sig = _provider_dict_to_rejection_signal(bad, "claude", 0)
        assert sig is not None
        assert sig.type == "REFRAME"

    def test_stable_id_deterministic_across_calls(self):
        """Same content → same id, so re-import dedups cleanly."""
        a = _provider_dict_to_rejection_signal(_good_rejection(), "claude", 0)
        b = _provider_dict_to_rejection_signal(_good_rejection(), "claude", 5)
        assert a.id == b.id  # seq deliberately NOT mixed in for true dedup

    def test_stable_id_distinguishes_providers(self):
        """Same quote captured by two providers → distinct ids (so both land)."""
        a = _provider_dict_to_rejection_signal(_good_rejection(), "claude", 0)
        b = _provider_dict_to_rejection_signal(_good_rejection(), "codex", 0)
        assert a.id != b.id


class TestMalformedListField:
    """A `rejections` field that's PRESENT but the WRONG TYPE (e.g. an agent emitted
    a JSON string where an array was expected) must FAIL LOUDLY — not silently coerce
    to [] and report success while dropping every rejection. The old code did the
    latter, so a malformed payload was indistinguishable from a legitimately-empty
    one (both ok=True, incoming=0) and the user's taste signal vanished unreported.
    This is the silent-failure shape the project guards hardest, on the write path."""

    def _run(self, home, tmp_path, payload: dict, capsys):
        f = tmp_path / "evals.json"
        f.write_text(json.dumps(payload))
        rc = handle_eval_import(Namespace(path=str(f), from_json=False, dry_run=False, as_json=True))
        return rc, json.loads(capsys.readouterr().out)

    def test_wrong_type_rejections_fails_loudly_no_silent_drop(self, home, tmp_path, capsys):
        rc, out = self._run(home, tmp_path, {"source_provider": "claude", "rejections": "not-a-list"}, capsys)
        assert rc == 2, "a wrong-type rejections field must return a non-zero exit code"
        assert out["ok"] is False
        assert "list" in (out.get("error") or "").lower(), out
        # Mutation: revert to `if not isinstance(...): raw_rejections = []` → rc=0,
        # ok=True, no error → this reds.
        # And it must NOT have silently written anything to the ledger.
        assert not preference_acts_path().exists(), "a malformed payload wrote to the ledger"

    def test_empty_rejections_list_is_ok_not_an_error(self, home, tmp_path, capsys):
        rc, out = self._run(home, tmp_path, {"source_provider": "claude", "rejections": []}, capsys)
        assert rc == 0 and out["ok"] is True, "a legitimately-empty list must NOT be treated as malformed"
        assert out["rejections"]["new"] == 0

    def test_absent_rejections_is_ok(self, home, tmp_path, capsys):
        rc, out = self._run(home, tmp_path, {"source_provider": "claude"}, capsys)
        assert rc == 0 and out["ok"] is True, "an absent rejections field is empty, not malformed"


class TestCliEndToEnd:
    def test_first_import_persists_to_preference_acts_jsonl(self, home, tmp_path, capsys):
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([
            _good_rejection("REFRAME"),
            _good_rejection("REDIRECT"),
            _good_rejection("SHARPENING"),
        ])))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            dry_run=False,
            as_json=True,
        )
        rc = handle_eval_import(args)
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["rejections"]["new"] == 3
        assert result["rejections"]["duplicates"] == 0
        # File written, lines match
        lines = preference_acts_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3

    def test_re_import_same_payload_dedups(self, home, tmp_path, capsys):
        """Same payload imported twice: second run sees all-duplicates."""
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([
            _good_rejection("REFRAME"),
            _good_rejection("REDIRECT"),
        ])))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            dry_run=False,
            as_json=True,
        )
        # First import — both land
        rc = handle_eval_import(args)
        assert rc == 0
        first = json.loads(capsys.readouterr().out)
        assert first["rejections"]["new"] == 2

        # Second import — same content → all dedup
        rc = handle_eval_import(args)
        assert rc == 0
        second = json.loads(capsys.readouterr().out)
        assert second["rejections"]["new"] == 0
        assert second["rejections"]["duplicates"] == 2
        # File still has only 2 lines (append-only didn't double)
        lines = preference_acts_path().read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_dry_run_does_not_write(self, home, tmp_path, capsys):
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([_good_rejection()])))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            dry_run=True,
            as_json=True,
        )
        rc = handle_eval_import(args)
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["dry_run"] is True
        assert result["rejections"]["new"] == 1
        assert _read_existing_ids() == set()  # never landed

    def test_malformed_axes_skipped_not_aborted(self, home, tmp_path, capsys):
        """One bad axis shouldn't kill the import of the good ones."""
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([
            _good_rejection("REFRAME"),
            _good_rejection("EXPLAIN"),  # invalid
            _good_rejection("COMPRESSION"),
        ])))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            dry_run=False,
            as_json=True,
        )
        rc = handle_eval_import(args)
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["rejections"]["new"] == 2
        assert result["rejections"]["skipped_malformed"] == 1

    def test_provider_rejections_reported_not_scoreable_with_honest_note(self, home, tmp_path, capsys):
        """Provider rejections carry no original prompt (prompt_id=None), so
        build_eval_set drops them as unresolved (#280). The import must (a) report
        scoreable_as_eval=0 in the JSON and (b) NOT print the false "eval-build to
        package these into an eval set" promise — instead an honest note that they
        enrich the lens but aren't scoreable eval items. Guards the false-green
        that would otherwise send the user into a build that silently skips them."""
        # JSON shape: scoreable_as_eval present and 0.
        f1 = tmp_path / "e1.json"
        f1.write_text(json.dumps(_payload([_good_rejection("REFRAME")])))
        rc = handle_eval_import(Namespace(path=str(f1), from_json=False, dry_run=False, as_json=True))
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["rejections"]["new"] == 1
        assert result["rejections"]["scoreable_as_eval"] == 0
        # Human shape: honest note, not the false promise (distinct axis → not a dup).
        f2 = tmp_path / "e2.json"
        f2.write_text(json.dumps(_payload([_good_rejection("COMPRESSION")])))
        rc = handle_eval_import(Namespace(path=str(f2), from_json=False, dry_run=False, as_json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "NOT scoreable eval items" in out, (
            "honest note missing — the false-green could mislead the user"
        )
        assert "package these into" not in out, (
            "the false 'eval-build to package these' promise must not show for "
            "unscoreable (no-prompt) provider imports"
        )

    def test_missing_file_exits_nonzero(self, home, tmp_path, capsys):
        args = Namespace(
            path=str(tmp_path / "nope.json"),
            from_json=False,
            dry_run=False,
            as_json=False,
        )
        rc = handle_eval_import(args)
        assert rc == 1
        assert "file not found" in capsys.readouterr().err

    def test_provider_flag_supplies_missing_source_provider(self, home, tmp_path, capsys):
        """Payload omits source_provider → --provider fills the gap."""
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps({"rejections": [_good_rejection()]}))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            provider="claude",
            dry_run=False,
            as_json=True,
        )
        rc = handle_eval_import(args)
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["source_provider"] == "claude"

    def test_provider_flag_overrides_payload_source_provider(self, home, tmp_path, capsys):
        """--provider wins over source_provider in the payload (re-attribution)."""
        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([_good_rejection()], provider="gemini")))
        args = Namespace(
            path=str(payload_file),
            from_json=False,
            provider="codex",
            dry_run=False,
            as_json=True,
        )
        rc = handle_eval_import(args)
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["source_provider"] == "codex"


class TestLedgerDualWrite:
    """EXTRACT Stage 4a: eval-import dual-writes each rejection to the unified
    ledger (preference_acts.jsonl) so the flipped read path sees provider
    imports without waiting for the next lens-build."""

    def test_import_also_appends_to_ledger(self, home, tmp_path, capsys):
        from trinity_local.me.preference_acts import (
            MODEL_MISS,
            load_preference_acts,
        )

        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([
            _good_rejection("REFRAME"),
            _good_rejection("REDIRECT"),
        ])))
        rc = handle_eval_import(Namespace(
            path=str(payload_file), from_json=False, dry_run=False, as_json=True,
        ))
        assert rc == 0
        capsys.readouterr()
        acts = load_preference_acts()
        assert len(acts) == 2
        assert all(a.trigger == MODEL_MISS for a in acts)
        # The persisted ledger id matches the in-memory act id.
        ledger_ids = {
            json.loads(ln)["id"]
            for ln in preference_acts_path().read_text(encoding="utf-8").splitlines()
        }
        assert {a.id for a in acts} == ledger_ids

    def test_dedup_reads_the_ledger(self, home, tmp_path, capsys):
        # Seed the ledger directly (as a prior lens-build would have), then
        # import the SAME rejection — it must dedup against the ledger even
        # without any legacy split-store file.
        from trinity_local.commands.eval_import import (
            _provider_dict_to_rejection_signal,
        )
        from trinity_local.me.preference_acts import (
            from_rejection,
            save_preference_acts,
        )

        sig = _provider_dict_to_rejection_signal(_good_rejection("REFRAME"), "claude", 0)
        save_preference_acts([from_rejection(sig)])

        payload_file = tmp_path / "evals.json"
        payload_file.write_text(json.dumps(_payload([_good_rejection("REFRAME")])))
        rc = handle_eval_import(Namespace(
            path=str(payload_file), from_json=False, dry_run=False, as_json=True,
        ))
        assert rc == 0
        result = json.loads(capsys.readouterr().out)
        assert result["rejections"]["new"] == 0
        assert result["rejections"]["duplicates"] == 1


class TestEvalPromptCli:
    def test_prompt_body_starts_with_the_user_instruction(self, capsys):
        rc = handle_eval_prompt(Namespace(with_instructions=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert out.lstrip().startswith("Look back over my recent work")
        # No intro README content
        assert "trinity-local eval-import" not in out

    def test_with_instructions_includes_install_hint(self, capsys):
        rc = handle_eval_prompt(Namespace(with_instructions=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Provider-side eval prompt" in out
        assert "trinity-local eval-import" in out


class TestOriginalPromptScoreable:
    """#280: a provider-imported rejection that carries `original_prompt` becomes
    a SCOREABLE eval item (carried inline as prompt_text), instead of being
    dropped as unresolved for having no resolvable prompt_id."""

    def test_original_prompt_carried_to_prompt_text(self):
        r = _good_rejection()
        r["original_prompt"] = "write me the SQL to join orders and customers"
        sig = _provider_dict_to_rejection_signal(r, "claude", 0)
        assert sig is not None
        assert sig.prompt_text == "write me the SQL to join orders and customers"

    def test_echoed_gold_prompt_dropped(self):
        # An original_prompt that just echoes the gold is the #247 degeneracy
        # (prompt == user_substitute ⇒ every model scores ~1.0) — drop it.
        r = _good_rejection()
        r["original_prompt"] = r["user_substitute"]
        sig = _provider_dict_to_rejection_signal(r, "claude", 0)
        assert sig is not None
        assert sig.prompt_text == "", "an echoed-gold prompt must not be carried"

    def test_absent_original_prompt_is_empty(self):
        sig = _provider_dict_to_rejection_signal(_good_rejection(), "claude", 0)
        assert sig is not None
        assert sig.prompt_text == ""

    def test_imported_with_prompt_becomes_scoreable_not_unresolved(self, home, tmp_path):
        """End-to-end: import a rejection WITH original_prompt, then build the
        eval set — it must produce a scoreable item (prompt = the original), not
        a skipped_unresolved drop. A sibling rejection WITHOUT the prompt stays
        unresolved."""
        import json as _json
        from argparse import Namespace
        scoreable = _good_rejection(axis="REFRAME")
        scoreable["original_prompt"] = "explain quantum entanglement simply"
        unscoreable = {
            "type": "REDIRECT",
            "model_quote": "here is a tangent about history",
            "user_substitute": "stay on the engineering question",
            "why_signal": "user wants focus",
            "confidence": "high",
        }  # no original_prompt
        payload_path = tmp_path / "p.json"
        payload_path.write_text(_json.dumps(_payload([scoreable, unscoreable])))
        rc = handle_eval_import(Namespace(
            provider="claude", path=str(payload_path), from_json=False,
            dry_run=False, as_json=True,
        ))
        assert rc == 0

        from trinity_local.evals.builder import build_eval_set
        es = build_eval_set()
        items = es.get("items") if isinstance(es, dict) else getattr(es, "items", [])
        stats = es.get("stats") if isinstance(es, dict) else getattr(es, "stats", {})
        prompts = [
            (it.get("prompt") if isinstance(it, dict) else getattr(it, "prompt", ""))
            for it in (items or [])
        ]
        assert "explain quantum entanglement simply" in prompts, (
            f"the imported rejection with original_prompt wasn't scoreable: {prompts}"
        )
        # The one without a prompt is unresolved, not silently scored against gold.
        assert (stats.get("skipped_unresolved") or 0) >= 1, (
            f"the prompt-less rejection should be unresolved: {stats}"
        )
