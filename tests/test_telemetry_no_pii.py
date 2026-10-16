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
        assert params["task_type"] == "other"     # "design" is not in the closed set
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
        assert params.get("task_type") == "other", (
            f"a free-text task_type leaked instead of being folded: {params}"
        )
        assert SECRET not in json.dumps(payload)
        # A single snake_case token is still the chairman's description of the
        # work (2026-10-15): only the registry's closed set goes out as itself.
        for label, wire in (("real_estate_development_planning", "other"),
                            ("architectural_standardization_analysis", "other"),
                            ("design", "other"),
                            ("architecture_decision", "architecture_decision"),
                            ("coding", "coding")):
            p = t.build_outbound_event_payload(
                "council_complete", {"task_type": label, "winner": "claude"}
            )["events"][0]["params"]
            assert p.get("task_type") == wire, (label, p)

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
            "activation_source": "channel",
            "surface": "first use",
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



class TestAppVersionIsReal:
    """app_version in the telemetry events must report the ACTUAL installed
    package version so version-cohort analytics works. The builders used to
    hard-code "0.1.0", so every install looked like 0.1.0 on the wire
    regardless of the real release (found 2026-06-01 by auditing the live
    launchpad pageData on a 1.7.x build still reporting 0.1.0)."""

    def test_app_version_is_not_the_stale_literal(self, patch_trinity_home: Path) -> None:
        # APP_VERSION is what the MCP server reports (the page's events that also carried it
        # went with its collector, 2026-10-15).
        assert t.APP_VERSION != "0.1.0", "APP_VERSION still hard-codes 0.1.0"

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
        v = t.APP_VERSION
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
        assert s.share_usage_events is False

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


def test_council_complete_member_count_reads_the_real_field(monkeypatch):
    """Surface-binding guard (2026-07-18, workflow finding): _emit_council_telemetry
    read `getattr(outcome, "responses", [])`, but CouncilOutcome's field is
    `member_results` (no `responses` attr), so getattr silently defaulted to []
    and the council_complete GA4 event logged member_count=0 on EVERY council —
    a DISCLOSED_EVENT_PARAM corrupted to a constant. MUTATION: revert to
    getattr(outcome, "responses", ...) and this reds (captured count → 0)."""
    from trinity_local import council_runner
    from trinity_local.council_schema import CouncilOutcome, CouncilMemberResult

    outcome = CouncilOutcome(
        council_run_id="c_x",
        bundle_id="b_x",
        task_cluster_id="t_x",
        primary_provider="claude",
        member_results=[
            CouncilMemberResult(provider="claude", output_text="a"),
            CouncilMemberResult(provider="codex", output_text="b"),
            CouncilMemberResult(provider="antigravity", output_text="c"),
        ],
    )
    captured = {}
    monkeypatch.setattr(council_runner, "record_event",
                        lambda name, **kw: captured.update(kw), raising=False)
    monkeypatch.setattr("trinity_local.telemetry.record_event",
                        lambda name, **kw: captured.update(kw))
    council_runner._emit_council_telemetry(outcome)
    assert captured.get("member_count") == 3, (
        "member_count must reflect the real member_results, not a constant 0 from "
        f"the wrong attribute name: {captured}"
    )
