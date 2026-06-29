"""Tests for the Phase 4 three-tier dispatch path.

Phase 4 of the macOS-Shortcuts → Chrome-extension transition routes
launchpad button clicks through one of three tiers in priority order:

  1. Chrome extension (via chrome.runtime.sendMessage to a known
     extension ID + the extension's onMessageExternal handler).
  2. macOS Shortcut (the existing shortcuts:// URL path).
  3. Inline install prompt banner.

This file covers the Python side of the contract:

- `launchpad_data._browser_extension()` reads the persisted ID written
  by `commands.install.handle_install_extension`.
- The launchpad pageData carries `browserExtension` so the file:// JS
  can call chrome.runtime.sendMessage(<id>, …) without guessing.
- `launchpad_runtime_js()` emits the `window.__TRINITY_DISPATCH__`
  contract on which both `launchCouncil` and `ingestOnce` rely.

The JS-side contract (probe → cache → dispatch) lives in
browser-extension/background.js + launchpad_runtime_js() and is
covered by the manifest + node --check smoke + the existing
test_install_extension.py for the manifest writes.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    return tmp_path


def test_browser_extension_empty_when_settings_file_missing(isolated_home):
    """Fresh install — no extension.json yet. dispatch should report
    `configured: False` so the JS skips the extension probe."""
    from trinity_local.launchpad_data import _browser_extension

    result = _browser_extension()
    assert result == {"extensionId": None, "configured": False, "webStoreUrl": ""}


def test_browser_extension_reads_persisted_id(isolated_home, monkeypatch):
    """When install-extension has persisted the ID, _browser_extension
    surfaces it for the launchpad. The 32-char `a-p` format is Chrome's
    canonical extension-ID encoding."""
    from trinity_local import state_paths
    from trinity_local.launchpad_data import _browser_extension

    settings_dir = state_paths.telemetry_settings_dir()
    payload = {
        "extension_id": "abcdefghijklmnopabcdefghijklmnop",
        "host_path": "/usr/local/bin/trinity-local-capture-host",
        "browsers": ["chrome: /tmp/local.trinity.capture.json"],
    }
    (settings_dir / "extension.json").write_text(json.dumps(payload))

    result = _browser_extension()
    assert result == {
        "extensionId": "abcdefghijklmnopabcdefghijklmnop",
        "configured": True,
        "webStoreUrl": "",
    }


def test_browser_extension_treats_missing_id_field_as_not_configured(isolated_home):
    """A malformed settings file (no extension_id, or empty string) must
    NOT promote the launchpad to `configured: True` — that would point
    chrome.runtime.sendMessage at a falsy ID and silently fail every
    dispatch."""
    from trinity_local import state_paths
    from trinity_local.launchpad_data import _browser_extension

    settings_dir = state_paths.telemetry_settings_dir()
    (settings_dir / "extension.json").write_text(json.dumps({"host_path": "/x"}))

    result = _browser_extension()
    assert result["configured"] is False
    assert result["extensionId"] is None


def test_browser_extension_treats_malformed_json_as_not_configured(isolated_home):
    """Corrupt settings file must not raise — degrade gracefully."""
    from trinity_local import state_paths
    from trinity_local.launchpad_data import _browser_extension

    settings_dir = state_paths.telemetry_settings_dir()
    (settings_dir / "extension.json").write_text("not-json{")

    result = _browser_extension()
    assert result["configured"] is False


def test_install_extension_persists_id_for_launchpad(isolated_home, monkeypatch, capsys):
    """End-to-end: `trinity-local install-extension --extension-id <X>`
    must write the settings file that `_browser_extension` reads."""
    from trinity_local import state_paths
    from trinity_local.commands.install import handle_install_extension

    monkeypatch.setattr(
        "shutil.which", lambda name: f"/usr/local/bin/{name}"
    )
    # Avoid touching the real Native Messaging directories on macOS/Linux.
    monkeypatch.setattr(
        "trinity_local.commands.install._native_messaging_dirs",
        lambda browsers: [("chrome", isolated_home / "fake-chrome-nm")],
    )

    args = SimpleNamespace(
        extension_id="abcdefghijklmnopabcdefghijklmnop",
        host_path=None,
        browsers=["chrome"],
        firefox=False,
    )
    rc = handle_install_extension(args)
    assert rc == 0 or rc is None

    settings_file = state_paths.telemetry_settings_dir() / "extension.json"
    assert settings_file.exists()
    payload = json.loads(settings_file.read_text())
    assert payload["extension_id"] == "abcdefghijklmnopabcdefghijklmnop"
    assert "host_path" in payload


def test_launchpad_runtime_js_emits_dispatch_contract():
    """The runtime block must define window.__TRINITY_DISPATCH__ with the
    methods that launchpad_template.py callers depend on (`dispatch`,
    `probe`, `state`, `extensionId`). If this block is renamed or moved,
    the Vue methods break silently — every launch goes through the
    fallback path forever."""
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    js = launchpad_runtime_js()
    assert "window.__TRINITY_DISPATCH__" in js
    assert "dispatch" in js
    assert "trinity-ping" in js
    assert "sessionStorage" in js
    # The two live tier names must appear so the result handler in Vue
    # can branch on them. (Tier-2 'shortcut' was retired 2026-05-18 with
    # the macOS Shortcut dispatcher kill — only extension + install-prompt
    # remain on the live dispatch path.)
    assert "'extension'" in js
    assert "'install-prompt'" in js
    assert "native-host-unavailable" in js
    # Tier-2 shortcut branch is GONE — Chrome extension is the only
    # live dispatch path. Regression guard against accidental re-add.
    # buildShortcutUrl() survives as a `return ''` no-op (callsites in
    # launchpad_template + council_review still reference the function
    # name; they pass '' into dispatch which ignores it).
    assert "'shortcut'" not in js
    assert "shortcuts://run-shortcut" not in js
    # Orphan `canUseShortcut()` getter must NOT be reintroduced — it
    # used to call a function that was deleted with the macOS Shortcut
    # tier, throwing ReferenceError every time anything read
    # __TRINITY_DISPATCH__.canUseShortcut. The getter was a silent crash
    # because nothing on the live launchpad reads it post-Shortcut-retirement,
    # but probes from external tooling (claude-in-chrome MCP, browser
    # extensions, devtools snippets) hit it. Found 2026-05-26 via real
    # browser dogfood — guard so the orphan can't sneak back in another
    # cleanup pass.
    assert "canUseShortcut" not in js, (
        "canUseShortcut is an orphan reference left over from the retired "
        "macOS Shortcut tier — readers of __TRINITY_DISPATCH__.canUseShortcut "
        "would get ReferenceError because the function it called was deleted "
        "with the tier."
    )


def test_launchpad_runtime_js_uses_pageData_for_extension_id():
    """The dispatch script must read the extension ID from pageData
    (the only path the file:// page has) — not hard-code anything."""
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    js = launchpad_runtime_js()
    assert "pageData.browserExtension" in js


def test_launchpad_runtime_js_includes_external_messaging_protocol():
    """sendMessage to a specific extension ID (not the default) is the
    only API that works for file:// → externally-connectable extension
    delivery. Regression-guard the signature."""
    from trinity_local.launchpad_runtime import launchpad_runtime_js

    js = launchpad_runtime_js()
    # chrome.runtime.sendMessage(extensionId, message, callback) is the
    # contract; rejecting `chrome.runtime.sendMessage(message, callback)`.
    assert re.search(r"chrome\.runtime\.sendMessage\(\s*extensionId", js), (
        "dispatch must target a specific extensionId, not the default extension"
    )


def test_launch_council_dispatch_forwards_status_token():
    """The launchCouncil() method must include `status_token` in its
    extensionAction payload.

    The launchpad generates a status_token locally at launchCouncil time,
    starts polling for `council_status_<token>.js`, then dispatches the
    action. Without the token in the dispatch payload, the CLI generates
    its own bundle_<id> and the two never reconnect — the launchpad's
    poll for launch_<token> 404s forever and the Council card sticks on
    "QUEUED" even though the backend finished.

    The capture-host ACTION_ALLOWLIST already maps payload['status_token']
    → --status-token, and council-launch already accepts the flag. The
    gap is purely on the launchpad side. Found 2026-05-26 via real-Chrome
    dogfood from claude-in-chrome MCP — the council ran to completion
    in 32s and wrote a perfectly valid bundle_status, but the
    launchpad's polling loop was reading a different file path.
    """
    from trinity_local.launchpad_template import render_launchpad_html
    # Build a minimal page_data so render_launchpad_html runs without
    # touching ~/.trinity. We only care about the JS shape this method
    # emits — content of the rest of the page doesn't matter.
    page_data = {
        "enabled": False, "endpoint": "", "anonymous_id": "",
        "autoChainEnabled": False, "polishAutoIterate": False,
        "personalRouting": {}, "globalRouting": {},
        "tasteLenses": {"paired_lenses": [], "orderings": [],
                        "rejections": [], "vocabulary": [],
                        "abstract_lenses": [], "rejections_share_text": "",
                        "vocabulary_share_text": "",
                        "abstract_lenses_share_text": "",
                        "combined_share_text": ""},
        "cortexRules": None, "councilQuerySuggestions": [],
        "providerHealth": {"providers": []}, "activeOperation": None,
        "memoryHealth": {"issues": [], "ok_count": 4, "total_count": 4},
        "memoryHealthDigest": "",
        "ratingsHistory": {"labels": [], "datasets": []},
        "personalRoutingEmptyState": {}, "modelLineup": [],
        "providerNames": {}, "providerLabels": {},
    }
    html = render_launchpad_html(page_data=page_data)
    # Find the launchCouncil() method body specifically — extensionAction
    # appears in several methods (stop-council, render-me-card, etc.).
    # The launchCouncil block can be located by the 'kind: \'launch-council\''
    # marker plus the surrounding extensionAction.
    import re
    block_match = re.search(
        r"extensionAction:\s*\{[^}]*kind:\s*'launch-council'[^}]*\}",
        html, re.DOTALL,
    )
    assert block_match, "launchCouncil's extensionAction payload not found"
    block = block_match.group(0)
    assert "status_token" in block, (
        "launchCouncil's extensionAction payload is missing status_token. "
        "The launchpad polls council_status_<launch_token>.js but the CLI "
        "writes council_status_<bundle_id>.js — they never reconnect. The "
        "fix is one line: add `status_token: statusToken,` to the "
        "extensionAction. capture_host's ACTION_ALLOWLIST already maps "
        "this field to --status-token, and council-launch already accepts "
        "the flag."
    )


def test_browser_extension_in_launchpad_pagedata(isolated_home):
    """The pageData payload the launchpad template consumes must carry
    `browserExtension` so the JS dispatch script can read it. Without
    this key, `pageData.browserExtension.extensionId` is undefined and
    the dispatcher defaults to `absent` forever."""
    from trinity_local.launchpad_data import build_page_data

    page_data = build_page_data(
        live_review_path=isolated_home / "stub-review.html",
        recent_councils=[],
    )
    assert "browserExtension" in page_data
    assert isinstance(page_data["browserExtension"], dict)
    assert "extensionId" in page_data["browserExtension"]
    assert "configured" in page_data["browserExtension"]


def test_no_blocking_dialogs_in_served_launchpad():
    """The served launchpad must never use a BLOCKING window.alert/prompt/confirm.
    They freeze the page until the user dismisses them AND block the Chrome
    extension's event loop (the browser-automation hazard). Validation surfaces via
    the inline `.status-error` ribbon (launchError); copy-fallback uses the silent
    `_copyFallback` textarea. Guards the v1.7.x replacement of the empty-prompt
    alert (launchCouncil) + the copyText window.prompt fallback — a fast default-
    shard sibling of the slow browser guard in test_launchpad_launch_form.py."""
    from trinity_local.launchpad_template import render_launchpad_html

    html = render_launchpad_html(page_data={})
    for blocking in ("window.alert(", "window.prompt(", "window.confirm("):
        assert blocking not in html, (
            f"the served launchpad uses a blocking `{blocking}…)` — use the inline "
            ".status-error ribbon / _copyFallback so the page (and the extension "
            "event loop) never freezes"
        )


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    chans = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _composite_over(rgba: tuple[int, int, int, float], bg_hex: str) -> str:
    """Composite an rgba(r,g,b,alpha) tint over an opaque hex background and
    return the resulting opaque hex — the SAME math the browser does when a card
    sets `background: rgba(...)`. The accent cards (capture / cold-open / council-
    value / councilTier) set their tint DIRECTLY on the grey page (`--bg-base`),
    not on the near-white `--surface`, so the effective background under their
    body text is darker than `--surface`."""
    r, g, b, a = rgba
    h = bg_hex.lstrip("#")
    br, bg_, bb = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    out = (round(r * a + br * (1 - a)), round(g * a + bg_ * (1 - a)), round(b * a + bb * (1 - a)))
    return "#%02x%02x%02x" % out


# The low-alpha ACCENT-CARD tints that sit directly on `--bg-base` (a `<section
# class="card" style="background: rgba(...)">`), each carrying `.meta` body text:
#   capture / cross-bootstrap   rgba(43, 80, 112, 0.04)   (launchpad_template ~L1038/1395)
#   hero-proof cold-open        rgba(79, 144, 149, 0.06)  (~L1066)
#   council-value proof         rgba(74, 111, 165, 0.07)  (~L1074)
#   council-tier upsell         rgba(45, 106, 79, 0.05)   (~L1220)
_ACCENT_CARD_TINTS = {
    "capture rgba(43,80,112,0.04)": (43, 80, 112, 0.04),
    "cold-open rgba(79,144,149,0.06)": (79, 144, 149, 0.06),
    "council-value rgba(74,111,165,0.07)": (74, 111, 165, 0.07),
    "council-tier rgba(45,106,79,0.05)": (45, 106, 79, 0.05),
}


def test_palette_meta_text_meets_aa_on_tinted_accent_cards():
    """FOUNDER SYMPTOM (panel-driven 2026-06-19): `.meta` body text (`--text-muted`)
    on the rgba-TINTED accent cards rendered at only 4.38:1 — below the 4.5 AA-normal
    floor. The capture card's "The extension is installed…" body and the stats
    hero's "← Back to the council" link both painted #616a73 over an effective
    background of ~[226,230,234] (the `rgba(43,80,112,0.04)` blue tint composited
    over the grey `--bg-base`, NOT over the near-white `--surface`).

    The sibling guard test_palette_meets_wcag_aa_for_body_text only checks
    `--text-muted` over the FLAT tokens (bg_base / surface_muted / surface) — where
    #616a73 squeaks by at 4.65 — so it was BLIND to the tinted-card composite where
    the muted body text actually fails. This pins the REAL effective background.

    Mutation-proven: revert COLORS["text_muted"] to "#616a73" and this reds at
    every accent tint (4.28–4.39 < 4.5); #5e666f clears them all (4.53–4.64)."""
    from trinity_local.design_system import COLORS

    muted = COLORS["text_muted"]
    bg_base = COLORS["bg_base"]
    for name, tint in _ACCENT_CARD_TINTS.items():
        eff_bg = _composite_over(tint, bg_base)
        r = _contrast(muted, eff_bg)
        assert r >= 4.5, (
            f"--text-muted ({muted}) on the {name} accent card is {r:.2f}:1 "
            f"(effective bg {eff_bg} = the tint composited over --bg-base {bg_base}) "
            f"— below AA 4.5 for body text. The card's .meta copy is unreadable-grade; "
            f"deepen --text-muted (it must clear AA on the tinted accent cards, not just "
            f"the flat surface tokens the sibling guard checks)."
        )


def test_palette_meets_wcag_aa_for_body_text():
    """The founder-flagged AA shortfalls (UX sweep 2026-06-16, "push to AA"):
    the primary CTA (white on the action teal) and muted body text were below the
    4.5:1 AA-normal threshold — the old fix bolded the button to fake AA-large
    rather than deepen the teal. This pins the REAL contrast so a palette refactor
    can't regress it: the action teal carries white text at >=4.5:1 on its OWN,
    and --text-muted clears 4.5:1 on every background it renders on (page base,
    code-chip, card surface)."""
    from trinity_local.design_system import COLORS

    btn = _contrast(COLORS["action_text"], COLORS["action_primary"])
    assert btn >= 4.5, (
        f"primary button white-on-teal is {btn:.2f}:1 — below AA-normal 4.5. "
        "Deepen action_primary (it must carry white text on its own, not via a bold hack)."
    )
    muted = COLORS["text_muted"]
    for bg_key in ("bg_base", "surface_muted", "surface"):
        r = _contrast(muted, COLORS[bg_key])
        assert r >= 4.5, (
            f"--text-muted on {bg_key} ({COLORS[bg_key]}) is {r:.2f}:1 — below AA 4.5 for body text"
        )


def test_warning_text_token_clears_aa_for_failure_message_body():
    """FOUNDER SYMPTOM (panel sweep 2026-06-20): `--warning` (#bd9658) was doing
    double duty as a FILL amber (3px border-left, icon) AND as readable failure-
    message body text — the bulk-import error banner ("⚠ No exports detected…", 13px)
    and the memory-health Refresh/Repair "couldn't dispatch" lines (12px) all painted
    #bd9658 at 2.3–2.6:1 on the light card backgrounds, FAR below the AA-normal 4.5
    floor. The fix split it the way --accent/--accent-deep already are: --warning
    stays the fill, --warning-text (deep amber) carries the text.

    This pins the TEXT token's real contrast on every light background the failure
    copy renders on — the flat card tokens AND the rgba(79,144,149,0.06) tint the
    import error block composites over its card. Mutation-proven: set
    COLORS["warning_text"] back to the fill "#bd9658" and this reds at every bg
    (2.3–2.6 < 4.5); #79591b clears them all (4.8–6.2)."""
    from trinity_local.design_system import COLORS

    warn_text = COLORS["warning_text"]
    # the import error block tints rgba(79,144,149,0.06) over its card before the text
    err_tint = _composite_over((79, 144, 149, 0.06), COLORS["bg_base"])
    for name, bg in (
        ("bg_base", COLORS["bg_base"]),
        ("surface_muted", COLORS["surface_muted"]),
        ("surface", COLORS["surface"]),
        ("import-error tint over bg_base", err_tint),
    ):
        r = _contrast(warn_text, bg)
        assert r >= 4.5, (
            f"--warning-text ({warn_text}) on {name} ({bg}) is {r:.2f}:1 — below AA 4.5 "
            f"for failure-message body text. The '⚠ … couldn't dispatch' / '⚠ No exports "
            f"detected' copy is unreadable-grade; deepen --warning-text (keep --warning as "
            f"the fill, don't merge them)."
        )


def test_danger_text_token_clears_aa_for_status_label_body():
    """FOUNDER SYMPTOM (memory-viewer sweep 2026-06-22): `--danger` (#bd6a5a) was doing
    double duty as a FILL terracotta (3px border-left, icon) AND as a readable small
    status label — the memory viewer's lens-TRUST banner "degraded" tag
    (.viewer-trust-label, 11px) painted #bd6a5a at 2.98:1 over the banner's
    rgba(189,106,90,0.10) tint, and the shared .badge.danger label at 3.26:1 over its
    rgba(163,60,47,0.12) tint — both FAR below the AA-normal 4.5 floor. --danger was
    the LONE tint color missing its deepened text sibling (--success-text /
    --warning-text already exist). The fix split it the way the others are: --danger
    stays the fill, --danger-text (deep terracotta) carries the small text.

    This pins the TEXT token's real contrast on the two tints it renders over: the
    .badge.danger background and the memory-viewer trust-banner background (which
    composites over the viewer's --bg-wash page). Mutation-proven: set
    COLORS["danger_text"] back to the fill "#bd6a5a" and this reds at every bg
    (2.98–3.26 < 4.5); #99392c clears them all (5.0–5.9)."""
    from trinity_local.design_system import COLORS

    danger_text = COLORS["danger_text"]
    # .badge.danger tints rgba(163,60,47,0.12) over its card (worst case: the grey page).
    badge_tint = _composite_over((163, 60, 47, 0.12), COLORS["bg_base"])
    # The memory viewer's trust banner tints rgba(189,106,90,0.10) over the viewer's
    # light page wash (~#eaecef) — the exact background the "degraded" label paints on.
    viewer_wash = COLORS.get("bg_wash", "#eaecef")
    banner_tint = _composite_over((189, 106, 90, 0.10), viewer_wash)
    for name, bg in (
        (".badge.danger tint over bg_base", badge_tint),
        ("trust-banner tint over viewer wash", banner_tint),
        ("surface_muted", COLORS["surface_muted"]),
    ):
        r = _contrast(danger_text, bg)
        assert r >= 4.5, (
            f"--danger-text ({danger_text}) on {name} ({bg}) is {r:.2f}:1 — below AA 4.5 "
            f"for status-label body text. The memory viewer's 'DEGRADED' lens-trust tag "
            f"(.viewer-trust-label) / .badge.danger label is unreadable-grade; deepen "
            f"--danger-text (keep --danger as the fill, don't merge them)."
        )
