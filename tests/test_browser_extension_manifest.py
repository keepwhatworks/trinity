"""Structural validation for browser-extension/manifest.json.

Catches manifest typos and broken file references BEFORE the user runs
"Load Unpacked" in chrome://extensions (where Chrome's error messages
are terse and the failure mode is silent capture). Same principle as
the doc-consistency guards in test_doc_count_consistency.py: every
claim has a regression guard at the surface that ships it.

Validates:
* Required MV3 fields are present
* Every file referenced (background service worker, content_scripts
  js arrays) actually exists in browser-extension/
* MAIN-world content_scripts list adapters/<provider>.js BEFORE
  page-hook.js (load order matters — page-hook reads from
  window.__TRINITY_ADAPTERS which adapters populate)
* Host permissions cover all four target sites
* minimum_chrome_version is high enough for MAIN-world content_scripts
"""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = REPO_ROOT / "browser-extension"
MANIFEST = EXT_DIR / "manifest.json"

EXPECTED_HOSTS = {
    "https://claude.ai/*",
    "https://chatgpt.com/*",
    "https://chat.openai.com/*",
    "https://gemini.google.com/*",
}


def _load_manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_manifest_exists():
    assert MANIFEST.exists(), f"missing {MANIFEST}"


def test_manifest_is_mv3():
    assert _load_manifest()["manifest_version"] == 3


def test_minimum_chrome_supports_main_world_content_scripts():
    # MAIN-world content_scripts shipped in Chrome 111. v1.6 declares
    # this requirement in the manifest so older Chrome doesn't load
    # silently-broken.
    m = _load_manifest()
    version = m.get("minimum_chrome_version")
    assert version is not None, "manifest missing minimum_chrome_version"
    assert int(version.split(".")[0]) >= 111, f"need Chrome 111+, manifest has {version}"


def test_host_permissions_cover_all_providers():
    m = _load_manifest()
    hosts = set(m.get("host_permissions", []))
    missing = EXPECTED_HOSTS - hosts
    assert not missing, f"host_permissions missing {missing}"


def test_service_worker_file_exists():
    m = _load_manifest()
    sw_path = EXT_DIR / m["background"]["service_worker"]
    assert sw_path.exists(), f"service_worker references missing file: {sw_path}"


def test_all_content_script_files_exist():
    m = _load_manifest()
    for entry in m["content_scripts"]:
        for js_file in entry["js"]:
            full = EXT_DIR / js_file
            assert full.exists(), f"content_scripts entry references missing file: {full}"


def test_main_world_content_script_loads_adapter_before_page_hook():
    """page-hook.js reads from window.__TRINITY_ADAPTERS — the adapter
    has to register itself first. Manifest content_scripts entries are
    loaded in list order, so adapters/claude.js must come BEFORE
    page-hook.js in the js array.
    """
    m = _load_manifest()
    main_entries = [e for e in m["content_scripts"] if e.get("world") == "MAIN"]
    assert main_entries, "no MAIN-world content_script entry"
    for entry in main_entries:
        js = entry["js"]
        if "page-hook.js" not in js:
            continue
        adapter_indices = [i for i, f in enumerate(js) if f.startswith("adapters/")]
        page_hook_index = js.index("page-hook.js")
        if adapter_indices:
            assert max(adapter_indices) < page_hook_index, (
                f"page-hook.js must come AFTER all adapter scripts in MAIN-world entry; "
                f"got order {js}"
            )


def test_isolated_and_main_world_entries_target_same_origins():
    """Both worlds need to run on the same set of pages. If the matches
    arrays drift, the postMessage bridge silently breaks on whichever
    set of pages only has one world loaded.
    """
    m = _load_manifest()
    isolated = next((e for e in m["content_scripts"] if e.get("world") == "ISOLATED"), None)
    main = next((e for e in m["content_scripts"] if e.get("world") == "MAIN"), None)
    assert isolated and main, "manifest missing ISOLATED or MAIN content_scripts entry"
    assert set(isolated["matches"]) == set(main["matches"]), (
        "ISOLATED and MAIN content_scripts entries must match the same origins; "
        f"isolated={isolated['matches']} main={main['matches']}"
    )


def test_native_messaging_permission_declared():
    m = _load_manifest()
    perms = set(m.get("permissions", []))
    assert "nativeMessaging" in perms, (
        "missing 'nativeMessaging' permission — chrome.runtime.connectNative will fail"
    )


def test_page_hook_wraps_both_fetch_and_xhr():
    """page-hook.js must wrap BOTH window.fetch AND XMLHttpRequest.

    gemini.google.com dispatches its batchexecute RPCs through XHR, not
    fetch. A page-hook that only wraps fetch silently drops every
    gemini capture (caught live 2026-05-23 — `window.fetch.name` was
    "trinityFetch" but 17 batchexecute POSTs hit the network panel
    with zero adapter calls). Both wrappers must exist or the gemini
    surface goes dark.
    """
    src = (EXT_DIR / "page-hook.js").read_text()
    assert 'Object.defineProperty(window, "fetch"' in src or "window.fetch = trinityFetch" in src, (
        "page-hook.js no longer installs the fetch wrapper"
    )
    assert "XMLHttpRequest.prototype.open" in src, (
        "page-hook.js must wrap XMLHttpRequest.prototype.open — gemini uses XHR for batchexecute"
    )
    assert "XMLHttpRequest.prototype.send" in src, (
        "page-hook.js must wrap XMLHttpRequest.prototype.send — gemini uses XHR for batchexecute"
    )


def test_page_hook_classifies_sidebar_list_endpoints():
    """page-hook.js must classify recent-conversations list endpoints as
    `kind: "sidebar_list"` so the auto-sync diff pipeline can compute
    "what's on the server that isn't on disk yet." claude.ai uses
    GET /api/organizations/<org>/chat_conversations (no trailing /<id>);
    chatgpt.com uses GET /backend-api/conversations. Distinct from the
    canonical single-thread endpoints which DO have /<id> suffix and
    are already classified as `kind: "canonical"`.
    """
    src = (EXT_DIR / "page-hook.js").read_text()
    assert '"sidebar_list"' in src, (
        "page-hook.js must emit kind: 'sidebar_list' for the recent-"
        "conversations list endpoints — needed by the mobile-to-"
        "desktop auto-sync diff pipeline"
    )
    # Claude: ends-with check on /chat_conversations (legacy) AND
    # /chat_conversations_v2 (current as of 2026-05-23 — Anthropic
    # upgraded the endpoint; live probe caught the v1 pattern matching
    # nothing on /recents). Both kept to handle rolling cohort
    # migration where some accounts hit v1, some v2.
    assert 'endsWith("/chat_conversations")' in src, (
        "page-hook.js missing legacy claude /chat_conversations match"
    )
    assert 'endsWith("/chat_conversations_v2")' in src, (
        "page-hook.js missing current claude /chat_conversations_v2 match"
    )
    # ChatGPT: ends-with check on /backend-api/conversations
    assert 'endsWith("/backend-api/conversations")' in src, (
        "page-hook.js missing chatgpt sidebar_list endpoint detection"
    )


def test_sync_pill_content_script_present_and_wired():
    """The in-provider sync-pill content-script (`sync-pill.js`) must
    exist and be registered in the ISOLATED-world content_scripts
    entry. Click runs sync (fetch canonical URLs → send as captured
    payloads) for claude+chatgpt; gemini falls back to a click-each-
    sidebar message since gemini has no clean direct canonical URL.
    """
    pill = EXT_DIR / "sync-pill.js"
    assert pill.exists(), f"missing {pill}"
    src = pill.read_text()
    assert '"sync_status"' in src, "pill must call query_kind=sync_status"
    assert 'missing_count' in src, "pill must read missing_count from response"
    assert 'PROVIDER_HOSTS' in src, "pill must scope to known provider hosts"
    # Click action — runs current-tab orchestrated sync (background.js
    # owns the state machine; pill is stateless renderer).
    # Evolution: 4b4b05f direct-fetch (auth blocker) → b09dadb iframes
    # (gemini detection) → c11dc9e background tabs (tab thrashing) →
    # current: orchestrated current-tab navigation (user watches their
    # own tab tour their conversations + ends back where they started).
    assert 'start_current_tab_sync' in src, (
        "pill click must dispatch start_current_tab_sync to background "
        "(background owns the orchestrator state machine)"
    )
    assert 'get_current_tab_sync_state' in src, (
        "pill must query background for sync state since content-script "
        "is destroyed on every navigation during sync"
    )
    assert 'cancel_current_tab_sync' in src, (
        "pill must offer cancel during in-flight sync (user took over "
        "their tab; they can take it back)"
    )
    assert 'window !== window.top' in src, (
        "pill must bail in iframes — all_frames:true is still set so "
        "page-hook injects into any embedded provider frames"
    )

    m = _load_manifest()
    isolated = next((e for e in m["content_scripts"] if e.get("world") == "ISOLATED"), None)
    assert isolated is not None, "missing ISOLATED content_scripts entry"
    assert "sync-pill.js" in isolated["js"], (
        "sync-pill.js must be in the ISOLATED content_scripts list — "
        "otherwise it won't load on provider pages"
    )
    # Iframes only work with all_frames so page-hook injects into them too
    assert any(entry.get("all_frames") for entry in m["content_scripts"]), (
        "manifest must enable all_frames:true so page-hook catches "
        "canonical fetches fired by sync iframes"
    )


def test_content_script_includes_gemini_sidebar_dom_scrape():
    """content-script.js must include the gemini sidebar DOM-scrape path.

    Gemini doesn't expose its sidebar conversation list via any
    batchexecute RPC (verified 2026-05-23 — inspected all 22 distinct
    rpcids in captures, none carry conv_ids). The list lives only in
    the rendered DOM. content-script reads it on gemini.google.com
    pages and emits a sidebar_list payload in the same shape as the
    network-intercept path used for claude.ai + chatgpt.com.
    """
    src = (EXT_DIR / "content-script.js").read_text()
    assert 'gemini.google.com' in src, (
        "content-script.js missing gemini host check for DOM-scrape path"
    )
    assert 'readGeminiSidebar' in src or 'sidebar' in src.lower(), (
        "content-script.js missing gemini sidebar DOM-scrape function"
    )
    assert '"sidebar_list"' in src, (
        "content-script.js must emit kind: 'sidebar_list' for gemini "
        "DOM scrape — same schema as claude/chatgpt network captures"
    )


def test_content_script_guards_against_invalidated_context():
    """content-script.js must check chrome.runtime.id before sendMessage.

    When the user reloads the extension in chrome://extensions, every
    previously-injected content-script keeps running in already-open
    tabs but loses access to chrome.* APIs. Without a guard, every
    postMessage from page-hook throws "Extension context invalidated"
    and spams the page console. Caught 2026-05-23 on gemini.google.com.
    """
    src = (EXT_DIR / "content-script.js").read_text()
    assert "chrome?.runtime?.id" in src or "chrome.runtime.id" in src, (
        "content-script.js must guard against extension context invalidation"
    )


# ── Toolbar entry-point contract: the #318 side-panel migration removed
#    `default_popup` from the manifest, so clicking the toolbar icon opens the
#    SIDE PANEL (sidepanel.html via openPanelOnActionClick), NOT a popup and NOT
#    a new-tab launchpad. popup.html survives only as a web-accessible resource;
#    it is NOT the toolbar surface anymore.
#
#    Found 2026-06-17 by driving the REAL popup at chrome-extension://<id>/
#    popup.html (every control worked + gave feedback — clean usability) and
#    discovering the install docs STILL described the retired pre-#318 model
#    ("toolbar icon → launchpad opens in a new tab" / "Popup → Send to council
#    quick action"). A user following INSTALL-extension.md clicked the toolbar
#    expecting a new tab/popup and got the side panel — a "did it work?" moment.
#    This pins the doc claim to the manifest's real entry-point so they can't
#    drift apart again.
INSTALL_DOC = REPO_ROOT / "docs" / "INSTALL-extension.md"


def test_toolbar_action_opens_side_panel_not_popup():
    """The manifest's toolbar entry-point: side panel, NOT a default_popup."""
    m = _load_manifest()
    assert "default_popup" not in m.get("action", {}), (
        "manifest must NOT set action.default_popup — the #318 side-panel "
        "migration retired the toolbar popup; the action opens the side panel"
    )
    assert m.get("side_panel", {}).get("default_path") == "sidepanel.html", (
        "manifest must declare side_panel.default_path=sidepanel.html — the "
        "toolbar action opens the side panel, the launchpad's real home"
    )
    bg = (EXT_DIR / "background.js").read_text()
    assert "openPanelOnActionClick: true" in bg, (
        "background.js must call setPanelBehavior({openPanelOnActionClick:true}) "
        "— without it the toolbar icon click does nothing (no default_popup)"
    )


def test_install_doc_describes_side_panel_not_retired_popup_model():
    """INSTALL-extension.md must describe the REAL toolbar behavior (opens the
    side panel), not the retired pre-#318 model. Bites if the docs drift back
    to "opens in a new tab" / "Popup → Send to council quick action"."""
    doc = INSTALL_DOC.read_text()
    assert "side panel" in doc, (
        "INSTALL-extension.md must tell the user the toolbar icon opens a SIDE "
        "PANEL — the real #318 behavior an installer hits"
    )
    # The exact stale claims that misled an installer following step 7 + the
    # "what ships" list. Each described a surface model that no longer exists.
    assert "launchpad opens in a\n   new tab" not in doc and "opens in a new tab" not in doc, (
        "INSTALL-extension.md still claims the toolbar 'opens in a new tab' — "
        "FALSE since #318 (it opens the side panel via openPanelOnActionClick)"
    )
    assert 'Popup → "Send to council" quick action' not in doc, (
        "INSTALL-extension.md still lists 'Popup → Send to council' as the "
        "toolbar surface — the popup was retired as the toolbar entry in #318"
    )
