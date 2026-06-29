// Trinity Local — content-script.js (ISOLATED world)
//
// Bridge between the MAIN-world page-hook.js (which can intercept
// the page's fetch calls) and the extension's background service
// worker (which holds the chrome.runtime.connectNative connection
// to the local capture host).
//
// Why two scripts: MV3's ISOLATED-world content scripts cannot
// monkey-patch window.fetch (they get their own window object).
// The MAIN-world script can patch fetch but cannot use chrome.* APIs.
// So we relay: page-hook emits via window.postMessage → here →
// chrome.runtime.sendMessage → background.

// Gemini sidebar DOM scrape — gemini.google.com renders the recent-
// conversations sidebar directly into the DOM (no batchexecute RPC
// returns the list; we verified this 2026-05-23 — 22 distinct
// batchexecute rpcids inspected, none carry conv_ids). claude.ai and
// chatgpt.com use the network-intercept path in page-hook.js
// (kind="sidebar_list"); gemini gets DOM-scrape as the equivalent.
// Same output payload shape so capture-host writes _sidebar.json
// uniformly across providers.
// Hoisted to top level (out of the gemini hostname block below) so node tests
// can require it via the module.exports guard at the end. Defined on every
// provider page but only CALLED on gemini — a bare function declaration that
// reads `document` lazily, so it's inert elsewhere.
//
// The conv_id regex is deliberately NARROW (hex, 8+) — UNLIKE the gemini
// ADAPTER's broad `[A-Za-z0-9_-]{6,}`. The adapter parses a KNOWN-conversation
// page URL (always /app/<conv-id>), but this scrapes ARBITRARY DOM /app/
// anchors, which include nav links (/app/settings, /app/new). Hex-only rejects
// those nav links while matching real gemini conv_ids (which are hex-16, e.g.
// 04f4e5eaa1a6a530 — verified 173/173 real sidebar items matched). Do NOT
// broaden this to the adapter's char class — it'd capture /app/settings as a
// phantom conversation and the sync-pill would over-count.
function readGeminiSidebar() {
  const seen = new Set();
  const items = [];
  for (const a of document.querySelectorAll('a[href*="/app/"]')) {
    const m = (a.getAttribute("href") || "").match(/\/app\/([0-9a-f]{8,})/);
    if (!m) continue;
    const conv_id = m[1];
    if (seen.has(conv_id)) continue;
    seen.add(conv_id);
    const title = (a.textContent || a.getAttribute("title") || "").trim();
    if (!title) continue;
    items.push({ conv_id, title });
  }
  return items;
}

if (location.hostname === "gemini.google.com") {
  function emitSidebar(items) {
    if (!chrome?.runtime?.id) return;
    if (!items || items.length === 0) return;
    try {
      chrome.runtime.sendMessage({
        type: "captured",
        payload: {
          provider: "gemini",
          kind: "sidebar_list",
          url: location.href,
          method: "DOM",
          sidebar: { items, source: "dom_scrape" },
          captured_at: new Date().toISOString(),
        },
      }).catch(() => {});
    } catch {}
  }

  // Poll for sidebar render — React mounts asynchronously after the
  // page is interactive. Check every 1s for up to 10s; emit when
  // we have a non-empty list AND it differs from the last snapshot.
  let lastSnapshot = "";
  let polls = 0;
  const sidebarTimer = setInterval(() => {
    polls++;
    const items = readGeminiSidebar();
    const snapshot = JSON.stringify(items);
    if (items.length > 0 && snapshot !== lastSnapshot) {
      lastSnapshot = snapshot;
      emitSidebar(items);
    }
    if (polls >= 10) clearInterval(sidebarTimer);
  }, 1000);
}

window.addEventListener("message", (event) => {
  // Only accept messages from the same page and from our own hook.
  if (event.source !== window) return;
  if (!event.data || event.data.source !== "trinity-hook") return;

  const payload = event.data.payload;
  if (!payload) return;

  // Guard against "Extension context invalidated" — when the user
  // reloads the extension in chrome://extensions, every previously-
  // injected content-script keeps running in already-open tabs but
  // loses access to chrome.* APIs. Reading chrome.runtime.id is the
  // canonical check: it returns undefined once the context is gone.
  // Without this guard, every postMessage from page-hook throws and
  // spams the page console (caught 2026-05-23 on gemini.google.com
  // after reload).
  if (!chrome?.runtime?.id) return;

  try {
    chrome.runtime.sendMessage({ type: "captured", payload }).catch((err) => {
      // Service worker may be asleep; chrome wakes it on sendMessage.
      // Genuine errors land here — log but don't break the page.
      console.warn("[trinity-content] sendMessage failed", err);
    });
  } catch (e) {
    // sendMessage can throw synchronously on context invalidation
    // (vs returning a rejected promise) depending on Chrome version.
    // Both paths reach here; we swallow to keep the page clean.
  }
});

// For node-based unit tests (no-op in the injected content-script context where
// `module` is undefined). Exposes the gemini sidebar DOM-scrape so its conv_id
// extraction + nav-link rejection + dedup is testable without a live browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { readGeminiSidebar };
}
