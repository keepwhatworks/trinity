"""Tests for `trinity-local extension repair` — diagnose + HAR + prompt build.

Skips the real council dispatch path (would actually shell out to
provider CLIs). The dispatch wiring is exercised end-to-end via the
existing council-runner test suite; here we lock down the new pieces:
diagnosis from `~/.trinity/conversations/`, HAR filtering, and prompt
construction shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trinity_local.commands import extension_repair


@pytest.fixture
def trinity_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_diagnose_reports_missing_directory_per_provider(trinity_home):
    diag = extension_repair.diagnose()
    assert set(diag["providers"].keys()) == {"claude", "chatgpt", "gemini"}
    for slug in ("claude", "chatgpt", "gemini"):
        assert diag["providers"][slug]["exists"] is False
        assert diag["providers"][slug]["captures"] == 0
        assert diag["providers"][slug]["hours_since_last"] is None


def test_diagnose_distinguishes_empty_dir_from_missing_dir(trinity_home):
    (trinity_home / "conversations" / "chatgpt").mkdir(parents=True)
    diag = extension_repair.diagnose()
    assert diag["providers"]["chatgpt"]["exists"] is True
    assert diag["providers"]["chatgpt"]["captures"] == 0
    assert diag["providers"]["gemini"]["exists"] is False


def test_diagnose_counts_captures_and_picks_latest(trinity_home):
    claude_dir = trinity_home / "conversations" / "claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "a.json").write_text("{}")
    (claude_dir / "b.json").write_text("{}")
    diag = extension_repair.diagnose()
    assert diag["providers"]["claude"]["captures"] == 2
    assert diag["providers"]["claude"]["hours_since_last"] is not None
    assert diag["providers"]["claude"]["hours_since_last"] >= 0
    assert diag["providers"]["claude"]["last_capture"]  # ISO string set


def test_diagnose_future_mtime_renders_no_negative_hours_ago(trinity_home):
    """A capture file with a FUTURE mtime (clock skew, or a restored/rsync'd
    file with a preserved future timestamp) must NOT render "last -3.0h ago"
    in the `trinity-local status` capture diagnostics line — the founder
    symptom this guards. `diagnose()` clamps `now - mtime` to 0, so a future
    capture reads as "just captured" (0h), the same future->now convention
    `_humanize_ago` enforces on the launchpad.
    """
    import time as _time

    claude_dir = trinity_home / "conversations" / "claude"
    claude_dir.mkdir(parents=True)
    f = claude_dir / "future.json"
    f.write_text("{}")
    # Render-INDEPENDENT precondition: the seed is genuinely in the FUTURE.
    future = _time.time() + 3 * 3600
    import os as _os
    _os.utime(f, (future, future))
    assert f.stat().st_mtime > _time.time(), "fixture mtime must be in the future to bite"

    diag = extension_repair.diagnose()
    h = diag["providers"]["claude"]["hours_since_last"]
    # (A) the surface paints a value (not None) ...
    assert h is not None, "diagnose() dropped the future-dated capture"
    # (B) ... and it is clamped non-negative — the assertion keyed on the binding.
    assert h >= 0, f"future-mtime capture rendered a NEGATIVE 'hours_since_last' ({h!r}) -> status would paint 'last {h}h ago'"

    # Render the exact status.py:312 line from the diagnose output and assert
    # no leaked negative reaches the user-painted relative-time string.
    h_str = f"{h}h ago" if h is not None else "unknown when"
    line = f"{diag['providers']['claude']['captures']:,} files · last {h_str}"
    assert "-" not in h_str, f"status capture line leaked a negative relative-time: {line!r}"


def test_diagnose_ignores_dotfiles(trinity_home):
    chatgpt_dir = trinity_home / "conversations" / "chatgpt"
    chatgpt_dir.mkdir(parents=True)
    (chatgpt_dir / ".DS_Store").write_text("noise")
    diag = extension_repair.diagnose()
    assert diag["providers"]["chatgpt"]["captures"] == 0


class TestDiagnoseCaptureCountMatchesCanonicalEnumerator:
    """The CLI `status` "Captures: N from Chrome extension" line reads
    `diagnose()["providers"][slug]["captures"]`; the launchpad cockpit
    card + CLI doctor read `capture_host.iter_capture_files()`. Both
    describe the SAME `~/.trinity/conversations/` data as "captured
    conversations", so they MUST agree.

    Founder symptom guarded: `diagnose()` open-coded its own glob (skip
    `.`/`_`/`stream-` only) and MISSED both dedups that the canonical
    enumerator owns — the `<conv_id>.stream.json` sidecar (claude/chatgpt
    double-count) AND gemini's per-SSE-frame `<conv_id>__<ts>.stream.json`
    explosion (~17× inflation). So `status` reported e.g. 10 captures
    while the launchpad showed 4 for the identical dir. This guard seeds
    exactly that discriminating corpus and asserts the two surfaces agree.
    """

    def _seed_discriminating(self, home: Path):
        conv = home / "conversations"
        # claude: 2 real convos, each with a canonical .json + sidecar .stream.json
        (conv / "claude").mkdir(parents=True)
        for cid in ("convA", "convB"):
            (conv / "claude" / f"{cid}.json").write_text("{}")
            (conv / "claude" / f"{cid}.stream.json").write_text("{}")
        # gemini: ONE real convo exploded into 5 per-frame .stream.json files
        (conv / "gemini").mkdir(parents=True)
        for ts in range(5):
            (conv / "gemini" / f"convG__{1000 + ts}.stream.json").write_text("{}")
        (conv / "gemini" / "_sidebar.json").write_text("{}")  # sentinel, not a convo
        # chatgpt: 1 real convo, canonical .json only
        (conv / "chatgpt").mkdir(parents=True)
        (conv / "chatgpt" / "convC.json").write_text("{}")
        # Ground truth: 2 (claude) + 1 (gemini) + 1 (chatgpt) = 4 conversations.
        return 4

    def test_status_captures_total_matches_iter_capture_files(self, trinity_home):
        from collections import Counter

        from trinity_local.capture_host import iter_capture_files

        ground_truth = self._seed_discriminating(trinity_home)

        # Canonical enumerator (launchpad cockpit card + CLI doctor read this).
        canonical = iter_capture_files()
        canonical_total = len(canonical)
        canonical_per_provider = dict(Counter(f.parent.name for f in canonical))

        # `status` "Captures: N" reads diagnose()["providers"][*]["captures"].
        diag = extension_repair.diagnose()
        status_total = sum(
            info.get("captures", 0) for info in diag["providers"].values()
        )
        status_per_provider = {
            slug: info.get("captures", 0)
            for slug, info in diag["providers"].items()
            if info.get("captures", 0)
        }

        # Precondition: the seed actually exercises the dedup (raw file count
        # is strictly larger than the deduped truth), so the assertion below
        # is not vacuous — a naive glob WOULD over-count here.
        raw_file_count = sum(
            1 for p in (trinity_home / "conversations").rglob("*.json")
        )
        assert raw_file_count > ground_truth, (
            "seed must contain sidecar/frame files the canonical filter dedups"
        )
        assert canonical_total == ground_truth

        assert status_total == canonical_total, (
            "CLI `status` 'Captures: N from Chrome extension' over-reports vs the "
            "launchpad cockpit card / CLI doctor on the SAME conversations dir — "
            f"status={status_total} but iter_capture_files={canonical_total} "
            f"(ground truth {ground_truth}). diagnose() must dedup the "
            "<conv_id>.stream.json sidecar (claude/chatgpt) AND collapse gemini's "
            f"per-SSE-frame explosion. per-provider: status={status_per_provider} "
            f"vs canonical={canonical_per_provider}."
        )
        assert status_per_provider == canonical_per_provider


def _make_har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


def _post(url, status=200, content_type="text/event-stream", body="data: hi"):
    return {
        "request": {"method": "POST", "url": url, "postData": {"text": body}},
        "response": {"status": status, "headers": [{"name": "Content-Type", "value": content_type}]},
    }


def test_extract_chat_posts_matches_chatgpt_new_endpoint():
    har = _make_har([
        _post("https://chatgpt.com/backend-api/f/conversation"),
        _post("https://chatgpt.com/backend-api/conversation/abc123"),  # canonical GET — POST won't match
    ])
    posts = extension_repair.extract_chat_posts(har)
    assert len(posts) == 2  # both are POSTs and on chatgpt.com
    assert all(p["provider"] == "chatgpt" for p in posts)


def test_extract_chat_posts_strips_telemetry_noise():
    har = _make_har([
        _post("https://chatgpt.com/ces/v1/t"),
        _post("https://chatgpt.com/backend-api/sentinel/ping"),
        _post("https://chatgpt.com/ces/statsc/flush"),
        _post("https://chatgpt.com/backend-api/lat/r"),
        _post("https://chatgpt.com/backend-api/f/conversation"),  # the real one
    ])
    posts = extension_repair.extract_chat_posts(har)
    assert len(posts) == 1
    assert "/backend-api/f/conversation" in posts[0]["url"]


def test_extract_chat_posts_provider_filter():
    har = _make_har([
        _post("https://chatgpt.com/backend-api/f/conversation"),
        _post("https://claude.ai/api/organizations/x/chat_conversations/y/completion"),
        _post("https://gemini.google.com/_/BardChatUi/data/batchexecute"),
    ])
    chatgpt_only = extension_repair.extract_chat_posts(har, provider="chatgpt")
    assert len(chatgpt_only) == 1
    assert chatgpt_only[0]["provider"] == "chatgpt"

    claude_only = extension_repair.extract_chat_posts(har, provider="claude")
    assert len(claude_only) == 1
    assert claude_only[0]["provider"] == "claude"


def test_extract_chat_posts_records_status_and_content_type():
    har = _make_har([_post("https://chatgpt.com/backend-api/f/conversation", status=503, content_type="text/event-stream")])
    posts = extension_repair.extract_chat_posts(har)
    assert posts[0]["status"] == 503
    assert posts[0]["content_type"] == "text/event-stream"
    assert posts[0]["has_request_body"] is True


def test_extract_chat_posts_skips_non_chat_domains():
    har = _make_har([
        _post("https://example.com/api/something"),
        _post("https://google.com/search"),  # google.com but not gemini.google.com
    ])
    posts = extension_repair.extract_chat_posts(har)
    assert posts == []


def test_build_repair_bundle_includes_diagnosis_har_and_source():
    diag = {"providers": {"chatgpt": {"captures": 0, "exists": True}}}
    har_posts = [{"provider": "chatgpt", "method": "POST", "url": "https://chatgpt.com/backend-api/f/conversation", "status": 200, "content_type": "text/event-stream", "has_request_body": True}]
    page_hook = "// page-hook.js stub"
    bundle = extension_repair.build_repair_bundle(diag=diag, har_posts=har_posts, page_hook_source=page_hook)
    assert "PROVIDER_PATTERNS" in bundle.task_text
    assert "page-hook.js" in bundle.task_text
    assert "backend-api/f/conversation" in bundle.task_text  # the new endpoint must reach the council
    assert "// page-hook.js stub" in bundle.task_text  # source verbatim
    assert "unified diff" in bundle.task_text  # asks for a patch
    assert bundle.metadata["kind"] == "extension_repair"


def test_build_repair_bundle_truncates_har_posts_at_50():
    diag = {"providers": {}}
    posts = [{"provider": "chatgpt", "method": "POST", "url": f"https://chatgpt.com/backend-api/f/conversation/{i}",
              "status": 200, "content_type": "text/event-stream", "has_request_body": False} for i in range(120)]
    bundle = extension_repair.build_repair_bundle(diag=diag, har_posts=posts, page_hook_source="x")
    # The 50th URL must appear; the 60th must not.
    assert "/backend-api/f/conversation/49" in bundle.task_text
    assert "/backend-api/f/conversation/60" not in bundle.task_text


def test_handle_repair_diagnose_only_doesnt_require_har(trinity_home, capsys):
    class _Args:
        har = None
        provider = None
        as_json = False
    extension_repair.handle_repair(_Args())
    out = capsys.readouterr().out
    assert "Chrome extension capture diagnosis" in out
    assert "claude" in out
    assert "chatgpt" in out
    assert "gemini" in out


def test_handle_repair_har_not_found_exits_nonzero(trinity_home):
    class _Args:
        har = Path("/nonexistent.har")
        provider = None
        as_json = False
    with pytest.raises(SystemExit) as exc:
        extension_repair.handle_repair(_Args())
    assert exc.value.code == 2


def test_handle_repair_json_mode_emits_valid_json_for_diagnose(trinity_home, capsys):
    class _Args:
        har = None
        provider = None
        as_json = True
    extension_repair.handle_repair(_Args())
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "providers" in payload
    assert set(payload["providers"].keys()) == {"claude", "chatgpt", "gemini"}


class TestDetectFailurePatterns:
    """#150: stale-auth-cookie surfaces as a recoverable failure pattern
    before the user is asked to capture a HAR.

    The detection band is 24h ≤ hours_since_last ≤ 168h:
    - Below 24h: captures are fresh, no diagnosis needed
    - Above 168h (7 days): can't distinguish stale-cookie from "user
      just hasn't visited" — don't false-alarm
    - In between: high likelihood the auth cookie expired since the
      provider previously WAS capturing successfully
    """

    def _diag(self, slug: str, captures: int, hours: float | None):
        return {
            "providers": {
                slug: {
                    "exists": True,
                    "captures": captures,
                    "last_capture": "2026-05-23T00:00:00",
                    "hours_since_last": hours,
                }
            }
        }

    def test_fresh_capture_no_pattern_surfaced(self):
        diag = self._diag("claude", captures=5, hours=2.5)
        assert extension_repair.detect_failure_patterns(diag) == []

    def test_in_band_stale_surfaces_stale_auth_cookie(self):
        diag = self._diag("claude", captures=10, hours=48.0)
        patterns = extension_repair.detect_failure_patterns(diag)
        assert len(patterns) == 1
        p = patterns[0]
        assert p["pattern"] == "stale-auth-cookie"
        assert p["provider"] == "claude"
        assert "auth cookie" in p["hint"].lower()
        assert "claude.ai" in p["fix_command"]
        assert "log out" in p["fix_command"].lower()

    def test_very_old_above_band_does_not_false_alarm(self):
        diag = self._diag("chatgpt", captures=3, hours=500.0)
        # 500h > 168h → user probably just hasn't used the provider
        assert extension_repair.detect_failure_patterns(diag) == []

    def test_zero_captures_no_pattern_surfaced(self):
        """If a provider has never captured anything, the stale-cookie
        framing doesn't apply — there's nothing to be stale from."""
        d = {
            "providers": {
                "claude": {
                    "exists": True,
                    "captures": 0,
                    "last_capture": None,
                    "hours_since_last": None,
                }
            }
        }
        assert extension_repair.detect_failure_patterns(d) == []

    def test_each_stale_provider_gets_its_own_pattern(self):
        diag = {
            "providers": {
                "claude": {
                    "exists": True, "captures": 5,
                    "last_capture": "...", "hours_since_last": 50.0,
                },
                "chatgpt": {
                    "exists": True, "captures": 2,
                    "last_capture": "...", "hours_since_last": 100.0,
                },
                "gemini": {
                    "exists": True, "captures": 1,
                    "last_capture": "...", "hours_since_last": 5.0,
                },
            }
        }
        patterns = extension_repair.detect_failure_patterns(diag)
        providers = {p["provider"] for p in patterns}
        assert providers == {"claude", "chatgpt"}  # gemini is fresh, not flagged

    def test_printed_diagnose_surfaces_pattern_before_har_ask(
        self, trinity_home, monkeypatch, capsys,
    ):
        """The recoverable-pattern block must appear BEFORE the HAR
        instructions — users shouldn't be told to capture a HAR for
        issues an auth-refresh fixes."""
        # Build a real on-disk state: 5 capture files, mtime 48h ago.
        import os
        claude_dir = trinity_home / "conversations" / "claude"
        claude_dir.mkdir(parents=True)
        for i in range(5):
            f = claude_dir / f"cap_{i}.json"
            f.write_text("{}")
            # Backdate mtime to 48h ago
            stale_time = (
                __import__("time").time() - 48 * 3600
            )
            os.utime(f, (stale_time, stale_time))

        class _Args:
            har = None
            provider = None
            as_json = False

        extension_repair.handle_repair(_Args())
        out = capsys.readouterr().out

        assert "[stale-auth-cookie]" in out
        assert "Likely-recoverable patterns" in out
        # Ordering: pattern block must come before the HAR instructions
        pattern_pos = out.find("Likely-recoverable patterns")
        har_pos = out.find("trinity-local extension repair --har")
        assert pattern_pos > 0
        assert har_pos > 0
        assert pattern_pos < har_pos

    def test_json_mode_includes_recoverable_patterns(self, trinity_home, capsys):
        import os
        claude_dir = trinity_home / "conversations" / "claude"
        claude_dir.mkdir(parents=True)
        f = claude_dir / "cap.json"
        f.write_text("{}")
        stale_time = __import__("time").time() - 50 * 3600
        os.utime(f, (stale_time, stale_time))

        class _Args:
            har = None
            provider = None
            as_json = True

        extension_repair.handle_repair(_Args())
        payload = json.loads(capsys.readouterr().out)
        assert "recoverable_patterns" in payload
        assert any(p["pattern"] == "stale-auth-cookie" for p in payload["recoverable_patterns"])


class TestAutoRepair:
    """#147: self-healing — `extension repair --auto` dispatches the
    council on code-patch patterns without requiring HAR."""

    def _stale_provider_dir(self, trinity_home, slug: str, count: int, hours_ago: float):
        import os
        provider_dir = trinity_home / "conversations" / slug
        provider_dir.mkdir(parents=True)
        for i in range(count):
            f = provider_dir / f"cap_{i}.json"
            f.write_text("{}")
            stale_time = __import__("time").time() - hours_ago * 3600
            os.utime(f, (stale_time, stale_time))

    def test_fix_kind_field_present_on_all_patterns(self, trinity_home):
        """Every detected pattern must carry fix_kind so --auto can
        filter user-action vs code-patch correctly."""
        self._stale_provider_dir(trinity_home, "claude", count=10, hours_ago=48.0)
        self._stale_provider_dir(trinity_home, "gemini", count=10, hours_ago=300.0)
        diag = extension_repair.diagnose()
        patterns = extension_repair.detect_failure_patterns(diag)
        for p in patterns:
            assert "fix_kind" in p, f"missing fix_kind: {p}"
            assert p["fix_kind"] in ("user-action", "code-patch"), p["fix_kind"]

    def test_stale_auth_cookie_is_user_action(self, trinity_home):
        self._stale_provider_dir(trinity_home, "claude", count=10, hours_ago=48.0)
        diag = extension_repair.diagnose()
        patterns = extension_repair.detect_failure_patterns(diag)
        cookie_patterns = [p for p in patterns if p["pattern"] == "stale-auth-cookie"]
        assert len(cookie_patterns) == 1
        assert cookie_patterns[0]["fix_kind"] == "user-action"

    def test_extended_silence_is_code_patch(self, trinity_home):
        """>168h + enough captures (≥5) to rule out 'user just doesn't
        use this provider' surfaces as code-patch — the
        PROVIDER_PATTERNS regex likely needs updating."""
        self._stale_provider_dir(trinity_home, "gemini", count=10, hours_ago=300.0)
        diag = extension_repair.diagnose()
        patterns = extension_repair.detect_failure_patterns(diag)
        silence_patterns = [p for p in patterns if p["pattern"] == "provider-extended-silence"]
        assert len(silence_patterns) == 1
        assert silence_patterns[0]["fix_kind"] == "code-patch"
        assert "PROVIDER_PATTERNS" in silence_patterns[0]["hint"]

    def test_extended_silence_below_threshold_does_not_fire(self, trinity_home):
        """4 captures = not enough history to rule out 'user just
        doesn't use this provider.' Don't flag as code-patch."""
        self._stale_provider_dir(trinity_home, "gemini", count=4, hours_ago=300.0)
        diag = extension_repair.diagnose()
        patterns = extension_repair.detect_failure_patterns(diag)
        assert not any(p["pattern"] == "provider-extended-silence" for p in patterns)

    def test_build_auto_repair_bundle_omits_har_includes_patterns(self, trinity_home):
        """Auto-repair bundle must NOT include HAR data but MUST
        include the code-patch pattern hints in lieu."""
        self._stale_provider_dir(trinity_home, "gemini", count=10, hours_ago=300.0)
        diag = extension_repair.diagnose()
        patterns = extension_repair.detect_failure_patterns(diag)
        bundle = extension_repair.build_auto_repair_bundle(
            diag=diag,
            patterns=patterns,
            page_hook_source="// fake page-hook contents",
        )
        # No HAR data
        assert "HAR_POSTS" not in bundle.task_text
        # But pattern hints ARE present
        assert "DETECTED_PATTERNS" in bundle.task_text
        assert "provider-extended-silence" in bundle.task_text
        # Self-healing path tagged in metadata so council outcome can
        # be filtered later
        assert bundle.metadata.get("kind") == "extension_repair_auto"

    def test_auto_with_no_patterns_falls_through(self, trinity_home, capsys):
        """When no patterns surface, --auto prints the diagnose +
        "all healthy" note. Does NOT dispatch council."""
        # Fresh state — no providers
        class _Args:
            har = None
            provider = None
            as_json = False
            auto = True

        extension_repair.handle_repair(_Args())
        out = capsys.readouterr().out
        assert "Chrome extension capture diagnosis" in out
        assert "no actionable patterns" in out
        assert "Dispatching" not in out  # council NOT dispatched

    def test_auto_with_only_user_action_skips_council(self, trinity_home, capsys):
        """User-action patterns shouldn't trigger council dispatch —
        the fix is on the user's side, not in Trinity's code."""
        self._stale_provider_dir(trinity_home, "claude", count=10, hours_ago=48.0)
        class _Args:
            har = None
            provider = None
            as_json = False
            auto = True

        extension_repair.handle_repair(_Args())
        out = capsys.readouterr().out
        assert "user-action pattern" in out
        assert "Dispatching" not in out

    def test_auto_json_mode_returns_pattern_payload(self, trinity_home, capsys):
        """--auto --json on a code-patch hit emits the bundle preview
        + patterns + bundle_id without dispatching the council. Useful
        for scripting / dry-run inspection."""
        import json as _json
        self._stale_provider_dir(trinity_home, "gemini", count=10, hours_ago=300.0)

        class _Args:
            har = None
            provider = None
            as_json = True
            auto = True

        extension_repair.handle_repair(_Args())
        payload = _json.loads(capsys.readouterr().out)
        assert "diagnosis" in payload
        assert "patterns" in payload
        assert "bundle_id" in payload
        # Pattern hints surfaced
        kinds = [p.get("fix_kind") for p in payload["patterns"]]
        assert "code-patch" in kinds


class TestAutoRepairActionAllowlist:
    """#147 launchpad UI surface: capture-host action dispatch entry
    so the launchpad's "Repair extension" button fires
    `extension repair --auto --json` via Native Messaging.

    Same guard shape as test_memory_health's test_dream_in_action_allowlist
    — without the allowlist entry the dispatch silently no-ops.
    """

    def test_extension_repair_auto_in_allowlist(self):
        from trinity_local.capture_host import ACTION_ALLOWLIST

        assert "extension-repair-auto" in ACTION_ALLOWLIST
        entry = ACTION_ALLOWLIST["extension-repair-auto"]
        # Tuple shape: (cli_subcommand, arg_spec, [constant_flags])
        assert entry[0] == "extension"
        assert entry[1] == [], (
            "extension-repair-auto should take no dynamic args — "
            "--auto + --json are constant flags (host-controlled)"
        )
        constant_flags = entry[2] if len(entry) == 3 else []
        # The nested subcommand 'repair' must be in constant_flags so
        # the argv becomes [bin, extension, repair, --auto, --json]
        assert "repair" in constant_flags
        assert "--auto" in constant_flags
        assert "--json" in constant_flags


def test_cli_registration_lists_extension_subcommand():
    import argparse
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    extension_repair.register(subparsers)
    # Top-level `extension` parser exists.
    args = parser.parse_args(["extension", "repair"])
    assert args.command == "extension"
    assert args.extension_command == "repair"
    assert args.har is None


class TestProviderDisplayDomain:
    """Found 2026-06-02 by driving the real served launchpad in a browser: the
    memory-health card surfaced a RAW web-era capture slug in a user-facing hint
    — "2 provider(s) with stale auth (claude, chatgpt). Log out + log back in…".
    The capture slugs (claude/chatgpt/gemini) are the web LOGIN surfaces; the
    same function's fix_command already names the domain ("Log out of
    chatgpt.com"), so the hint both leaked an internal token AND contradicted its
    own instruction. detect_failure_patterns now carries a display-ready
    `provider_url` (the login domain) and the launchpad renders that.

    NOT the #275 brand-vs-slug call (that's CLI codex→GPT in the cheat-sheet) —
    a domain is factual + strictly more actionable for a "go re-login" message.
    """

    def _stale_diag(self):
        # chatgpt in the user-action (stale-cookie) band; gemini in the
        # code-patch (extended-silence) band; claude fresh (not flagged).
        return {"providers": {
            "claude":  {"exists": True, "captures": 9,  "hours_since_last": 5.0},
            "chatgpt": {"exists": True, "captures": 12, "hours_since_last": 72.0},
            "gemini":  {"exists": True, "captures": 30, "hours_since_last": 400.0},
        }}

    def test_detect_failure_patterns_carries_login_domain(self):
        patterns = extension_repair.detect_failure_patterns(self._stale_diag())
        by_provider = {p["provider"]: p for p in patterns}
        # Both bands must carry the display domain — mutation: drop the
        # provider_url field in detect_failure_patterns and this reds.
        assert by_provider["chatgpt"]["provider_url"] == "chatgpt.com"
        assert by_provider["gemini"]["provider_url"] == "gemini.google.com"
        # And it must equal the canonical _provider_url (single source of truth).
        assert by_provider["chatgpt"]["provider_url"] == extension_repair._provider_url("chatgpt")

    def test_memory_health_hint_renders_domain_not_raw_slug(self, trinity_home, monkeypatch):
        """The launchpad memory-health hint must show chatgpt.com, never the bare
        web-era slug — mutation: revert launchpad_data to join p["provider"] and
        " chatgpt)" reappears while "chatgpt.com" vanishes."""
        from trinity_local import launchpad_data

        # Inject a deterministic stale-auth pattern (with the new provider_url);
        # _memory_health imports these names at call time from the module.
        monkeypatch.setattr(extension_repair, "diagnose", lambda: self._stale_diag())
        health = launchpad_data._memory_health()
        auth_issues = [i for i in health["issues"] if i.get("status") == "auth-cookie-stale"]
        assert auth_issues, "stale-auth issue should surface for a 72h-silent provider"
        hint = auth_issues[0]["hint"]
        assert "chatgpt.com" in hint, f"hint should name the login domain: {hint!r}"
        # Every "chatgpt" occurrence must be part of the domain "chatgpt.com" —
        # a bare slug (chatgpt followed by ) or , or space) is the leak. Mutation:
        # revert launchpad_data to join p["provider"] → "(chatgpt)" → this reds.
        import re
        assert not re.search(r"chatgpt(?!\.com)", hint), (
            f"raw capture slug leaked into the user-facing hint: {hint!r}"
        )
