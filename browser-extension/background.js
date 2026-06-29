// Trinity Local — background.js (service worker)
//
// Receives captured payloads from content-script.js and forwards
// them to the native messaging host (trinity-local-capture-host)
// via chrome.runtime.connectNative.
//
// Chrome spawns the host process on the first connect; the host
// reads length-prefixed JSON from stdin and writes captured turns
// to ~/.trinity/conversations/<provider>/<conv_id>.json. When the
// service worker goes idle and the port disconnects, Chrome reaps
// the host process.
//
// Day 1 (this tick): no adapters yet. We forward the raw captured
// payload to the host with kind="raw"; the host logs it. Day 5
// claude.js adapter normalizes the stream/canonical payloads into
// Trinity's conversation schema before forwarding.

const NATIVE_HOST = "local.trinity.capture";

let port = null;

// ─── Standalone capture sink (works WITHOUT the native host) ──────────
// The extension is a good standalone "download your transcripts" tool: every
// captured conversation is mirrored into chrome.storage.local, so a user who
// installed ONLY the extension (no CLI) still owns their history and can export
// it. When the host IS present it ALSO writes ~/.trinity (the source of truth);
// the local mirror is cheap (unlimitedStorage) and dedup'd by conv_id, so its
// size tracks the user's unique conversation count, not the capture rate.
// A sandboxed extension cannot write ~/.trinity itself — only the native host
// can — so chrome.downloads (→ ~/Downloads/trinity/) is the only standalone sink.
const CAPTURE_KEY_PREFIX = "cap:";

function captureStorageKey(provider, conv_id) {
  return `${CAPTURE_KEY_PREFIX}${provider}:${conv_id}`;
}

function extensionCaptureStats(records) {
  const providers = {};
  let last_at = null;
  for (const r of records || []) {
    if (!r || !r.provider) continue;
    providers[r.provider] = (providers[r.provider] || 0) + 1;
    if (r.captured_at && (!last_at || r.captured_at > last_at)) last_at = r.captured_at;
  }
  return { count: (records || []).length, providers, last_at };
}

function buildTranscriptBundle(records, now) {
  const stamp = now || new Date().toISOString();
  const stats = extensionCaptureStats(records);
  return {
    bundle: {
      schema: "trinity-transcripts-v1",
      generated_at: stamp,
      count: stats.count,
      providers: stats.providers,
      // Mirror what the host writes per conversation, so `trinity-local
      // import-export ~/Downloads/trinity` can slurp the bundle later.
      conversations: (records || []).map((r) => ({
        provider: r.provider, conv_id: r.conv_id, captured_at: r.captured_at,
        payload: r.payload,
      })),
    },
    // chrome.downloads roots at the Downloads dir; a relative subpath is allowed,
    // escaping it is not. Visible `trinity/` (no dot) — the user should SEE their files.
    filename: `trinity/transcripts-${stamp.slice(0, 10)}.json`,
  };
}

// ─── Extension onboarding tip ladder (mirrors src/trinity_local/tips.py) ──────
// Prereq-gated ordered ladder: the first eligible, un-seen rung surfaces. The
// order IS the dependency graph. capture-nudge → install-cli → create-lens.
const EXTENSION_TIPS = [
  {
    // The header (.sub) already owns "captured locally … never leave your machine"
    // and the count card's empty-hint already says "browse … to start capturing", so
    // this rung must NOT restate either — it earns its callout by naming the PAYOFF
    // (what a captured corpus unlocks) the cold surface states nowhere else. (The
    // privacy reassurance + provider trio were duplicated here verbatim; founder UX
    // sweep flagged the same line appearing twice on one narrow panel.)
    key: "capture-nudge",
    prereq: (s) => (s.captured || 0) === 0,
    message: "Once a few are captured, you can run them through a council across all three — and build a taste lens that answers in your voice.",
    cta: "",
  },
  {
    key: "install-cli",
    prereq: (s) => (s.captured || 0) > 0 && !s.hostPresent,
    message: "Your transcripts are yours. Run a council across all three + build a taste lens — install Trinity (one command).",
    cta: "keepwhatworks.com",
  },
  {
    key: "create-lens",
    prereq: (s) => s.hostPresent && !s.lensBuilt,
    message: "Build your taste lens from your captured history — answers come back in your voice.",
    cta: "trinity-local lens-setup",
  },
];

function nextExtensionTip(state) {
  const seen = (state && state.seen) || [];
  for (const tip of EXTENSION_TIPS) {
    if (seen.includes(tip.key)) continue;
    try {
      if (tip.prereq(state || {})) return { key: tip.key, message: tip.message, cta: tip.cta };
    } catch { /* a bad prereq never breaks the ladder */ }
  }
  return null;
}

function storeCaptureLocally(payload) {
  // Defensive: a payload without provider+conv_id can't be keyed/dedup'd.
  const provider = payload && payload.provider;
  const conv_id = payload && payload.conv_id;
  if (!provider || !conv_id) return;
  try {
    const key = captureStorageKey(provider, conv_id);
    chrome.storage.local.set({
      [key]: { provider, conv_id, payload, captured_at: new Date().toISOString() },
    });
  } catch (e) {
    console.warn("[trinity-bg] local capture store failed", e);
  }
}

function readCaptureRecords() {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get(null, (all) => {
        if (chrome.runtime.lastError) return resolve([]);
        const records = [];
        for (const [k, v] of Object.entries(all || {})) {
          if (k.startsWith(CAPTURE_KEY_PREFIX) && v) records.push(v);
        }
        resolve(records);
      });
    } catch {
      resolve([]);
    }
  });
}

// ─── Current-tab sync state (survives content-script reloads) ─────────
// The sync orchestrator drives the user's current tab through each
// missing conv_id. State lives here in the service worker because
// content-script context is destroyed on each navigation. The pill
// re-queries us for state when it re-injects via get_current_tab_sync_state.
const CURRENT_TAB_SYNC_STATE = {
  active: false,
  provider: null,
  total: 0,
  landed: 0,
  currentIndex: 0,
  tabId: null,
  originalUrl: null,
  canceled: false,
  finishedAt: 0,
};

const SYNC_NAV_TIMEOUT_MS = 12_000;
const SYNC_CAPTURE_POLL_MS = 500;

function providerThreadUrl(provider, conv_id) {
  if (provider === "claude") return `https://claude.ai/chat/${conv_id}`;
  if (provider === "chatgpt") return `https://chatgpt.com/c/${conv_id}`;
  if (provider === "gemini") return `https://gemini.google.com/app/${conv_id}`;
  return null;
}

function querySyncStatus(provider) {
  return new Promise((resolve) => {
    const payload = { kind: "query", query_kind: "sync_status", provider };
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, payload, (resp) => {
        if (chrome.runtime.lastError) return resolve(null);
        resolve(resp || null);
      });
    } catch {
      resolve(null);
    }
  });
}

function pollForCapture(provider, conv_id, deadline) {
  return new Promise((resolve) => {
    (async function poll() {
      if (Date.now() >= deadline || CURRENT_TAB_SYNC_STATE.canceled) {
        return resolve(false);
      }
      const status = await querySyncStatus(provider);
      const stillMissing = status && status.ok && Array.isArray(status.missing_ids)
        ? status.missing_ids.includes(conv_id)
        : true;
      if (!stillMissing) return resolve(true);
      setTimeout(poll, SYNC_CAPTURE_POLL_MS);
    })();
  });
}

async function runCurrentTabSync({ tabId, originalUrl, provider, missing_ids }) {
  Object.assign(CURRENT_TAB_SYNC_STATE, {
    active: true, provider, total: missing_ids.length, landed: 0,
    currentIndex: 0, tabId, originalUrl, canceled: false, finishedAt: 0,
  });

  for (let i = 0; i < missing_ids.length; i++) {
    if (CURRENT_TAB_SYNC_STATE.canceled) break;
    const conv_id = missing_ids[i];
    CURRENT_TAB_SYNC_STATE.currentIndex = i;
    const url = providerThreadUrl(provider, conv_id);
    if (!url) continue;
    try {
      await chrome.tabs.update(tabId, { url });
    } catch {
      break;  // tab probably closed
    }
    const ok = await pollForCapture(
      provider, conv_id, Date.now() + SYNC_NAV_TIMEOUT_MS,
    );
    if (ok) CURRENT_TAB_SYNC_STATE.landed += 1;
  }

  // Restore the user's original URL
  try {
    if (!CURRENT_TAB_SYNC_STATE.canceled) {
      await chrome.tabs.update(tabId, { url: originalUrl });
    }
  } catch { /* tab closed */ }

  CURRENT_TAB_SYNC_STATE.active = false;
  CURRENT_TAB_SYNC_STATE.finishedAt = Date.now();
}

function ensurePort() {
  if (port) return port;
  try {
    port = chrome.runtime.connectNative(NATIVE_HOST);
    port.onMessage.addListener((msg) => {
      console.log("[trinity-bg] host ack", msg);
    });
    port.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError;
      if (err) {
        console.warn("[trinity-bg] host disconnected:", err.message);
      }
      port = null;
    });
  } catch (e) {
    console.warn("[trinity-bg] connectNative failed", e);
    port = null;
  }
  return port;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // ─── v1.6 capture flow (existing) ─────────────────────────────
  // Content scripts on claude.ai/chatgpt.com/gemini.google.com send
  // {type: "captured", payload: ...} when they observe a conversation.
  if (message?.type === "captured") {
    // Standalone mirror FIRST: always store locally so the extension owns the
    // user's transcripts even with no CLI/host (the "download your transcripts"
    // win). Dedup'd by conv_id, so re-capturing a thread overwrites.
    storeCaptureLocally(message.payload);
    const p = ensurePort();
    if (!p) {
      // No host — fine now; the local mirror has it (standalone mode).
      sendResponse({ ok: true, stored: "local" });
      return false;
    }
    try {
      p.postMessage({
        kind: "captured",
        payload: message.payload,
        origin_tab_url: sender?.tab?.url,
        received_at: new Date().toISOString(),
      });
      sendResponse({ ok: true, stored: "host+local" });
    } catch (e) {
      console.warn("[trinity-bg] postMessage to host failed", e);
      sendResponse({ ok: false, reason: String(e), stored: "local" });
    }
    return false;
  }

  // ─── Standalone capture stats + bundle (for the side-panel standalone view) ──
  // The view reads stats to render "N transcripts captured" and pulls the bundle
  // to build the download in a PAGE context (service workers can't createObjectURL).
  if (message?.type === "get_capture_stats") {
    readCaptureRecords().then((records) => {
      sendResponse({ ok: true, ...extensionCaptureStats(records) });
    });
    return true;  // async sendResponse
  }
  if (message?.type === "get_transcript_bundle") {
    readCaptureRecords().then((records) => {
      sendResponse({ ok: true, ...buildTranscriptBundle(records) });
    });
    return true;  // async sendResponse
  }
  // Onboarding tip: the side-panel shell passes live state; we fold in the
  // persisted seen-set and return the next eligible rung (reuses the tested
  // nextExtensionTip ladder — no duplicated logic in the page).
  if (message?.type === "next_tip") {
    chrome.storage.local.get(["tips_seen"], (res) => {
      const seen = res && Array.isArray(res.tips_seen) ? res.tips_seen : [];
      sendResponse({ ok: true, tip: nextExtensionTip({ ...(message.state || {}), seen }) });
    });
    return true;
  }
  if (message?.type === "dismiss_tip" && message.key) {
    chrome.storage.local.get(["tips_seen"], (res) => {
      const seen = res && Array.isArray(res.tips_seen) ? res.tips_seen : [];
      if (!seen.includes(message.key)) seen.push(message.key);
      chrome.storage.local.set({ tips_seen: seen }, () => sendResponse({ ok: true }));
    });
    return true;
  }

  // ─── Current-tab sync orchestrator (used by sync pill) ──────────────
  // The pill sends start_current_tab_sync; this handler takes over the
  // user's current tab, navigates it through each missing conv_id,
  // polls capture-host for each capture to land, then restores the
  // original URL. State lives here (the service worker survives navi-
  // gations; the pill's content-script context is destroyed on each
  // page load and re-queries us for state when it re-injects).
  //
  // Why current-tab instead of background tabs: user sees the sync
  // happen visually (informative + trustworthy) and there's no tab-bar
  // thrashing. Trade-off: user's tab is "borrowed" during sync. The
  // pill renders a "Syncing N/M — cancel" overlay during the run.
  if (message?.type === "start_current_tab_sync") {
    const senderTabId = sender?.tab?.id;
    const originalUrl = sender?.tab?.url;
    if (!senderTabId || !originalUrl) {
      sendResponse({ ok: false, error: "no-tab-context" });
      return false;
    }
    if (CURRENT_TAB_SYNC_STATE.active) {
      sendResponse({ ok: false, error: "sync-already-running" });
      return false;
    }
    const { provider, missing_ids } = message;
    if (!Array.isArray(missing_ids) || !missing_ids.length) {
      sendResponse({ ok: false, error: "no-missing-ids" });
      return false;
    }
    runCurrentTabSync({
      tabId: senderTabId,
      originalUrl,
      provider,
      missing_ids,
    });
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === "get_current_tab_sync_state") {
    sendResponse({
      ok: true,
      active: CURRENT_TAB_SYNC_STATE.active,
      provider: CURRENT_TAB_SYNC_STATE.provider,
      total: CURRENT_TAB_SYNC_STATE.total,
      landed: CURRENT_TAB_SYNC_STATE.landed,
      current_index: CURRENT_TAB_SYNC_STATE.currentIndex,
      finished_at: CURRENT_TAB_SYNC_STATE.finishedAt,
      // Expose the user-cancel flag so the pill's terminal feedback can tell a
      // DELIBERATE cancellation apart from a sync FAILURE. Without this, a finished
      // canceled sync looks byte-identical to a failed/partial one (active:false,
      // finished_at set, landed<total) and renderJustFinished mislabels it as
      // "Sync failed" (landed=0) or a bare "Synced N/M" partial.
      canceled: CURRENT_TAB_SYNC_STATE.canceled,
    });
    return false;
  }
  if (message?.type === "cancel_current_tab_sync") {
    CURRENT_TAB_SYNC_STATE.canceled = true;
    sendResponse({ ok: true });
    return false;
  }

  // ─── Background-tab sync (kept for callers that prefer it) ───────────
  // Gemini blocks iframe-based sync (the bundle detects iframe context
  // and doesn't fire its canonical hNvQHb fetch). Background tabs
  // bypass this: real top-level navigation that triggers the full
  // page-load flow including all auth-injected fetches, while
  // active:false keeps them out of the user's focus.
  if (message?.type === "open_sync_tab") {
    try {
      chrome.tabs.create({ url: message.url, active: false }, (tab) => {
        if (chrome.runtime.lastError) {
          sendResponse({ ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        sendResponse({ ok: true, tabId: tab?.id });
      });
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
    return true;
  }
  if (message?.type === "close_sync_tab") {
    try {
      chrome.tabs.remove(message.tabId, () => {
        sendResponse({ ok: !chrome.runtime.lastError });
      });
    } catch {
      sendResponse({ ok: false });
    }
    return true;
  }

  // ─── Read-only query path (new — used by the in-provider sync pill) ──
  // Content scripts ask the host for cheap read-only info like
  // "how many threads in the sidebar aren't captured locally?" Same
  // sendNativeMessage one-shot pattern as actions; the host's
  // QUERY_HANDLERS dispatches on `query_kind`.
  if (message?.type === "query") {
    const { type: _ignore, ...hostPayload } = message;
    hostPayload.kind = "query";  // host gates by kind="query" + query_kind
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, hostPayload, (response) => {
        if (chrome.runtime.lastError) {
          sendResponse({ ok: false, error: "native-host-unavailable",
                         detail: chrome.runtime.lastError.message });
          return;
        }
        sendResponse(response);
      });
    } catch (e) {
      sendResponse({ ok: false, error: "send-failed", detail: String(e) });
    }
    return true;  // signal async sendResponse
  }

  // ─── Phase 1+3 action-dispatch (new — launchpad bridge) ───────
  // Popup/launchpad sends {type: "action", kind: "launch-council",
  // task: "..."} to invoke a CLI command via Native Messaging. The
  // host's ACTION_ALLOWLIST gates which kinds are runnable; this
  // service worker is a transparent forwarder.
  if (message?.type === "action") {
    const { type: _ignore, ...hostPayload } = message;
    // One-shot request/response (sendNativeMessage spawns a fresh
    // host process per call, exits when message returns). Cleaner
    // for actions than a persistent port — action != streaming.
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, hostPayload, (response) => {
        if (chrome.runtime.lastError) {
          sendResponse({
            ok: false,
            error: "native-host-unavailable",
            detail: chrome.runtime.lastError.message,
            hint: "Run `trinity-local install-extension --extension-id <ID>` to register the Native Messaging manifest.",
          });
          return;
        }
        sendResponse(response);
      });
    } catch (e) {
      sendResponse({ ok: false, error: "send-failed", detail: String(e) });
    }
    return true;  // signal async sendResponse
  }

  return false;
});

// ─── Phase 4: external messaging from the launchpad ───────
// The launchpad calls chrome.runtime.sendMessage(TRINITY_EXTENSION_ID, ...)
// directly. That path uses `onMessageExternal`, NOT `onMessage` — internal
// popups + content scripts use onMessage, externally-connectable pages use
// the External variant. They are NOT interchangeable.
//
// Security gates (codex's Phase 4 verdict, council_fb374b01311885cc):
//   1. sender.url must be a recognized launchpad origin (file:// or
//      http://localhost / http://127.0.0.1 — matching the manifest's
//      externally_connectable allowlist).
//   2. message.type must be in {trinity-ping, action}
//   3. action.kind must clear capture_host's ACTION_ALLOWLIST anyway
//      (defense in depth — the host is the final enforcement)
// Phase 8 hardening (council_bf1ab3f4dd70f75e, codex verdict): the prior
// `url.includes("/.trinity/portal_pages/launchpad.html")` substring check
// was spoofable by any local file matching the substring, e.g.
// `~/Downloads/.trinity/portal_pages/launchpad.html`. Tighten by requiring
// the path to END with the launchpad path. Chrome populates `sender.url`
// itself (cannot be forged in the message payload), so a strict
// origin-and-suffix match closes the spoof window without needing a
// per-install token. 2026-05-26: HTTP-localhost path added once
// `trinity-local serve` became the recommended dev-mode entry to dodge
// the file:// unique-origin restrictions Chrome enforces on iframes.
// Trinity-served local pages allowed to dispatch actions. The launchpad is one;
// the live COUNCIL page is the other — Refine / Continue / Auto-chain / Stop
// council all dispatch `council-iterate` from review_pages/live_council.html,
// NOT the launchpad. Found 2026-05-31 (founder report): the council page was
// rejected as a sender ("rejected-sender"), so every in-council refine failed
// with a misleading "is the extension installed?" even though it was. Same
// suffix-and-origin hardening (Phase 8) applies to each — only the exact
// Trinity page path under file://.trinity or http://localhost|127.0.0.1.
const ALLOWED_URL_SUFFIXES = [
  "/portal_pages/launchpad.html",
  "/review_pages/live_council.html",
];

function isLaunchpadSender(sender) {
  const url = sender?.url || "";
  // Strip any query/hash so a crafted ?foo=… can't tail the path.
  const cleaned = url.split("?")[0].split("#")[0];
  const suffix = ALLOWED_URL_SUFFIXES.find((s) => cleaned.endsWith(s));
  if (!suffix) return false;
  if (url.startsWith("file://") &&
      cleaned.endsWith("/.trinity" + suffix)) {
    // Pre-existing path: a file:// Trinity page must live under .trinity/.
    return true;
  }
  // HTTP path: `trinity-local serve` binds 127.0.0.1:8765 on the user's
  // machine. The same machine can hit it via either http://localhost:<port>
  // or http://127.0.0.1:<port>; the manifest's externally_connectable list
  // mirrors both. Anything outside those origins won't even reach this
  // function — Chrome gates the message at the manifest level — but we
  // re-check here as defense in depth.
  const isLocalhostHttp =
    cleaned.startsWith("http://localhost/") ||
    cleaned.startsWith("http://localhost:") ||
    cleaned.startsWith("http://127.0.0.1/") ||
    cleaned.startsWith("http://127.0.0.1:");
  return isLocalhostHttp;
}

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (!isLaunchpadSender(sender)) {
    sendResponse({ ok: false, error: "rejected-sender",
                   detail: "external messages accepted only from the launchpad (file:// or http://localhost)" });
    return false;
  }
  const messageType = message?.type;

  if (messageType === "trinity-ping") {
    sendResponse({
      ok: true,
      type: "trinity-pong",
      extensionVersion: chrome.runtime.getManifest().version,
    });
    return false;
  }

  if (messageType === "action") {
    const { type: _ignore, ...hostPayload } = message;
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, hostPayload, (response) => {
        if (chrome.runtime.lastError) {
          sendResponse({
            ok: false,
            error: "native-host-unavailable",
            detail: chrome.runtime.lastError.message,
            hint: "Run `trinity-local install-extension --extension-id <ID>` to register the Native Messaging manifest.",
          });
          return;
        }
        sendResponse(response);
      });
    } catch (e) {
      sendResponse({ ok: false, error: "send-failed", detail: String(e) });
    }
    return true;
  }

  sendResponse({ ok: false, error: "unknown-message-type", detail: String(messageType) });
  return false;
});

console.log("[trinity-bg] service worker started (v0.2 — capture + actions + external)");

// Clicking the toolbar icon opens the side panel (the launchpad/council UI, like
// Claude's sidebar) instead of a popup — there's no default_popup in the manifest,
// so this behavior is what the action click does. setPanelBehavior is idempotent;
// set it on startup and on install so a reload picks it up.
if (typeof chrome !== "undefined" && chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  chrome.runtime.onInstalled.addListener(() => {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  });
}

// For node-based unit tests (no-op in the MV3 service-worker context, where
// `module` is undefined). Exposes the pure provider→thread-URL builder so the
// current-tab-sync navigation target — which must match each provider's real
// conversation URL AND stay consistent with the content-script /app/ scrape for
// gemini — is testable. Same module.exports guard the other extension files use.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    providerThreadUrl, NATIVE_HOST, isLaunchpadSender, ALLOWED_URL_SUFFIXES,
    captureStorageKey, extensionCaptureStats, buildTranscriptBundle, nextExtensionTip,
  };
}
