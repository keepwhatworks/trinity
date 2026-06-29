// Parent side of the sandbox RPC bridge (runs in the CSP-safe shell page).
//
// The launchpad/council UI runs inside a SANDBOXED iframe (manifest sandbox.pages)
// so petite-vue's eval is allowed — but a sandboxed page has an opaque origin and
// CANNOT touch chrome.* APIs. So the iframe postMessages each of its
// chrome.runtime.sendMessage calls up to here; we replay them on the REAL
// chrome.runtime and post the response back.
//
// Replays use the INTERNAL form (no extensionId), which reaches background.js's
// onMessage — that handler already routes type:'query' (launchpad_data, council
// status) and type:'action' (dispatch) to the native host. We never use the
// external/onMessageExternal path, so there's no isLaunchpadSender gate to clear:
// the messages originate inside the extension. The generic shape (forward the
// message object, return the response) keeps this a thin channel, not a per-feature
// surface (the council's "keep the bridge ~150 lines" guardrail).
const frame = document.getElementById("app");

window.addEventListener("message", async (event) => {
  // Only handle bridge requests coming FROM our sandboxed iframe.
  if (event.source !== frame.contentWindow) return;
  const msg = event.data;
  if (!msg || msg.__trinityBridge !== true || msg.dir !== "req") return;

  let response = null;
  let error = null;
  try {
    response = await chrome.runtime.sendMessage(msg.message);
  } catch (e) {
    error = String((e && e.message) || e);
  }
  // The opaque sandbox origin means we post with "*" (the iframe verifies the
  // shape, and only same-frame requests are answered).
  frame.contentWindow.postMessage(
    { __trinityBridge: true, dir: "res", id: msg.id, response, error },
    "*",
  );
});

// In-panel navigation broker. A sandboxed page (opaque origin) can't navigate
// ITSELF to another extension page — Chrome blocks it ("This page has been
// blocked"). So the sandbox postMessages the target URL up here and we swap the
// iframe src; this page IS the extension origin, so it can load its own sandbox
// pages (exactly how launchpad.html loaded at start). SECURITY: the sandbox runs
// with a relaxed CSP and may render attacker-influenced corpus content, so we
// NEVER navigate to an arbitrary URL — only ever to our OWN two sandbox siblings
// (launchpad.html / live_council.html), keeping just the basename + query.
const NAV_RX = /(?:^|\/)(launchpad\.html|live_council\.html)(\?[^#]*)?(?:#.*)?$/;

// Swapping frame.src RELOADS the sandbox page VISIBLY, and the reloaded app waits
// on an async host fetch before petite-vue mounts — so without a cover the panel
// flashes the raw, un-mounted template (literal {{ }} + every v-if section
// expanded) for the seconds in between (founder-caught navigating back to the
// launchpad). Re-show the shell's loading spinner across the swap and reveal the
// page only when the sandbox signals it mounted (v-cloak removed). A fallback
// timer guarantees the spinner can NEVER get stuck (the #6 lesson).
let navLoaderTimer = null;
function showNavLoader() {
  const l = document.getElementById("loading");
  const app = document.getElementById("app");
  if (l) l.hidden = false;
  if (app) app.hidden = true;
  clearTimeout(navLoaderTimer);
  navLoaderTimer = setTimeout(hideNavLoader, 8000);
}
function hideNavLoader() {
  clearTimeout(navLoaderTimer);
  navLoaderTimer = null;
  const l = document.getElementById("loading");
  const app = document.getElementById("app");
  if (l) l.hidden = true;
  if (app) app.hidden = false;
}

window.addEventListener("message", (event) => {
  if (event.source !== frame.contentWindow) return;
  const msg = event.data;
  if (!msg) return;
  // Reveal once the freshly-navigated page mounts. Gate on an ACTIVE nav
  // (navLoaderTimer set) so the initial-load mount signal can't override the
  // shell's host-detection — which may have chosen the standalone view, where
  // #app must stay hidden.
  if (msg.__trinityMounted === true) {
    if (navLoaderTimer) hideNavLoader();
    return;
  }
  if (msg.__trinityNav !== true || typeof msg.url !== "string") return;
  const m = msg.url.match(NAV_RX);
  if (!m) return; // not one of our sandbox pages → ignore
  showNavLoader();
  frame.src = "sandbox/" + m[1] + (m[2] || "");
});
