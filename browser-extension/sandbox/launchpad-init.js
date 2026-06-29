const { createApp } = window.__TRINITY_VUE__;

(async () => {
  let resp = null;
  try {
    resp = await chrome.runtime.sendMessage({ type: 'query', query_kind: 'launchpad_data' });
  } catch (e) { resp = null; }
  if (!resp || !resp.ok) {
    // The capture host the side panel reaches over Native Messaging is wired up
    // by `install-extension` (writes the Native Messaging manifest), NOT
    // `install-mcp` (registers the MCP server in the CLI harnesses — a separate
    // subsystem that does nothing for this panel). Naming `install-mcp` here was
    // a wrong-CTA dead-end: a user who hit this fallback and ran it reloaded to
    // the exact same error. Match every sibling host-unavailable message
    // (popup.js / background.js / launchpad-init's dispatch-failure reasons /
    // live_council.html) and auto-fill the extension id so it's copy-pasteable.
    var __extId = (window.chrome && chrome.runtime && chrome.runtime.id) || '&lt;ID&gt;';
    document.body.innerHTML =
      '<div style="max-width:640px;margin:80px auto;padding:24px;'
      + 'font:15px/1.5 system-ui,sans-serif;color:#1a2b2e">'
      + '<h2 style="margin:0 0 8px">Trinity launchpad</h2>'
      + '<p>Couldn\u2019t reach the local Trinity engine over Native Messaging. '
      + 'Wire up the capture host once (<code>trinity-local install-extension '
      + '--extension-id ' + __extId + '</code>), '
      + 'then reload this page.</p></div>';
    return;
  }
  const __sb = document.getElementById('recent-sidebar-mount');
  if (__sb && resp.recentSidebarHtml) {
    // The host's rail-council links point at ../review_pages/live_council.html (the
    // file:// path). In the SIDE PANEL (sandbox) that resolves to a nonexistent
    // chrome-extension://.../review_pages/... page -> "This page has been blocked by
    // Chrome" (founder-caught). Repoint them at the sandbox's OWN ./live_council.html
    // sibling, which renders the council via the host bridge -- same fix as the
    // liveCouncilUrl computed prop. Gate on __TRINITY_HOST_FETCH__ so it only fires
    // in the sandbox (where ./live_council.html exists), never the file:// launchpad.
    __sb.innerHTML = window.__TRINITY_HOST_FETCH__
      ? resp.recentSidebarHtml.split('../review_pages/live_council.html').join('./live_council.html')
      : resp.recentSidebarHtml;
  }
  const pageData = resp.pageData;


    // The single empty-task VALIDATION message — referenced both where it's set
    // (launchCouncil) and where it's cleared (onPromptInput, on the first
    // keystroke that makes the textarea non-empty) so the two can never drift.
    const EMPTY_TASK_ERROR = 'Please enter a task first.';

    
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
  var NAV_RX = /(?:^|\/)(launchpad\.html|live_council\.html)(\?[^#]*)?(?:#.*)?$/;
  var STATS_RX = /(?:^|\/)stats\.html(\?[^#]*)?(?:#.*)?$/;
  var HOME_RX = /(?:^|\/)launchpad\.html(\?[^#]*)?(?:#.*)?$/;
  var PORTAL_RX = /portal_pages\/[A-Za-z0-9_.-]+\.html/;
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
  if (/^(file|https?):\/\//.test(path)) {
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


    // Provider slug normalizer — the harness rename gemini → antigravity
    // happened 2026-05-20, but historical council_outcomes/*.json files on
    // disk still carry provider="gemini". This helper centralizes the
    // alias at the read boundary so all downstream maps key on the
    // canonical "antigravity" slug. Hoisted to module scope so both
    // renderChart() (palette lookup) and the Vue-scoped formatProviderLabel
    // can reach it. When the historical outcomes are far enough in the past
    // to stop caring (or a one-time batch migration ships), delete this
    // function — the maps stay clean. (Per Trinity ask council 2026-05-21:
    // "maps encode what's canonical; normalization encodes what's historical.")
    function normalizeProviderSlug(slug) {
      // Canonicalize every legacy / web-era / lab / brand-name slug to its CLI
      // provider so every label/colour keyed on this (formatProviderLabel,
      // modelBrand, the chart palette) folds e.g. chatgpt→codex / gpt→codex /
      // google→antigravity / claude_ai→claude instead of leaking "Chatgpt" /
      // "Gpt" / "Google" / "Claude Ai". This is the FULL mirror of the Python
      // council_schema._LEGACY_PROVIDER_ALIASES boundary — it must match it
      // entry-for-entry, NOT a three-key subset. A short subset (the old
      // {gemini,chatgpt,claude_ai}) silently diverged from Python AND from the
      // memory viewer's canonProviderSlug: a picks.json winner of "gpt" painted
      // "Gpt" on this cheat-sheet while Python (and the memory viewer's picks
      // reader) both branded it "GPT". One source of truth, no drift.
      const aliases = {
        gemini: 'antigravity', google: 'antigravity', bard: 'antigravity',
        chatgpt: 'codex', openai: 'codex', gpt: 'codex',
        claude_ai: 'claude', 'claude.ai': 'claude', anthropic: 'claude',
      };
      return aliases[slug] || slug;
    }

    // Model-brand display (Claude / GPT / Gemini) — the brand a reader
    // recognizes, for user-facing MODEL/eval COMPARISON surfaces (the eval +
    // preference bar charts). Mirrors council_schema._MODEL_BRAND_DISPLAY (the
    // Python single-source, used by _elo_chart_data) so the JS charts speak the
    // same trio as the Local-Elo summary. As of #275 (2026-06-06) this AGREES
    // with formatProviderLabel — the old split where council/routing surfaces
    // read the harness trio (Codex / Antigravity) while perf surfaces read the
    // model brand was folded onto one brand display (Claude / GPT / Gemini).
    // Charts compare model performance →
    // model trio.
    function modelBrand(provider) {
      if (!provider) {
        return '';
      }
      const normalized = normalizeProviderSlug(String(provider).trim().toLowerCase());
      const brand = { claude: 'Claude', codex: 'GPT', antigravity: 'Gemini' };
      if (brand[normalized]) {
        return brand[normalized];
      }
      return normalized
        .split(/[_\s-]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
    }

    function maybeSendTelemetry() {
      const telemetry = pageData.telemetry || {};
      const settings = telemetry.settings || {};
      if (!settings.sharing_enabled || !settings.endpoint) {
        return;
      }
      // Skip obvious test/placeholder endpoints — sending to them produces
      // ERR_NAME_NOT_RESOLVED noise in the console with no upside.
      // example.invalid is the RFC 6761 reserved stub used during dev.
      if (/example\.invalid|localhost(?:[:/]|$)|127\.0\.0\.1/.test(settings.endpoint)) {
        return;
      }

      const endpoint = settings.endpoint;
      const send = (payload) => {
        const body = JSON.stringify(payload);
        if (navigator.sendBeacon) {
          const blob = new Blob([body], { type: 'application/json' });
          navigator.sendBeacon(endpoint, blob);
          return;
        }
        fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body,
          keepalive: true,
          mode: 'cors',
        }).catch(() => null);
      };

      if (settings.share_usage_events !== false && telemetry.view_event) {
        send(telemetry.view_event);
      }

      if (settings.share_elo_summaries !== false && telemetry.elo_event && telemetry.snapshot_hash) {
        const hashKey = `trinity:last-elo-hash:${settings.share_install_id || 'default'}`;
        const tsKey = `trinity:last-elo-ts:${settings.share_install_id || 'default'}`;
        const lastHash = localStorage.getItem(hashKey);
        const lastTs = Number(localStorage.getItem(tsKey) || '0');
        const dayMs = 24 * 60 * 60 * 1000;
        if (lastHash !== telemetry.snapshot_hash || (Date.now() - lastTs) > dayMs) {
          send(telemetry.elo_event);
          localStorage.setItem(hashKey, telemetry.snapshot_hash);
          localStorage.setItem(tsKey, String(Date.now()));
        }
      }
    }

    function renderChart() {
      // Charts live ONLY on /stats; the home (.lp-view-home) hides them, so skip
      // there — and DON'T pull the ~205 KB Chart.js on the chart-less home (it's
      // no longer eager in <head>; lazy-load it the first time a stats view needs
      // it, then re-run). Graceful: on load failure we simply show no chart.
      if (document.querySelector('.lp-view-home')) return;
      if (!window.Chart) {
        const s = document.createElement('script');
        s.src = '../vendor/chart.umd.min.js';
        s.onload = () => renderChart();
        s.onerror = () => {};
        document.head.appendChild(s);
        return;
      }
      const baseScales = {
        y: { ticks: { color: '#5b646b' }, grid: { color: 'rgba(215, 204, 185, 0.45)' } },
        x: { ticks: { color: '#5b646b' }, grid: { display: false } },
      };

      const eloData = pageData.eloChart;
      const eloCtx = document.getElementById('provider-elo-chart');
      // Skip the one-bar chart entirely when degenerate: a single-entrant Elo is
      // not a ranking, so the template hides the canvas (it's null here) and
      // paints a single-entrant explanation instead. Guard explicitly so a
      // future template change can't resurrect the meaningless lone bar.
      if (eloCtx && eloData && !eloData.degenerate && eloData.labels && eloData.labels.length) {
        // Bars are signed deviation from the 1500 base (eloData.data = elo - 1500),
        // so the axis is CENTERED on 0, not floored at 1400. A 2-0 coin-flip
        // (Claude 1523 / GPT 1477) renders as a small +23 / -23 pair around the
        // midline instead of a tower vs a stub — the 1400-floor magnified that
        // 46-point gap into a confident "Claude crushes GPT" ranking off two
        // games (Trinity council council_21a5b74fb1df3fda, unanimous). The axis
        // ticks add the base back so they read as real Elo; the tooltip restores
        // the absolute rating + game count so a thin bar can't masquerade as a
        // settled verdict.
        const base = (eloData.base || 1500);
        const elos = eloData.elos || [];
        const games = eloData.games || [];
        // Symmetric span so above- and below-base bars are visually comparable,
        // with a small floor (so a near-zero delta still draws a visible nub) and
        // ~20% headroom (so the tallest bar doesn't slam the axis edge). The
        // deltas live on the dataset, not the top-level payload.
        const deltas = (eloData.datasets && eloData.datasets[0] && eloData.datasets[0].data) || [];
        const peak = Math.max(0, ...deltas.map((v) => Math.abs(v)));
        const maxAbs = Math.max(20, Math.ceil(peak * 1.2));
        new Chart(eloCtx, {
          type: 'bar',
          data: eloData,
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              tooltip: {
                callbacks: {
                  label: (ctx) => {
                    const i = ctx.dataIndex;
                    const elo = (elos[i] != null) ? elos[i] : (base + ctx.parsed.y);
                    const n = games[i];
                    const nStr = (n != null) ? ` · ${n} council${n === 1 ? '' : 's'}` : '';
                    return `${elo} Elo${nStr}`;
                  },
                },
              },
            },
            scales: {
              y: {
                ...baseScales.y,
                min: -maxAbs,
                max: maxAbs,
                ticks: {
                  ...baseScales.y.ticks,
                  callback: (value) => base + value,
                },
              },
              x: baseScales.x,
            },
          },
        });
      }

      // Provider color palette shared across both /100 charts.
      const palette = {
        claude: 'rgba(213, 130, 79, 0.85)',
        antigravity: 'rgba(86, 120, 156, 0.85)',
        codex: 'rgba(78, 138, 109, 0.85)',
        mlx: 'rgba(124, 96, 130, 0.85)',
      };

      // Trinity task_type → category map, injected from the server's
      // canonical CATEGORY_REGISTRY so the launchpad chart never drifts
      // out-of-sync with new task_types. Unknown task_types bucket into
      // `defaultCategoryForUnknownTaskType` (default: hard_prompts) instead
      // of disappearing from the chart.
      const TASK_TYPE_TO_CATEGORY = pageData.taskTypeToCategory || {};
      const DEFAULT_CATEGORY = pageData.defaultCategoryForUnknownTaskType || 'overall';

      const benchmarks = pageData.globalBenchmarks || {};
      const providers = pageData.benchmarkProviders || [];
      const categories = Object.keys(benchmarks);
      const labels = categories.map((c) => c.charAt(0).toUpperCase() + c.slice(1));

      function buildGroupedBar(ctxId, datasets) {
        const ctx = document.getElementById(ctxId);
        if (!ctx) return;
        new Chart(ctx, {
          type: 'bar',
          data: { labels, datasets },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: 'bottom', labels: { color: '#5b646b' } } },
            scales: {
              y: { ...baseScales.y, min: 0, max: 100 },
              x: baseScales.x,
            },
          },
        });
      }

      // Reference evals chart.
      if (providers.length && categories.length) {
        const datasets = providers.map((provider) => ({
          label: modelBrand(provider),
          data: categories.map((cat) => {
            const v = benchmarks[cat]?.models?.[provider];
            return (v === null || v === undefined) ? null : v;
          }),
          backgroundColor: palette[normalizeProviderSlug(provider)] || 'rgba(120, 120, 120, 0.85)',
          borderRadius: 4,
        }));
        buildGroupedBar('reference-evals-chart', datasets);
      }

      // Personal preference chart — uses the LMArena-aligned CATEGORY_REGISTRY
      // for its X-axis (NOT the globalBenchmarks keys, which are a different
      // scheme: ArtificialAnalysis intelligence/coding/agentic). Reusing the
      // benchmarks X-axis was the original bug — task_types bucketed into
      // hard_prompts / overall / etc never matched intelligence / agentic.
      const personal = pageData.personalRoutingTable;
      const personalCtx = document.getElementById('personal-preference-chart');
      const personalCategoryKeys = pageData.personalChartCategoryKeys || [];
      const personalCategoryLabels = pageData.personalChartCategoryLabels || {};
      if (personalCtx && personal && providers.length && personalCategoryKeys.length) {
        const byTaskType = personal.by_task_type || {};
        // Per (provider, category) mean score. 'overall' is the AGGREGATE across
        // every task_type (the total over all your councils — not a niche
        // "general" bucket); the other categories only sum the task_types that
        // map to them. Without this, a user whose councils are all real-world
        // reasoning (not coding/math) saw an empty Overall + one lonely bar.
        const rawData = providers.map((provider) => (
          personalCategoryKeys.map((cat) => {
            const scores = [];
            for (const [taskType, providerScores] of Object.entries(byTaskType)) {
              const mappedCat = TASK_TYPE_TO_CATEGORY[taskType] || DEFAULT_CATEGORY;
              if (cat !== 'overall' && mappedCat !== cat) continue;
              const entry = providerScores?.[provider];
              if (entry && typeof entry.overall === 'number') {
                scores.push(entry.overall);
              }
            }
            if (!scores.length) return null;
            const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
            return Math.round(mean * 10 * 10) / 10;  // 0-10 → 0-100, 1 decimal
          })
        ));
        // Keep 'overall' (the aggregate) + any category that has data AND
        // differs from it. Two effects: (1) hide LMArena buckets with no
        // councils (Coding/Math/Multi-Turn for a non-coding user); (2) when a
        // user's councils all fall in ONE bucket, that bucket equals overall,
        // so we show a single clean 'Overall' instead of two identical groups.
        const overallIdx = personalCategoryKeys.indexOf('overall');
        const keepIdx = personalCategoryKeys
          .map((_, i) => {
            if (!rawData.some((row) => row[i] !== null)) return -1;
            if (i !== overallIdx && overallIdx >= 0
                && rawData.every((row) => row[i] === row[overallIdx])) return -1;
            return i;
          })
          .filter((i) => i >= 0);
        const personalDatasets = providers.map((provider, pi) => ({
          label: modelBrand(provider),
          data: keepIdx.map((i) => rawData[pi][i]),
          backgroundColor: palette[normalizeProviderSlug(provider)] || 'rgba(120, 120, 120, 0.85)',
          borderRadius: 4,
        }));
        const keptLabels = keepIdx.map((i) => personalCategoryLabels[personalCategoryKeys[i]] || personalCategoryKeys[i]);
        if (keepIdx.length) {
          // Build chart inline — it uses a DIFFERENT X-axis from buildGroupedBar
          // (which is closed over the global `labels` from globalBenchmarks).
          new Chart(personalCtx, {
            type: 'bar',
            data: {
              labels: keptLabels,
              datasets: personalDatasets,
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: { legend: { position: 'bottom', labels: { color: '#5b646b' } } },
              scales: {
                y: { ...baseScales.y, min: 0, max: 100 },
                x: baseScales.x,
              },
            },
          });
        }
      }
    }

    function normalizeOperation(raw, fallback = null) {
      if (!raw) {
        return fallback;
      }
      const kind = raw.kind || raw.metadata?.kind || fallback?.kind || 'council';
      const memberMap = raw.members || fallback?.members || {};
      const fallbackOrder = fallback?.memberOrder || [];
      const rawOrder = raw.memberOrder || raw.metadata?.members || Object.keys(memberMap);
      return {
        ...fallback,
        ...raw,
        kind,
        statusToken: raw.statusToken || raw.status_token || fallback?.statusToken || '',
        label: raw.label || raw.task_text || fallback?.label || '',
        memberOrder: rawOrder?.length ? rawOrder : fallbackOrder,
        members: memberMap,
        activeProvider: raw.activeProvider || raw.active_provider || fallback?.activeProvider || null,
        activeProviders: raw.activeProviders || raw.active_providers || fallback?.activeProviders || [],
        synthesis: raw.synthesis || fallback?.synthesis || {},
        reviewPath: raw.reviewPath || raw.review_path || fallback?.reviewPath || '',
        error: raw.error || fallback?.error || '',
      };
    }

    function formatProviderLabel(provider) {
      if (!provider) {
        return '';
      }
      // Normalize before lookup — gemini → antigravity per the 2026-05-20
      // harness rename. Historical outcomes on disk still carry "gemini"
      // but the canonical labels map keys on "antigravity".
      const normalized = normalizeProviderSlug(String(provider).trim().toLowerCase());
      // #275: provider labels read as the MODEL BRAND (Claude / GPT / Gemini),
      // matching the Elo chart + eval cards. Folded in 2026-06-06 (founder call)
      // so every council surface — this panel, the live council page, AND the
      // extension popup — agrees, instead of the old split where these read the
      // harness trio (Codex / Antigravity) while the perf surfaces read the brand.
      const labels = {
        claude: 'Claude',
        antigravity: 'Gemini',
        codex: 'GPT',
        mlx: 'MLX',
        openai: 'GPT',
      };
      if (labels[normalized]) {
        return labels[normalized];
      }
      return normalized
        .split(/[_\s-]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
    }

    function LaunchpadApp(pageData) {
      const initialOperation = normalizeOperation(pageData.activeOperation || null);
      return {
        prompt: '',
        // True when this launchpad is rendered INSIDE the extension side panel
        // (sandbox/_bridge.js sets window.__TRINITY_HOST_FETCH__), false on the
        // file:// / localhost launchpad. Used to reframe the "Install the Chrome
        // extension" cross-bootstrap card — a side-panel viewer is ALREADY inside
        // the installed extension, so telling them to install it is contradictory;
        // the real remaining step there is wiring the Native-Messaging host.
        inExtensionPanel: !!window.__TRINITY_HOST_FETCH__,
        launchError: '',
        // Phase 4 — single global banner that opens when dispatch hits
        // tier 3 (no extension + no Shortcut) or when the extension is
        // present but install-extension wasn't run (`native-host-unavailable`).
        // Per codex's verdict: ONE inline banner, not per-button replacement.
        // Buttons stay clickable; failed click reopens the banner if dismissed.
        dispatchBannerOpen: false,
        dispatchBannerReason: '',  // 'no-route' | 'native-host-unavailable'
        // Stuck-launch rollback (2026-05-26): launchCouncil etc. snapshot the
        // user's typed prompt here before clearing the textarea, so a failed
        // dispatch (extension absent / native host missing) can restore it.
        // Without this, a failed launch ate the prompt and the user had to
        // retype while staring at "Council in Progress" that would never
        // resolve. See handleDispatchResult for the rollback.
        pendingPrompt: '',
        operation: initialOperation,
        // Set the instant "Stop council" is clicked so the button can ACK the
        // click ("Stopping…", disabled) while the host writes the canceled status
        // the poller is waiting on — otherwise Stop looks like a no-op (the
        // spinner keeps cycling "running"). Reset on begin/clear.
        stopRequested: false,
        statusPollHandle: null,
        statusRotateHandle: null,
        currentStatusIndex: 0,
        settingsOpen: false,
        _settingsTrigger: null,
        // WCAG 4.1.3 — a copy ✓ is VISUAL-ONLY (copiedKey swaps the button glyph
        // to "✓ Copied"), so a screen-reader user clicking any "Copy command"
        // button hears SILENCE (the clipboard write + the icon swap are both
        // mute to AT). Route the confirmation through the persistent sr-only
        // role=status region by stashing the announce text here; liveAnnouncement
        // surfaces it at the lowest precedence so it never masks a dispatch error
        // or a live council status. Cleared on the same timer as copiedKey.
        copyAnnouncement: '',
        // WCAG 4.1.3 — the Bulk-import PROBE result is a v-if banner that mounts
        // AFTER a dry-run dispatch ("✓ Detected N export(s)" or "⚠ <error>"). It's
        // a plain <div> with no role=status, so a screen-reader user who clicks
        // Probe / Import hears SILENCE — the success/error/dispatched outcome is
        // VISUAL-ONLY. Route the outcome text through the persistent sr-only
        // role=status region (liveAnnouncement) so the probe/import result is
        // announced. Set by probeImportPath / confirmImport on every terminal
        // outcome; cleared on the next probe. Lowest-tier (just above copy) so it
        // never masks a dispatch error or a live council status.
        importAnnouncement: '',
        // Transient ack for a side-panel memory-viewer deep-link bounce (the
        // runtime opens the full dashboard in a browser window because memory.html
        // isn't in the sandbox). Visible note + AT announcement so the click is
        // never a silent no-op; self-clears. Empty = nothing to show.
        portalNotice: '',
        portalNoticeHandle: null,
        railOpen: false,
        // Desktop rail-collapse state (the persistent sidebar reclaimed by main).
        // Tracked reactively (not just as a body class) so the rail-toggle's
        // aria-expanded reflects the true SHOWN/HIDDEN state. Default false =
        // expanded (the desktop rail shows on first paint). railIsDesktop is set
        // at init + on resize so railExpanded picks the right pole per breakpoint.
        railCollapsed: false,
        railIsDesktop: true,
        showReferenceRatings: false,
        settingsLinks: pageData.settingsLinks || {},
        // The Local Elo chart payload (labels/elos/games/thin/minGames/thinFloor).
        // Reactive so the thin-sample caveat + per-bar n=N disclosure render; the
        // chart canvas itself is drawn imperatively in renderChart().
        eloChart: pageData.eloChart || null,
        providerHealth: pageData.providerHealth || { providers: [], hasMissing: false, footerNote: '' },
        telemetry: {
          enabled: !!pageData.telemetry?.settings?.sharing_enabled,
          endpoint: pageData.telemetry?.settings?.endpoint || '',
          shareInstallId: pageData.telemetry?.settings?.share_install_id || '',
        },
        coreStatus: pageData.coreStatus || { state: 'empty' },
        memoryHealth: pageData.memoryHealth || { issues: [], ok_count: 0, total_count: 0 },
        // "Refresh memory" button state machine (council_1f9cbecd7104f90f #3).
        // States: 'idle' | 'running' | 'done' | 'failed'. Set by refreshMemory()
        // — fires dream via Chrome extension; flips to 'done' for ~3s on
        // success, 'failed' on dispatch error. NEVER auto-fires.
        refreshMemoryStatus: 'idle',
        // Co-located failure detail for the Refresh-memory / Repair-extension
        // buttons. A FAILED dispatch used to route through handleDispatchResult,
        // which sets `launchError` — a surface that lives ONLY in the COUNCIL
        // composer ribbon. So a failed Refresh-memory click on /stats showed a
        // bare "⚠ Failed" while the real reason ("capture host crashed …") leaked
        // onto the home-view council ribbon, reading as if a council had failed
        // (the #242(a) cross-surface-misattribution class). These hold the error
        // ON the card that owns the button, like lensBuildError.
        refreshMemoryError: '',
        repairExtensionError: '',
        // #242(a) lens-build card button state:
        //   'idle' | 'stopping' | 'restarting' | 'stop-failed' | 'restart-failed'.
        // A FAILED stop/restart used to silently reset to 'idle' after 2s while the
        // only error surfaced in the COUNCIL composer ribbon (handleDispatchResult →
        // launchError) — an unrelated surface, so the lens-build button read as a
        // no-op. The *-failed states + lensBuildError keep the feedback CO-LOCATED.
        lensBuildAction: 'idle',
        lensBuildError: '',
        // #147 self-healing UI surface — same shape as refreshMemoryStatus.
        // Set by repairExtension(); fires extension-repair-auto via Chrome
        // extension dispatch. NEVER auto-fires (council call is expensive
        // and surprising — same intent the dream button respects).
        repairExtensionStatus: 'idle',
        // #148 bulk-import UI state. Two-step probe → confirm flow:
        // importPath: text input the user pastes a filesystem path into
        // importStatus: 'idle' | 'probing' | 'importing' | 'imported'
        // importProbeResult: response object from import-export-dry-run
        //   ({detected: [...], error: '...', hint: '...'}). Renders the
        //   per-source detected list + Import button on success, or a
        //   warning banner on failure.
        importPath: '',
        importStatus: 'idle',
        importProbeResult: null,
        // The exact path the current probe result describes. Edits to importPath
        // that diverge from this INVALIDATE the "✓ Detected N exports" banner —
        // otherwise the confirm-before-cost contract breaks: a probe of folder A
        // followed by an edit to folder B left A's "Detected 3 exports" banner up,
        // and clicking Import dispatched the FULL (paid) ingest against the
        // un-probed B. (founder symptom: "it said 3 detected but imported a folder
        // I'd just retyped".)
        importProbedPath: '',
        liveReviewUrlBase: pageData.liveReviewUrl || '',
        globalBenchmarks: pageData.globalBenchmarks || {},
        benchmarkProviders: pageData.benchmarkProviders || [],
        providerModels: pageData.providerModels || {},
        personalRoutingTable: pageData.personalRoutingTable || null,
        cortexRules: pageData.cortexRules || null,
        tasteLenses: pageData.tasteLenses || null,
        // Tooltip lookup for cross-memory chips that deep-link to
        // topology basins. {basin_id: "top_term1 · top_term2 · ..."}
        // Resolved server-side from topics.json (tick #38). Empty
        // when no consolidation; chips fall back to "Open basin <id>".
        topologyBasinLabels: pageData.topologyBasinLabels || {},
        // Used by .lens-basin-chip + .cortex-topology-chip tooltips.
        basinHoverLabel(bid) {
          if (!bid) return '';
          const terms = this.topologyBasinLabels[bid];
          if (terms) return 'Basin ' + bid + ' — ' + terms;
          return 'Open basin ' + bid + ' in the topology graph';
        },
        // The cheat-sheet row label: NAME the kind of question (the basin's top
        // terms) instead of the opaque internal id "b00". Tries the pick's
        // topology basin first (post-collapse the routing basin IS the topology
        // basin), then the pick's own id, and only falls back to the cleaned id
        // when topics.json carries no terms for it — so the "one line per KIND of
        // question" headline never resolves to a meaningless "b00".
        cheatSheetLabel(r) {
          const terms = this.topologyBasinLabels[r.topology_basin]
                     || this.topologyBasinLabels[r.basin_id];
          if (terms) return terms;
          return (r.basin_id || '').replace(/_/g, ' ');
        },
        // The taste-card "Spans" chip body: NAME the basin (its top terms)
        // instead of the opaque internal id "b00" — same call the cheat-sheet
        // makes (cheatSheetLabel), but for the lens-tension basins-spanned row.
        // The :title still carries "Basin b00 — design · arch" for traceability;
        // the BODY must read so a TOUCH user (no hover) knows what the chip is.
        // Falls back to the cleaned id only when topics.json carries no terms.
        spansBasinLabel(bid) {
          const terms = this.topologyBasinLabels[bid];
          if (terms) return terms;
          return (bid || '').replace(/_/g, ' ');
        },
        formatProviderLabel,
        copiedKey: '',
        // PNG me-card render lifecycle: 'idle' | 'rendering' | 'done' | 'error'.
        // Kept SEPARATE from copiedKey (the optimistic-flash channel) so the
        // button can show an HONEST pending→success/failure progression instead
        // of flashing "✓ Rendered" before the dispatch resolves.
        meCardStatus: 'idle',
        meCardError: '',
        debugMode: new URLSearchParams(location.search).has('debug'),
        copyLens(text, key) {
          if (!text) return;
          const restore = () => { this.copiedKey = ''; };
          const setCopied = () => {
            this.copiedKey = key;
            this.announceCopy();
            setTimeout(restore, 1800);
          };
          if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text).then(setCopied, () => {
              this._copyFallback(text);
              setCopied();
            });
          } else {
            this._copyFallback(text);
            setCopied();
          }
        },
        _copyFallback(text) {
          // file:// pages on some browsers block navigator.clipboard. Use the
          // legacy textarea + execCommand path so the lens copies even when
          // the launchpad is opened directly from disk.
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand('copy'); } catch (_) { /* ignore */ }
          document.body.removeChild(ta);
        },
        get routingTableProviders() {
          if (!this.personalRoutingTable?.by_task_type) return [];
          const set = new Set();
          for (const taskType in this.personalRoutingTable.by_task_type) {
            for (const provider in this.personalRoutingTable.by_task_type[taskType]) {
              set.add(provider);
            }
          }
          return Array.from(set).sort();
        },
        get routingCheatSheetMap() {
          // The cheat-sheet shows ONE routing pattern per task_type — but a
          // task_type with a SINGLE council (n_personal < 2) is one data point,
          // not a pattern, and the real corpus has hundreds of them (the n=1
          // long tail: 352 of 430 on the founder's data). Rendering them all
          // turned this table into a ~36k-px scroll-monster that buried the rest
          // of the launchpad, and put rows on a "Who the chairman picks" sheet
          // whose Best column is just "—". The PICK column (best_per_task_type)
          // was already confidence-filtered; the ROWS were the missed sibling
          // (confidence_honesty_arc). Suppress single-council rows and sort by
          // evidence so the strongest patterns lead. The full table stays in
          // routing.json (linked per row + the note below). Insertion-ordered
          // object so petite-vue's `(scores, taskType) in map` keeps the sort.
          const t = this.personalRoutingTable;
          if (!t?.by_task_type) return {};
          const cold = t.cold_start || {};
          const nOf = (tt) => (cold[tt]?.n_personal) || 0;
          const out = {};
          Object.keys(t.by_task_type)
            .filter((tt) => nOf(tt) >= 2)
            // Council count DESC, then task_type name ASC as a stable tie-break
            // so two task_types with the same n don't swap rows when the server
            // re-aggregates by_task_type in a different order (the sort would
            // otherwise fall back to Object.keys insertion order).
            .sort((a, b) => (nOf(b) - nOf(a)) || String(a).localeCompare(String(b)))
            .forEach((tt) => { out[tt] = t.by_task_type[tt]; });
          return out;
        },
        get routingCheatSheetHasRows() {
          // True iff at least one task_type cleared the n>=2 floor — i.e. the
          // cheat-sheet table has body rows. When false (data exists but every
          // task_type is a single council), the template suppresses the header-
          // only ghost table and shows an honest "no routing pattern yet" state.
          return Object.keys(this.routingCheatSheetMap).length > 0;
        },
        get routingHiddenSingletons() {
          // How many task_types were suppressed for having a single council —
          // surfaced in the note below so the suppression is never silent.
          const t = this.personalRoutingTable;
          if (!t?.by_task_type) return 0;
          const cold = t.cold_start || {};
          let hidden = 0;
          for (const tt in t.by_task_type) {
            if (((cold[tt]?.n_personal) || 0) < 2) hidden += 1;
          }
          return hidden;
        },
        coldStartFor(taskType) {
          // Returns {n_personal, alpha, personalization_pct} for a task_type
          // when the cold-start block is present (server-side computed by
          // launchpad_data._load_personal_routing_table). Null when the block
          // is missing (older launchpad_data without the augmentation) so the
          // column degrades to a "—" gracefully.
          return this.personalRoutingTable?.cold_start?.[taskType] || null;
        },
        winsFor(taskType) {
          // Chairman wins per task_type. Returns {wins, total} for the
          // current best_per_task_type[taskType] provider, or null when
          // wins_per_task_type is missing (legacy data without
          // chairman_winner) so the cell falls back to the chip alone.
          const wins = this.personalRoutingTable?.wins_per_task_type?.[taskType];
          const best = this.personalRoutingTable?.best_per_task_type?.[taskType];
          if (!wins || !best) return null;
          const winCount = wins[best] || 0;
          const total = Object.values(wins).reduce((a, b) => a + b, 0);
          if (!total) return null;
          return { wins: winCount, total };
        },
        pickIsTie(taskType) {
          // True when the "best" is a tie / coin-flip (no strict chairman-win
          // lead, or no chairman supervision). Computed server-side in
          // personal_routing.aggregate_routing_table → pick_is_tie; the
          // surface demotes the confident chip for these (green-gate #35).
          return !!(this.personalRoutingTable?.pick_is_tie?.[taskType]);
        },
        evidenceUrl(councilId) {
          // Each evidence chip links to the existing live council page for
          // that outcome. Uses ?thread_id= so the harness page renders the
          // full council UI with members + chairman synthesis. Falls back to
          // a plain `?council_id=` fragment when the launchpad's live-council
          // base URL isn't configured — degrades to a same-page anchor.
          const base = pageData.liveReviewUrl || '';
          if (base) {
            const sep = base.includes('?') ? '&' : '?';
            return `${base}${sep}thread_id=${encodeURIComponent(councilId)}`;
          }
          return `#${councilId}`;
        },
        statusScriptBaseUrl: pageData.statusScriptBaseUrl || '',
        councilStatusMessages: pageData.councilLoadingMessages || [],
        ingestStatusMessages: [
          'Scanning recent transcripts...',
          'Extracting task signals...',
          'Writing launchpad updates...',
        ],
        init() {
          if (this.operation?.statusToken) {
            this.startOperationPolling(this.operation.statusToken);
          }
          // Keep railIsDesktop fresh so the rail-toggle's aria-expanded reports the
          // correct pole (desktop sidebar vs narrow drawer) — the 1024px boundary
          // is the same one toggleRail() / the rail-close click branch on.
          const syncRailBreakpoint = () => { this.railIsDesktop = window.innerWidth >= 1024; };
          syncRailBreakpoint();
          window.addEventListener('resize', syncRailBreakpoint);
          // Escape dismisses whatever overlay is open. The settings modal is a
          // true modal (z-index 1000, backdrop) — without an Esc path the × was
          // the ONLY way out (the keyboard sibling of the founder's "can't close
          // the modal" × bug); standard modals close on Escape, so it wins first.
          // Otherwise Esc closes the mobile councils drawer (selecting a council
          // closes it on narrow; the desktop rail shows via CSS). railOpen is the
          // MOBILE drawer flag only.
          document.addEventListener('keydown', (e) => {
            // Tab inside the open settings modal must STAY inside it. Without a
            // trap, Tab walked focus straight out to the page behind the backdrop
            // (the textarea, Launch Council, the rail) — a keyboard/SR user could
            // operate the obscured page while the modal claimed to be modal.
            if (e.key === 'Tab' && this.settingsOpen) { this.trapSettingsTab(e); return; }
            // The open mobile drawer presents a SCRIM that pointer-blocks the page
            // (a tap on the obscured composer/gear is caught by the scrim), so the
            // page is intentionally non-interactive while the drawer is open. But
            // without a Tab trap, KEYBOARD focus walked straight OUT of the rail to
            // the hamburger, the settings gear, and the page links BEHIND the scrim
            // — a keyboard/SR user could operate obscured, pointer-blocked content
            // (the exact leak the settings modal traps above; the drawer was the
            // asymmetric sibling). railOpen is true only on the narrow drawer
            // (desktop keeps the rail as a persistent sidebar, no scrim), so the
            // trap is mobile-only and desktop-safe.
            if (e.key === 'Tab' && this.railOpen) { this.trapRailTab(e); return; }
            if (e.key !== 'Escape') return;
            if (this.settingsOpen) { this.closeSettings(); return; }
            this.closeRail();
          });
          document.querySelectorAll('.council-rail .rail-council').forEach((a) => {
            a.addEventListener('click', () => { if (window.innerWidth < 1024) this.closeRail(); });
          });
          // A dispatch fired OUTSIDE the Vue instance (the sandbox click
          // interceptor's __trinityOpenFullLaunchpad — the /stats memory-viewer
          // chips, which open-launchpad rather than self-nav to a blocked
          // chrome-extension page) routes its RESULT back here via this event,
          // so a FAILED open (no native host) raises the SAME failure banner
          // every in-app dispatch shows instead of being silently swallowed.
          window.addEventListener('trinity:dispatch-result', (e) => {
            if (e && e.detail) { this.handleDispatchResult(e.detail); }
          });
          // A memory-viewer deep link (→ topology / picks.json / a lens chip) in
          // the side panel can't self-nav to memory.html (not in the sandbox), so
          // the runtime bounces it to open-launchpad — the FULL dashboard in a
          // SEPARATE browser window. open-launchpad's success is silent in
          // handleDispatchResult, so the click read as a dead no-op. Acknowledge it
          // immediately: a transient, dismissible, AT-announced notice that names
          // where the click went (the views live in the browser dashboard, not the
          // panel). Fires BEFORE the dispatch resolves so the ack is immediate.
          window.addEventListener('trinity:portal-open', (e) => {
            const label = (e && e.detail && e.detail.label) || 'the memory viewer';
            this.showPortalNotice(label);
          });
        },
        toggleRail() {
          if (window.innerWidth >= 1024) {
            // Track the collapse state reactively (not just the body class) so
            // railExpanded — and thus the toggle's aria-expanded — flips in lockstep.
            this.railCollapsed = !this.railCollapsed;
            document.body.classList.toggle('rail-collapsed', this.railCollapsed);
          } else {
            this.railOpen = !this.railOpen;
            document.body.classList.toggle('rail-open', this.railOpen);
            // On OPEN, move focus into the drawer (the search filter) so the very
            // first Tab is already trapped inside the rail — without this the trap
            // engages only AFTER the user has manually tabbed into the rail, and a
            // user who never does keeps walking the obscured page. Mirrors
            // openSettings' focus-into-modal. railOpen toggled off → no focus move.
            if (this.railOpen) {
              requestAnimationFrame(() => {
                const els = this.railFocusables();
                if (els.length) els[0].focus();
              });
            }
          }
        },
        // Focusable controls for the open drawer trap, in DOM/Tab order: the rail's
        // own controls (search filter + council anchors) PLUS the hamburger toggle,
        // which IS the keyboard-reachable CLOSE control (Enter/Space on it closes
        // the drawer) — so the trap never strands a keyboard user with no way out
        // besides Esc.
        railFocusables() {
          const out = [];
          const ham = document.querySelector('.rail-toggle');
          if (ham && ham.offsetParent !== null) out.push(ham);
          const rail = document.querySelector('.council-rail');
          if (rail) {
            rail.querySelectorAll(
              'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
            ).forEach((el) => { if (el.offsetParent !== null || el === document.activeElement) out.push(el); });
          }
          return out;
        },
        trapRailTab(e) {
          const els = this.railFocusables();
          if (!els.length) return;
          const first = els[0];
          const last = els[els.length - 1];
          const active = document.activeElement;
          const rail = document.querySelector('.council-rail');
          const ham = document.querySelector('.rail-toggle');
          const inside = (rail && rail.contains(active)) || active === ham;
          // Wrap at the edges; if focus has already leaked outside the rail+toggle
          // (e.g. it started on the page), pull it back to the first control.
          if (e.shiftKey) {
            if (!inside || active === first) { e.preventDefault(); last.focus(); }
          } else {
            if (!inside || active === last) { e.preventDefault(); first.focus(); }
          }
        },
        closeRail() {
          if (this.railOpen) {
            this.railOpen = false;
            document.body.classList.remove('rail-open');
            // Return focus to the hamburger (the trigger) so a keyboard user who
            // closed via Esc / scrim lands back on the control that opened it, not
            // on a now-behind-the-page rail anchor or at <body> top.
            const ham = document.querySelector('.rail-toggle');
            if (ham && typeof ham.focus === 'function') requestAnimationFrame(() => ham.focus());
          }
        },
        // Focusable controls inside the open settings modal, in DOM order.
        settingsFocusables() {
          const m = document.querySelector('.settings-modal');
          if (!m) return [];
          return Array.from(m.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
          )).filter((el) => el.offsetParent !== null || el === document.activeElement);
        },
        openSettings() {
          // Remember what opened the modal so focus can return there on close —
          // without this a keyboard user is dumped to <body> (top of page) every
          // time the settings modal closes.
          this._settingsTrigger = document.activeElement;
          this.settingsOpen = true;
          // v-if mounts the modal on the next frame; move focus INTO it then so
          // the first Tab stays trapped (focus started on the gear, outside).
          requestAnimationFrame(() => {
            const els = this.settingsFocusables();
            if (els.length) els[0].focus();
          });
        },
        closeSettings() {
          this.settingsOpen = false;
          // Return focus to the trigger (the gear) so the keyboard user lands
          // back where they were, not at the top of the document.
          const t = this._settingsTrigger;
          this._settingsTrigger = null;
          if (t && typeof t.focus === 'function' && document.contains(t)) {
            requestAnimationFrame(() => t.focus());
          }
        },
        trapSettingsTab(e) {
          const els = this.settingsFocusables();
          if (!els.length) return;
          const first = els[0];
          const last = els[els.length - 1];
          const active = document.activeElement;
          const modal = document.querySelector('.settings-modal');
          const inside = modal && modal.contains(active);
          // Wrap at the edges; if focus somehow sits outside the modal, pull it
          // back to the first (forward Tab) or last (Shift+Tab) control.
          if (e.shiftKey) {
            if (!inside || active === first) { e.preventDefault(); last.focus(); }
          } else {
            if (!inside || active === last) { e.preventDefault(); first.focus(); }
          }
        },
        // After a dispatch DISABLES the trigger button that fired it, the
        // browser drops focus to <body> (top of document) — a keyboard/SR user
        // who clicked "Launch Council" is dumped to the page top with no place
        // to resume (WCAG 2.4.3 Focus Order — the SAME "dumped to <body>"
        // failure the settings modal's openSettings/closeSettings already
        // remedy). The relevant next controls ("Open council page" / "Stop
        // council") materialize in the .launch-status region the moment busy
        // flips true; move focus to the FIRST of them so the keyboard user
        // lands on the next action, not at the top. rAF defers until v-if has
        // mounted the region (it appears the same frame busy becomes true).
        _focusOperationActions() {
          requestAnimationFrame(() => {
            const region = document.querySelector('.launch-status');
            if (!region) return;
            const target = region.querySelector('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])');
            if (target && typeof target.focus === 'function') target.focus();
          });
        },
        // For a self-disabling action button (Refresh memory / Repair
        // extension) that re-ENABLES when its status settles: restore focus to
        // it once it's focusable again, so a keyboard user lands back on the
        // control they pressed instead of <body> (WCAG 2.4.3 — the trigger-
        // return half of the dispatch-focus class). rAF defers past the DOM
        // update that flips :disabled off.
        _restoreTriggerFocus(trigger) {
          if (!trigger || typeof trigger.focus !== 'function') return;
          requestAnimationFrame(() => {
            if (document.contains(trigger) && !trigger.disabled) trigger.focus();
          });
        },
        // Dismissing a `v-if`-gated banner removes the <section> that CONTAINS the
        // Dismiss link the user just activated — so the focused element vanishes and
        // focus falls to <body>, stranding a keyboard user at the top of the page
        // (WCAG 2.4.3, the focus-loss sibling of _restoreTriggerFocus). After the DOM
        // patch removes the banner, re-home to the first present + visible + enabled
        // focusable candidate (the launch button on home, else any visible button) so
        // they land on a real control instead of nowhere.
        _focusAfterDismiss() {
          requestAnimationFrame(() => {
            const candidates = [
              '.actions .button.primary',
              '.council-rail .rail-toggle',
              'button:not([disabled])',
              'a[href]',
            ];
            for (const sel of candidates) {
              const el = document.querySelector(sel);
              if (el && typeof el.focus === 'function' && el.offsetParent !== null && !el.disabled) {
                el.focus();
                if (document.activeElement === el) return;
              }
            }
          });
        },
        get busy() {
          return !!this.operation && this.operation.status === 'running';
        },
        // Whether the council-history rail is currently SHOWN, folding the
        // breakpoint in: on desktop (>=1024) it's the persistent sidebar, visible
        // unless collapsed; on narrow it's the off-canvas drawer, hidden unless
        // opened. Drives the rail-toggle's aria-expanded so AT announces the right
        // state at every width (railIsDesktop is kept fresh by the init resize hook).
        get railExpanded() {
          return this.railIsDesktop ? !this.railCollapsed : this.railOpen;
        },
        // Mirror of Python `is_polish_task` (task_types.py). Heuristic
        // tuned for recall — better to over-suggest iteration than miss
        // a polish task. Two paths:
        //   1) literal polish phrase ("make this better", "tighten this",
        //      "any better?", …)
        //   2) ≤20 words AND contains a short imperative hint
        //      ("shorter", "simpler", "clearer", …)
        get isPolishLike() {
          const text = (this.prompt || '').toLowerCase().trim();
          if (!text) return false;
          const phrases = [
            'make this better', 'make it better',
            'make this stronger', 'make it stronger',
            'make this sharper', 'make it sharper',
            'improve this', 'polish this', 'polish it',
            'tighten this', 'tighten it',
            'rewrite this', 'refine this', 'edit this',
            'is this clearer', 'is this stronger', 'is this better',
            'any better', 'does this make sense', 'is this right',
          ];
          for (const p of phrases) {
            if (text.includes(p)) return true;
          }
          const wordCount = text.split(/\s+/).filter(Boolean).length;
          if (wordCount <= 20) {
            const hints = ['shorter', 'simpler', 'clearer', 'stronger', 'punchier', 'crisper'];
            for (const h of hints) {
              if (text.includes(h)) return true;
            }
          }
          return false;
        },
        get polishHintVisible() {
          // Only surface the hint once the user has actually typed something
          // and we recognized polish. No hint = no noise.
          return this.isPolishLike && (this.prompt || '').trim().length >= 8;
        },
        get heroTitle() {
          if (this.operation?.kind === 'council' && this.busy) {
            return 'Council in Progress';
          }
          if (this.operation?.kind === 'ingest' && this.busy) {
            return 'Ingest in Progress';
          }
          // Workspace-first: H1 names what this surface does. The tagline
          // lives in the lede below, where it has room to breathe.
          return 'Run a Council';
        },
        // Hide developer/placeholder endpoint values from the settings UI;
        // example.invalid is the RFC 6761 stub used during dev, localhost
        // is a test value. Show "Not configured" so users don't think a
        // broken URL is intentional.
        get displayedEndpoint() {
          const ep = this.telemetry?.endpoint || '';
          if (!ep) return 'Not configured';
          if (/example\.invalid|^https?:\/\/(localhost|127\.0\.0\.1)/.test(ep)) {
            return 'Not configured';
          }
          return ep;
        },
        // Whether sharing is BOTH consented AND wired — i.e. events actually leave
        // this machine. A stock public install ships NO GA4 credentials, so
        // launchpad_telemetry_state() pops `endpoint` and maybeSendTelemetry()
        // early-returns: sharing is "on" as a CONSENT state but transmits nothing.
        // The active-benefit nudge ("Trinity surfaces broken flows across users")
        // is only TRUE when this is true; otherwise it overclaims a collective
        // benefit that can't happen on this install. Mirrors displayedEndpoint's
        // dev/placeholder gate so the two agree.
        get shareIsLive() {
          if (!this.telemetry?.enabled) return false;
          return this.displayedEndpoint !== 'Not configured';
        },
        get heroLede() {
          if (this.operation?.kind === 'council' && this.busy) {
            return 'Trinity is asking every model you use. Routing JSON outcome lands when chairman finishes.';
          }
          if (this.operation?.kind === 'ingest' && this.busy) {
            return 'Trinity is refreshing your local context and getting the launchpad ready.';
          }
          return 'Ask all three. Keep what works.';
        },
        get heroMechanism() {
          if (this.busy) {
            return '';
          }
          return 'Just an MCP. No new app, no service, no API key.';
        },
        // The primary Launch button is disabled whenever ANY operation is busy —
        // but `busy` is kind-agnostic, so an INGEST (fired from the settings
        // modal's "Ingest transcripts") flips it true too. The label/tooltip used
        // to hardcode council language: while an ingest ran, the button read
        // "Council in progress…" with tooltip "A council is already running —
        // open or stop it below first" — both FALSE (no council; and an ingest
        // has no Open/Stop affordance to act on), directly contradicting the hero
        // that correctly read "Ingest in Progress" two rows up. Key the copy on
        // operation.kind so the disabled state names what's ACTUALLY running (the
        // Iter-314/315/440 self-contradicting-state-label class).
        get launchButtonLabel() {
          if (!this.busy) return 'Launch Council';
          if (this.operation?.kind === 'ingest') return 'Scanning transcripts…';
          return 'Council in progress…';
        },
        get launchButtonTitle() {
          if (!this.busy) return '';
          if (this.operation?.kind === 'ingest') {
            return 'A transcript scan is running — it finishes on its own, then you can launch a council.';
          }
          return 'A council is already running — open or stop it below first';
        },
        get operationHeading() {
          if (!this.operation) {
            return '';
          }
          if (this.operation.status === 'failed') {
            return this.operation.kind === 'ingest' ? 'Transcript ingest failed' : 'Council failed';
          }
          if (this.operation.status === 'canceled') {
            return 'Council stopped';
          }
          if (this.operation.status === 'completed') {
            // Reachable only for ingest - a completed council clearOperation()s +
            // navigates to its review page, so it never lingers in this section.
            return this.operation.kind === 'ingest' ? 'Transcript scan started' : 'Council complete';
          }
          return this.operation.kind === 'ingest' ? 'Transcript ingest running' : 'Council running';
        },
        // WCAG 4.1.3 — the single text that the persistent sr-only role=status
        // region announces. Reflects whichever dynamic status is live, with the
        // dispatch-FAILURE banner taking precedence (it's the error the user most
        // needs told): a failed Launch otherwise produced silence for AT users.
        // Empty string = nothing live → the region stays silent (no spurious
        // announcement on a quiet page).
        get liveAnnouncement() {
          if (this.dispatchBannerOpen) {
            // Lead with the failure, then the one-line remedy headline so a
            // screen-reader user hears WHY Launch did nothing + what to do.
            if (this.dispatchBannerReason === 'native-host-unavailable') {
              return 'Dispatch failed: the extension responded but the native host is not registered. Run trinity-local install-extension to wire it up.';
            }
            if (this.inExtensionPanel) {
              return 'Dispatch failed: the extension is installed but its capture host is not wired up yet. Run trinity-local install-extension.';
            }
            return 'Dispatch failed: no dispatch path is wired up. Install the Trinity browser extension to dispatch from any platform.';
          }
          if (this.launchError) {
            return this.launchError;
          }
          if (this.operation) {
            const heading = this.operationHeading;
            if (this.busy) {
              return `${heading}. ${this.currentStatusMessage}`;
            }
            return heading;
          }
          // NB: portalNotice is NOT surfaced here — it renders in its OWN visible
          // role=status region (the .portal-open-notice card), so routing it through
          // this sr-only region too would double-announce it to AT.
          // The Bulk-import probe/import outcome (success/error/dispatched). Above
          // copy so a "Detected N export(s)" / failure isn't masked by a stale copy
          // ack, below a live dispatch/council so an in-flight launch wins.
          if (this.importAnnouncement) {
            return this.importAnnouncement;
          }
          // Lowest precedence: a transient copy confirmation. Only reaches AT when
          // nothing more important (a failed dispatch, a live council) is speaking.
          if (this.copyAnnouncement) {
            return this.copyAnnouncement;
          }
          return '';
        },
        get liveCouncilUrl() {
          // In the side panel (sandbox), the council page is the sibling
          // ./live_council.html — a same-origin chrome-extension nav that
          // PRESERVES the query (unlike the file:// open the OS strips). On
          // file:///localhost, use the baked review-page base.
          const base = window.__TRINITY_HOST_FETCH__ ? './live_council.html' : this.liveReviewUrlBase;
          if (!this.operation?.statusToken || !base) {
            return '';
          }
          const params = new URLSearchParams();
          params.set('status_token', this.operation.statusToken);
          if (this.operation.label) {
            params.set('task', this.operation.label);
          }
          if (this.operation.memberOrder?.length) {
            params.set('members', this.operation.memberOrder.join(','));
          }
          return `${base}?${params.toString()}`;
        },
        get currentStatusMessage() {
          // Once Stop is requested, the cycling "running" messages are misleading
          // — pin a single honest status until the poller finalizes to canceled.
          if (this.stopRequested) {
            return 'Stopping the council…';
          }
          const messages = this.operation?.kind === 'ingest' ? this.ingestStatusMessages : this.councilStatusMessages;
          const message = messages[this.currentStatusIndex % messages.length] || 'Working...';
          if (this.operation?.kind === 'council') {
            const synthesisStatus = this.operation?.synthesis?.status;
            if (synthesisStatus === 'running') {
              return 'Synthesizing the strongest answer...';
            }
            const activeProvider = this.operation?.activeProvider;
            if (activeProvider) {
              return `${formatProviderLabel(activeProvider)}: ${message}`;
            }
          }
          return message;
        },
        get showProviderRows() {
          if (this.operation?.kind !== 'council' || this.providerStatusRows.length === 0) {
            return false;
          }
          // On a TERMINAL failure/cancel the member map is frozen at its last
          // optimistic state. When the poller gives up because the status file
          // never appeared (dispatch accepted but the council runner / native
          // host died — line ~3099 merges {status:'failed'} onto the prior
          // operation, KEEPING the all-'pending' members), every row renders
          // "Queued" + the analysis row "Waiting for member responses." UNDER a
          // "Council failed" header + "dispatch may not have started" error —
          // a contradiction (found 2026-06-02 driving the cold-start launch on
          // an empty home). Hide the grid in that case; the launchError message
          // + Dismiss are the correct terminal UX. But KEEP it when members
          // made REAL progress (some Done/Failed/Running) — a partially-run
          // council that then failed is informative, not contradictory.
          const status = this.operation?.status;
          if (status === 'failed' || status === 'canceled') {
            return this.providerStatusRows.some((row) => row.statusClass !== 'pending');
          }
          return true;
        },
        get providerStatusRows() {
          if (this.operation?.kind !== 'council') {
            return [];
          }
          const memberMap = this.operation?.members || {};
          // Drop empty/blank member keys — a member with no provider slug rendered
          // a BLANK status row (founder-caught in the running card). Never trust
          // the status file to carry only well-formed keys.
          const providers = (this.operation?.memberOrder?.length ? this.operation.memberOrder : Object.keys(memberMap))
            .filter((p) => p && String(p).trim());
          // On a TERMINAL failure/cancel the member map is FROZEN at its last
          // optimistic state — finalize_council_run_state() only flips the
          // top-level status, never the per-member statuses (council_status.py).
          // So a council stopped/failed while one member finished and the rest
          // were still in flight keeps those members at 'running'/'pending' on
          // disk. Without this rewrite the launchpad running card rendered
          // "Council stopped"/"Council failed" ABOVE rows reading "Running" and
          // "Queued" — a flat self-contradiction (a member can't still be running
          // on a dead council). The live council page already normalizes these
          // (council_review.py memberRowsFor: terminal && pending/running →
          // 'didnt-run'/'stopped'); this launchpad twin had drifted. Mirror it:
          // a never-finished member on a FAILED council "Didn't run"; on a
          // CANCELED (Stop) council it was "Stopped". 'done'/'failed' members are
          // already terminal — keep them (a stopped council can still carry one
          // landed answer). Muted 'pending' badge style, not red — a never-ran
          // member is not an error.
          const opStatus = this.operation?.status;
          const terminal = opStatus === 'failed' ? 'failed-council'
            : opStatus === 'canceled' ? 'canceled-council' : '';
          const rows = providers.map((provider) => {
            const item = memberMap[provider] || {};
            let status = item.status || 'pending';
            if (terminal && (status === 'pending' || status === 'running')) {
              status = terminal === 'failed-council' ? 'didnt-run' : 'stopped';
            }
            // Never blank: fall back to the raw slug if the label map misses it.
            const providerLabel = formatProviderLabel(provider) || String(provider);
            return {
              provider,
              label: providerLabel,
              statusLabel: status === 'done' ? 'Done' : status === 'failed' ? 'Failed' : status === 'running' ? 'Running' : status === 'didnt-run' ? "Didn't run" : status === 'stopped' ? 'Stopped' : 'Queued',
              statusClass: status === 'done' ? 'done' : status === 'failed' ? 'failed' : status === 'running' ? 'running' : 'pending',
              detail: status === 'done'
                ? (item.reasoning_summary || 'Response ready.')
                : status === 'failed'
                  ? (item.reasoning_summary || 'Provider failed.')
                  : '',
            };
          });
          const synthesisStatus = this.operation?.synthesis?.status || 'pending';
          const memberPending = rows.slice(0, providers.length).some((row) => row.statusClass === 'pending' || row.statusClass === 'running');
          rows.push({
            provider: 'analysis',
            label: 'Analysis',
            statusLabel: synthesisStatus === 'done' ? 'Done' : synthesisStatus === 'failed' ? 'Failed' : synthesisStatus === 'running' ? 'Running' : 'Queued',
            statusClass: synthesisStatus === 'done' ? 'done' : synthesisStatus === 'failed' ? 'failed' : synthesisStatus === 'running' ? 'running' : 'pending',
            detail: synthesisStatus === 'done'
              ? 'Final comparison complete.'
              : synthesisStatus === 'failed'
                ? 'Final comparison failed.'
                : synthesisStatus === 'running'
                  ? 'Comparing responses and writing the final recommendation.'
                  : memberPending
                    ? 'Waiting for member responses.'
                    : 'Ready to start final comparison.',
          });
          return rows;
        },
        copyText(value, flashKey) {
          if (!value) {
            return;
          }
          if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(value).catch(() => this._copyFallback(value));
          } else {
            // file:// + older browsers block navigator.clipboard. Use the silent
            // textarea+execCommand path (same as copyLens) — NOT a blocking
            // window.prompt the user has to dismiss.
            this._copyFallback(value);
          }
          // Optional flash-feedback: caller passes a string key that
          // template v-if expressions can compare against `copiedKey`
          // to show "✓ Copied" briefly. Resets after 2400ms — matches
          // copyHealthCommand's existing cadence so transient chips
          // animate consistently across the launchpad.
          if (flashKey) {
            this.copiedKey = flashKey;
            // Announce the copy to AT (the ✓ glyph swap is visual-only). Re-set to
            // '' first so two copies in a row re-fire the polite region.
            this.announceCopy();
            setTimeout(() => {
              if (this.copiedKey === flashKey) this.copiedKey = '';
            }, 2400);
          }
        },
        announceCopy() {
          // Push "Copied to clipboard" through the sr-only role=status region by
          // way of liveAnnouncement. Clear-then-set on a microtask so a repeated
          // copy still triggers an announcement (an unchanged aria-live string is
          // not re-announced). Self-clears so the region falls silent after.
          this.copyAnnouncement = '';
          setTimeout(() => {
            this.copyAnnouncement = 'Copied to clipboard';
            setTimeout(() => {
              if (this.copyAnnouncement === 'Copied to clipboard') this.copyAnnouncement = '';
            }, 2000);
          }, 30);
        },
        copyCodeBlock(ev, flashKey) {
          // One-tap copy for a primary command code block. Reads the rendered
          // <code> text from the wrapper (the browser decodes "&lt;your
          // question&gt;" to the literal "<your question>"), so the @click never
          // has to duplicate or escape the command string. Makes the cold-start
          // commands (council / lens / extension install) as copyable as the
          // eval + setup chips. currentTarget is the Copy <button>; the code is
          // its sibling <pre> inside the shared .code-copy-wrap.
          const wrap = ev.currentTarget.closest('.code-copy-wrap');
          const code = wrap && wrap.querySelector('code');
          this.copyText(code ? code.innerText : '', flashKey);
        },
        copyHealthCommand(issue) {
          // Build the per-issue flash key + delegate to copyText, which
          // owns the setTimeout + reset since tick #76 made the helper
          // accept (value, flashKey). The inline setTimeout that used
          // to live here was a duplicate of #76's flash logic — third
          // shape would have made principle #17 ("three inline shapes
          // = missing helper") fire on something that's already a helper.
          if (!issue || !issue.command) return;
          this.copyText(issue.command, 'health-' + issue.name + issue.status);
        },
        formatScore(score, unit) {
          if (score === null || score === undefined) {
            return '—';
          }
          if (unit.includes('score')) {
            return score.toFixed(1);
          }
          if (unit.includes('%')) {
            return score.toFixed(1);
          }
          return score.toFixed(1);
        },
        thinkingLevel(provider) {
          // Surface the variant tag the reference-eval source attaches to a
          // model name, e.g. "Adaptive Reasoning, Max Effort" or "xhigh" —
          // the bit in parens after the display name. Empty if no variant.
          const meta = this.referenceEvalsMeta && this.referenceEvalsMeta.providers;
          if (!meta || !meta[provider] || !meta[provider].name) return '';
          const m = String(meta[provider].name).match(/\(([^)]+)\)\s*$/);
          return m ? m[1] : '';
        },
        triggerShortcut(url) {
          const link = document.createElement('a');
          link.href = url;
          link.rel = 'noreferrer';
          document.body.appendChild(link);
          link.click();
          link.remove();
        },
        handleDispatchResult(result) {
          // Phase 4: surface dispatch-tier failures inline. The dispatcher
          // returns {tier: 'extension'|'shortcut'|'install-prompt', ok, response, reason}.
          // We act on three cases:
          //   - tier === 'install-prompt': show the banner (neither path works)
          //   - tier === 'extension' && reason === 'native-host-unavailable':
          //       extension found but `install-extension` wasn't run — show
          //       the install-extension hint specifically, not the generic
          //       install banner
          //   - tier === 'extension' && !ok && other error: surface to the
          //       launchError ribbon
          //
          // Stuck-launch fix (2026-05-26): when dispatch FAILED, also roll
          // back the optimistic activeOperation that beginOperation() set
          // before the dispatch attempt. Without this rollback the UI sits
          // forever showing "Council in Progress" polling a status file
          // that will never be written + Launch button stays disabled
          // (.busy never clears) — exactly the user-reported stuck state.
          // Also restore the prompt so the user can edit + retry without
          // retyping. The original launchCouncil/etc snapshot the prompt
          // into this.pendingPrompt before clearing it for this rollback.
          if (!result) return;
          const failed = (result.tier === 'install-prompt')
                      || (result.tier === 'extension' && !result.ok);
          if (failed) {
            // Roll back optimistic UI: stop polling, drop activeOperation,
            // re-enable the Launch button by clearing .busy via .operation.
            this.clearOperation();
            if (this.pendingPrompt) {
              this.prompt = this.pendingPrompt;
              this.pendingPrompt = '';
            }
            // Roll back the OTHER optimistic claim too: a portal deep-link bounce
            // (a /stats memory-viewer chip) fired showPortalNotice("Opening the
            // full dashboard … lives there") BEFORE this dispatch resolved. On a
            // FAILED open-launchpad that notice is a flat LIE — the dashboard never
            // opened — and it would sit for 6s NEXT TO the failure banner this same
            // method is about to raise, a self-contradiction (the 291 deferred-action
            // NO-FEEDBACK class: an optimistic "doing X…" that ignores {ok:false}).
            // Clear it so the honest failure banner stands alone.
            this.dismissPortalNotice();
          }
          if (result.tier === 'install-prompt') {
            this.dispatchBannerOpen = true;
            this.dispatchBannerReason = 'no-route';
          } else if (result.tier === 'extension' && !result.ok) {
            if (result.reason === 'native-host-unavailable') {
              this.dispatchBannerOpen = true;
              this.dispatchBannerReason = 'native-host-unavailable';
            } else {
              const detail = result.response?.detail || result.response?.error || 'extension error';
              this.launchError = String(detail);
            }
          }
        },
        dismissDispatchBanner() { this.dispatchBannerOpen = false; this._focusAfterDismiss(); },
        showPortalNotice(label) {
          // Immediate, honest ack that a deep-link click is bouncing to the full
          // dashboard in a browser window. The bounce opens the launchpad HOME (the
          // open-launchpad action can't carry the memory.html?file= target yet), so
          // the copy must NOT claim we navigated to the named view — only that the
          // dashboard opened and the view LIVES there (reachable from it), not in
          // this panel. Lead with where the click actually went.
          this.portalNotice = 'Opening the full dashboard (a browser window) — ' +
            (label || 'the memory viewer') + ' lives there, not in this panel.';
          if (this.portalNoticeHandle) { window.clearTimeout(this.portalNoticeHandle); }
          this.portalNoticeHandle = window.setTimeout(() => { this.portalNotice = ''; }, 6000);
        },
        dismissPortalNotice() {
          if (this.portalNoticeHandle) { window.clearTimeout(this.portalNoticeHandle); }
          this.portalNotice = '';
          this._focusAfterDismiss();
        },
        scheduleLaunchpadReload(delay = 1400) {
          window.setTimeout(() => {
            // In the side panel a bare reload self-navigates a sandboxed
            // (opaque-origin) page → "blocked by Chromium" (the whole panel
            // bricks). __trinityReload brokers the reload through the shell; on
            // file:///localhost it's a normal window.location.reload().
            __trinityReload();
          }, delay);
        },
        triggerSettingsAction(entry) {
          // Settings actions ship as {shortcutUrl, extensionKind, cliCommand}
          // and apply through the Chrome extension's Native Messaging. But these
          // are PRIVACY toggles the modal promises you can "toggle off anytime",
          // and the dispatcher is ALWAYS present (launchpad_runtime.py injects it
          // whether or not the extension is installed) — so when the extension
          // isn't reachable (not installed: the common case), the dispatch FAILS
          // and handleDispatchResult would show the generic "install our Chrome
          // extension" banner. For a privacy opt-out, "install our browser
          // extension to turn telemetry OFF" is backwards. Hand the user the
          // equivalent CLI command instead (copied) — see fallbackToSettingsCli.
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (dispatcher && entry?.extensionKind) {
            dispatcher.dispatch({
              extensionAction: { kind: entry.extensionKind },
              onResult: (r) => {
                if (r && r.ok) {
                  // The extension applied it — reflect the new server state.
                  this.settingsOpen = false;
                  this.scheduleLaunchpadReload();
                } else if (entry.cliCommand) {
                  // No reachable extension — offer the CLI command, don't
                  // dead-end a privacy toggle on the install-extension banner.
                  this.fallbackToSettingsCli(entry);
                } else {
                  this.handleDispatchResult(r);
                }
              },
            });
            return;
          }
          // No dispatcher object at all (defensive) — same CLI fallback.
          if (entry?.cliCommand) { this.fallbackToSettingsCli(entry); return; }
          this.settingsOpen = false;
          this.triggerShortcut(entry?.shortcutUrl || entry);
          this.scheduleLaunchpadReload();
        },
        fallbackToSettingsCli(entry) {
          // Keep the settings modal open + copy the CLI command so the ✓
          // confirmation renders NEXT TO the control the user clicked. The
          // feedbackKey routes the line: 'settings-cli' below the sharing toggle,
          // 'settings-reset-cli' inline beside the Reset button — without it a
          // Reset click confirmed 155px away near the toggle with toggle-worded
          // copy ("the toggle stays as-is"), reading as the WRONG action. The
          // command is COPIED, not applied here, so the displayed state stays
          // as-is until the user runs it — which keeps the shown state honest.
          this.settingsOpen = true;
          this.copyLens(entry.cliCommand, entry.feedbackKey || 'settings-cli');
        },
        toggleSharing(event) {
          event.target.checked = this.telemetry.enabled;
          const isNowEnabled = !this.telemetry.enabled;
          const entry = isNowEnabled ? this.settingsLinks.enable : this.settingsLinks.disable;
          this.triggerSettingsAction(entry);
        },
        resetAnonymousId() {
          this.triggerSettingsAction(this.settingsLinks.reset);
        },
        dismissOperation() {
          this.launchError = '';
          this.clearOperation();
          // The Dismiss button lives INSIDE the `v-if="operation || launchError"`
          // launch-status section it just cleared, so petite-vue un-mounts the
          // focused button and focus falls to <body> — the same focus-loss class as
          // dismissDispatchBanner / dismissPortalNotice (WCAG 2.4.3). Re-home it.
          this._focusAfterDismiss();
        },
        stopCurrentCouncil() {
          if (!this.operation?.statusToken || this.operation.kind !== 'council' || !this.busy) {
            return;
          }
          // Immediate ACK — the poller finalizes to 'canceled' once the host
          // writes it, but until then the button must show the click landed.
          this.stopRequested = true;
          const payload = {
            name: 'stop_council',
            args: {
              status_token: this.operation.statusToken,
            },
            metadata: {
              kind: 'stop_council',
              source: 'launchpad',
            },
          };
          // Phase 4b (council_bf1ab3f4dd70f75e residual-drift fix): route
          // through the dispatcher so Stop works cross-platform. Extension
          // tier fires `stop-council --status-token <X>`; macOS Shortcut
          // tier keeps the run_command payload as the legacy fallback.
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: {
                kind: 'stop-council',
                status_token: this.operation.statusToken,
              },
              shortcutUrl: buildShortcutUrl(payload),
              // A STOP failure must NOT route through handleDispatchResult: that
              // method's rollback (clearOperation) is the LAUNCH semantics — it
              // drops the optimistic operation that never really started. On a
              // failed STOP the council is REAL and STILL RUNNING, so clearing it
              // makes the spinner + "Open/Stop council" vanish and silently reads
              // as "stopped" — the council keeps running on the backend with the
              // UI claiming nothing's there (founder: "clicked Stop, the whole
              // council display disappeared, but it never actually stopped").
              onResult: (r) => this.handleStopResult(r),
            });
          } else {
            this.triggerShortcut(buildShortcutUrl(payload));
          }
        },
        handleStopResult(result) {
          // The STOP-specific result handler (NOT handleDispatchResult — that
          // clears the still-running council on any failed tier). On a successful
          // stop dispatch the host writes the 'canceled' status and the poller
          // finalizes the operation; nothing to do here. On a FAILED stop the
          // council is still running, so KEEP the operation alive, recover the
          // "Stop council" button from its disabled "Stopping…" state so it can be
          // retried, and surface an honest message in the running ribbon (the
          // council did NOT stop) instead of vanishing the display.
          if (!result) return;
          const stopFailed = (result.tier === 'install-prompt')
                          || (result.tier === 'extension' && !result.ok);
          if (!stopFailed) return;
          this.stopRequested = false;
          const detail = result.response?.detail || result.response?.error;
          this.launchError = detail
            ? ('Couldn’t stop the council (' + String(detail) + ') — it’s still running.')
            : 'Couldn’t stop the council — it’s still running. Retry, or open the council page to stop it there.';
        },
        beginOperation(operation) {
          this.operation = normalizeOperation({
            ...operation,
            status: 'running',
            members: Object.fromEntries((operation.memberOrder || []).map((provider) => [provider, { status: 'pending' }])),
            synthesis: { status: 'pending' },
          });
          this.launchError = '';
          this.stopRequested = false;
          // Clear the STALE dispatch-failure banner from a PRIOR operation. A
          // prior no-route / native-host-unavailable dispatch opened the
          // "No dispatch path is wired up" banner; once the user wires the host
          // and clicks Launch again — WITHOUT dismissing it — a SUCCESSFUL
          // dispatch B would otherwise render its "Council in progress…" spinner
          // RIGHT BELOW that stale banner (a self-contradiction: the council is
          // provably running, yet a banner claims no dispatch path exists). This
          // is the cross-operation stale-error leak — single-dispatch tests pass
          // because the banner is correct WITHIN one operation; only the A-fail →
          // B-succeed SEQUENCE exposes it. The banner stays an honest reactive
          // surface: if THIS dispatch also fails no-route, handleDispatchResult
          // re-opens it ("a failed click reopens this"). Cleared here, in the
          // single entry point every dispatch (council + ingest) flows through.
          this.dispatchBannerOpen = false;
          // Fresh launch: skip the synchronous first probe — the status file
          // can't exist until the dispatch we're about to fire writes it (a
          // no-route dispatch never will, and is rolled back before the 1.5s
          // interval ticks). The interval handles the real-council case.
          this.startOperationPolling(operation.statusToken, false);
        },
        stopOperationPolling() {
          if (this.statusPollHandle) {
            clearInterval(this.statusPollHandle);
            this.statusPollHandle = null;
          }
          if (this.statusRotateHandle) {
            clearInterval(this.statusRotateHandle);
            this.statusRotateHandle = null;
          }
        },
        clearOperation() {
          this.operation = null;
          this.stopRequested = false;
          this.stopOperationPolling();
        },
        startOperationPolling(token, immediate = true) {
          // `immediate` runs the first probe synchronously. That's right for the
          // RESUME path (init() re-attaching to an in-flight council whose status
          // file already exists) but WRONG for a FRESH launch: beginOperation()
          // starts polling BEFORE the dispatch fires, so the status file provably
          // cannot exist yet — the synchronous probe is a guaranteed 404 with zero
          // value (a real council writes its first 'running' frame seconds later,
          // picked up by the 1.5s interval; a no-route dispatch is rolled back by
          // handleDispatchResult before the interval's first tick). So fresh
          // launches pass immediate=false to keep a console error off the new
          // user's very first action. (Found 2026-06-06 dogfooding the cold-home
          // first launch: net::ERR_FILE_NOT_FOUND on council_status_<token>.js.)
          this.stopOperationPolling();
          this.currentStatusIndex = 0;
          // Give-up cap for a launch whose status file never materializes. An
          // optimistic dispatch whose native host / council process died or
          // never started writes NO council_status_<token>.js, so the <script>
          // probe 404s every poll. Without a cap the launchpad spins "running"
          // forever — the un-fixed sibling of the live_council pollers (see
          // council_review.py MAX_MISSING_POLLS, v1.7.194; same name on purpose
          // so a "grep every poller" sweep finds all three). ~30s at 1500ms: a
          // real council writes its first 'running' status within seconds, so a
          // sustained 404 streak means the dispatch never reached a process.
          let missingPollCount = 0;
          const MAX_MISSING_POLLS = 20;
          this.statusRotateHandle = window.setInterval(() => {
            this.currentStatusIndex++;
          }, 2500);
          const check = () => {
            loadStatusScript(token, (status) => {
              if (!this.operation) {
                return;
              }
              if (!status) {
                missingPollCount++;
                if (missingPollCount >= MAX_MISSING_POLLS) {
                  this.launchError = 'Council status unavailable — the dispatch may not have started. If a council did launch it will still appear below when it finishes.';
                  this.operation = normalizeOperation({ status: 'failed', error: this.launchError }, this.operation);
                  this.stopOperationPolling();
                }
                return;
              }
              missingPollCount = 0;
              if (status.status === 'running') {
                this.operation = normalizeOperation(status, this.operation);
                return;
              }
              if (status.status === 'failed') {
                this.launchError = status.error || 'Council failed.';
                this.operation = normalizeOperation({ ...status, status: 'failed', error: this.launchError }, this.operation);
                this.stopOperationPolling();
                return;
              }
              if (status.status === 'canceled') {
                this.launchError = status.error || 'Council stopped.';
                this.operation = normalizeOperation({ ...status, status: 'canceled', error: this.launchError }, this.operation);
                this.stopOperationPolling();
                return;
              }
              if (status.status === 'completed') {
                this.clearOperation();
                if (status.review_path) {
                  // Pass council_id so the SANDBOX (side panel) can hydrate the
                  // verdict — a token-less ./live_council.html lands blank there
                  // (the stay-on-launchpad auto-poll strand, driven 2026-06-18).
                  navigateToReviewPath(status.review_path, status.council_id);
                  return;
                }
                // No review_path — refresh in place. __trinityReload brokers
                // through the shell in the panel (a bare reload self-navigates a
                // sandboxed page → "blocked by Chromium").
                __trinityReload();
              }
            });
          };
          if (immediate) {
            check();
          }
          this.statusPollHandle = window.setInterval(check, 1500);
        },
        onPromptInput() {
          // Clear the "Please enter a task first." VALIDATION error the moment
          // the textarea becomes non-empty — otherwise the red ribbon stayed
          // pinned under a textarea the user had since filled, contradicting
          // itself ("enter a task" shown below a full task). Scoped to the
          // validation message AND to the no-operation state so a real dispatch
          // error (council failed/stopped, which carries an `operation` + its
          // own Dismiss button) isn't silently swallowed by a keystroke.
          if (this.operation) return;
          if (this.launchError === EMPTY_TASK_ERROR && (this.prompt || '').trim()) {
            this.launchError = '';
          }
        },
        launchCouncil() {
          if (this.busy) {
            return;
          }
          const prompt = this.prompt.trim();
          if (!prompt) {
            // Inline validation, consistent with every other launch error
            // (status-error ribbon) — NOT a blocking window.alert that freezes
            // the page until dismissed.
            this.launchError = EMPTY_TASK_ERROR;
            return;
          }
          this.launchError = '';  // clear any prior validation error on a real launch
          const statusToken = `launch_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
          // Snapshot before clear so handleDispatchResult can restore it on
          // dispatch failure (stuck-launch rollback, 2026-05-26).
          this.pendingPrompt = prompt;
          this.prompt = '';
          const payload = {
            name: 'launch_council',
            args: {
              task: prompt,
              goal: pageData.defaultGoal,
              members: pageData.defaultMembers,
              primary_provider: pageData.defaultPrimaryProvider,
              cwd: '.',
              status_token: statusToken,
              notify: true,
              open_browser: false,
            },
            metadata: {
              kind: 'launch_council',
              source: 'launchpad',
            },
          };
          this.beginOperation({
            kind: 'council',
            statusToken,
            label: prompt,
            memberOrder: [...pageData.defaultMembers],
          });
          // The Launch button just disabled itself (:disabled="busy") — without
          // this the keyboard user who pressed it is dropped to <body>. Move
          // focus onto the freshly-rendered "Open council page" / "Stop council"
          // controls instead (WCAG 2.4.3).
          this._focusOperationActions();
          // Phase 4: route through window.__TRINITY_DISPATCH__ which tries
          // the Chrome extension first, falls back to the macOS Shortcut,
          // and surfaces the install banner if neither is available. The
          // `extensionAction` shape matches capture_host's ACTION_ALLOWLIST
          // (kind=launch-council, task=…); `shortcutUrl` is the existing
          // path so macOS users keep working unchanged.
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: {
                kind: 'launch-council',
                task: prompt,
                goal: pageData.defaultGoal,
                primary_provider: pageData.defaultPrimaryProvider,
                // Forward the launchpad-generated status_token so the
                // CLI writes its status file to the SAME path the
                // launchpad is already polling. Without this, the CLI
                // generates its own bundle_<id> and the launchpad's
                // poll for launch_<token> 404s forever — Council card
                // sticks on "QUEUED" even though the council finished.
                // The capture-host allowlist + CLI flag already exist;
                // this just wires the launchpad side. Bug found 2026-05-26
                // via real-Chrome dogfood from claude-in-chrome MCP.
                status_token: statusToken,
              },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => this.handleDispatchResult(r),
            });
          } else {
            this.triggerShortcut(buildShortcutUrl(payload));
          }
        },
        renderMeCard() {
          if (this.busy || this.meCardStatus === 'rendering') return;
          // Render the strongest lens as a PNG + open it. Card is the
          // spec-v1 "social object". Phase 4b closes the last residual-
          // drift gap: extension tier fires `me-card --open` (the CLI
          // grew an --open flag so the host can't shell-chain); macOS
          // Shortcut tier keeps the run_command payload as fallback.
          const out = '~/.trinity/share/me_card.png';
          const command = `trinity-local me-card --out ${out} && open ${out}`;
          const payload = {
            name: 'run_command',
            args: { command },
            metadata: { kind: 'launchpad_me_card', source: 'launchpad' },
          };
          // HONEST feedback: a PNG render is a deferred, fail-able op. Show a
          // PENDING "Rendering…" cue (NOT an optimistic "✓ Rendered — opening",
          // which lied when the render failed) and resolve to done/error via a
          // DEDICATED handler so a failure surfaces NEXT TO this button — not in
          // the unrelated council-launch ribbon that handleDispatchResult uses.
          this.meCardStatus = 'rendering';
          this.meCardError = '';
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'render-me-card' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => this.handleMeCardResult(r),
            });
          } else {
            // No dispatcher (file:// without the extension): the Shortcut tier
            // can't report back, so optimistically mark done — the macOS
            // Shortcut opens the PNG itself.
            this.triggerShortcut(buildShortcutUrl(payload));
            this.meCardStatus = 'done';
            setTimeout(() => { if (this.meCardStatus === 'done') this.meCardStatus = 'idle'; }, 2400);
          }
        },
        handleMeCardResult(r) {
          // render-me-card is a discrete, fail-able op with its OWN co-located
          // feedback — it must NOT route through handleDispatchResult (that
          // surfaces failures in the council-launch ribbon, a different surface,
          // and would leave the button's optimistic "✓ Rendered" lie standing).
          const ok = !!(r && r.ok);
          if (ok) {
            this.meCardStatus = 'done';
            this.meCardError = '';
            setTimeout(() => { if (this.meCardStatus === 'done') this.meCardStatus = 'idle'; }, 2400);
            return;
          }
          const detail = (r && (r.response?.detail || r.response?.error || r.reason)) || '';
          this.meCardStatus = 'error';
          this.meCardError = detail
            ? ('Could not render the PNG card: ' + String(detail) + '.')
            : 'Could not render the PNG card.';
        },
        ingestOnce() {
          if (this.busy) {
            return;
          }
          const statusToken = `ingest_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
          // watch-once was retired 2026-05-18 (commit 07ea7da); the
          // launchpad's "Scan recent transcripts" button now fires
          // ingest-recent — the same passive cursor-based path MCP `ask`
          // hits. status-token guard removed since ingest-recent doesn't
          // write to ~/.trinity/portal_pages/status/.
          const command = `trinity-local ingest-recent`;
          const payload = {
            name: 'run_command',
            args: {
              command,
            },
            metadata: {
              kind: 'launchpad_ingest_once',
              source: 'launchpad',
            },
          };
          this.beginOperation({
            kind: 'ingest',
            statusToken,
            label: 'Scan recent transcripts once',
          });
          this.settingsOpen = false;
          // Fired from the settings modal, which we just closed — focus would
          // otherwise fall to <body>. Land the keyboard user on the ingest
          // status region's action (WCAG 2.4.3, same as launchCouncil).
          this._focusOperationActions();
          // Phase 4: Same routing as launchCouncil — extension first,
          // then Shortcut, then install prompt. The `ingest-recent`
          // allowlist entry replaces the run_command/watch-once payload
          // (the extension surface is intentionally narrower than the
          // Shortcut bridge — the host doesn't run arbitrary commands).
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'ingest-recent' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => this.handleIngestResult(r),
            });
          } else {
            this.triggerShortcut(buildShortcutUrl(payload));
          }
        },
        handleIngestResult(r) {
          // ingest-recent is FIRE-AND-FORGET: the host runs the bare command and
          // writes NO council_status file (the allowlist entry passes no
          // status_token). So the status poller beginOperation() started would
          // 404 every tick and FALSELY flip a SUCCESSFUL scan to "Transcript
          // ingest failed - Council status unavailable" at the 30s give-up cap
          // (founder-driving 2026-06-17 - a real success reported as a council-
          // worded failure). On a successful dispatch we therefore stop polling
          // and show a brief, honest "scan started" confirmation; only a genuine
          // dispatch FAILURE routes to the shared rollback/banner.
          if (r && r.ok) {
            this.stopOperationPolling();
            this.operation = normalizeOperation({
              ...this.operation,
              status: 'completed',
              label: 'New captures are being pulled into your prompt index - they\'ll appear on your next council.',
            }, this.operation);
            return;
          }
          // FAILURE. handleDispatchResult clears the operation and writes the bare
          // error to `launchError` — a string that renders ONLY in the COUNCIL
          // composer ribbon with NO heading + NO Dismiss button. For an INGEST
          // failure that orphaned a council-worded "capture host crashed" error in
          // the council composer though the user never launched a council (the
          // #242(a) cross-surface-misattribution class — same shape as the
          // Refresh-memory / lens-build Stop/Restart leaks). The fix: KEEP the
          // ingest operation in a `failed` state so the ribbon shows the ingest-
          // aware "Transcript ingest failed" heading (operationHeading) + the
          // error + a working Dismiss — co-located with its own context. Only the
          // install-prompt / native-host-unavailable cases (the dispatch couldn't
          // route at all) still delegate to handleDispatchResult's wire-up banner.
          const r2 = r || {};
          const isExtError = r2.tier === 'extension' && !r2.ok
            && r2.reason !== 'native-host-unavailable';
          if (isExtError) {
            this.stopOperationPolling();
            const detail = r2.response?.detail || r2.response?.error || 'extension error';
            this.operation = normalizeOperation({
              ...this.operation,
              status: 'failed',
              error: String(detail),
            }, this.operation);
            return;
          }
          this.handleDispatchResult(r);
        },
        // Shared resolution for the lens-build Stop/Restart buttons. On a FAILED
        // dispatch the error must land CO-LOCATED on the lens-build card (the
        // *-failed action state + lensBuildError), NOT in the council composer
        // ribbon — routing it to handleDispatchResult sent it to an unrelated
        // surface and the button silently reset to 'Stop'/'Restart', reading as a
        // no-op (the founder NO-FEEDBACK-on-failure lineage). Success resets to
        // idle after a short window; failure holds the error a beat longer.
        _resolveLensBuildAction(r, failedState) {
          const ok = !r || r.ok !== false;
          if (ok) {
            this.lensBuildError = '';
            setTimeout(() => {
              if (this.lensBuildAction === 'stopping' || this.lensBuildAction === 'restarting') {
                this.lensBuildAction = 'idle';
              }
            }, 2000);
            return;
          }
          this.lensBuildError = String((r && (r.response?.detail || r.response?.error || r.error)) || 'dispatch failed');
          this.lensBuildAction = failedState;
          setTimeout(() => {
            if (this.lensBuildAction === failedState) {
              this.lensBuildAction = 'idle';
              this.lensBuildError = '';
            }
          }, 5000);
        },
        stopLensBuild() {
          // #242(a) — write the cancel flag via the extension; the running
          // build aborts at the next stage edge (never mid-chairman-call).
          if (this.lensBuildAction !== 'idle' && this.lensBuildAction !== 'stop-failed' && this.lensBuildAction !== 'restart-failed') return;
          this.lensBuildAction = 'stopping';
          this.lensBuildError = '';
          const dispatcher = window.__TRINITY_DISPATCH__;
          const payload = { name: 'run_command', args: { command: 'trinity-local lens-stop' } };
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'lens-stop' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => this._resolveLensBuildAction(r, 'stop-failed'),
            });
          } else { this.triggerShortcut(buildShortcutUrl(payload)); this._resolveLensBuildAction(null, 'stop-failed'); }
        },
        restartLensBuild() {
          // #242(a) — re-kick the build (detached, like council-launch).
          if (this.lensBuildAction !== 'idle' && this.lensBuildAction !== 'stop-failed' && this.lensBuildAction !== 'restart-failed') return;
          this.lensBuildAction = 'restarting';
          this.lensBuildError = '';
          const dispatcher = window.__TRINITY_DISPATCH__;
          const payload = { name: 'run_command', args: { command: 'trinity-local lens --force' } };
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'lens-build' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => this._resolveLensBuildAction(r, 'restart-failed'),
            });
          } else { this.triggerShortcut(buildShortcutUrl(payload)); this._resolveLensBuildAction(null, 'restart-failed'); }
        },
        refreshMemory() {
          // Council_1f9cbecd7104f90f priority #3 — "Refresh memory" button.
          // The council's verdict on auto-running dream: "User's intent is
          // 'don't make me open a terminal' — not 'run LLM calls without
          // my knowledge.' Dream is expensive and surprising (10+ flagship
          // calls, several minutes). A single button labeled 'Refresh
          // memory' that shows a spinner and then 'Updated' satisfies
          // the intent." This is the explicit-click path.
          if (this.refreshMemoryStatus === 'running') return;
          // Capture the trigger so focus returns to it once it re-enables —
          // :disabled while 'running' drops a keyboard user's focus to <body>
          // (WCAG 2.4.3, the launchCouncil class).
          const trigger = document.activeElement;
          this.refreshMemoryStatus = 'running';
          const dispatcher = window.__TRINITY_DISPATCH__;
          const payload = {
            name: 'run_command',
            args: { command: 'trinity-local dream' },
            metadata: { kind: 'launchpad_refresh_memory', source: 'launchpad' },
          };
          const finish = (r) => {
            const ok = !r || r.ok !== false;
            this.refreshMemoryStatus = ok ? 'done' : 'failed';
            this._restoreTriggerFocus(trigger);
            // Keep the failure reason CO-LOCATED on this card — NOT in the
            // council composer ribbon (the #242(a) misattribution class). A
            // failed Refresh-memory click on /stats must not paint a council
            // failure on the home view.
            this.refreshMemoryError = ok
              ? ''
              : String((r && (r.response?.detail || r.response?.error || r.error)) || 'dispatch failed');
            setTimeout(() => {
              if (this.refreshMemoryStatus === 'done' || this.refreshMemoryStatus === 'failed') {
                this.refreshMemoryStatus = 'idle';
                this.refreshMemoryError = '';
              }
            }, ok ? 3000 : 5000);
          };
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'dream' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => {
                // Dream returns asynchronously from the host — the dispatch
                // result only confirms the subprocess was launched, not
                // that it completed. Reporting 'Updated' on launch is the
                // honest signal; the user re-renders the launchpad later
                // to see the issues clear. This matches how ingest-recent
                // behaves on this page. The failure detail stays on THIS card
                // (refreshMemoryError) instead of leaking to the council ribbon.
                finish(r);
              },
            });
          } else {
            this.triggerShortcut(buildShortcutUrl(payload));
            finish(null);
          }
        },
        repairExtension() {
          // #147 self-healing UI: dispatches extension-repair-auto. The
          // CLI runs `trinity-local extension repair --auto --json` which
          // diagnoses + detects code-patch patterns + dispatches the
          // repair council with the diagnostic context only (no HAR
          // required). Same async/launch semantics as refreshMemory —
          // the dispatch return only confirms subprocess launch, the
          // council itself runs minutes later. User re-renders the
          // launchpad to see the proposed patch when ready.
          if (this.repairExtensionStatus === 'running') return;
          // Return focus to the trigger once it re-enables (WCAG 2.4.3, the
          // launchCouncil class — :disabled while 'running' drops focus to body).
          const trigger = document.activeElement;
          this.repairExtensionStatus = 'running';
          const dispatcher = window.__TRINITY_DISPATCH__;
          const payload = {
            name: 'run_command',
            args: { command: 'trinity-local extension repair --auto --json' },
            metadata: { kind: 'launchpad_repair_extension', source: 'launchpad' },
          };
          const finish = (r) => {
            const ok = !r || r.ok !== false;
            this.repairExtensionStatus = ok ? 'done' : 'failed';
            this._restoreTriggerFocus(trigger);
            // Co-locate the failure on this card (same #242(a) class as
            // refreshMemory) — never route the error to the council ribbon.
            this.repairExtensionError = ok
              ? ''
              : String((r && (r.response?.detail || r.response?.error || r.error)) || 'dispatch failed');
            setTimeout(() => {
              if (this.repairExtensionStatus === 'done' || this.repairExtensionStatus === 'failed') {
                this.repairExtensionStatus = 'idle';
                this.repairExtensionError = '';
              }
            }, ok ? 3000 : 5000);
          };
          if (dispatcher) {
            dispatcher.dispatch({
              extensionAction: { kind: 'extension-repair-auto' },
              shortcutUrl: buildShortcutUrl(payload),
              onResult: (r) => {
                finish(r);
              },
            });
          } else {
            this.triggerShortcut(buildShortcutUrl(payload));
            finish(null);
          }
        },
        onImportPathInput() {
          // The "✓ Detected N exports" banner (and its Import button) describe the
          // path the LAST probe ran against. The moment the user edits the path to
          // something else, that banner is stale — leaving it up let the user
          // confirm an import of folder A's "3 detected exports" while Import
          // actually dispatched the full (paid) ingest against the freshly-typed,
          // UN-PROBED folder B (the confirm-before-cost contract, broken). Clear the
          // probe result (and any terminal '✓ Dispatched' badge) so the card falls
          // back to "Probe" — the user must re-probe the new path before Import is
          // offered again. Whitespace-only / no-op edits that still match the probed
          // path keep the banner (re-trimming the same path shouldn't nuke a result).
          if ((this.importPath || '') === (this.importProbedPath || '')) return;
          if (this.importProbeResult || this.importStatus === 'imported') {
            this.importProbeResult = null;
            this.importStatus = 'idle';
            this.importAnnouncement = '';
          }
        },
        probeImportPath() {
          // #148 bulk-import probe step. Fires import-export-dry-run
          // via Chrome extension dispatch. The CLI walks the path,
          // detects export types (ChatGPT / Claude.ai / Gemini Takeout),
          // and returns the list WITHOUT ingesting. User sees what was
          // found, then clicks Import to actually pull it in.
          //
          // The response is a JSON dict; we parse `r.stdout` (the host
          // returns the subprocess's stdout verbatim in the dispatch
          // result). On parse failure or {ok: false} we surface the
          // error text in the warning banner.
          if (!this.importPath || this.importStatus === 'probing') return;
          this.importStatus = 'probing';
          this.importProbeResult = null;
          this.importAnnouncement = '';
          // Bind the result we're about to render to the EXACT path it describes,
          // so a later edit can invalidate it (onImportPathInput).
          const probedPath = this.importPath;
          this.importProbedPath = probedPath;
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (!dispatcher) {
            this.importProbeResult = {
              error: 'No Chrome extension or Shortcut dispatcher available.',
              hint: 'Install the Trinity browser extension (see browser-extension/README.md) or run `trinity-local import-export --path <PATH> --dry-run` directly from the terminal.',
            };
            this.importStatus = 'idle';
            this.announceImportResult();
            return;
          }
          dispatcher.dispatch({
            extensionAction: { kind: 'import-export-dry-run', path: this.importPath },
            onResult: (r) => {
              this.importStatus = 'idle';
              if (!r || r.ok === false) {
                // The CLI prints a STRUCTURED diagnostic to stdout even when it
                // exits non-zero (the "no exports detected" case — a valid path
                // pointing at the wrong folder / an un-extracted Takeout zip is
                // the common mistake). The host's `r.error` is filled from
                // STDERR (empty here) → falls back to a useless "exit code 1",
                // discarding the actionable {error, hint} sitting in r.stdout.
                // Prefer the CLI's own diagnostic so the user is told WHAT
                // Trinity expects, not just that something exited non-zero.
                let cliErr = null;
                const rawErr = (r && r.stdout || '').trim();
                if (rawErr) {
                  try {
                    const parsed = JSON.parse(rawErr);
                    if (parsed && parsed.error) {
                      cliErr = parsed;
                    }
                  } catch (e) { /* stdout wasn't JSON — fall through */ }
                }
                this.importProbeResult = cliErr || {
                  error: (r && r.error) || 'Dispatch failed.',
                  hint: 'Make sure the path exists and points at an export file or directory.',
                };
                this.announceImportResult();
                return;
              }
              // The host returns stdout; parse the JSON the CLI prints.
              const raw = (r.stdout || '').trim();
              if (!raw) {
                this.importProbeResult = { error: 'Empty response from import-export probe.' };
                this.announceImportResult();
                return;
              }
              try {
                this.importProbeResult = JSON.parse(raw);
              } catch (e) {
                this.importProbeResult = { error: 'Could not parse probe output as JSON: ' + e.message };
              }
              this.announceImportResult();
            },
          });
        },
        announceImportResult() {
          // WCAG 4.1.3 — mirror the just-set importProbeResult / importStatus into
          // the sr-only role=status region (via liveAnnouncement) so a screen-reader
          // user hears the Probe/Import outcome. The visible banner is a plain <div>
          // with no live-region wiring, so without this the result is silent to AT.
          const r = this.importProbeResult;
          if (this.importStatus === 'imported') {
            this.importAnnouncement = 'Import dispatched. Trinity is ingesting the export in the background.';
            return;
          }
          if (r && r.error) {
            this.importAnnouncement = 'Import probe failed: ' + r.error;
            return;
          }
          if (r && Array.isArray(r.detected)) {
            const n = r.detected.length;
            this.importAnnouncement = 'Detected ' + n + ' export' + (n === 1 ? '' : 's') + '. Click Import to ingest.';
            return;
          }
          this.importAnnouncement = '';
        },
        confirmImport() {
          // #148 full-ingest step. Fires import-export (no --dry-run)
          // with the same path the probe was run against. Async: the
          // dispatch return only confirms subprocess launch. User
          // re-renders the launchpad later to see the new captures
          // surfaced in the Browser-capture card / cortex.
          if (!this.importPath || this.importStatus === 'importing') return;
          this.importStatus = 'importing';
          const dispatcher = window.__TRINITY_DISPATCH__;
          if (!dispatcher) {
            // HONEST no-dispatcher feedback — mirror probeImportPath's sibling
            // branch. The dispatcher can vanish between a successful Probe and
            // this Import (extension disabled/reloaded), and a silent rollback to
            // "Import N source(s)" read as a dead button (NO-FEEDBACK class). Tell
            // the user WHY + the exact terminal command (no --dry-run — the full
            // ingest), co-located on this card.
            this.importStatus = 'idle';
            this.importProbeResult = {
              error: 'No Chrome extension or Shortcut dispatcher available.',
              hint: 'Install the Trinity browser extension (see browser-extension/README.md) or run `trinity-local import-export --path ' + this.importPath + '` directly from the terminal.',
            };
            this.announceImportResult();
            return;
          }
          dispatcher.dispatch({
            extensionAction: { kind: 'import-export', path: this.importPath },
            onResult: (r) => {
              if (r && r.ok !== false) {
                this.importStatus = 'imported';
                this.announceImportResult();
                setTimeout(() => {
                  if (this.importStatus === 'imported') {
                    this.importStatus = 'idle';
                  }
                }, 4000);
              } else {
                this.importStatus = 'idle';
                this.importProbeResult = {
                  error: (r && r.error) || 'Import dispatch failed.',
                };
                this.announceImportResult();
              }
            },
          });
        },
      };
    }

    createApp({ LaunchpadApp, pageData }).mount();
    maybeSendTelemetry();
    renderChart();

    // ── Council-rail title filter (founder 2026-06-01): with the full
    // history in the sidebar (305+ councils), a search box keeps it usable.
    // Pure client-side substring match over the server-emitted data-title
    // attrs — no petite-vue store, no server round-trip; recency is implicit
    // in the server-emitted order. The Rated/Unrated/All chips were sunset
    // 2026-05-21 (chairman picks are the verdict); the old in-page card grid +
    // its pagination were removed 2026-06-06 (the rail is the single home now).
    (function() {
      const input = document.getElementById('rail-filter');
      if (!input) return;
      const rows = Array.from(document.querySelectorAll('.council-rail .rail-council'));
      // ORPHAN-CONTROL GUARD (founder symptom: cold side panel showed a live
      // "Search councils…" box above "No councils yet" — typing did nothing,
      // and the no-match line is structurally suppressed when rows.length===0,
      // so the box read as a dead control on the first-impression surface).
      // A filter over an EMPTY collection has nothing to filter: hide it so the
      // empty-state message stands alone. Re-shows automatically once a council
      // lands and the rail re-renders with rows.
      if (rows.length === 0) { input.style.display = 'none'; return; }
      const noMatch = document.getElementById('rail-no-match');
      // WCAG 4.1.3 — the visual no-match line is toggled via display, which AT does
      // not reliably announce (the static string never CHANGES). Mirror the filter
      // RESULT into a persistent role=status region whose text mutates, so a
      // keyboard / screen-reader user hears "No councils match" when the list empties
      // and "N of M councils" when matches return (an empty filter clears it — the
      // unfiltered full list is the baseline, not a status worth speaking). The
      // text-clear-then-set guards re-announcing the SAME count on a fresh keystroke.
      const srStatus = document.getElementById('rail-filter-status');
      let __railSrTimer = null;
      function announceRail(msg) {
        if (!srStatus) return;
        srStatus.textContent = '';
        if (!msg) return;
        Promise.resolve().then(function() { srStatus.textContent = msg; });
        if (__railSrTimer) clearTimeout(__railSrTimer);
        __railSrTimer = setTimeout(function() { srStatus.textContent = ''; }, 3000);
      }
      input.addEventListener('input', function() {
        const q = (input.value || '').trim().toLowerCase();
        let shown = 0;
        for (const row of rows) {
          const hit = !q || (row.getAttribute('data-title') || '').includes(q);
          row.style.display = hit ? '' : 'none';
          if (hit) shown++;
        }
        if (noMatch) noMatch.style.display = (rows.length && shown === 0) ? '' : 'none';
        if (!q) { announceRail(''); }
        else if (shown === 0) { announceRail('No councils match that search.'); }
        else { announceRail(shown + ' of ' + rows.length + ' councils match.'); }
      });
    })();
})();
