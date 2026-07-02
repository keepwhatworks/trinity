"""Tests for the council-first frontend flow."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from trinity_local.adapters import AdapterStatus
from trinity_local.commands.council import handle_council_launch, handle_council_start
from trinity_local.config import AppConfig, ProviderConfig
from trinity_local.council_runner import run_council
from trinity_local.council_runtime import create_prompt_bundle, save_prompt_bundle
from trinity_local.council_status import load_council_status, write_council_status
from trinity_local.dispatch_registry import command_for_dispatch, make_dispatch_action
from trinity_local.launchpad_page import write_portal_html
from trinity_local.providers import ProviderError, ProviderResult
from trinity_local.telemetry import (
    build_elo_snapshot,
    disable_telemetry,
    enable_telemetry,
    launchpad_telemetry_state,
    load_telemetry_settings,
    reset_share_install_id,
)


def _write_council_fixture(home: Path) -> tuple[str, str]:
    bundle = create_prompt_bundle(
        task_cluster_id="cluster_marketing_launch",
        task_text="Write a launch announcement for Trinity Local",
        goal="Find the strongest answer.",
        comparison_instructions="Prefer the clearest and most persuasive draft.",
        metadata={"project_hint": "marketing"},
    )
    save_prompt_bundle(bundle)

    council_id = "council_test_launchpad"
    payload = {
        "council_run_id": council_id,
        "bundle_id": bundle.bundle_id,
        "task_cluster_id": bundle.task_cluster_id,
        "primary_provider": "claude",
        "winner_provider": "antigravity",
        "created_at": "2026-04-28T10:00:00+00:00",
        "member_results": [
            {
                "provider": "claude",
                "model": "claude-sonnet",
                "output_text": "Launch copy focused on product clarity.",
            },
            {
                "provider": "antigravity",
                "model": "gemini-pro",
                "output_text": "Launch copy focused on narrative and social spread.",
            },
            {
                "provider": "codex",
                "model": "o3",
                "output_text": "Launch copy focused on technical builders.",
            },
        ],
    }
    path = home / "council_outcomes" / f"{council_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return bundle.bundle_id, council_id


class TestLaunchpadFlow:
    def test_write_portal_html_renders_primary_flow(self, patch_trinity_home: Path, monkeypatch):
        _write_council_fixture(patch_trinity_home)
        enable_telemetry(endpoint="https://telemetry.example/collect")
        monkeypatch.setattr(
            "trinity_local.launchpad_data.check_all_adapters",
            lambda: [
                AdapterStatus(provider="claude", cli_name="claude", installed=True, version="1.0.0"),
                AdapterStatus(
                    provider="codex",
                    cli_name="codex",
                    installed=False,
                    error="codex not found in PATH",
                ),
            ],
        )

        path = write_portal_html(title="Launchpad")

        assert path.exists()
        html = path.read_text(encoding="utf-8")
        # Hero copy for cold-start users (no recent councils) — locked
        # to the post-2026-05-16 brand axis hero. Returning users see
        # "Run a Council" instead.
        assert "Ask all three. Keep what works." in html
        assert "launch_council" in html
        assert "Launchpad controls" in html
        # IIFE form — Chrome blocks ES module imports on file:// (every
        # file URL is its own origin). Renamed from .es.js → .iife.js
        # when the launchpad switched to plain <script src>.
        assert "petite-vue.iife.js" in html
        assert "chart.umd.min.js" in html
        # The fixture's task_text appears in the recent-councils section
        # (legit rendering) — keep this assertion as the smoke check that
        # the recent-council rendering pipeline still wires through.
        assert "Write a launch announcement for Trinity Local" in html
        # Autofill ("Top used council queries", "Matching previous council
        # queries") removed 2026-05-26 — session-noise prompts were
        # polluting the launchpad. The whole replay-candidates pipeline
        # came out: _load_replay_candidates, councilSuggestions data,
        # filteredCouncilSuggestions getter, applySuggestion handler,
        # suggestions-panel template, suggestion-* CSS — all gone.
        assert "Top used council queries" not in html
        assert "Matching previous council queries" not in html
        # Council history moved to the persistent left rail (the
        # ChatGPT/claude.ai pattern, founder 2026-06-01: "why dup council
        # history on the main page"). The in-page "Every council you've run"
        # grid was removed; the rail is now the single home for the full list.
        assert 'class="council-rail"' in html
        assert ">Councils</h2>" in html
        # The rail's "+ Ask a new council" button was removed 2026-06-17
        # (a397d2d1, founder call) — it was redundant with the focal composer
        # the rail already scrolls back to. Assert it's GONE so the removal
        # can't silently regress.
        assert "+ Ask a new council" not in html
        assert "Every council you've run" not in html
        assert "telemetry-enable" in html
        assert "Ingest transcripts once now" in html
        assert "Reference evals" in html
        # Lens readability fix 2026-05-21: paired-lens failure-mode
        # lines changed from "pure-<pole> fails as <failure>" to
        # "<pole> → <failure>". Drops 2 filler tokens per line; the
        # arrow IS the verb. Guard the change because the previous
        # shape was sticky muscle memory across the codebase.
        assert 'taste-failure-arrow' in html, (
            "lens card lost the pole → failure-mode arrow shape"
        )
        # The Vue template literal is `{{ p.pole_a }} <span>→</span> <b>...</b>`
        # in the rendered page. After Vue mounts, this becomes
        # "boldness → arrogance". The pre-change shape was
        # "pure-boldness fails as arrogance" — guard the absence
        # of BOTH artifacts at the template (pre-mount) level.
        # Note: "fails as" CAN appear in HTML comments documenting
        # the change; the guard targets RENDERED text inside the
        # failure-line spans only.
        import re
        failure_lines = re.findall(
            r'<span class="taste-failure-line">.*?</span>\s*</span>',
            html, re.DOTALL
        )
        for line in failure_lines:
            assert "pure-" not in line, (
                f'lens line still carries "pure-" prefix: {line[:200]}'
            )
            assert "fails as" not in line, (
                f'lens line still carries "fails as" filler: {line[:200]}'
            )
        # The browser-capture card stays demoted into a <details> wrapper
        # (2026-05-21, council_1f9cbecd7104f90f #4) so the launchpad stays
        # focused. The EVAL value-proof card was PROMOTED out of its wrapper
        # 2026-06-07 (founder: "promote the eval moat") — the rejection-signal
        # benchmark is the one artifact no model vendor / request-router can copy,
        # so it leads as a value card rather than hiding behind a click.
        assert 'class="demoted-card-wrapper"' in html, (
            "the browser-capture card must stay demoted into a <details> wrapper"
        )
        # Exactly ONE wrapper now (browser-capture); the eval card is promoted.
        assert html.count('class="demoted-card-wrapper"') == 1, (
            "Expected exactly 1 demoted-card-wrapper (browser-capture); the eval "
            "value-proof card was promoted out of its <details>."
        )
        # The eval moat is a PROMINENT value card, not demoted: its lead copy
        # renders, and it is NOT inside the demoted wrapper.
        assert "proven on your rejections" in html, (
            "the promoted eval card must lead with the rejection-signal moat copy"
        )
        assert "no model vendor or request-router can copy" in html, (
            "the eval moat's unfakable-signal differentiator must render"
        )
        assert "liveReviewUrl" in html
        assert "Stop council" in html
        assert "Open council page" in html
        assert "Codex CLI" in html
        assert "npm install -g @openai/codex && codex --login" in html
        assert "Quick start examples" not in html
        # Autofill ("Top used council queries") removed 2026-05-26 —
        # session-noise prompts were polluting the launchpad. The
        # backend (_load_replay_candidates), the Vue reactive state
        # (councilSuggestions, filteredCouncilSuggestions, suggestionsOpen),
        # the methods (openSuggestions, applySuggestion, etc.), and the
        # template <div class="suggestions-panel"> all came out together.
        assert "councilSuggestions" not in html
        assert "filteredCouncilSuggestions" not in html
        assert "suggestions-panel" not in html
        assert "applySuggestion" not in html
        assert "examplePrompts" not in html
        assert "ACTIVE_OPERATION_KEY" not in html
        assert "trinity:pending-operation" not in html
        assert "defaultIngestSources" not in html
        assert '"recentCouncils"' not in html
        assert '"launchpadUrl"' not in html
        assert "progressScriptBaseUrl" not in html
        assert "loadProgressScript" not in html
        # Optional-chained 2026-06-17 (6272543a): petite-vue re-evaluates this
        # binding in the same flush that clears `operation` to null on a failed
        # dispatch, so `operation.label` would throw — `operation?.label` guards it.
        assert "{{ operation?.label }}" in html
        assert "councilLoadingMessages" in html
        assert "window.addEventListener('pageshow'" in html
        assert "back_forward" in html
        assert "Reticulating splines..." in html
        assert "formatProviderLabel" in html
        assert "label: 'Analysis'" in html
        assert "Queued" in html
        assert "Running" in html
        assert "base.includes('?') ? `&t=${Date.now()}` : `?t=${Date.now()}`" in html
        # Combined ratings card: local Elo + reference evals charts side-by-side.
        assert "provider-elo-chart" in html
        assert "reference-evals-chart" in html
        assert "@{ example }" not in html
        assert "signal_page" not in html
        assert "Open review and choose winner" not in html

    def test_write_portal_html_cold_start_no_data(self, patch_trinity_home: Path, monkeypatch):
        """First-run user — no councils, no memories, no telemetry config.
        Existing test_write_portal_html_renders_primary_flow seeds a council
        fixture, which means the cold-start path (literally every first-time
        user's first paint) has never been exercised. This test fills that gap.

        Per principle #2 (file:// is the substrate), the cold-start launchpad
        must render without errors directly off `portal-html` on an empty home —
        no templates referencing missing data, no Python exceptions on the
        render path, no v-if guards left open.

        Per principle #14, this test now also serves as a regression guard:
        any future feature that assumes data exists will fail loud here.
        """
        # NB: no _write_council_fixture call — empty TRINITY_HOME.
        monkeypatch.setattr(
            "trinity_local.launchpad_data.check_all_adapters",
            lambda: [],
        )

        path = write_portal_html(title="Launchpad")

        assert path.exists()
        html = path.read_text(encoding="utf-8")
        # Hero copy + brand tagline must render even with zero data — these
        # are the FIRST thing a new user sees and they can't be data-gated.
        assert "Ask all three. Keep what works." in html
        # Petite-vue + Chart.js must load — the JS deps aren't data-conditional.
        # IIFE form post-2026-05-19 (ES module imports break on file:// in Chrome).
        assert "petite-vue.iife.js" in html
        # ...and the referenced vendor files must actually EXIST on disk, not just
        # be referenced in the HTML. write_portal_html (the public writer) used to
        # skip publish_vendor_files — only refresh_launchpad published — so a direct
        # call produced a launchpad that 404s `./vendor/petite-vue.iife.js`, leaving
        # window.__TRINITY_VUE__ undefined and the whole Vue app dead on first paint
        # (cold-start browser-found 2026-06-01). String-presence above is necessary
        # but not sufficient; this is the green-while-degenerate guard.
        vendor_dir = path.parent / "vendor"
        assert (vendor_dir / "petite-vue.iife.js").exists(), (
            "launchpad references ./vendor/petite-vue.iife.js but the file was not "
            "published — the Vue app will 404 its own runtime and never mount"
        )
        assert (vendor_dir / "chart.umd.min.js").exists()
        # Empty-state copy now lives in the council RAIL (the in-page grid was
        # removed when councils moved to the sidebar, founder 2026-06-01). Exact
        # string comes from build_recent_sidebar_html's fallback path.
        assert "No councils yet — ask one above." in html
        assert 'class="council-rail"' in html
        # Memory-health row must NOT render when nothing exists (per principle
        # #15: silence is the all-good state — v-if guards on empty issues
        # list). This guards against accidentally surfacing "stale" for files
        # that don't even exist.
        assert "memory-health-card" not in html or "0 issues" not in html
        # No raw template tokens leaked from petite-vue not resolving — these
        # are the classic "render-broke" signals.
        assert "{{ undefined }}" not in html
        assert "[object Object]" not in html

    def test_dead_runner_running_status_is_coerced_to_failed(self, patch_trinity_home: Path, monkeypatch):
        write_council_status(
            "launch_stale_123",
            status="running",
            task_text="Stale council",
            bundle_id="bundle_stale",
            council_id="bundle_stale",
            members={
                "claude": {"status": "done", "reasoning_summary": "Done."},
                "antigravity": {"status": "running", "started_at": "2026-04-30T12:00:00+00:00"},
            },
            active_provider="antigravity",
            active_providers=["antigravity"],
            metadata={"kind": "council"},
        )

        status_path = patch_trinity_home / "portal_pages" / "status" / "council_status_launch_stale_123.json"
        raw = json.loads(status_path.read_text(encoding="utf-8"))
        raw["runner_pid"] = 424242
        status_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")

        def fake_kill(pid, sig):
            raise OSError("No such process")

        monkeypatch.setattr("trinity_local.council_status.os.kill", fake_kill)
        monkeypatch.setattr("trinity_local.council_status.os.killpg", fake_kill)

        updated = load_council_status("launch_stale_123")

        assert updated is not None
        assert updated["status"] == "failed"
        assert updated["error"] == "Council runner exited before completion."
        assert updated["active_provider"] is None
        assert updated["members"]["antigravity"]["status"] == "failed"


class TestTelemetryFlow:
    def test_enable_disable_and_reset_round_trip(self, patch_trinity_home: Path):
        _write_council_fixture(patch_trinity_home)

        enabled = enable_telemetry(endpoint="https://telemetry.example/collect")
        assert enabled.sharing_enabled is True
        assert enabled.share_usage_events is True
        assert enabled.share_elo_summaries is True
        assert enabled.share_install_id.startswith("share_")

        persisted = load_telemetry_settings()
        assert persisted.endpoint == "https://telemetry.example/collect"
        assert persisted.share_install_id == enabled.share_install_id

        state = launchpad_telemetry_state()
        assert state["settings"]["sharing_enabled"] is True
        assert state["view_event"]["event"] == "launchpad_view"
        assert state["elo_event"]["event"] == "elo_snapshot"
        assert state["snapshot"]["council_count"] == 1
        assert state["snapshot"]["providers"]["antigravity"]["elo"] > 1500

        reset = reset_share_install_id()
        assert reset.share_install_id.startswith("share_")
        assert reset.share_install_id != enabled.share_install_id

        disabled = disable_telemetry()
        assert disabled.sharing_enabled is False

    def test_settings_modal_copy_matches_default_on_behaviour(self, patch_trinity_home: Path):
        """The settings-modal telemetry copy MUST match the code default
        (`sharing_enabled: bool = True` → default-ON; the same default this
        class round-trips above). Found 2026-06-02 driving the settings modal in
        a real browser on a COLD home: the toggle renders ON (default-on) while
        the copy said "Telemetry is opt-in" (off-by-default) — a self-
        contradictory, dishonest privacy disclosure. Founder decision
        (telemetry_feedback_loop_no_pii memory): keep `sharing_enabled=True`,
        reconcile the COPY toward on-by-default. This pins that reconciliation so
        the disclosure can't silently drift back to claiming opt-in while the
        behaviour is opt-out. The no-PII guarantee is guarded separately
        (test_telemetry_no_pii / test_launchpad_telemetry_send)."""
        from trinity_local.launchpad_template import render_launchpad_html
        from trinity_local.telemetry import TelemetrySettings

        # Anchor the guard to the actual default so it tracks the code, not a
        # hardcoded expectation: if the default is ON, the copy must not claim opt-in.
        assert TelemetrySettings().sharing_enabled is True, (
            "this guard assumes default-ON; if the default flips to off, the copy "
            "wording (and this assertion) must be revisited together"
        )
        html = render_launchpad_html(page_data={})
        assert "Telemetry is opt-in" not in html, (
            "settings copy claims 'Telemetry is opt-in' (off-by-default) while "
            "sharing_enabled defaults to True (ON) — a dishonest disclosure; the "
            "toggle renders ON. Reconcile the copy toward on-by-default."
        )
        assert "on by default" in html, (
            "settings copy must honestly state telemetry is on by default (with an "
            "off toggle) to match sharing_enabled=True"
        )

    def test_elo_snapshot_uses_chairman_winner(self, patch_trinity_home: Path):
        # The Elo snapshot ranks providers by the chairman's winner_provider —
        # no user-verdict override (the user-pick layer was retired).
        _write_council_fixture(patch_trinity_home)

        snapshot = build_elo_snapshot()
        assert snapshot["providers"]["antigravity"]["elo"] > snapshot["providers"]["claude"]["elo"]


class TestDispatchFlow:
    def test_launch_council_dispatch_maps_to_command(self):
        action = make_dispatch_action(
            "launch_council",
            args={
                "task": "Write a launch announcement",
                "goal": "Find the strongest answer.",
                "members": ["claude", "antigravity", "codex"],
                "primary_provider": "claude",
                "cwd": "/tmp/project",
                "notify": True,
                "open_browser": True,
            },
        )

        command = command_for_dispatch(action)

        assert command is not None
        assert command.startswith("trinity-local council-launch")
        assert "--task 'Write a launch announcement'" in command
        assert "--members claude antigravity codex" in command
        assert "--primary-provider claude" in command
        assert "--cwd /tmp/project" in command
        assert "--open-browser" in command

    def test_stop_council_dispatch_maps_to_command(self):
        action = make_dispatch_action(
            "stop_council",
            args={"status_token": "launch_123"},
        )

        command = command_for_dispatch(action)

        assert command == "trinity-local council-stop --status-token launch_123"


class TestCouncilLaunchCommand:
    def test_handle_council_launch_creates_bundle_and_delegates(
        self,
        patch_trinity_home: Path,
        monkeypatch,
    ):
        captured: dict[str, object] = {}

        def fake_start(args):
            captured["bundle"] = args.bundle
            captured["members"] = args.members
            captured["primary_provider"] = args.primary_provider
            captured["cwd"] = args.cwd
            captured["open_browser"] = args.open_browser

        monkeypatch.setattr("trinity_local.commands.council.handle_council_start", fake_start)

        args = SimpleNamespace(
            task="Compare launch announcement drafts",
            goal="Pick the strongest launch copy.",
            instructions="Prefer the clearest and most persuasive draft.",
            context_file=None,
            project_hint="marketing",
            members=["claude", "antigravity"],
            primary_provider="claude",
            cwd=".",
            open_browser=True,
            config=None,
            status_token="launch_token_123",
        )

        handle_council_launch(args)

        bundle_id = str(captured["bundle"])
        bundle_path = patch_trinity_home / "prompt_bundles" / f"{bundle_id}.json"
        assert bundle_path.exists()
        raw = json.loads(bundle_path.read_text(encoding="utf-8"))
        assert raw["task_text"] == "Compare launch announcement drafts"
        assert raw["goal"] == "Pick the strongest launch copy."
        assert raw["comparison_instructions"] == "Prefer the clearest and most persuasive draft."
        assert raw["origin_provider"] == "launchpad"
        assert raw["origin_session_id"] == "launch_token_123"
        assert raw["metadata"]["launch_source"] == "launchpad"
        assert raw["metadata"]["project_hint"] == "marketing"
        assert captured["members"] == ["claude", "antigravity"]
        assert captured["primary_provider"] == "claude"
        assert captured["open_browser"] is True
        assert (patch_trinity_home / "review_pages" / "live_council.html").exists()

    def test_handle_council_start_initializes_runner_state_and_refreshes_launchpad(
        self,
        patch_trinity_home: Path,
        monkeypatch,
    ):
        bundle = create_prompt_bundle(
            task_cluster_id="cluster_live_status",
            task_text="Explain the difference between a list and a tuple in Python.",
            goal="Find the strongest answer.",
            comparison_instructions="Prefer the clearest answer.",
        )
        save_prompt_bundle(bundle)

        refresh_calls: list[str] = []
        captured_status: dict[str, object] = {}

        monkeypatch.setattr("trinity_local.commands.council.load_config", lambda config: SimpleNamespace())
        monkeypatch.setattr(
            "trinity_local.commands.council.ensure_task_record",
            lambda **kwargs: SimpleNamespace(task_id="task_live", title="Live council", status="running"),
        )
        monkeypatch.setattr(
            "trinity_local.commands.council.save_task_record",
            lambda task: patch_trinity_home / "tasks" / "task_live.json",
        )
        monkeypatch.setattr(
            "trinity_local.commands.council.save_sync_record",
            lambda task: patch_trinity_home / "sync" / "task_live.json",
        )
        monkeypatch.setattr(
            "trinity_local.commands.council.refresh_launchpad",
            lambda: (refresh_calls.append("refresh") or patch_trinity_home / "portal_pages" / "launchpad.html"),
        )

        def fake_run_council(**kwargs):
            status = load_council_status("launch_token_live")
            captured_status["status"] = status
            return SimpleNamespace(
                task_path=patch_trinity_home / "tasks" / "task_live.json",
                sync_path=patch_trinity_home / "sync" / "task_live.json",
                review_path=patch_trinity_home / "review_pages" / "council_live.html",
                launches=[],
                outcome=SimpleNamespace(council_run_id="council_live"),
            )

        monkeypatch.setattr("trinity_local.commands.council.run_council", fake_run_council)
        # (load_task_record / create_review_ready_action / save_action patches
        # removed 2026-07-02 — the per-council action record retired, #332.)
        monkeypatch.setattr("trinity_local.commands.council.open_path", lambda path: False)

        args = SimpleNamespace(
            config=None,
            bundle=bundle.bundle_id,
            members=["claude", "antigravity", "codex"],
            primary_provider="claude",
            cwd=".",
            status_token="launch_token_live",
            open_browser=False,
            notify=False,
        )

        handle_council_start(args)

        assert refresh_calls
        assert len(refresh_calls) >= 2
        status = captured_status["status"]
        assert status is not None
        assert status["status"] == "running"
        assert status["runner_pid"] is not None
        assert status["runner_pgid"] is not None
        assert status["metadata"]["members"] == ["claude", "antigravity", "codex"]


# TestWatchStatusFlow retired 2026-05-17: the watch-once CLI + its
# watch_once() runtime were dropped along with the rest of the watcher
# subsystem. MCP `ask` fires incremental_ingest.ingest_recent() on
# every call now; ingest-recent CLI covers the manual case.


class TestCouncilFailureMetadata:
    def test_run_council_records_member_and_synthesis_failures(
        self,
        patch_trinity_home: Path,
        monkeypatch,
    ):
        # iter #106 strict contract: save_council_outcome refuses partial
        # outcomes (routing_label=None on a synthesis-failure path → raise).
        # This test originally asserted that synthesis-failure metadata was
        # persisted on the outcome; that path now correctly fails at save.
        # Reframed: chairman succeeds with a valid routing-json stub so save
        # succeeds, and the test pins member-failure metadata only. The
        # synthesis-failure-raises behavior is covered by the regression
        # guard `TestSaveCouncilOutcomeEnforcesSchemaRequiredFields` in
        # tests/test_doc_count_consistency.py (added in iter #106).
        config = AppConfig(
            max_turns=4,
            notifications=False,
            providers={
                "claude": ProviderConfig(
                    name="claude",
                    type="cli",
                    enabled=True,
                    label="Claude",
                    command=["claude"],
                    args=[],
                    task_types=set(),
                ),
                "antigravity": ProviderConfig(
                    name="antigravity",
                    type="cli",
                    enabled=True,
                    label="Gemini",
                    command=["antigravity"],
                    args=[],
                    task_types=set(),
                ),
                "codex": ProviderConfig(
                    name="codex",
                    type="codex",
                    enabled=True,
                    label="Codex",
                    command=["codex"],
                    args=[],
                    task_types=set(),
                ),
            },
            task_preferences={},
        )
        bundle = create_prompt_bundle(
            task_cluster_id="cluster_failure_case",
            task_text="Compare answers for this market question.",
            goal="Find the strongest answer.",
            comparison_instructions="Prefer the clearest answer.",
        )
        save_prompt_bundle(bundle)

        chairman_synthesis = """## Winner
- Provider: antigravity
- Confidence: medium

```routing-json
{"winner":"antigravity","confidence":"medium","task_type":"comparison"}
```
"""

        class FakeProvider:
            def __init__(self, name: str) -> None:
                self.name = name

            def run(self, prompt: str, cwd: Path) -> ProviderResult:
                if self.name == "antigravity":
                    return ProviderResult(
                        provider="antigravity",
                        stdout="Gemini answer",
                        stderr="",
                        returncode=0,
                    )
                # Chairman synthesizer call on claude — return a stub with
                # routing-json so save_council_outcome accepts the outcome.
                if self.name == "claude" and "synthesizer" in prompt.lower():
                    return ProviderResult(
                        provider="claude",
                        stdout=chairman_synthesis,
                        stderr="",
                        returncode=0,
                    )
                raise ProviderError(f"Provider binary not found: {self.name}")

        monkeypatch.setattr(
            "trinity_local.council_runner.make_provider",
            lambda provider_config: FakeProvider(provider_config.name),
        )

        result = run_council(
            config=config,
            bundle=bundle,
            member_providers=["claude", "antigravity", "codex"],
            primary_provider="claude",
            cwd=patch_trinity_home,
        )

        metadata = result.outcome.metadata
        # Member failures still bubble through to metadata; the synthesis
        # path now succeeds (via the chairman stub) so synthesis_failure /
        # synthesis_error are no longer populated on this path.
        assert metadata["failed_members"] == ["claude", "codex"]
        assert metadata["member_failures"] == [
            {
                "provider": "claude",
                "stage": "member",
                "reason": "exception",
                "error": "Provider binary not found: claude",
            },
            {
                "provider": "codex",
                "stage": "member",
                "reason": "exception",
                "error": "Provider binary not found: codex",
            },
        ]
        assert "synthesis_error" not in metadata
        assert "synthesis_failure" not in metadata


class TestCouncilStopCommand:
    def test_handle_council_stop_updates_status_and_kills_process(self, patch_trinity_home: Path, monkeypatch, capsys):
        from trinity_local.commands.council import handle_council_stop
        from trinity_local.council_status import write_council_status

        write_council_status(
            "launch_stop_123",
            status="running",
            task_text="Stop this council",
            bundle_id="bundle_123",
            council_id="bundle_123",
            metadata={
                "kind": "council",
                "members": ["claude", "antigravity", "codex"],
                "pid": 111,
                "process_group_id": 222,
            },
        )
        monkeypatch.setattr("trinity_local.commands.council.refresh_launchpad", lambda: Path("/tmp/launchpad.html"))
        killed: list[tuple[int, int]] = []
        monkeypatch.setattr("trinity_local.commands.council.os.killpg", lambda pgid, sig: killed.append((pgid, sig)))

        handle_council_stop(SimpleNamespace(status_token="launch_stop_123"))

        payload = json.loads(capsys.readouterr().out)
        assert payload["stopped"] is True
        assert payload["process_group_id"] == 222
        assert killed

        status_path = patch_trinity_home / "portal_pages" / "status" / "council_status_launch_stop_123.json"
        updated = json.loads(status_path.read_text(encoding="utf-8"))
        assert updated["status"] == "canceled"
        assert updated["error"] == "Council stopped by user."
