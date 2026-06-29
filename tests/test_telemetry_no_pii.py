"""Privacy guards for the outbound telemetry + share-card surfaces.

Founder principle: telemetry stays default-ON to close the feedback loop,
but it must be PROVABLY no-PII. These tests assert the contract
structurally rather than by code review:

  #231(a) the council event payload is a strict subset of the disclosed
          categorical params, and no value carries prompt/lens/
          user_substitute text.
  #231(b) the elo_snapshot (provider win-rates) the launchpad transmits is
          disclosed (within DISCLOSED_ELO_KEYS) and carries no free text.
  #231(c) the browser send path is gated on the SAME credentials guarantee
          as Python — absent GA4 creds, no `endpoint` reaches pageData, so
          `maybeSendTelemetry()` can't POST.
  #237   the share-card PNG generators (council/eval) don't bake raw
          prompt / member-output / user_substitute text into the image.

Per CLAUDE.md "Architectural commitments" #2: only categorical routing
labels leave the machine; NO prompt content, NO lens text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local import telemetry as t

# #231/#237: this file is the written contract for the telemetry no-PII
# payload API (build_outbound_event_payload / build_elo_snapshot /
# _browser_send_enabled / DISCLOSED_* constants) + the share-card content
# redaction. Implemented in telemetry.py + the card collectors (v1.7.80);
# the TEST-FIRST xfail marker was removed once the contract went green.


# A distinctive sentinel we feed into the corpus / params; if it ever
# shows up in an outbound payload or a card, that's a leak.
SECRET = "ZZZSECRETPROMPTLEAKZZZ"


def _write_council_outcome(home: Path) -> None:
    """A saved council outcome whose member outputs + prompt-ish fields
    carry the SECRET sentinel. The elo snapshot reads these files; the
    guard proves none of that free text reaches the wire payload."""
    payload = {
        "council_run_id": "council_pii_guard",
        "bundle_id": "bundle_pii_guard",
        "primary_provider": "claude",
        "winner_provider": "antigravity",
        "created_at": "2026-05-29T10:00:00+00:00",
        "task_text": f"Decide the launch plan {SECRET}",
        "member_results": [
            {"provider": "claude", "model": "m", "output_text": f"draft {SECRET}"},
            {"provider": "antigravity", "model": "m", "output_text": f"draft {SECRET}"},
            {"provider": "codex", "model": "m", "output_text": f"draft {SECRET}"},
        ],
        "routing_label": {"task_type": "design", "winner": "antigravity"},
    }
    path = home / "council_outcomes" / "council_pii_guard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_ga4_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the GA4 + custom-endpoint env vars OFF for every test here
    so the credentials gate is exercised in its real shipping state.
    Tests that need creds set them explicitly."""
    monkeypatch.delenv("TRINITY_GA4_MEASUREMENT_ID", raising=False)
    monkeypatch.delenv("TRINITY_GA4_API_SECRET", raising=False)
    monkeypatch.delenv("TRINITY_TELEMETRY_ENDPOINT", raising=False)


# ── #231(a) ───────────────────────────────────────────────────────────

class TestTelemetryPayloadIsCategoricalOnly:
    def test_telemetry_payload_is_categorical_only(self) -> None:
        """The outbound council event payload's params are a subset of the
        disclosed categorical set, and no value carries free text."""
        # Build the payload exactly as council_runner does — plus hostile
        # extra keys an over-eager caller might pass. The allowlist must
        # drop them.
        payload = t.build_outbound_event_payload(
            "council_complete",
            {
                "task_type": "design",
                "winner": "claude",
                "member_count": 3,
                "mode": "parallel",
                # Hostile injections — must NOT survive.
                "prompt": f"the user asked {SECRET}",
                "lens": f"tension pole {SECRET}",
                "user_substitute": f"rewrite as {SECRET}",
                "output_text": f"member draft {SECRET}",
            },
        )
        params = payload["events"][0]["params"]
        # Keys are a strict subset of the disclosed categorical contract.
        assert set(params.keys()) <= {"task_type", "winner", "member_count", "mode"}
        assert set(params.keys()) <= t.DISCLOSED_EVENT_PARAMS
        # No value (anywhere in the serialized payload) carries the leak.
        blob = json.dumps(payload)
        assert SECRET not in blob
        # And the disclosed param values are the categorical ones we passed.
        assert params["task_type"] == "design"
        assert params["winner"] == "claude"
        assert params["member_count"] == 3
        assert params["mode"] == "parallel"

    def test_freetext_task_type_is_dropped_not_just_length_capped(self) -> None:
        """`task_type` is the only DISCLOSED param that's content-derived (the
        chairman names it). Real labels are single snake_case tokens with no
        whitespace; prose has whitespace. A free-text task_type that slipped past
        the routing schema must be DROPPED — so the 'categorical-only' guarantee
        is structural (format-enforced), not merely length-capped upstream.
        Without this, a chairman emitting `task_type = "the user asked <SECRET>"`
        would leak ~40 chars of prose through the allowlist."""
        leaky = f"the user asked about {SECRET}"
        payload = t.build_outbound_event_payload(
            "council_complete",
            {"task_type": leaky, "winner": "claude", "member_count": 3},
        )
        params = payload["events"][0]["params"]
        assert "task_type" not in params, (
            f"a free-text task_type leaked instead of being dropped: {params}"
        )
        assert SECRET not in json.dumps(payload)
        # Real single-token snake_case labels (incl. domain-y ones) still pass —
        # the guard discriminates on whitespace, not domain.
        for real in ("design", "real_estate_development_planning",
                     "architectural_standardization_analysis"):
            p = t.build_outbound_event_payload(
                "council_complete", {"task_type": real, "winner": "claude"}
            )["events"][0]["params"]
            assert p.get("task_type") == real, f"real categorical label dropped: {real}"

    def test_council_runner_emits_only_disclosed_params_at_the_source(self, monkeypatch) -> None:
        """Defense-in-depth at the EMIT SITE: the real council_runner emit
        (`_emit_council_telemetry`) must pass record_event only disclosed
        categorical params — verified by capturing what it actually sends, NOT a
        hardcoded mirror (the prior version asserted a literal set == DISCLOSED,
        which couldn't catch a `prompt=outcome.prompt` added at the source). A
        content field added there is caught here, before the allowlist filter in
        build_outbound_event_payload is even reached. Mutation: add a free-text
        kwarg to the record_event call → its key isn't in DISCLOSED → this fails."""
        from types import SimpleNamespace

        from trinity_local import council_runner

        captured: dict = {}

        def _capture(event_name, **params):
            captured["event"] = event_name
            captured["params"] = params

        # _emit_council_telemetry does `from .telemetry import record_event` at call
        # time, so patching the source module's attribute reaches it.
        monkeypatch.setattr(t, "record_event", _capture)
        SECRET = "PRIVATE-PROMPT-LEAK-XYZ"
        outcome = SimpleNamespace(
            routing_label=SimpleNamespace(task_type="architecture_decision", winner="claude"),
            responses=[1, 2, 3],
            mode="parallel",
            # content fields the emit must NEVER forward:
            prompt=SECRET,
            member_outputs=[SECRET],
            chairman_synthesis=SECRET,
        )
        council_runner._emit_council_telemetry(outcome)

        assert captured.get("event") == "council_complete"
        params = captured.get("params", {})
        extra = set(params.keys()) - set(t.DISCLOSED_EVENT_PARAMS)
        assert not extra, (
            f"council_runner emitted param(s) outside the disclosed allowlist AT THE "
            f"SOURCE: {extra} — a field added to the record_event call escapes disclosure"
        )
        assert SECRET not in json.dumps(params), "a content field leaked into the emit params"

    def test_privacy_copy_discloses_every_sent_param(self) -> None:
        """The settings-modal privacy copy is the user's CONTRACT for what leaves
        the machine. It must name a human term for EVERY param in the structural
        allowlist (DISCLOSED_EVENT_PARAMS) — a sent field the copy omits is an
        undisclosed leak (the #231 honesty class, inverted: documents LESS than it
        sends). And it must NOT name a field that is never sent — a phantom erodes
        trust under audit. Found 2026-06-06: the copy claimed 'confidence' (never
        in any disclosure allowlist) and omitted member_count + mode (both sent)."""
        from trinity_local.launchpad_template import render_launchpad_html

        html = render_launchpad_html(page_data={})
        start = html.find("Telemetry is on by default")
        assert start != -1, "settings-modal privacy copy missing from the template"
        copy = html[start:html.find("</p>", start)].lower()

        # A human term for each disclosed param — sent-but-unnamed = undisclosed.
        param_terms = {
            "task_type": "task type",
            "winner": "winn",        # winner / winning provider
            "member_count": "member",
            "mode": "mode",
        }
        for param in t.DISCLOSED_EVENT_PARAMS:
            term = param_terms.get(param)
            assert term is not None, (
                f"new disclosed param {param!r} has no copy mapping — add one AND "
                "update the settings-modal privacy copy to name it"
            )
            assert term in copy, (
                f"privacy copy omits disclosed param {param!r} (term {term!r}) — "
                "it is sent but the user is never told"
            )
        # The no-PII negative list must stay explicit.
        assert "never raw prompts" in copy
        # No phantom field — naming something we never send erodes audit trust.
        assert "confidence" not in copy, (
            "privacy copy names 'confidence', which telemetry never sends "
            "(not in any DISCLOSED_* allowlist)"
        )


# ── #231(b) ───────────────────────────────────────────────────────────

class TestEloSnapshotIsDisclosed:
    def test_elo_snapshot_keys_are_disclosed(self, patch_trinity_home: Path) -> None:
        _write_council_outcome(patch_trinity_home)
        snapshot = t.build_elo_snapshot()
        assert set(snapshot.keys()) <= t.DISCLOSED_ELO_KEYS
        # Provider sub-dicts carry only numeric/categorical stats.
        for provider, stats in snapshot["providers"].items():
            assert set(stats.keys()) <= t.DISCLOSED_ELO_PROVIDER_KEYS
            # provider key is a slug, values are numbers.
            assert isinstance(provider, str)
            for v in stats.values():
                assert isinstance(v, (int, float))

    def test_elo_event_filters_undisclosed_fields_at_the_SINK(
        self, patch_trinity_home: Path, monkeypatch
    ) -> None:
        """STRUCTURAL no-PII parity (#231): build_elo_snapshot_event must DROP a
        snapshot field that isn't in the disclosed allowlist — not merely trust
        build_elo_snapshot()'s shape + the source test above. The other two wire
        builders (build_outbound_event_payload, build_launchpad_view_event) filter
        structurally; this one used to spread `**snapshot` verbatim, so a future
        upstream field (or per-provider stat) carrying prompt text would reach the
        wire even though the contract promises "a coding mistake upstream can't
        leak." Simulate exactly that upstream mistake and assert the SINK stops it.
        Mutation: revert to `**snapshot` → the planted prompt text leaks → reds."""
        monkeypatch.setattr(t, "build_elo_snapshot", lambda: {
            "version": 1, "window": "all", "council_count": 7, "matchups": {},
            "providers": {"claude": {"elo": 1500, "wins": 4,
                                     "LEAK_prompt": "a private question the user asked"}},
            "LEAK_top_level": "raw lens text that must NOT reach the wire",
        })
        ev = t.build_elo_snapshot_event()
        blob = json.dumps(ev)
        assert "LEAK_top_level" not in ev, "undisclosed top-level field reached the wire"
        assert "LEAK_prompt" not in blob, "undisclosed per-provider field reached the wire"
        assert "private question" not in blob and "raw lens text" not in blob, (
            "planted free text leaked onto the wire — the elo sink isn't filtering"
        )
        # Disclosed fields still pass through unchanged (no over-filtering).
        assert ev["council_count"] == 7
        assert ev["providers"]["claude"] == {"elo": 1500, "wins": 4}

    def test_elo_snapshot_caches_and_invalidates_on_new_council(
        self, patch_trinity_home: Path
    ) -> None:
        """build_elo_snapshot is a pure function of council_outcomes/, yet a
        single launchpad render computed it FIVE times (build_page_data + the
        view-event reading two fields with two calls + the elo event), and it
        fires again on every telemetry view event — profiled at 0.40s/render on
        the real 562-council home. It's now cached per-window, keyed by the
        council_outcomes/ per-file signature. Two guarantees the cache must keep:
          (1) a repeated call with no council change returns the SAME object
              (the cache is genuinely hit, not recomputed), and
          (2) adding a council INVALIDATES it — a stale snapshot would
              silently under-report the user's councils on the launchpad ELO.
        Mutations: drop the signature check → (2) fails (snap2 keeps the old
        count); drop the cache entirely → (1) fails (a fresh dict each call)."""
        _write_council_outcome(patch_trinity_home)
        snap1 = t.build_elo_snapshot()
        assert t.build_elo_snapshot() is snap1, (
            "no council changed between calls — the second must hit the cache "
            "and return the same dict object (the cache isn't engaged)"
        )
        before = snap1["council_count"]
        # A SECOND, distinct council changes the council_outcomes/ signature.
        second = {
            "council_run_id": "council_cache_invalidation",
            "primary_provider": "claude",
            "winner_provider": "claude",
            "created_at": "2026-05-30T11:00:00+00:00",
            "task_text": "second council",
            "member_results": [
                {"provider": "claude", "model": "m", "output_text": "a"},
                {"provider": "codex", "model": "m", "output_text": "b"},
            ],
            "routing_label": {"task_type": "code", "winner": "claude"},
        }
        (
            patch_trinity_home
            / "council_outcomes"
            / "council_cache_invalidation.json"
        ).write_text(json.dumps(second), encoding="utf-8")
        snap2 = t.build_elo_snapshot()
        assert snap2["council_count"] > before, (
            "elo cache didn't invalidate after a new council — a stale snapshot "
            "under-reports councils on the launchpad ELO"
        )

    def test_elo_cache_misses_when_payload_source_is_swapped(
        self, monkeypatch: pytest.MonkeyPatch, patch_trinity_home: Path
    ) -> None:
        """The cache key also pins the live `_iter_council_payloads` function by
        identity. Tests (and only tests) monkeypatch that source directly, which
        the on-disk signature CANNOT observe — so without the source-identity
        key a prior call's cached snapshot leaks into a test that swapped the
        source. That is the exact cross-test contamination that turned CI red
        2026-06-06: with only the disk-signature key,
        `test_build_elo_snapshot_excludes_old_web_era_councils` read a stale
        `council_count=0` from an earlier test's cache entry (same empty-home
        signature) instead of recomputing its 1 current-era council. This guard
        reproduces it deterministically. Mutation: drop `cached[1] is source`
        from the key → the swapped source returns the stale primed snapshot."""
        # Prime the cache under the ambient (empty) disk signature.
        primed = t.build_elo_snapshot()
        assert primed["council_count"] == 0, "patch_trinity_home should be empty"
        # Swap the in-memory source WITHOUT touching disk (signature unchanged).
        councils = [
            {"council_run_id": "cur1", "winner_provider": "claude", "member_results": [
                {"provider": "claude", "model": "claude-opus-4-8"},
                {"provider": "codex", "model": "gpt-5.5"}]},
        ]
        monkeypatch.setattr(t, "_iter_council_payloads", lambda: iter(councils))
        snap = t.build_elo_snapshot()
        assert snap["council_count"] == 1, (
            "a swapped council source must MISS the cache — a snapshot keyed only "
            "on the unchanged disk signature would return the stale primed count"
        )

    def test_launchpad_elo_event_carries_no_free_text(
        self, patch_trinity_home: Path
    ) -> None:
        """The elo_event the browser transmits must not carry the SECRET
        free text that lives in the underlying council outcome."""
        _write_council_outcome(patch_trinity_home)
        t.enable_telemetry()
        state = t.launchpad_telemetry_state()
        blob = json.dumps(state["elo_event"]) + json.dumps(state["view_event"])
        blob += json.dumps(state["snapshot"])
        assert SECRET not in blob
        # elo_event is a disclosed-snapshot superset + the install id +
        # categorical event fields — assert its data keys stay disclosed.
        elo_event = state["elo_event"]
        allowed = t.DISCLOSED_ELO_KEYS | {
            "event", "share_install_id", "app_version", "timestamp",
        }
        assert set(elo_event.keys()) <= allowed

    def test_view_event_keys_disclosed_and_values_categorical(
        self, patch_trinity_home: Path
    ) -> None:
        """The browser sends `launchpad_view` verbatim (it bypasses the GA4
        chokepoint), so its keys must stay inside DISCLOSED_VIEW_EVENT_KEYS AND
        every value must be categorical/id/numeric — never free text. This is
        the gap a planted-SECRET scan misses: a future field pulling DIFFERENT
        user text (a council title, a recent prompt) would slip past
        `SECRET not in blob`. The structural assertion catches it (audited the
        live payload 2026-06-01 — only elo_event keys were guarded before)."""
        _write_council_outcome(patch_trinity_home)
        t.enable_telemetry()
        ve = t.build_launchpad_view_event()
        undisclosed = set(ve.keys()) - t.DISCLOSED_VIEW_EVENT_KEYS
        assert not undisclosed, f"view_event leaked undisclosed key(s): {undisclosed}"
        for k, v in ve.items():
            if isinstance(v, str):
                # Categorical labels / slugs / ids / ISO timestamps / versions are
                # all single-token and short; free text (a council title, a prompt)
                # has spaces. A SPACE in any value is the tell — none of the legit
                # fields (launchpad_view, share_…, 0.1.0, 10-49, ISO ts) contain one.
                assert " " not in v and len(v) <= 40, (
                    f"view_event[{k!r}] looks like free text (PII risk): {v!r}"
                )

    def test_view_event_is_filtered_through_the_allowlist(
        self, monkeypatch: pytest.MonkeyPatch, patch_trinity_home: Path
    ) -> None:
        """Prove build_launchpad_view_event FILTERS through the constant (not
        just that its literal happens to match): shrink the allowlist and the
        output must shrink with it. Without the chokepoint this test fails —
        a mutation guard so the filter can't be silently removed."""
        _write_council_outcome(patch_trinity_home)
        t.enable_telemetry()
        monkeypatch.setattr(t, "DISCLOSED_VIEW_EVENT_KEYS", frozenset({"event", "surface"}))
        ve = t.build_launchpad_view_event()
        assert set(ve.keys()) == {"event", "surface"}


# ── #231(c) ───────────────────────────────────────────────────────────

class TestBrowserSendHonorsCredentialGate:
    def test_endpoint_stripped_from_pagedata_without_creds(
        self, patch_trinity_home: Path
    ) -> None:
        """Absent GA4 creds, the Python path no-ops — the browser must too.
        With no `endpoint` in pageData, maybeSendTelemetry() returns early."""
        t.enable_telemetry()
        state = t.launchpad_telemetry_state()
        assert "endpoint" not in state["settings"]
        assert t._browser_send_enabled() is False

    def test_endpoint_present_with_ga4_creds(
        self, patch_trinity_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRINITY_GA4_MEASUREMENT_ID", "G-TESTXXXX")
        monkeypatch.setenv("TRINITY_GA4_API_SECRET", "test-secret")
        t.enable_telemetry()
        state = t.launchpad_telemetry_state()
        assert state["settings"].get("endpoint")
        assert t._browser_send_enabled() is True

    def test_endpoint_present_with_custom_collector(
        self, patch_trinity_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The explicit-collector escape hatch opts the browser back in."""
        monkeypatch.setenv(
            "TRINITY_TELEMETRY_ENDPOINT", "https://collector.example/collect"
        )
        t.enable_telemetry()
        state = t.launchpad_telemetry_state()
        assert state["settings"].get("endpoint")
        assert t._browser_send_enabled() is True


# ── #237 share-card content leak ──────────────────────────────────────

class TestShareCardsNoRawContentLeak:
    """The card PNGs render text into the image. council/eval cards must
    only carry chairman-extracted claims + scores + categorical labels —
    never the verbatim prompt, member output, or user_substitute. These
    guards assert the COLLECT step (the data-shaping boundary) drops raw
    content; that's where a leak would enter the renderer."""

    def test_council_card_excludes_prompt_and_member_output(self) -> None:
        from trinity_local.council_card import collect_card_data_from_outcome

        class _Member:
            def __init__(self, provider: str) -> None:
                self.provider = provider

        class _Label:
            task_type = "design"
            winner = "claude"
            agreed_claims = ["models converged on clarity"]
            disagreed_claims = [
                {"provider": "codex", "claim": "ship sooner",
                 "why_matters": "speed beats polish here"}
            ]

        class _Outcome:
            member_results = [_Member("claude"), _Member("codex")]
            winner_provider = "claude"
            routing_label = _Label()
            # Fields that MUST NOT cross to the card:
            task_text = f"the user asked {SECRET}"
            responses = [{"output_text": f"member draft {SECRET}"}]

        data = collect_card_data_from_outcome(_Outcome())
        blob = json.dumps(data.to_dict())
        assert SECRET not in blob
        # Only chairman-extracted claims survive.
        assert data.agreed_claims == ["models converged on clarity"]
        assert data.disagreed_claim == "ship sooner"

    def test_eval_card_carries_only_scores_and_labels(self) -> None:
        from trinity_local.eval_card import collect_card_data_from_result

        class _Result:
            target_provider = "claude"
            target_model = "claude-opus-4-8"
            aggregate_score = 0.66
            items_total = 20
            items_completed = 20
            by_rejection_type = {
                "REFRAME": {"mean_score": 0.8, "count": 5},
                "COMPRESSION": {"mean_score": 0.5, "count": 4},
            }

        data = collect_card_data_from_result(_Result())
        blob = json.dumps(data.to_dict())
        assert SECRET not in blob
        # Only categorical axis names + numeric scores cross.
        for axis_name, mean, count in data.by_axis:
            assert axis_name in {"REFRAME", "COMPRESSION"}
            assert isinstance(mean, float)
            assert isinstance(count, int)


class TestAppVersionIsReal:
    """app_version in the telemetry events must report the ACTUAL installed
    package version so version-cohort analytics works. The builders used to
    hard-code "0.1.0", so every install looked like 0.1.0 on the wire
    regardless of the real release (found 2026-06-01 by auditing the live
    launchpad pageData on a 1.7.x build still reporting 0.1.0)."""

    def test_app_version_is_not_the_stale_literal(self, patch_trinity_home: Path) -> None:
        ve = t.build_launchpad_view_event()
        ee = t.build_elo_snapshot_event()
        assert ve["app_version"] != "0.1.0", "view_event still hard-codes 0.1.0"
        assert ee["app_version"] != "0.1.0", "elo_event still hard-codes 0.1.0"
        # Both report the same resolved version (single source: APP_VERSION).
        assert ve["app_version"] == t.APP_VERSION
        assert ee["app_version"] == t.APP_VERSION

    def test_app_version_tracks_source_version_not_stale_metadata(self) -> None:
        """APP_VERSION reports the CURRENT version. From a source checkout (where
        the test suite runs), that's pyproject.toml's version — NOT the
        importlib.metadata version, which is FROZEN at editable-install time and
        goes stale: the founder's editable box reported 1.0.0 (the install-time
        metadata) while the repo was at 1.7.x, mislabeling every telemetry event
        AND the MCP serverInfo. Found 2026-06-01 via a real stdio MCP handshake
        (serverInfo.version came back 1.0.0). A real `pip install` with no source
        tree beside the module falls back to importlib.metadata (correct there)."""
        import re
        from pathlib import Path

        pyproject = Path(t.__file__).resolve().parents[2] / "pyproject.toml"
        assert pyproject.is_file(), "this test must run from a source checkout"
        m = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M
        )
        assert m, "pyproject.toml has no [project] version"
        # Call _resolve_app_version() FRESH (reads pyproject at test time) rather
        # than the module-level APP_VERSION cached at import — the ship ritual
        # bumps pyproject AFTER the suite starts, so the cached value can lag the
        # current pyproject by one version mid-run. The unit under test is the
        # resolver; the v1.7.199 `server.version == APP_VERSION` test pins the
        # APP_VERSION = _resolve_app_version() wiring separately.
        assert t._resolve_app_version() == m.group(1), (
            f"_resolve_app_version() ({t._resolve_app_version()!r}) must match the "
            f"source pyproject version ({m.group(1)!r}); a regression to "
            f"importlib.metadata-only would report the stale editable-install version"
        )

    def test_app_version_stays_categorical_no_pii(self, patch_trinity_home: Path) -> None:
        """Whatever the resolved version, it stays a short single-token value —
        no spaces (the free-text/PII tell the no-PII guard relies on)."""
        v = t.build_launchpad_view_event()["app_version"]
        assert " " not in v and 0 < len(v) <= 40


class TestTelemetryToggleHasWorkingFallbackWithoutExtension:
    """Privacy honesty: the settings modal promises "toggle it off anytime",
    but the launchpad applies settings through the Chrome extension's Native
    Messaging dispatcher. That dispatcher (`window.__TRINITY_DISPATCH__`) is
    ALWAYS injected (launchpad_runtime.py) whether or not the extension is
    installed — so when the extension isn't reachable (not installed: the common
    case), the dispatch FAILS and `handleDispatchResult` shows the generic
    "install our Chrome extension" banner. For a privacy opt-out, telling the
    user to INSTALL a browser extension to turn telemetry OFF is backwards.

    The fix: every settings action carries a `cliCommand`, and a FAILED settings
    dispatch routes to `fallbackToSettingsCli` — which copies the equivalent CLI
    command and keeps the modal open with a ✓ confirmation that the displayed
    toggle is unchanged until the user runs it. This guard pins the data (the
    command) + the JS wiring (the failed-dispatch → CLI fallback) so the toggle
    can never regress to dead-ending on the install-extension banner."""

    def test_settings_links_carry_cli_fallback(self) -> None:
        from trinity_local.launchpad_data import _settings_links

        links = _settings_links()
        for action, command in (
            ("disable", "trinity-local telemetry-disable"),
            ("enable", "trinity-local telemetry-enable"),
            ("reset", "trinity-local telemetry-reset-id"),
        ):
            assert links[action].get("cliCommand") == command, (
                f"settings action {action!r} lost its no-extension CLI fallback "
                f"(got {links[action]!r}); a user without the Chrome extension "
                f"can't apply the setting and the toggle dead-ends on a banner"
            )

    def test_js_routes_failed_dispatch_to_cli_not_install_banner(self) -> None:
        from trinity_local.launchpad_template import render_launchpad_html

        html = render_launchpad_html(page_data={})
        # A failed settings dispatch must hand the user the CLI command — NOT the
        # generic install-extension banner handleDispatchResult would otherwise show.
        assert "fallbackToSettingsCli(entry)" in html, (
            "triggerSettingsAction lost its failed-dispatch CLI fallback — a "
            "telemetry toggle without the extension dead-ends on the install banner"
        )
        # The fallback copies the CLI command keyed to the action's feedbackKey
        # (so Reset's confirmation renders inline beside ITS button, not down at
        # the sharing toggle with toggle-worded copy — see the side-panel guard
        # test_reset_anonymous_id_feedback_is_inline_and_reset_worded).
        assert "copyLens(entry.cliCommand, entry.feedbackKey || 'settings-cli')" in html, (
            "the settings-action fallback must COPY the working CLI command keyed to "
            "the action's feedbackKey (so each control's confirmation renders inline)"
        )
        # And both confirmation lines must exist: the sharing-toggle line and the
        # inline reset line — so the copy is never invisible for either control.
        assert "copiedKey === 'settings-cli'" in html, (
            "no ✓ confirmation near the toggle — the copy is invisible and the "
            "displayed toggle state would look like it changed when it didn't"
        )
        assert "copiedKey === 'settings-reset-cli'" in html, (
            "no ✓ confirmation beside the Reset button — Reset's copy would land "
            "155px away at the toggle with toggle-worded copy (the wrong action)"
        )


class TestSettingsCorruptionFailsClosed:
    """guard_shape_not_just_parse + privacy fail-closed: load_telemetry_settings runs
    on EVERY launchpad render AND every telemetry event. It used to json.loads with no
    try/except and then `raw.items()`, so a malformed or valid-but-non-dict
    settings/telemetry.json (a partial write or a hand-edit) crashed the whole render
    (verified: portal-html rc=1, AttributeError: 'str' object has no attribute 'items').
    Now it degrades — and degrades FAIL-CLOSED (sharing off), because a corrupt file
    means the user's opt-out is unreadable and we must not send; this also preserves
    the prior effective behavior (the crash meant nothing was sent). A genuinely-new
    user (no file) still gets the founder-chosen default-ON.

    Mutation: drop the `isinstance(raw, dict)` / try-except guard → load crashes on a
    non-dict file → these red."""

    @pytest.mark.parametrize("bad", ['"corrupt"', "[1, 2, 3]", "42", "null", "not json at all"])
    def test_corrupt_settings_degrades_fail_closed(self, patch_trinity_home: Path, bad):
        t.telemetry_settings_path().parent.mkdir(parents=True, exist_ok=True)
        t.telemetry_settings_path().write_text(bad, encoding="utf-8")
        s = t.load_telemetry_settings()  # must not raise
        assert s.sharing_enabled is False, "corrupt settings must fail CLOSED (no send)"
        assert s.share_usage_events is False and s.share_elo_summaries is False

    def test_valid_settings_unchanged(self, patch_trinity_home: Path):
        t.save_telemetry_settings(t.TelemetrySettings(sharing_enabled=True, share_install_id="abc"))
        s = t.load_telemetry_settings()
        assert s.sharing_enabled is True and s.share_install_id == "abc"

    def test_absent_file_keeps_founder_default_on(self, patch_trinity_home: Path):
        p = t.telemetry_settings_path()
        if p.exists():
            p.unlink()
        # No file = a genuinely-new user → founder-chosen default-ON (NOT fail-closed).
        assert t.load_telemetry_settings().sharing_enabled is True
