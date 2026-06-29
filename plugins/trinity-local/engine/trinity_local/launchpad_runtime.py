from __future__ import annotations


def launchpad_runtime_js() -> str:
    """Shared JS runtime block injected into Launchpad and live council pages."""
    return """
window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};

// Side-panel (sandboxed chrome-extension) pages can't read the ~/.trinity .js
// files the file:// council page injects via <script> — the sandbox has an opaque
// origin. When __TRINITY_HOST_FETCH__ is set (by sandbox/_bridge.js), the data
// loaders fetch the SAME objects from the capture host over the bridged
// chrome.runtime instead. The host returns the verbatim JSON from those same .js
// files, so the two transports are byte-identical.
function __trinityHostFetch() {
  return !!window.__TRINITY_HOST_FETCH__ &&
    !!(window.chrome && chrome.runtime && chrome.runtime.sendMessage);
}
// A council status / outcome / manifest payload is ALWAYS a plain JSON object on
// the wire (json.dumps of a dict). A wrong-TYPE payload — a string/number/array,
// from a truncated, concurrently-written, or old-schema sidecar — must degrade to
// `null` (the "no usable payload" signal every consumer already handles: the status
// pollers fall into their MAX_MISSING_POLLS give-up, the outcome consumer into its
// "Could not load council outcome" branch). Without this, a string status payload
// is TRUTHY but matches no terminal branch — it RESETS the give-up counter on every
// poll, so the spinner spins FOREVER (no pageerror, no honest state); and a string
// outcome flows into `(outcome.member_results || []).map` → an uncaught TypeError
// that strands the render blank. Coercing non-object roots to null at the loader
// chokepoint closes the class across ALL three pollers + every JSONP consumer.
function __trinityCoerceObj(value) {
  return (value && typeof value === 'object' && !Array.isArray(value)) ? value : null;
}
function __trinityHostQuery(query_kind, params, onComplete) {
  try {
    const p = chrome.runtime.sendMessage(
      Object.assign({ type: 'query', query_kind: query_kind }, params || {}));
    Promise.resolve(p)
      .then((r) => onComplete((r && r.ok) ? __trinityCoerceObj(r.result) : null))
      .catch(() => onComplete(null));
  } catch (e) {
    onComplete(null);
  }
}

window.addEventListener('pageshow', (event) => {
  const navEntry = typeof performance.getEntriesByType === 'function'
    ? performance.getEntriesByType('navigation')[0]
    : null;
  if (event.persisted || navEntry?.type === 'back_forward') {
    __trinityReload();
  }
});

// buildShortcutUrl: retired no-op as of 2026-05-18 (W — Pass B JS).
// The macOS Shortcut dispatcher was killed in Pass A (commit 53db635);
// Tier-2 branch in __TRINITY_DISPATCH__.dispatch is gone (Chrome
// extension is the only live dispatch path). buildShortcutUrl()
// survives as a no-op that returns '' so that callsites in
// launchpad_template.py and council_review.py keep parsing — they
// pass the empty string into dispatch() which now ignores it.
function buildShortcutUrl(payload) { return ''; }

// --- Side-panel navigation broker -------------------------------------------
// A sandboxed extension page (manifest sandbox.pages) has an OPAQUE origin and
// CANNOT navigate ITSELF to another chrome-extension page — Chrome blocks both a
// link click AND a window.location assignment ("This page has been blocked by
// Chrome", founder-caught clicking a rail-council link). So in the sandbox we hand
// every in-extension navigation UP to the shell (sidepanel.html), which owns the
// iframe and CAN swap its src to a sibling sandbox page (it's how the launchpad
// loaded in the first place). On file:// / localhost __TRINITY_HOST_FETCH__ is
// false → a normal same-document navigation, unchanged.
function __trinityInSandbox() {
  return !!window.__TRINITY_HOST_FETCH__ && !!window.parent && window.parent !== window;
}
function __trinityNavigate(url) {
  if (!url) { return; }
  if (__trinityInSandbox()) {
    try {
      window.parent.postMessage({ __trinityNav: true, url: String(url) }, '*');
      return;
    } catch (e) { /* fall through to a direct nav */ }
  }
  window.location.assign(url);
}
// Reload THIS page. In the sandbox a bare window.location.reload() is the SAME
// opaque-origin self-navigation Chrome blocks ("This page has been blocked by
// Chrome") — so a settings toggle that calls scheduleLaunchpadReload() bricked the
// WHOLE side panel to a chrome-error page (driven 2026-06-19: telemetry toggle →
// blank "blocked by Chromium" panel). Broker the reload UP to the shell exactly
// like a cross-page nav: re-navigate to the CURRENT sandbox page (basename + the
// SAME query, so a token-bearing live_council reload keeps its token). The shell's
// NAV_RX only accepts our own launchpad.html / live_council.html siblings, and the
// frame.src swap goes through the spinner-covered reveal path (no raw-template
// flash). On file:// / localhost __trinityInSandbox() is false → a normal reload.
function __trinityReload() {
  if (__trinityInSandbox()) {
    try {
      var name = (window.location.pathname || '').split('/').pop() || 'launchpad.html';
      window.parent.postMessage(
        { __trinityNav: true, url: './' + name + (window.location.search || '') }, '*');
      return;
    } catch (e) { /* fall through to a direct reload */ }
  }
  window.location.reload();
}
// Flip the launchpad between its home and /stats views WITHOUT a navigation. Both
// card sets are ALWAYS in the DOM (the page "contains every card"); the root's
// lp-view-{home,stats} class is the only switch. The side panel has no separate
// sandbox/stats.html, so a real nav to ./stats.html is "blocked by Chrome"
// (founder-caught, Image #10) — toggling the class in place is the file://-safe
// equivalent. renderChart() no-ops on home and lazy-pulls Chart.js the first time
// stats actually renders, so calling it here is cheap + correct.
function setLaunchpadView(view) {
  var root = document.getElementById('launchpad-app');
  if (!root) { return false; }
  var stats = view === 'stats';
  root.classList.remove('lp-view-home', 'lp-view-stats');
  root.classList.add(stats ? 'lp-view-stats' : 'lp-view-home');
  try { window.scrollTo(0, 0); } catch (e) {}
  if (stats && typeof renderChart === 'function') {
    try { renderChart(); } catch (e) {}
  }
  return true;
}
// Memory-viewer deep links (../portal_pages/memory.html?file=…) point at a page
// that does NOT exist in the sandbox, so a self-nav there is the same "blocked by
// Chrome" dead-end. The deep-link surface lives on the FULL file:// launchpad, so
// the graceful escape is to dispatch open-launchpad (opens the dashboard in a
// browser) rather than strand the user on a chrome-error page.
// Bridge a runtime-level dispatch RESULT back into the Vue launchpad app so it can
// raise its dispatch-failure banner. __trinityOpenFullLaunchpad lives OUTSIDE the
// Vue instance (it's a module function called from the sandbox click interceptor),
// so it can't touch handleDispatchResult directly — without this, a FAILED
// open-launchpad (no native host) was swallowed by an empty onResult and the user
// got ZERO feedback: the memory-viewer chip on /stats clicked into nothing (no nav,
// no banner). Emit the result as an event the app listens for so the failure
// surfaces the SAME "host not registered" banner every in-app dispatch shows.
function __trinityEmitDispatchResult(result) {
  try {
    window.dispatchEvent(new CustomEvent('trinity:dispatch-result', { detail: result }));
  } catch (e) {}
}
// IMMEDIATE acknowledgment for a portal deep-link bounce. open-launchpad opens the
// FULL dashboard in a SEPARATE BROWSER WINDOW (the memory/topology/lens views live
// there, not in the panel sandbox), and on SUCCESS handleDispatchResult does
// nothing (it only fires on FAILURE) — so clicking "→ topology" / "picks.json" /
// a lens chip in the side panel changed NOTHING visible in the panel: it read as a
// dead link / no-op (driven 2026-06-21 — the open-launchpad fired, the panel was
// silent). A deferred / out-of-panel action MUST acknowledge the click immediately,
// so emit a "portal-opening" event the app raises a transient notice from BEFORE the
// dispatch resolves. `label` names what the user clicked so the notice is specific.
function __trinityEmitPortalOpen(label) {
  try {
    window.dispatchEvent(new CustomEvent('trinity:portal-open', { detail: { label: label || '' } }));
  } catch (e) {}
}
// Derive a human label ("the topology view", "picks.json", "the lens") from the
// clicked memory-viewer href so the ack names the destination the user expected.
function __trinityPortalLabel(href) {
  try {
    var m = /[?&]file=([^&#]+)/.exec(href || '');
    var file = m ? decodeURIComponent(m[1]) : '';
    if (/[?&]basin=/.test(href || '') || file === 'topics.json') { return 'the topology view'; }
    if (file === 'lens.md') { return 'your lens'; }
    if (file === 'core.md') { return 'your core memory'; }
    if (file === 'vocabulary.md') { return 'your vocabulary'; }
    if (file === 'generators.md') { return 'your generators'; }
    if (file) { return file; }
  } catch (e) {}
  return 'the memory viewer';
}
function __trinityOpenFullLaunchpad(label) {
  // Acknowledge the click NOW, before the dispatch round-trips — the dashboard
  // opens in another window, so without this the panel sits silent.
  __trinityEmitPortalOpen(label);
  try {
    var d = window.__TRINITY_DISPATCH__;
    if (d && typeof d.dispatch === 'function') {
      d.dispatch({
        extensionAction: { kind: 'open-launchpad' },
        onResult: function (r) { __trinityEmitDispatchResult(r); },
      });
      return true;
    }
  } catch (e) {}
  // No dispatcher at all — synthesize the install-prompt result so the app still
  // tells the user WHY the dashboard didn't open (instead of a silent dead click).
  __trinityEmitDispatchResult({ tier: 'install-prompt', ok: false, reason: 'no-dispatcher' });
  return false;
}
// One sandbox click interceptor for EVERY in-extension destination, so a sandboxed
// page can never self-navigate (= "blocked by Chrome"). Capture phase + event
// delegation so dynamically-rendered links (the council rail, cheat-sheet rows)
// are covered too. External (http/mailto/#) links fall through untouched.
(function () {
  // Genuine cross-page nav the shell must broker by swapping the iframe src.
  var NAV_RX = /(?:^|\\/)(launchpad\\.html|live_council\\.html)(\\?[^#]*)?(?:#.*)?$/;
  var STATS_RX = /(?:^|\\/)stats\\.html(\\?[^#]*)?(?:#.*)?$/;
  var HOME_RX = /(?:^|\\/)launchpad\\.html(\\?[^#]*)?(?:#.*)?$/;
  var PORTAL_RX = /portal_pages\\/[A-Za-z0-9_.-]+\\.html/;
  document.addEventListener('click', function (ev) {
    if (!__trinityInSandbox()) { return; }
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) { return; }
    if (a.target && a.target !== '_self') { return; }
    var href = a.getAttribute('href') || '';
    // On the launchpad page, home<->stats are in-page view switches (NOT nav).
    // The same launchpad.html link on the COUNCIL page (no #launchpad-app) falls
    // through to the broker below as a genuine cross-page nav.
    var onLaunchpad = !!document.getElementById('launchpad-app');
    if (onLaunchpad && STATS_RX.test(href)) { ev.preventDefault(); setLaunchpadView('stats'); return; }
    if (onLaunchpad && HOME_RX.test(href)) { ev.preventDefault(); setLaunchpadView('home'); return; }
    if (PORTAL_RX.test(href)) { ev.preventDefault(); __trinityOpenFullLaunchpad(__trinityPortalLabel(href)); return; }
    if (!NAV_RX.test(href)) { return; }
    ev.preventDefault();
    __trinityNavigate(href);
  }, true);
})();

// Tell the shell the moment petite-vue has MOUNTED, so it can drop the nav
// spinner and reveal this page. The signal = v-cloak removed from the root
// [v-scope] element (petite-vue strips it on mount). Without this, a back-nav
// reload waits on the async host fetch before mounting and the shell would
// either flash the raw template or sit on a blank spinner (founder-caught:
// a multi-second raw-template flash navigating back to the launchpad).
(function () {
  if (!__trinityInSandbox()) { return; }
  var signal = function () {
    try { window.parent.postMessage({ __trinityMounted: true }, '*'); } catch (e) {}
  };
  var watch = function () {
    var root = document.querySelector('[v-scope]');
    if (!root) { signal(); return; }              // nothing to mount → reveal
    if (!root.hasAttribute('v-cloak')) { signal(); return; }  // already mounted
    var obs = new MutationObserver(function () {
      if (!root.hasAttribute('v-cloak')) { obs.disconnect(); signal(); }
    });
    obs.observe(root, { attributes: true, attributeFilter: ['v-cloak'] });
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watch);
  } else {
    watch();
  }
})();

function navigateToReviewPath(path, councilId) {
  if (!path) {
    return;
  }
  // Side panel: a sandboxed page can't self-navigate (see __trinityNavigate).
  // The only council page in the sandbox is the ./live_council.html sibling, so
  // collapse any review_path to it and let the shell swap the iframe src — never
  // ../review_pages/… (a blocked chrome-extension path).
  //
  // The review_path is an absolute ~/.trinity/review_pages/… filesystem path that
  // does NOT exist in the sandbox, so it CANNOT be passed through — but a bare
  // ./live_council.html lands TOKEN-LESS, with no status_token AND no council_id.
  // The live page's init() then has nothing to load: it falls to the
  // loadActiveCouncilScript fallback, which reads _active_council.js — a sidecar
  // ONLY written by the popup's `open-council-page` dispatch. On the
  // stay-on-the-launchpad auto-poll completion path (the user Launches, never
  // clicks "Open council page"), that sidecar was never written, so the host's
  // active_council query returns null and the live page renders BLANK — the
  // council completed but the user lands on an empty page and never sees the
  // verdict (driven 2026-06-18: "navigate-to-nowhere on the panel's own
  // auto-poll completion branch"). Carry the council_id through so the live page
  // hydrates the outcome via its existing ?council_id= init path (host-fetched
  // council_outcome) — the same identity finalize_council_run_state writes into
  // the completed status alongside review_path.
  if (__trinityInSandbox()) {
    __trinityNavigate(
      councilId
        ? `./live_council.html?council_id=${encodeURIComponent(councilId)}`
        : './live_council.html'
    );
    return;
  }
  if (/^(file|https?):\\/\\//.test(path)) {
    window.location.replace(path);
    return;
  }
  if (window.location.protocol === 'file:') {
    window.location.replace(`file://${path}`);
    return;
  }
  if (path.includes('/review_pages/')) {
    const parts = path.split('/review_pages/');
    window.location.replace(`../review_pages/${parts[parts.length - 1]}`);
    return;
  }
  window.location.replace(path);
}

function loadStatusScript(token, onComplete) {
  if (__trinityHostFetch()) {
    if (!token) { onComplete(null); return; }
    __trinityHostQuery('council_status', { status_token: token }, onComplete);
    return;
  }
  const base = (typeof pageData !== 'undefined' && pageData.statusScriptBaseUrl) || '';
  if (!base || !token) {
    onComplete(null);
    return;
  }
  delete window.__TRINITY_COUNCIL_STATUS__[token];
  const script = document.createElement('script');
  // file:// URLs don't honor query-string cache busters — browsers look for
  // a literal file named `foo.js?t=…` and 404. Only append the buster on
  // http(s):// so the local server can refresh between polls.
  // Page-data URLs are now relative (work under both file:// and localhost),
  // so we can't sniff the protocol from `base`. Use the document's protocol
  // instead — file:// is the trigger, regardless of how `base` is shaped.
  const isFile = window.location.protocol === 'file:';
  const cacheBuster = isFile ? '' : (base.includes('?') ? `&t=${Date.now()}` : `?t=${Date.now()}`);
  script.src = `${base}/council_status_${encodeURIComponent(token)}.js${cacheBuster}`;
  script.async = true;
  script.onload = () => {
    const status = window.__TRINITY_COUNCIL_STATUS__?.[token];
    onComplete(__trinityCoerceObj(status));
    script.remove();
  };
  script.onerror = () => {
    onComplete(null);
    script.remove();
  };
  document.body.appendChild(script);
}

window.__TRINITY_COUNCIL_OUTCOME__ = window.__TRINITY_COUNCIL_OUTCOME__ || {};

function loadOutcomeScript(councilId, onComplete) {
  if (__trinityHostFetch()) {
    if (!councilId) { onComplete(null); return; }
    __trinityHostQuery('council_outcome', { council_id: councilId }, onComplete);
    return;
  }
  const base = (typeof pageData !== 'undefined' && pageData.outcomeScriptBaseUrl) || '';
  if (!base || !councilId) {
    onComplete(null);
    return;
  }
  delete window.__TRINITY_COUNCIL_OUTCOME__[councilId];
  const script = document.createElement('script');
  // file:// URLs treat `?t=…` as part of the literal filename, so the
  // browser 404s `foo.js?t=174…`. Skip cache-busting on file://.
  // Page-data URLs are now relative (work under both file:// and localhost),
  // so we can't sniff the protocol from `base`. Use the document's protocol
  // instead — file:// is the trigger, regardless of how `base` is shaped.
  const isFile = window.location.protocol === 'file:';
  const cacheBuster = isFile ? '' : (base.includes('?') ? `&t=${Date.now()}` : `?t=${Date.now()}`);
  script.src = `${base}/${encodeURIComponent(councilId)}.js${cacheBuster}`;
  script.async = true;
  script.onload = () => {
    const outcome = window.__TRINITY_COUNCIL_OUTCOME__?.[councilId];
    onComplete(__trinityCoerceObj(outcome));
    script.remove();
  };
  script.onerror = () => {
    onComplete(null);
    script.remove();
  };
  document.body.appendChild(script);
}

// Reads review_pages/_active_council.js — the file://-safe pointer the capture
// host writes on `open-council-page`. macOS strips the URL query from file://
// opens (open/open location), so the council page can't get its status_token
// from `?status_token=…` when launched from the popup; it reads this sidecar
// instead. Same <script src> injection as loadStatusScript (file:// blocks
// fetch). The sidecar sets window.__TRINITY_ACTIVE_COUNCIL__; we read it on load.
function loadActiveCouncilScript(onComplete) {
  if (__trinityHostFetch()) {
    __trinityHostQuery('active_council', {}, onComplete);
    return;
  }
  const isFile = window.location.protocol === 'file:';
  const cacheBuster = isFile ? '' : ('?t=' + Date.now());
  const script = document.createElement('script');
  script.src = './_active_council.js' + cacheBuster;
  script.async = true;
  script.onload = () => {
    onComplete(__trinityCoerceObj(window.__TRINITY_ACTIVE_COUNCIL__));
    script.remove();
  };
  script.onerror = () => { onComplete(null); script.remove(); };
  document.body.appendChild(script);
}

// ─── Phase 4 dispatch runtime ─────────────────────────────────────
// Routes button clicks across three tiers in priority order:
//   1. Chrome extension present  → chrome.runtime.sendMessage(id, …)
//   2. macOS Shortcut installed  → shortcuts:// URL (existing path)
//   3. Neither                   → install banner
//
// Verdict: council_fb374b01311885cc (codex won). The detection cannot
// be synchronous from file:// JS, so we warm-probe on page load and
// cache in sessionStorage. Native-host-unavailable surfaces the
// install-extension hint inline rather than silently falling through
// to Shortcuts — silent fallback masks the setup bug.
window.__TRINITY_DISPATCH__ = window.__TRINITY_DISPATCH__ || (function() {
  // 1500ms was too short: an MV3 service worker that's gone dormant (after
  // ~30s idle) regularly takes >1.5s to wake on the first message, so the warm
  // probe spuriously timed out and cached state='absent' — which then made
  // dispatch() refuse to even try, surfacing "No dispatch path available" on a
  // perfectly-installed extension (founder report 2026-05-31).
  const PROBE_TIMEOUT_MS = 3000;
  // A user CLICKED this dispatch — wait generously for a dormant worker to wake
  // rather than wrongly report "no extension". The host accepts-then-runs
  // async, so the response we await is just the ack.
  const ACTION_TIMEOUT_MS = 8000;
  const CACHE_KEY = 'trinityDispatchState';
  const ext = (typeof pageData !== 'undefined' && pageData.browserExtension) || {};
  const extensionId = ext.extensionId || null;
  let state = sessionStorage.getItem(CACHE_KEY) || (extensionId ? 'unknown' : 'absent');
  let lastProbedAt = 0;
  const listeners = new Set();

  function setState(next) {
    if (next === state) return;
    state = next;
    try { sessionStorage.setItem(CACHE_KEY, state); } catch (_) {}
    listeners.forEach((cb) => { try { cb(state); } catch (_) {} });
  }

  function sendExt(message, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (!extensionId || !window.chrome?.runtime?.sendMessage) {
        reject(new Error('extension-unconfigured'));
        return;
      }
      const timer = setTimeout(() => reject(new Error('extension-timeout')),
                               timeoutMs || PROBE_TIMEOUT_MS);
      try {
        chrome.runtime.sendMessage(extensionId, message, (response) => {
          clearTimeout(timer);
          const err = chrome.runtime.lastError;
          if (err) { reject(new Error(err.message)); return; }
          resolve(response);
        });
      } catch (e) {
        clearTimeout(timer);
        reject(e);
      }
    });
  }

  async function probe(force) {
    if (!extensionId) {
      setState('absent');
      return state;
    }
    const stale = (Date.now() - lastProbedAt) > 30_000;
    if (!force && state === 'present' && !stale) return state;
    lastProbedAt = Date.now();
    // Retry once on timeout: the first ping wakes a dormant MV3 service worker,
    // the second lands. Without this the proactive state (and the "install
    // extension" banner it drives) spuriously read 'absent' on an installed,
    // working extension whose worker had simply gone to sleep.
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const r = await sendExt({ type: 'trinity-ping' });
        // 'rejected-sender' means the extension IS installed but too old to
        // recognize this page as a sender — it needs a RELOAD, not a reinstall.
        // Distinguish it from 'absent' so the UI can say the accurate thing.
        if (r && r.error === 'rejected-sender') setState('stale');
        else setState(r && r.ok ? 'present' : 'absent');
        return state;
      } catch (e) {
        if (attempt === 0 && /timeout/.test(String(e && e.message))) continue;
        setState('absent');
      }
    }
    return state;
  }

  async function dispatch({ extensionAction, onResult }) {
    // Tier 1 — Chrome extension Native Messaging. Tier-2 macOS Shortcut
    // was retired pre-launch (commit 53db635 + this commit's JS cleanup).
    //
    // The user CLICKED this — do NOT gate on the cached probe `state`. A stale
    // 'absent' (a warm-probe that timed out while the MV3 service worker was
    // dormant) must never block a dispatch the user explicitly asked for.
    // Always attempt when the extension is configured; let the real send
    // succeed-or-fail decide. Retry once on timeout: the first message wakes a
    // dormant service worker, the retry lands.
    if (extensionId && extensionAction) {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const r = await sendExt({ type: 'action', ...extensionAction },
                                  ACTION_TIMEOUT_MS);
          setState('present');
          if (r && r.ok) {
            onResult && onResult({ tier: 'extension', ok: true, response: r });
            return { tier: 'extension', ok: true, response: r };
          }
          // Extension reached the host but action failed. Surface the error
          // — never silently swallow native-host-unavailable; the user
          // needs to fix the install.
          if (r && r.error === 'native-host-unavailable') {
            setState('native-missing');
            onResult && onResult({ tier: 'extension', ok: false, response: r,
                                   reason: 'native-host-unavailable' });
            return { tier: 'extension', ok: false, response: r };
          }
          onResult && onResult({ tier: 'extension', ok: false, response: r });
          return { tier: 'extension', ok: false, response: r };
        } catch (e) {
          // A first-attempt timeout is almost always a dormant service worker
          // that THIS message just woke — retry once before giving up.
          if (attempt === 0 && /timeout/.test(String(e && e.message))) continue;
          setState('absent');
        }
      }
    }
    // Genuinely couldn't reach the extension (no id, or it never answered even
    // after a wake retry). reason lets the caller show an accurate message
    // instead of a generic "is it installed?" on an extension that IS installed.
    onResult && onResult({ tier: 'install-prompt', ok: false,
                           reason: 'extension-unreachable' });
    return { tier: 'install-prompt', ok: false };
  }

  function onStateChange(cb) { listeners.add(cb); return () => listeners.delete(cb); }

  // Warm probe on script load + on focus when stale or unknown.
  if (extensionId) {
    setTimeout(() => probe(false), 50);
    window.addEventListener('focus', () => {
      if (state !== 'present' || (Date.now() - lastProbedAt) > 30_000) {
        probe(false);
      }
    });
  }

  return { dispatch, probe, onStateChange,
           get state() { return state; },
           get extensionId() { return extensionId; } };
})();
"""
