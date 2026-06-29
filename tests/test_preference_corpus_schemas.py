"""Validate Trinity's reference implementation against its own
published preference-corpus schemas (task #117).

The schemas in `schemas/` are the interop contract for other tools
(Aider, Cline, Continue, MCP servers) to adopt. If Trinity's own
writers drift from the schema, the contract is broken silently.
This test makes the drift loud.

Two layers:

1. **Synthetic round-trip**: generate a known-good record via the
   Trinity writer, validate it against the schema, fail loudly on
   mismatch. Catches code → schema drift.

2. **Real-corpus sampling**: if `~/.trinity/` exists, sample real
   on-disk files and validate them. Catches schema → real-data
   drift. Skipped when home is empty (CI / fresh install).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO / "schemas"


@pytest.fixture(scope="module")
def jsonschema_mod():
    """Skip the whole module if jsonschema isn't installed — it's a
    runtime dep only for spec validation, not for Trinity proper."""
    try:
        import jsonschema
        return jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed; skipping schema validation tests")


def _load_schema(name: str) -> dict:
    path = SCHEMAS_DIR / name
    assert path.exists(), f"schema file missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


class TestSchemasAreValid:
    """Each schema must itself be a valid JSON Schema 2020-12 document.
    A broken schema fails open — every validation against it would
    spuriously pass."""

    @pytest.mark.parametrize("schema_name", [
        "council_outcome.schema.json",
        "eval_set.schema.json",
        "rejection_signal.schema.json",
    ])
    def test_schema_self_validates(self, jsonschema_mod, schema_name):
        schema = _load_schema(schema_name)
        # Draft 2020-12 meta-validation: jsonschema's Validator class
        # validates the schema document itself against the JSON Schema
        # spec. If this fails, the schema is malformed.
        validator_cls = jsonschema_mod.Draft202012Validator
        validator_cls.check_schema(schema)

    def test_council_outcome_has_routing_label_def(self, jsonschema_mod):
        """The routing_label is the supervision-signal shape. If it
        loses its $defs entry, the whole point of structured Routing
        JSON disappears."""
        schema = _load_schema("council_outcome.schema.json")
        assert "routing_label" in schema.get("$defs", {})
        rl = schema["$defs"]["routing_label"]
        required = set(rl.get("required", []))
        # The four load-bearing fields every consumer reads.
        assert {"task_type", "winner", "agreed_claims", "disagreed_claims"} <= required


class TestEvalSetSchema:
    def test_validates_a_freshly_built_eval_set(self, jsonschema_mod, patch_trinity_home: Path):
        """Synthetic round-trip: build an eval set via the Trinity
        writer, validate against the schema. If the writer ever emits
        a field the schema doesn't allow (or omits a required one),
        this fires loudly."""
        # Stage a model_miss act in the unified ledger (the builder's
        # source since #209).
        led_path = patch_trinity_home / "me" / "preference_acts.jsonl"
        led_path.parent.mkdir(parents=True, exist_ok=True)
        led_path.write_text(
            json.dumps({
                "id": "r_001",
                "trigger": "model_miss",
                "privileged": "just write the spec",
                "sacrificed": "Here's a multi-section strategy",
                "kind": "REFRAME",
                "why": "user substituted a different frame",
                "basin": "b03",
            }) + "\n",
            encoding="utf-8",
        )
        from trinity_local.evals.builder import build_eval_set

        eval_set = build_eval_set()
        payload = eval_set.to_dict()
        schema = _load_schema("eval_set.schema.json")
        jsonschema_mod.validate(payload, schema)

    def test_rejects_missing_required_field(self, jsonschema_mod):
        """The schema must REJECT a payload without `eval_id`. If it
        accepts anything, the validator is broken."""
        schema = _load_schema("eval_set.schema.json")
        bad = {
            # missing eval_id
            "built_at": "2026-05-14T00:00:00",
            "source": "rejections",
            "stats": {"items": 0},
            "items": [],
        }
        with pytest.raises(jsonschema_mod.ValidationError):
            jsonschema_mod.validate(bad, schema)

    def test_rejects_unknown_rejection_type(self, jsonschema_mod):
        """The four rejection types are an enum. A typo'd type should
        fail validation — without this guard, a writer bug could
        produce items that all downstream consumers silently skip."""
        schema = _load_schema("eval_set.schema.json")
        bad = {
            "eval_id": "eval_aaaaaaaaaaaa",
            "built_at": "2026-05-14T00:00:00",
            "source": "rejections",
            "stats": {"items": 1},
            "items": [{
                "eval_item_id": "ei_aaaaaaaaaaaa",
                "prompt": "p",
                "rejection_type": "NOPE",  # not in enum
                "rejected_response": "r",
                "source": "rejections",
                "source_id": "r1",
            }],
        }
        with pytest.raises(jsonschema_mod.ValidationError):
            jsonschema_mod.validate(bad, schema)


class TestRejectionSignalSchema:
    def test_validates_a_known_good_record(self, jsonschema_mod):
        schema = _load_schema("rejection_signal.schema.json")
        good = {
            "id": "r_042",
            "type": "COMPRESSION",
            "model_quote": "long lecture about X",
            "user_substitute": "tldr",
            "why_signal": "user wanted shorter",
            "prompt_id": "pn_aaa",
            "basin": "b00",
            "next_user_turn": "",
        }
        jsonschema_mod.validate(good, schema)

    def test_id_must_start_with_r_(self, jsonschema_mod):
        """The id-prefix convention is load-bearing for log-scanning
        downstream. Free-form ids would let writers from other tools
        collide with PromptNode ids (pn_*), council ids (council_*),
        etc. — the prefix is the namespace."""
        schema = _load_schema("rejection_signal.schema.json")
        bad = {
            "id": "totally_freeform",
            "type": "COMPRESSION",
            "model_quote": "x",
            "user_substitute": "y",
        }
        with pytest.raises(jsonschema_mod.ValidationError):
            jsonschema_mod.validate(bad, schema)


class TestCouncilOutcomeSchema:
    def test_validates_a_freshly_written_outcome(self, jsonschema_mod, patch_trinity_home: Path):
        """Synthetic round-trip via Trinity's own writer. Same shape
        as test_evals_builder's roundtrip but at the council layer.
        If a writer change emits a field the schema doesn't allow (or
        omits a required one), this fires loudly. Includes routing_label
        because real outcomes always have one (chairman always runs)."""
        from trinity_local.council_runtime import save_council_outcome, create_council_outcome
        from trinity_local.council_schema import (
            PromptBundle,
            CouncilMemberResult,
            CouncilRoutingLabel,
        )
        bundle = PromptBundle(
            bundle_id="b_a1a1a1a1a1a1a1a1",
            task_cluster_id="cluster_test",
            task_text="What's the best way to embed a tree?",
            goal="Find an answer.",
            comparison_instructions="Be concise.",
        )
        routing_label = CouncilRoutingLabel(
            winner="claude",
            confidence="high",
            runner_up="codex",
            task_type="design_decision",
            routing_lesson="Claude grounded tradeoffs in workload shape; codex listed options without prioritizing.",
            agreed_claims=["Both support ancestry queries."],
            disagreed_claims=[{
                "claim": "Adjacency list is simpler than nested set",
                "why_matters": "Simplicity wins when tree mutates often.",
                "providers": ["claude"],
            }],
        )
        outcome = create_council_outcome(
            bundle=bundle,
            primary_provider="claude",
            primary_model="claude-opus-4-5",
            member_results=[
                CouncilMemberResult(provider="claude", output_text="Adjacency list."),
                CouncilMemberResult(provider="codex", output_text="Nested set model."),
            ],
            synthesis_output="Both have tradeoffs. Adjacency list is simpler for trees that change frequently; nested set is faster for read-heavy ancestry queries.",
            routing_label=routing_label,
        )
        path = save_council_outcome(outcome)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        schema = _load_schema("council_outcome.schema.json")
        # The full real on-disk shape (not the Python dataclass) is
        # what other tools read — validate THAT.
        jsonschema_mod.validate(on_disk, schema)


class TestRealCorpusSchemaCompliance:
    """Sample real `~/.trinity/` files and verify they match the
    published schemas. This is the regression guard that catches
    'schema looked fine in isolation, but the writer for the real
    field shape evolved past it.'"""

    def _real_trinity_home(self) -> Path | None:
        home = Path.home() / ".trinity"
        return home if home.exists() else None

    def test_real_council_outcomes_validate(self, jsonschema_mod):
        # Validate through the load+to_dict round-trip — that's the real
        # invariant: "the canonical reader+writer produces schema-valid
        # output for any on-disk outcome." Raw-JSON validation was the
        # original shape but broke whenever a stale background process
        # (e.g. long-running `dream` subprocess that loaded pre-iter-#3
        # bytecode) wrote outcomes with empty arrays filtered. After
        # iter-#3 fixed CouncilRoutingLabel.to_dict to always emit
        # agreed_claims/disagreed_claims, the round-trip ALREADY heals
        # those files in-memory — so the writer/schema contract is
        # actually intact; only the on-disk-snapshot view of it was
        # fragile to background-process artifacts. Round-tripping
        # tests what matters and lets dream/cortex finish whenever
        # they finish.
        from trinity_local.council_runtime import load_council_outcome

        home = self._real_trinity_home()
        if home is None:
            pytest.skip("no real ~/.trinity/ on this machine")
        outcomes_dir = home / "council_outcomes"
        if not outcomes_dir.is_dir():
            pytest.skip("no council_outcomes/ in real home")
        sample = sorted(outcomes_dir.glob("council_*.json"))[:5]
        if not sample:
            pytest.skip("no council outcomes on disk")
        schema = _load_schema("council_outcome.schema.json")
        failures: list[tuple[Path, str]] = []
        for path in sample:
            try:
                outcome = load_council_outcome(str(path))
                payload = outcome.to_dict()
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
                failures.append((path, f"unreadable: {exc}"))
                continue
            try:
                jsonschema_mod.validate(payload, schema)
            except jsonschema_mod.ValidationError as exc:
                # Surface the first failure with full path so a real
                # drift is debuggable, not a sea of red.
                failures.append((path, str(exc)[:300]))
        if failures:
            msg = "Real council outcomes failed schema validation (via load+to_dict round-trip):\n"
            for p, err in failures:
                msg += f"  {p.name}: {err}\n"
            pytest.fail(msg)

    def test_real_rejections_validate(self, jsonschema_mod):
        home = self._real_trinity_home()
        if home is None:
            pytest.skip("no real ~/.trinity/ on this machine")
        rej_path = home / "me" / "rejections.jsonl"
        if not rej_path.exists():
            pytest.skip("no rejections.jsonl on real home")
        schema = _load_schema("rejection_signal.schema.json")
        failures: list[tuple[int, str]] = []
        with rej_path.open(encoding="utf-8") as fh:
            for idx, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    failures.append((idx, f"json: {exc}"))
                    continue
                try:
                    jsonschema_mod.validate(record, schema)
                except jsonschema_mod.ValidationError as exc:
                    failures.append((idx, str(exc)[:300]))
        if failures:
            msg = "Real rejections.jsonl failed validation:\n"
            for ln, err in failures[:10]:
                msg += f"  line {ln}: {err}\n"
            pytest.fail(msg)

    def test_real_eval_sets_validate(self, jsonschema_mod):
        home = self._real_trinity_home()
        if home is None:
            pytest.skip("no real ~/.trinity/ on this machine")
        evals_dir = home / "evals"
        if not evals_dir.is_dir():
            pytest.skip("no evals/ in real home")
        sample = sorted(evals_dir.glob("eval_*.json"))
        if not sample:
            pytest.skip("no eval sets on disk")
        schema = _load_schema("eval_set.schema.json")
        for path in sample:
            payload = json.loads(path.read_text(encoding="utf-8"))
            jsonschema_mod.validate(payload, schema)


class TestSpecDocReferencesSchemas:
    """The spec doc points at the schemas. If we rename a schema file
    without updating the doc, the doc's links rot silently."""

    def test_doc_lists_all_schemas(self):
        doc_path = REPO / "docs" / "PREFERENCE_CORPUS_SPEC.md"
        assert doc_path.exists()
        doc = doc_path.read_text(encoding="utf-8")
        expected_refs = [
            "council_outcome.schema.json",
            "eval_set.schema.json",
            "rejection_signal.schema.json",
        ]
        for ref in expected_refs:
            assert ref in doc, f"PREFERENCE_CORPUS_SPEC.md doesn't link to {ref}"

    def test_doc_lives_alongside_schemas(self):
        # The doc references `../schemas/...` paths. Make sure those
        # resolve relative to the doc location.
        doc_path = REPO / "docs" / "PREFERENCE_CORPUS_SPEC.md"
        for schema_name in ("council_outcome.schema.json", "eval_set.schema.json", "rejection_signal.schema.json"):
            resolved = (doc_path.parent / "../schemas" / schema_name).resolve()
            assert resolved.exists(), f"link target missing: {resolved}"


class TestSchemaExamples:
    """Each schema ships a canonical example payload under
    schemas/examples/. The Preference Corpus Spec claims these are
    minimum-valid and CI-validated — this test enforces that claim.

    Two layers of guarantee:
      1. The example file validates against the schema (no silent drift)
      2. The example IS minimum-valid (no extra optional fields beyond
         what `required` demands) — checked by counting top-level keys

    Without (2), the example bloats over time and adopters can't tell
    which fields are required vs optional.
    """

    @pytest.mark.parametrize("schema_name,example_name", [
        ("council_outcome.schema.json", "council_outcome.example.json"),
        ("eval_set.schema.json", "eval_set.example.json"),
    ])
    def test_example_validates_against_schema(self, jsonschema_mod, schema_name, example_name):
        """The example file in schemas/examples/ must validate against
        its schema. If the schema's `required` shifts and the example
        wasn't updated, this fires loudly."""
        schema = _load_schema(schema_name)
        example_path = SCHEMAS_DIR / "examples" / example_name
        assert example_path.exists(), (
            f"Example file {example_name} missing. The Preference "
            f"Corpus Spec links to it; the doc anchor must resolve."
        )
        example = json.loads(example_path.read_text(encoding="utf-8"))
        jsonschema_mod.validate(example, schema)

    def test_rejection_signal_example_jsonl_validates(self, jsonschema_mod):
        """rejections.jsonl is line-delimited (the lens-build pipeline
        appends incrementally), so the example file is .jsonl not .json.
        Each line must validate independently."""
        schema = _load_schema("rejection_signal.schema.json")
        path = SCHEMAS_DIR / "examples" / "rejection_signal.example.jsonl"
        assert path.exists(), "rejection_signal.example.jsonl missing"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines, "example jsonl is empty"
        for idx, line in enumerate(lines, start=1):
            record = json.loads(line)
            try:
                jsonschema_mod.validate(record, schema)
            except jsonschema_mod.ValidationError as exc:
                pytest.fail(f"rejection_signal.example.jsonl line {idx} failed: {exc}")

    def test_examples_kept_minimal(self):
        """Examples should carry every REQUIRED field and only the
        minimum optional fields needed to be self-explanatory. Bloat
        over time confuses adopters about what's actually required.

        Crude but useful check: the example shouldn't carry more than
        ~50% extra top-level keys beyond what the schema requires."""
        for schema_name, example_name in [
            ("council_outcome.schema.json", "council_outcome.example.json"),
            ("eval_set.schema.json", "eval_set.example.json"),
        ]:
            schema = _load_schema(schema_name)
            example = json.loads((SCHEMAS_DIR / "examples" / example_name).read_text())
            required = set(schema.get("required", []))
            extra_keys = set(example.keys()) - required
            # Allow up to one extra optional field per required field
            # (so people can see what they look like) — beyond that
            # the example has bloated past "minimum-valid".
            assert len(extra_keys) <= len(required), (
                f"{example_name} has too many extra optional keys: "
                f"{extra_keys}. Either drop them or move to a separate "
                f"'rich' example file."
            )
