"""Tests for #148 — trinity-local import-export bulk Takeout import.

Detection-side tests use small synthetic files (no real ingest runs).
The actual parsers (parse_chatgpt_export / parse_claude_ai_export /
parse_gemini_takeout_html) are exercised by their own dedicated test
suites; this file locks down the auto-detect + dry-run + CLI shape.
"""
from __future__ import annotations

import argparse
import json

import pytest

from trinity_local.commands import import_export


@pytest.fixture
def export_root(tmp_path):
    return tmp_path / "exports"


def _write_chatgpt_conversations(path):
    """Synthetic ChatGPT export — first conversation has `mapping` key."""
    path.write_text(json.dumps([
        {
            "id": "conv1",
            "title": "test",
            "mapping": {"root": {"id": "root"}},
        }
    ]), encoding="utf-8")


def _write_claude_ai_conversations(path):
    """Synthetic Claude.ai export — first conversation has `chat_messages` key."""
    path.write_text(json.dumps([
        {
            "uuid": "conv1",
            "name": "test",
            "chat_messages": [],
        }
    ]), encoding="utf-8")


def _write_gemini_takeout_html(path):
    """Minimal stub — file existence + name is what _detect_single_file checks."""
    path.write_text("<html><body>fake takeout</body></html>", encoding="utf-8")


def _write_chatgpt_conversations_real(path):
    """ChatGPT export with a real id + user/assistant message nodes — unlike
    the detection stub, this actually produces a prompt on ingest. The top-
    level `id` is REQUIRED (parse_chatgpt_export skips conversations without
    one); real exports always include it."""
    path.write_text(json.dumps([{
        "id": "conv-real-1", "title": "DB choice", "create_time": 1735689600,
        "current_node": "n2",
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
            "n1": {"id": "n1", "parent": "root", "children": ["n2"], "message": {
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": ["Postgres or DynamoDB for an event log?"]},
                "create_time": 1735689600}},
            "n2": {"id": "n2", "parent": "n1", "children": [], "message": {
                "author": {"role": "assistant"},
                "content": {"content_type": "text", "parts": ["Depends on access patterns."]},
                "create_time": 1735689601}},
        },
    }]), encoding="utf-8")


def _write_claude_ai_conversations_real(path):
    """Claude.ai export with real chat_messages — produces a prompt on ingest."""
    path.write_text(json.dumps([{
        "uuid": "conv-real-2", "name": "Refactor", "created_at": "2026-01-02T00:00:00Z",
        "chat_messages": [
            {"uuid": "m1", "sender": "human", "text": "Staged refactor or rewrite?",
             "created_at": "2026-01-02T00:00:00Z"},
            {"uuid": "m2", "sender": "assistant", "text": "Stage it.",
             "created_at": "2026-01-02T00:00:01Z"},
        ],
    }]), encoding="utf-8")


def _write_gemini_takeout_myactivity(path):
    """Gemini Takeout MyActivity.html with one real activity cell — matches the
    outer-cell/content-cell shape parse_gemini_takeout_html expects (Prompted
    <a href>{prompt}</a> + <p>{response}</p> + a caption timestamp)."""
    cell = (
        '<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">'
        '<div class="mdl-grid">'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">'
        'Prompted <a href="https://gemini.google.com/app/abc123">'
        'Best framework for a small static site?</a><br>'
        '<p>Astro or 11ty for content-heavy static sites.</p></div>'
        '<div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1 '
        'mdl-typography--text-right"></div>'
        '<div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">'
        'Jan 3, 2026, 10:00:00 AM UTC</div>'
        '</div></div></div>'
    )
    path.write_text("<html><body>" + cell + "</body></html>", encoding="utf-8")


class TestDetectSingleFile:
    def test_chatgpt_conversations_json_detected(self, tmp_path):
        p = tmp_path / "conversations.json"
        _write_chatgpt_conversations(p)
        assert import_export._detect_single_file(p) == "chatgpt"

    def test_claude_ai_conversations_json_detected(self, tmp_path):
        p = tmp_path / "conversations.json"
        _write_claude_ai_conversations(p)
        assert import_export._detect_single_file(p) == "claude_ai"

    def test_gemini_takeout_html_detected_at_root(self, tmp_path):
        p = tmp_path / "MyActivity.html"
        _write_gemini_takeout_html(p)
        assert import_export._detect_single_file(p) == "gemini_takeout"

    def test_unknown_file_returns_none(self, tmp_path):
        p = tmp_path / "random.json"
        p.write_text('{"foo": "bar"}', encoding="utf-8")
        assert import_export._detect_single_file(p) is None

    def test_conversations_json_without_known_keys_returns_none(self, tmp_path):
        """A file named conversations.json that has neither `mapping` nor
        `chat_messages` shouldn't be classified — it's not a real export."""
        p = tmp_path / "conversations.json"
        p.write_text(json.dumps([{"foo": "bar"}]), encoding="utf-8")
        assert import_export._detect_single_file(p) is None

    def test_chatgpt_not_misdetected_when_text_mentions_chat_messages(self, tmp_path):
        """Disambiguation robustness (adversarial 2026-06-01): a ChatGPT export
        whose MESSAGE TEXT mentions `chat_messages` must still detect as chatgpt.
        Two properties make the 8KB substring probe safe: the structural
        `"mapping"` key precedes the message content, and JSON escaping means a
        quote-delimited `"chat_messages"` can only be a key (content becomes
        \\"chat_messages\\"). Guards a future order-insensitive refactor that would
        route the whole export through the wrong parser → silent garbage import."""
        p = tmp_path / "conversations.json"
        p.write_text(
            json.dumps([{
                "title": "t",
                "mapping": {"n1": {"id": "n1", "parent": None, "children": [],
                    "message": {"author": {"role": "user"}, "content": {
                        "content_type": "text",
                        "parts": ['does my "chat_messages" json mean claude?']}}}},
                "current_node": "n1", "conversation_id": "c1", "id": "c1",
            }]),
            encoding="utf-8",
        )
        assert import_export._detect_single_file(p) == "chatgpt"

    def test_claude_ai_not_misdetected_when_text_mentions_mapping(self, tmp_path):
        """Mirror: a Claude.ai export whose message text mentions `mapping` must
        still detect as claude_ai — `"chat_messages"` precedes the content."""
        p = tmp_path / "conversations.json"
        p.write_text(
            json.dumps([{
                "uuid": "u1", "name": "n", "created_at": "2024-01-01T00:00:00Z",
                "chat_messages": [{"uuid": "m1", "sender": "human",
                    "created_at": "2024-01-01T00:00:00Z",
                    "text": 'explain the "mapping" key in chatgpt exports'}],
            }]),
            encoding="utf-8",
        )
        assert import_export._detect_single_file(p) == "claude_ai"


class TestDetectExports:
    def test_directory_with_chatgpt_export(self, export_root):
        export_root.mkdir(parents=True)
        _write_chatgpt_conversations(export_root / "conversations.json")
        results = import_export.detect_exports(export_root)
        assert len(results) == 1
        assert results[0]["source"] == "chatgpt"
        assert results[0]["path"].endswith("conversations.json")

    def test_directory_with_multiple_exports(self, export_root):
        """A user may have downloaded both ChatGPT and Claude.ai exports
        and dropped them in one directory. All detected."""
        (export_root / "chatgpt").mkdir(parents=True)
        (export_root / "claude").mkdir(parents=True)
        _write_chatgpt_conversations(export_root / "chatgpt" / "conversations.json")
        _write_claude_ai_conversations(export_root / "claude" / "conversations.json")

        results = import_export.detect_exports(export_root)
        sources = sorted(r["source"] for r in results)
        assert sources == ["chatgpt", "claude_ai"]

    def test_gemini_takeout_nested_path(self, export_root):
        """Real Gemini Takeout layout: Takeout/My Activity/Gemini Apps/MyActivity.html"""
        nested = export_root / "Takeout" / "My Activity" / "Gemini Apps"
        nested.mkdir(parents=True)
        _write_gemini_takeout_html(nested / "MyActivity.html")

        results = import_export.detect_exports(export_root)
        assert len(results) == 1
        assert results[0]["source"] == "gemini_takeout"

    def test_empty_directory_returns_empty(self, export_root):
        export_root.mkdir(parents=True)
        assert import_export.detect_exports(export_root) == []

    def test_skips_common_noise_dirs(self, export_root):
        """node_modules, .venv, __pycache__ etc. shouldn't be probed even
        if they happen to contain a conversations.json."""
        for noise in ("node_modules", ".venv", "__pycache__"):
            d = export_root / noise
            d.mkdir(parents=True)
            _write_chatgpt_conversations(d / "conversations.json")

        # No real exports → detect returns empty (the synthetic
        # conversations.json files in noise dirs are skipped)
        results = import_export.detect_exports(export_root)
        assert results == []


class TestCliHandler:
    def _args(self, **overrides):
        defaults = dict(
            path=None, source=None, dry_run=True,
            limit=None, batch_size=64, dim=768,
            progress=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_missing_path_exits_with_error_json(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            import_export.handle_import_export(self._args(path=str(tmp_path / "missing")))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["ok"] is False
        assert "path not found" in payload["error"]

    def test_no_exports_detected_exits_with_hint(self, tmp_path, capsys):
        # Empty directory, no detection
        (tmp_path / "empty").mkdir()
        with pytest.raises(SystemExit) as exc:
            import_export.handle_import_export(self._args(path=str(tmp_path / "empty")))
        assert exc.value.code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert "no exports detected" in payload["error"]
        # Hint must mention the expected shapes so user knows what to try
        assert "conversations.json" in payload["hint"]
        assert "Takeout" in payload["hint"]

    def test_dry_run_reports_detected_without_ingesting(self, tmp_path, capsys):
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        _write_chatgpt_conversations(export_dir / "conversations.json")

        import_export.handle_import_export(self._args(path=str(export_dir), dry_run=True))
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["mode"] == "dry-run"
        assert len(payload["detected"]) == 1
        assert payload["detected"][0]["source"] == "chatgpt"

    def test_force_source_bypasses_detection(self, tmp_path, capsys):
        """--source overrides auto-detect. Useful when probe heuristics
        get it wrong (e.g., renamed file)."""
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        renamed = export_dir / "my_chatgpt_dump.json"
        _write_chatgpt_conversations(renamed)

        import_export.handle_import_export(
            self._args(path=str(renamed), source="chatgpt", dry_run=True)
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["detected"][0]["source"] == "chatgpt"
        assert "forced" in payload["detected"][0]["hint"]

    def test_real_ingest_round_trip_lands_prompts(self, tmp_path, monkeypatch, capsys):
        """The WHOLE POINT of import-export: a non-coder's ChatGPT + Claude.ai
        exports must actually become indexed prompts. Detection + dry-run tests
        stay green even if the parse→ingest wiring breaks, so the real round-trip
        was unguarded — and the founder (CLI transcripts) never exercises it.
        Forces TF-IDF for a fast, deterministic ingest (we assert prompt COUNT,
        not embedding quality)."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        export_dir = tmp_path / "ex"
        (export_dir / "chatgpt").mkdir(parents=True)
        (export_dir / "claude").mkdir(parents=True)
        _write_chatgpt_conversations_real(export_dir / "chatgpt" / "conversations.json")
        _write_claude_ai_conversations_real(export_dir / "claude" / "conversations.json")

        import_export.handle_import_export(self._args(path=str(export_dir), dry_run=False))
        payload = json.loads(capsys.readouterr().out)

        assert payload["totals"]["prompts_indexed"] >= 2, payload["totals"]
        seen = payload["per_source_sessions_seen"]
        assert seen.get("chatgpt", 0) >= 1, seen
        assert seen.get("claude_ai", 0) >= 1, seen
        # Prompts actually landed in the index with the right provenance.
        from trinity_local.state_paths import trinity_home
        pf = trinity_home() / "prompts" / "prompt_nodes.jsonl"
        assert pf.exists(), "import did not create the prompt index"
        provs = {
            json.loads(line).get("provider")
            for line in pf.read_text().splitlines() if line
        }
        assert "chatgpt" in provs and "claude_ai" in provs, provs

    def test_real_ingest_round_trip_gemini_takeout(self, tmp_path, monkeypatch, capsys):
        """Completes the 3-format round-trip coverage: Gemini Takeout
        MyActivity.html → handle_import_export → indexed prompts. The HTML
        parser has its own suite; this locks the import-export WIRING for the
        gemini path (detect → parse → ingest), which the chatgpt/claude
        round-trip doesn't exercise (different format, different parser).
        TF-IDF-forced so the parser uses the deterministic v2 time-proximity
        grouping rather than the MLX-only v3 embedding grouping."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        _write_gemini_takeout_myactivity(export_dir / "MyActivity.html")

        import_export.handle_import_export(self._args(path=str(export_dir), dry_run=False))
        payload = json.loads(capsys.readouterr().out)

        assert payload["per_source_sessions_seen"].get("gemini_takeout", 0) >= 1, payload
        assert payload["totals"]["prompts_indexed"] >= 1, payload["totals"]
        from trinity_local.state_paths import trinity_home
        pf = trinity_home() / "prompts" / "prompt_nodes.jsonl"
        assert pf.exists(), "gemini import did not create the prompt index"
        provs = {
            json.loads(line).get("provider")
            for line in pf.read_text().splitlines() if line
        }
        assert "gemini" in provs, provs
        # A successful import must end with a NEXT STEP, not a dead-end count —
        # a non-coder who just imported their history needs to know building the
        # lens is the actual value (usefulness, 2026-06-06). Gated on
        # prompts_indexed > 0, so a zero-yield import gets the honest `warnings`
        # instead (TestZeroYieldWarning), never a premature "now build your lens".
        assert payload.get("next_steps"), f"successful import gave no next-step guidance: {payload}"
        assert any("trinity-local lens" in s for s in payload["next_steps"]), payload["next_steps"]


class TestZeroYieldWarning:
    """A source can be DETECTED yet parse to 0 sessions — an empty-activity
    export (Takeout from an account that never used the product) or vendor
    HTML drift. ``ok: true`` + all-zero totals then reads as a successful
    import when nothing landed (the green-while-degenerate trap). The
    ``warnings`` array makes that honest. Guards #238 honest-degradation."""

    def _args(self, **overrides):
        defaults = dict(
            path=None, source=None, dry_run=True,
            limit=None, batch_size=64, dim=768, progress=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_detected_but_empty_source_warns(self):
        detected = [{"source": "gemini_takeout", "path": "/x/MyActivity.html", "hint": "h"}]
        w = import_export._zero_yield_warnings(detected, {"gemini_takeout": 0}, None)
        assert len(w) == 1
        assert w[0]["source"] == "gemini_takeout"
        assert "/x/MyActivity.html" in w[0]["paths"]
        assert "0 sessions" in w[0]["message"]

    def test_source_with_sessions_no_warning(self):
        detected = [{"source": "chatgpt", "path": "/x/conversations.json", "hint": "h"}]
        assert import_export._zero_yield_warnings(
            detected, {"chatgpt": 3}, None, prompts_indexed=3
        ) == []

    def test_mixed_only_empty_source_warns(self):
        detected = [
            {"source": "chatgpt", "path": "/c.json", "hint": "c"},
            {"source": "gemini_takeout", "path": "/g.html", "hint": "g"},
        ]
        w = import_export._zero_yield_warnings(
            detected, {"chatgpt": 2, "gemini_takeout": 0}, None, prompts_indexed=2
        )
        assert [x["source"] for x in w] == ["gemini_takeout"]

    def test_zero_or_negative_limit_suppresses_warning(self):
        # --limit 0 (or negative) legitimately yields nothing; not a degradation.
        detected = [{"source": "chatgpt", "path": "/c.json", "hint": "c"}]
        assert import_export._zero_yield_warnings(detected, {"chatgpt": 0}, 0) == []
        assert import_export._zero_yield_warnings(detected, {"chatgpt": 0}, -1) == []
        # A positive limit that simply wasn't reached still warns on a 0 yield.
        assert len(import_export._zero_yield_warnings(detected, {"chatgpt": 0}, 5)) == 1

    def test_parsed_but_nothing_staged_warns(self):
        """The SECOND zero-yield shape (360-loop live probe 2026-07-02): sessions
        parse fine (seen > 0) but 0 prompts index — a re-import where everything
        dedups, or threads with no user turns. Previously returned bare zeros
        with NO warning: ``ok: true`` + all-zero totals + silence, the exact
        dead-end shape 1 was built to prevent."""
        detected = [{"source": "chatgpt", "path": "/c.json", "hint": "c"}]
        w = import_export._zero_yield_warnings(
            detected, {"chatgpt": 1}, None, prompts_indexed=0
        )
        assert len(w) == 1 and w[0]["source"] == "all", w
        assert "indexed 0 prompts" in w[0]["message"]
        # A healthy import (prompts landed) must not fire it.
        assert import_export._zero_yield_warnings(
            detected, {"chatgpt": 1}, None, prompts_indexed=5
        ) == []
        # --limit <= 0 suppresses this shape too (expected zero, not degraded).
        assert import_export._zero_yield_warnings(
            detected, {"chatgpt": 1}, 0, prompts_indexed=0
        ) == []

    def test_handler_warns_when_sessions_parse_but_nothing_stages(
        self, tmp_path, monkeypatch, capsys
    ):
        """End-to-end through the handler: a ChatGPT export whose only thread
        carries NO user turns parses (sessions_seen=1) but stages nothing
        (prompts_indexed=0). The output must carry the parsed-but-nothing-staged
        warning, not silent ``ok: true`` zeros (360-loop live probe 2026-07-02)."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        conv = [{
            "title": "assistant-only thread",
            "create_time": 1750000000, "update_time": 1750000001,
            "mapping": {
                "root": {"id": "root", "message": None, "parent": None,
                         "children": ["m1"]},
                "m1": {"id": "m1", "parent": "root", "children": [],
                       "message": {"id": "m1", "author": {"role": "assistant"},
                                   "create_time": 1750000000,
                                   "content": {"content_type": "text",
                                               "parts": ["assistant only"]},
                                   "status": "finished_successfully"}},
            },
            "conversation_id": "c1", "current_node": "m1",
        }]
        (export_dir / "conversations.json").write_text(
            json.dumps(conv), encoding="utf-8"
        )
        import_export.handle_import_export(
            self._args(path=str(export_dir), dry_run=False)
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["totals"]["sessions_seen"] == 1
        assert payload["totals"]["prompts_indexed"] == 0
        assert "warnings" in payload, payload
        assert any(w["source"] == "all" for w in payload["warnings"]), payload
        # No false success CTA on a zero-yield import.
        assert "next_steps" not in payload, payload

    def test_handler_surfaces_warning_for_empty_export(self, tmp_path, monkeypatch, capsys):
        """End-to-end through the handler: an empty-activity Gemini Takeout
        (valid file, 0 outer-cells) imports nothing — the output must carry a
        warning, not silent ``ok: true``."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        ga = tmp_path / "ex" / "My Activity" / "Gemini Apps"
        ga.mkdir(parents=True)
        (ga / "MyActivity.html").write_text(
            "<html><body><div class='header-cell'>Gemini Apps Activity</div>"
            "</body></html>",
            encoding="utf-8",
        )
        import_export.handle_import_export(self._args(path=str(tmp_path / "ex"), dry_run=False))
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True  # command ran fine...
        assert payload["totals"]["prompts_indexed"] == 0  # ...but imported nothing
        assert "warnings" in payload, payload
        assert payload["warnings"][0]["source"] == "gemini_takeout"

    def test_handler_no_warning_when_all_sources_yield(self, tmp_path, monkeypatch, capsys):
        """A healthy import must NOT carry a spurious warning."""
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        _write_chatgpt_conversations_real(export_dir / "conversations.json")
        import_export.handle_import_export(self._args(path=str(export_dir), dry_run=False))
        payload = json.loads(capsys.readouterr().out)
        assert payload["totals"]["prompts_indexed"] >= 1, payload["totals"]
        assert "warnings" not in payload, payload


def test_cli_registration_lists_import_export_subcommand():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    import_export.register(subparsers)
    args = parser.parse_args(["import-export", "/tmp/example", "--dry-run"])
    assert args.command == "import-export"
    assert args.path == "/tmp/example"
    assert args.dry_run is True


class TestActionAllowlist:
    """#148 launchpad UI surface (slice 2 of #148): capture-host action
    dispatch entries so the launchpad's file-picker button fires
    `import-export` via Native Messaging.

    Same guard shape as test_memory_health's test_dream_in_action_allowlist
    — without the allowlist entry the dispatch silently no-ops.
    """

    def test_import_export_in_allowlist(self):
        from trinity_local.capture_host import ACTION_ALLOWLIST
        assert "import-export" in ACTION_ALLOWLIST
        entry = ACTION_ALLOWLIST["import-export"]
        assert entry[0] == "import-export"
        # path is required so the dispatch can't fire blind
        arg_spec = entry[1]
        required_args = [name for name, _, required in arg_spec if required]
        assert "path" in required_args

    def test_import_export_dry_run_in_allowlist(self):
        """Detection-only variant — same CLI, --dry-run as a constant
        flag so payload can't escalate to full ingest by omitting it."""
        from trinity_local.capture_host import ACTION_ALLOWLIST
        assert "import-export-dry-run" in ACTION_ALLOWLIST
        entry = ACTION_ALLOWLIST["import-export-dry-run"]
        assert entry[0] == "import-export"
        # Constant flags include --dry-run — that's host-controlled,
        # not payload-influenced (the security property).
        constant_flags = entry[2] if len(entry) == 3 else []
        assert "--dry-run" in constant_flags

    def test_launchpad_renders_import_card_with_dispatch_wiring(self):
        """#148 UI slice: the launchpad import-export card must render
        with the paste-path input, Probe/Import buttons, and both
        extensionAction kinds wired (dry-run + full). The Vue state
        machine must include importPath / importStatus / importProbeResult.
        Same guard pattern as memory-health's button test."""
        from trinity_local.launchpad_template import render_launchpad_html

        html = render_launchpad_html(page_data={})
        # Card header + intro copy
        assert "Bulk import" in html, "card eyebrow missing"  # no internal task # in rendered copy
        assert "Import old Claude / ChatGPT / Gemini exports" in html, "h2 missing"
        # Paste-path input
        assert 'v-model="importPath"' in html, "path input missing"
        # Both action buttons
        assert '@click="probeImportPath"' in html, "Probe handler missing"
        assert '@click="confirmImport"' in html, "Import handler missing"
        # Both extensionAction kinds — dry-run for probe + full for confirm
        assert "kind: 'import-export-dry-run'" in html, "dry-run kind missing"
        assert "kind: 'import-export'" in html, "full-import kind missing"
        # Vue state machine fields
        assert "importPath:" in html, "importPath state field missing"
        assert "importStatus:" in html, "importStatus state field missing"
        assert "importProbeResult:" in html, "importProbeResult state field missing"
        # Path is passed through the dispatch payload — required for the
        # capture-host action allowlist to receive a real value.
        assert "path: this.importPath" in html, "path payload not threaded"

    def test_path_flag_alias_works(self, tmp_path, capsys):
        """The launchpad action dispatcher invokes the CLI via
        --flag VALUE pairs, so --path must work as an alias for the
        positional path argument."""
        import argparse
        export_dir = tmp_path / "ex"
        export_dir.mkdir()
        _write_chatgpt_conversations(export_dir / "conversations.json")

        ns = argparse.Namespace(
            path=None,  # positional unset
            path_flag=str(export_dir),
            source=None,
            dry_run=True,
            limit=None,
            batch_size=64,
            dim=768,
        )
        import_export.handle_import_export(ns)
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["mode"] == "dry-run"
