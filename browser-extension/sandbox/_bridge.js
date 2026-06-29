// Sandbox side of the RPC bridge (runs INSIDE the sandboxed launchpad/council
// iframe, where chrome.* is unavailable). Shims chrome.runtime.sendMessage so the
// UI's existing data-fetch + dispatch code works unchanged: each call is forwarded
// to the parent shell (sidepanel-bridge.js) over postMessage, which replays it on
// the real chrome.runtime and posts the response back.
//
// MUST load BEFORE the app + vendor scripts so window.chrome.runtime exists when
// the launchpad's fetch bootstrap and __TRINITY_DISPATCH__ first read it.

// A sandboxed page without allow-same-origin throws SecurityError on ANY
// localStorage / sessionStorage access — and the launchpad uses both (the dispatch
// state cache + theme/settings). We can't add allow-same-origin (that re-imposes
// the extension CSP and kills the eval the sandbox exists for), so swap in an
// in-memory store when the real one is inaccessible. The UI just doesn't persist
// across reloads, which is fine for a side panel.
(function () {
  function mkStore() {
    const m = Object.create(null);
    return {
      getItem: (k) => (k in m ? m[k] : null),
      setItem: (k, v) => { m[k] = String(v); },
      removeItem: (k) => { delete m[k]; },
      clear: () => { for (const k in m) delete m[k]; },
      key: (i) => Object.keys(m)[i] || null,
      get length() { return Object.keys(m).length; },
    };
  }
  for (const name of ["localStorage", "sessionStorage"]) {
    let broken = false;
    try { void window[name].length; } catch (_) { broken = true; }
    if (broken) {
      try { Object.defineProperty(window, name, { value: mkStore(), configurable: true }); } catch (_) {}
    }
  }
})();

// Tell the page's data loaders (loadStatusScript / loadOutcomeScript /
// loadThreadScript / loadActiveCouncilScript) to fetch from the host over the
// bridge instead of injecting ~/.trinity .js files (which a sandboxed opaque
// origin can't read).
window.__TRINITY_HOST_FETCH__ = true;

(function () {
  let seq = 0;
  const pending = {};
  let lastErr = null;

  window.addEventListener("message", (event) => {
    // Responses come from the parent (window.parent), shape-checked.
    if (event.source !== window.parent) return;
    const d = event.data;
    if (!d || d.__trinityBridge !== true || d.dir !== "res") return;
    const cb = pending[d.id];
    if (!cb) return;
    delete pending[d.id];
    cb(d.error, d.response);
  });

  function send(message) {
    return new Promise((resolve) => {
      const id = ++seq;
      pending[id] = (err, resp) => {
        // chrome.runtime.lastError is only meant to be read inside the callback;
        // set it just before invoking, mirroring Chrome's semantics.
        lastErr = err ? { message: err } : null;
        resolve(resp);
      };
      window.parent.postMessage(
        { __trinityBridge: true, dir: "req", id, message },
        "*",
      );
    });
  }

  const chrome = (window.chrome = window.chrome || {});
  const runtime = (chrome.runtime = chrome.runtime || {});
  Object.defineProperty(runtime, "lastError", { get: () => lastErr });

  // Supports every call shape the UI uses:
  //   sendMessage(message)                      → query (returns a Promise)
  //   sendMessage(extId, message[, callback])   → dispatch (callback form)
  runtime.sendMessage = function (...args) {
    const callback = typeof args[args.length - 1] === "function" ? args.pop() : null;
    const message = typeof args[0] === "string" ? args[1] : args[0];
    const p = send(message);
    if (callback) {
      p.then((r) => callback(r));
      return undefined;
    }
    return p;
  };
})();
