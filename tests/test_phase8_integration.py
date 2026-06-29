"""Phase 8 integration tests — exercise the full extension transition
pipeline end-to-end across the layer boundaries we built in Phases 1-7.

Each unit test pinned one layer. These integrations exercise the
contracts BETWEEN layers — the cross-layer invariants that no single
layer test could catch alone:

  1. install-extension persists settings → launchpad pageData reads
     them → dispatch_readiness reports them → doctor surfaces them.
  2. The Native Messaging frame protocol (4-byte LE length + JSON
     body) round-trips through a real subprocess invocation of
     trinity-local-capture-host with `--help` as a benign safe action.
  3. The 11-tier dispatch flow's data contract: the same payload
     shape the launchpad JS emits matches what capture_host parses.
  4. Public CLI surface includes every install-* command the
     MIGRATION doc references — drift principle #21.

These are the regression guards for "did Phase X break Phase X-1's
contract" — the failure mode an isolated unit test cannot see.
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_end_to_end_install_extension_to_doctor_signal(
    isolated_home, monkeypatch, capsys
):
    """Layer 2 → Layer 5: install-extension writes settings → doctor
    reports dispatch_ready as OK. This is the contract that proves the
    install path actually wires up what the launchpad / banner consumes.
    Break this and a user can run install-extension, get success output,
    yet still see the banner — Phase 6 of the 6-stage Pillar 4 funnel.
    """
    from trinity_local.commands.install import handle_install_extension

    monkeypatch.setattr(
        "shutil.which",
        lambda name, **kw: f"/usr/local/bin/{name}",
    )
    # No-op the actual NM-dir writes; we're testing the settings file path.
    monkeypatch.setattr(
        "trinity_local.commands.install._native_messaging_dirs",
        lambda browsers: [("chrome", isolated_home / "fake-chrome-nm")],
    )

    rc = handle_install_extension(SimpleNamespace(
        extension_id="abcdefghijklmnopabcdefghijklmnop",
        host_path=None,
        browsers=["chrome"],
        firefox=False,
    ))
    assert rc in (None, 0)

    # Force shortcut applicable=False so the test is platform-independent.
    monkeypatch.setattr(
        "trinity_local.launchpad_data._shortcut_status",
        lambda: {"ok": True, "applicable": False},
    )
    from trinity_local.health_checks import _check_dispatch_ready
    result = _check_dispatch_ready()
    assert result.ok is True, (
        f"After install-extension, doctor must report dispatch_ready=ok. "
        f"Got detail={result.detail!r}"
    )


def test_native_messaging_frame_round_trips_action_message(isolated_home):
    """Layer 1 (capture_host stdio frame) integration. Encodes a real
    4-byte-length-prefixed action message, pipes it to a child python
    running `capture_host.main()`, and decodes the response from stdout.

    This exercises the actual byte protocol the Chrome extension uses
    to talk to the host. Any drift between the JS side
    (browser-extension/background.js sendNativeMessage) and the Python
    side (capture_host._read_message) means the extension silently fails.

    Uses kind="doesnotexist" so the action is rejected by the allowlist —
    that exercises the parse + reject path without invoking a real CLI.
    """
    # Use a real allowlist kind with the required field MISSING. That
    # routes through _is_action_message → _run_action and produces a
    # structured rejection — exercises the parse + dispatch path
    # without launching an actual CLI subprocess.
    action = {"kind": "launch-council"}  # missing required `task`
    body = json.dumps(action).encode("utf-8")
    frame = struct.pack("<I", len(body)) + body

    # Run capture_host as a subprocess to get the real stdin/stdout
    # buffer semantics. Use the same venv python that runs the test.
    proc = subprocess.run(
        [sys.executable, "-m", "trinity_local.capture_host"],
        input=frame,
        capture_output=True,
        timeout=10,
    )
    # Parse the response frame: 4-byte LE length + JSON body.
    out = proc.stdout
    assert len(out) >= 4, f"response too short: {out!r}"
    resp_len = struct.unpack("<I", out[:4])[0]
    response = json.loads(out[4:4 + resp_len].decode("utf-8"))
    assert response["ok"] is False
    assert "missing required field" in response["error"]
    assert "task" in response["error"]


def test_dispatch_payload_shape_matches_capture_host_contract():
    """Layer 3 (dispatch script in launchpad JS) → Layer 1 (capture_host
    ACTION_ALLOWLIST). The launchpad JS emits {kind, task, ...}; the host
    parses on the same `kind` field. Drift here = dispatch silently
    rejected.

    This test asserts: for every kind the dispatch script emits, the
    host's allowlist has a matching entry. The launchpad calls two
    kinds today (`launch-council`, `ingest-recent` — see Phase 4
    commit 1c6ab25, launchpad_template.py callsites). If either is
    removed from the allowlist, the corresponding launchpad button
    silently no-ops.
    """
    from trinity_local.capture_host import ACTION_ALLOWLIST

    launchpad_kinds = set()
    # The dispatch script accepts kinds as arbitrary strings; callers
    # (launchpad template Vue methods) pick the kind. Crawl the template
    # for the actual call sites.
    from trinity_local import launchpad_template as lt
    template_src = Path(lt.__file__).read_text()
    # The two callsites land as {kind: 'launch-council', ...} and
    # {kind: 'ingest-recent'} (Phase 4 wiring).
    if "kind: 'launch-council'" in template_src or 'kind: "launch-council"' in template_src:
        launchpad_kinds.add("launch-council")
    if "kind: 'ingest-recent'" in template_src or 'kind: "ingest-recent"' in template_src:
        launchpad_kinds.add("ingest-recent")
    assert launchpad_kinds, (
        "No dispatch kinds detected in launchpad_template — Phase 4 wiring "
        "may have regressed."
    )
    for kind in launchpad_kinds:
        assert kind in ACTION_ALLOWLIST, (
            f"Launchpad fires kind={kind!r} but capture_host has no allowlist "
            f"entry for it. Either add to ACTION_ALLOWLIST in capture_host.py "
            f"or remove the dispatcher callsite in launchpad_template.py."
        )


def test_public_cli_includes_all_extension_transition_commands():
    """MIGRATION.md references install-extension and install-launcher —
    both must be reachable from the public CLI. Drift principle #21:
    every command name we tell the user to run in docs gets a regression
    guard at the CLI surface.
    """
    import argparse
    from trinity_local.commands.install import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register(subparsers)

    for cmd in ("install-mcp", "install-extension", "install-launcher",
                "install-hooks", "uninstall"):
        # argparse raises SystemExit on unknown subcommands; success means
        # the subcommand exists in the parser. We don't actually call the
        # handler — just confirm the parse succeeds.
        try:
            args = parser.parse_args([cmd])
        except SystemExit:
            pytest.fail(f"{cmd!r} is referenced in MIGRATION.md but not "
                        f"registered in commands/install.py register()")
        assert getattr(args, "handler", None) is not None, (
            f"{cmd!r} parser exists but has no handler set"
        )


def test_dispatch_readiness_doctor_launchpad_agree(
    isolated_home, monkeypatch
):
    """Drift principle #20 in action: the three surfaces consuming
    dispatch readiness (CLI hint, doctor check, launchpad banner) must
    all read the same underlying snapshot.

    Concretely: install nothing, then check that:
      - dispatch_readiness() returns ready=False
      - doctor's _check_dispatch_ready returns ok=False
      - pageData.browserExtension.configured is False
      - pageData.shortcutStatus.applicable controls Shortcut tier
    """
    monkeypatch.setattr("shutil.which", lambda name, **kw: None)
    monkeypatch.setattr(
        "trinity_local.launchpad_data._shortcut_status",
        lambda: {"ok": True, "applicable": False},
    )
    from trinity_local.launchpad_data import (
        dispatch_readiness, build_page_data
    )
    from trinity_local.health_checks import _check_dispatch_ready

    readiness = dispatch_readiness()
    doctor_check = _check_dispatch_ready()
    page_data = build_page_data(
        live_review_path=isolated_home / "stub.html",
        recent_councils=[],
    )

    # All three must agree on the "not ready" verdict.
    assert readiness["ready"] is False
    assert doctor_check.ok is False
    assert page_data["browserExtension"]["configured"] is False

    # The recommended_action text the doctor + dispatch_readiness agree on.
    assert "install-extension" in readiness["recommended_action"]
    assert "install-extension" in doctor_check.fix


def test_manifest_declares_external_messaging_contract_for_file_url():
    """Phase 8 structural pre-flight (council_bf1ab3f4dd70f75e flagged
    the real-Chrome boundary). Three contracts the real-Chrome smoke
    depends on; all must be present in the static artifacts so CI
    catches drift even without Chrome available:

      1. manifest.externally_connectable.matches includes file://
         (otherwise chrome.runtime.sendMessage from the launchpad
         is rejected before reaching the extension)
      2. background.js registers chrome.runtime.onMessageExternal
         (the internal onMessage listener does NOT receive
         externally-connectable messages)
      3. background.js handles the `trinity-ping` probe type
         (used by __TRINITY_DISPATCH__.probe() to detect the extension)
    """
    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo / "browser-extension" / "manifest.json").read_text())
    matches = manifest.get("externally_connectable", {}).get("matches", [])
    assert any("file:" in m for m in matches), (
        f"manifest.externally_connectable.matches must include file:// — got {matches!r}"
    )
    # 2026-05-26: HTTP launchpad path (trinity-local serve) needs both
    # localhost and 127.0.0.1 in the manifest because Chrome treats them
    # as distinct origins. Found via real-Chrome dogfood — the localhost
    # launchpad couldn't reach the extension before this expansion.
    assert any("localhost" in m for m in matches), (
        f"manifest.externally_connectable.matches must include http://localhost — "
        f"otherwise the `trinity-local serve` HTTP launchpad can't dispatch. "
        f"Got {matches!r}"
    )
    assert any("127.0.0.1" in m for m in matches), (
        f"manifest.externally_connectable.matches must include http://127.0.0.1 — "
        f"Chrome treats localhost and 127.0.0.1 as distinct origins, so both "
        f"need to be allowed. Got {matches!r}"
    )
    bg = (repo / "browser-extension" / "background.js").read_text()
    assert "onMessageExternal" in bg, (
        "background.js must register chrome.runtime.onMessageExternal — "
        "the internal onMessage listener does NOT receive externally-"
        "connectable messages."
    )
    assert "trinity-ping" in bg, (
        "background.js must handle the trinity-ping probe type — that's "
        "the warm-probe __TRINITY_DISPATCH__ uses to detect the extension."
    )


def test_background_sender_gate_rejects_path_spoof():
    """Phase 8 hardening (council_bf1ab3f4dd70f75e codex verdict): the
    sender.url check in background.js must REJECT path-suffix spoofs like
    `~/Downloads/.trinity/portal_pages/launchpad.html`. Previously the
    check used `includes(LAUNCHPAD_URL_SUFFIX)`; now it must use a strict
    `endsWith` after stripping query+hash.

    This test inspects the JS source for the load-bearing predicate
    shape — a structural guard so a future refactor doesn't quietly
    revert to the spoofable `includes()` form.
    """
    repo_root = Path(__file__).resolve().parents[1]
    bg_src = (repo_root / "browser-extension" / "background.js").read_text()
    # The hardened predicate must use endsWith (anchored to end-of-path)
    # against an allowed-page suffix, NOT the old `.includes(...)`. (The single
    # LAUNCHPAD_URL_SUFFIX became ALLOWED_URL_SUFFIXES when the council page was
    # added 2026-05-31, but the anchored-endsWith hardening is unchanged.)
    assert "cleaned.endsWith(s)" in bg_src, (
        "background.js must use endsWith (anchored) on the allowed page suffix — "
        "council_bf1ab3f4dd70f75e flagged the prior `.includes(...)` as "
        "spoofable by a malicious local file."
    )
    assert "split(\"?\")" in bg_src or "split('?')" in bg_src, (
        "background.js must strip query string before matching — without "
        "this, ?foo=… can tail the launchpad path."
    )


def test_background_sender_gate_accepts_council_page():
    """The live COUNCIL page (review_pages/live_council.html) is the other
    Trinity-served page that dispatches actions — Refine / Continue / Auto-chain
    / Stop council all fire council-iterate from THERE, not the launchpad. Found
    2026-05-31 (founder report): the sender gate only allowed launchpad.html, so
    every in-council refine got `rejected-sender` and surfaced a misleading "is
    the extension installed?" Both Trinity page suffixes must be allowed."""
    repo_root = Path(__file__).resolve().parents[1]
    bg_src = (repo_root / "browser-extension" / "background.js").read_text()
    assert "/portal_pages/launchpad.html" in bg_src, "launchpad must stay allowed"
    assert "/review_pages/live_council.html" in bg_src, (
        "the council page must be an allowed sender, or in-council refine/"
        "continue/stop all fail with rejected-sender."
    )


def test_background_sender_gate_accepts_http_localhost_launchpad():
    """2026-05-26: `trinity-local serve` binds 127.0.0.1:8765 and the
    launchpad-over-HTTP path is the recommended dev-mode entry (dodges
    Chrome's file:// unique-origin restrictions on iframes). The
    sender gate must accept BOTH http://localhost:<port>/... AND
    http://127.0.0.1:<port>/... — Chrome treats them as distinct
    origins, so the manifest lists both, and the runtime check must
    too. Found via real-Chrome dogfood: a localhost launchpad got
    `rejected-sender` from the extension despite the manifest update
    because the sender check was hardcoded to file://.

    This is a structural source-shape test — the live behavior is
    additionally covered by the e2e Chrome smoke when it runs.
    """
    repo_root = Path(__file__).resolve().parents[1]
    bg_src = (repo_root / "browser-extension" / "background.js").read_text()
    # Both origin shapes need to be explicitly accepted.
    assert "localhost" in bg_src and "127.0.0.1" in bg_src, (
        "background.js sender gate must explicitly accept http://localhost "
        "AND http://127.0.0.1 — Chrome routes both as distinct origins to "
        "the externally_connectable listener."
    )
    # The OLD diagnostic message (`accepted only from the file:// launchpad`)
    # must NOT survive — keeping it means the gate's behavior diverged
    # from its own error message, which is what surfaced the bug in the
    # field.
    assert "only from the file:// launchpad" not in bg_src, (
        "background.js still claims to reject all non-file:// senders in "
        "its rejection message — but the gate now accepts http://localhost. "
        "Update the diagnostic so the error message matches reality."
    )


def test_chrome_extension_popup_action_kinds_match_allowlist():
    """The capture_host action allowlist and the extension popup's
    action kinds must agree.

    The extension's duplicate launchpad.html was removed (2026-05-19)
    in favor of a single canonical file:// launchpad — the popup
    now dispatches `open-launchpad` to open that. popup.js is the
    only extension-specific UI; its dispatch() calls must reference
    kinds in capture_host's ACTION_ALLOWLIST.
    """
    from trinity_local.capture_host import ACTION_ALLOWLIST

    repo_root = Path(__file__).resolve().parents[1]
    popup_js = repo_root / "browser-extension" / "popup.js"
    assert popup_js.exists(), "browser-extension/popup.js missing"
    js_src = popup_js.read_text()
    # The popup dispatches via `dispatch("kind", ...)` calls.
    import re
    kinds = set(re.findall(r'dispatch\("([^"]+)"', js_src))
    assert kinds, "No dispatch() calls found in popup.js"
    for kind in kinds:
        assert kind in ACTION_ALLOWLIST, (
            f"popup.js dispatches kind={kind!r} but capture_host "
            f"allowlist does not include it. "
            f"Allowlist: {sorted(ACTION_ALLOWLIST.keys())!r}"
        )


def test_popup_setup_card_offers_paste_into_agent_brief():
    """The popup's setup card (shown when Native Messaging isn't wired)
    must offer a paste-into-Claude-Code / Claude-Desktop brief as the
    PRIMARY install path — that's the non-technical-user entry point
    the audience-expansion claim depends on.

    Pins:
      - "Copy install brief" button exists and is primary (not .secondary)
      - "Copy shell commands" button exists for terminal users (secondary)
      - The brief mentions Claude Code or Claude Desktop by name
      - The brief includes the three install steps and the extension ID
        placeholder so the agent has everything it needs
    """
    repo_root = Path(__file__).resolve().parents[1]
    popup_js = (repo_root / "browser-extension" / "popup.js").read_text()

    # Primary brief button (paste-into-agent path).
    assert 'id: "copy-setup-brief"' in popup_js, (
        "popup.js must define the 'copy-setup-brief' button — primary "
        "non-technical-user install path."
    )
    # Secondary shell-commands button still available for terminal users.
    assert 'id: "copy-setup-cmds"' in popup_js, (
        "popup.js must keep the 'copy-setup-cmds' button as a secondary "
        "affordance for terminal-native users."
    )
    # The shell button must be marked secondary so the brief is visibly
    # the primary action.
    assert 'class: "btn secondary",\n    id: "copy-setup-cmds"' in popup_js, (
        "'copy-setup-cmds' must be styled as a secondary button so the "
        "brief reads as the primary action."
    )
    # Brief content must reference Claude Code / Desktop explicitly so
    # the user knows where to paste, and must include the three install
    # steps the agent needs to run.
    for required in (
        "Claude Code",
        "Claude Desktop",
        "install-extension",
        "trinity-local status",
    ):
        assert required in popup_js, (
            f"Install brief missing {required!r} — agent or user can't "
            f"complete setup without it."
        )
