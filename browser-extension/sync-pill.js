// Trinity Local — sync-pill.js (ISOLATED world)
//
// In-provider sync UI. On each page load:
//
//   1) Ask background.js whether a current-tab sync is in flight
//      (background owns the state machine — it survives our content-
//      script being destroyed on every navigation).
//   2) If yes: render the in-flight progress pill.
//   3) If no: query capture-host for sidebar-vs-on-disk diff. If
//      missing > 0, render the "⠕ N to sync" pill. Click starts the
//      current-tab sync.
//
// History:
// - 4b4b05f: direct fetch from content-script (failed — auth headers)
// - b09dadb: iframes (failed for gemini — bundle detects iframe ctx)
// - c11dc9e: background tabs (works but visible tab thrashing)
// - THIS:    current-tab orchestrated by background.js (no tab spam;
//            user watches their own tab tour their conversations,
//            ends back where they started)

(() => {
  // Bail in iframes — all_frames:true means we inject everywhere,
  // including any embedded provider frames. Pill only belongs in
  // the top-level page.
  if (window !== window.top) return;

  const PROVIDER_HOSTS = {
    "claude.ai": "claude",
    "chatgpt.com": "chatgpt",
    "chat.openai.com": "chatgpt",
    "gemini.google.com": "gemini",
  };

  const provider = PROVIDER_HOSTS[location.hostname];
  if (!provider) return;
  if (document.getElementById("__trinity_sync_pill__")) return;

  const PILL_ID = "__trinity_sync_pill__";
  const POLL_INTERVAL_MS = 60_000;
  const FIRST_POLL_DELAY_MS = 3_000;
  const SYNC_PROGRESS_POLL_MS = 750;     // when sync is in-flight, refresh pill
  const SYNCED_FADE_AFTER_MS = 4_000;

  function queryStatus() {
    return new Promise((resolve) => {
      if (!chrome?.runtime?.id) return resolve(null);
      try {
        chrome.runtime.sendMessage(
          { type: "query", query_kind: "sync_status", provider },
          (response) => {
            if (chrome.runtime.lastError) return resolve(null);
            resolve(response || null);
          },
        );
      } catch { resolve(null); }
    });
  }

  function getCurrentSyncState() {
    return new Promise((resolve) => {
      if (!chrome?.runtime?.id) return resolve(null);
      try {
        chrome.runtime.sendMessage(
          { type: "get_current_tab_sync_state" },
          (response) => {
            if (chrome.runtime.lastError) return resolve(null);
            resolve(response || null);
          },
        );
      } catch { resolve(null); }
    });
  }

  function startCurrentTabSync(missing_ids) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(
          { type: "start_current_tab_sync", provider, missing_ids },
          (response) => resolve(!!response?.ok),
        );
      } catch { resolve(false); }
    });
  }

  function cancelCurrentTabSync() {
    try {
      chrome.runtime.sendMessage({ type: "cancel_current_tab_sync" }, () => {});
    } catch { /* ignore */ }
  }

  function ensurePillStyles() {
    if (document.getElementById("__trinity_sync_pill_style__")) return;
    const style = document.createElement("style");
    style.id = "__trinity_sync_pill_style__";
    style.textContent = `
      #${PILL_ID} {
        /* Bottom-LEFT, not bottom-right. At z-index 2147483647 (the max, so
           nothing on the provider page can paint over us) a bottom-RIGHT pill
           sat directly on top of the provider's SEND button — claude.ai /
           chatgpt.com / gemini.google.com all anchor Send to the bottom-right of
           a bottom-fixed composer, and on narrow/mobile widths that composer is
           full-bleed so Send reaches the viewport's bottom-right corner. Driven
           2026-06-23: at 393/375/320 the pill overlapped the send button by
           ~1737px², elementFromPoint at the send center returned the PILL, and a
           tap on Send fired a Trinity sync instead of sending the user's message
           (send_clicks:0, start_current_tab_sync:1) — a fixed top-z overlay
           hijacking the host page's primary control. The bottom-left corner is
           clear of the send button on all three providers (and the left history
           sidebar collapses behind a hamburger at these widths), so Send is
           never obscured. */
        position: fixed; bottom: 16px; left: 16px; z-index: 2147483647;
        /* Calm/Muted-Teal palette (design_system.py): deep brand teal fill
           (action_primary_hover #34666b — white text 6.31:1, comfortably AA) +
           near-white action text. */
        background: #34666b; color: #fbfdfc;
        font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        padding: 8px 14px; border-radius: 999px;
        border: 1px solid rgba(251, 253, 252, 0.30);
        cursor: pointer; user-select: none;
        box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        opacity: 0.92; transition: opacity 0.15s ease;
        display: inline-flex; align-items: center; gap: 8px;
      }
      #${PILL_ID}:hover { opacity: 1.0; }
      /* Visible keyboard focus (WCAG 2.4.7): the idle pill is a button widget
         (role=button + tabindex=0). Without a ring a keyboard user can't see
         where focus is on top of the provider page. High-contrast near-white
         outline reads on the deep-teal fill. */
      #${PILL_ID}:focus-visible {
        opacity: 1.0;
        outline: 3px solid #fbfdfc;
        outline-offset: 2px;
      }
      #${PILL_ID}[hidden] { display: none !important; }
      #${PILL_ID} .__trinity_cancel {
        /* Recessed lighter chip on the deep-teal pill. The white-tint alpha is
           held LOW so the near-white "Cancel" label (13px body, color:inherit
           #fbfdfc) clears WCAG AA 4.5:1 over the COMPOSITED chip background:
           0.16 over #34666b composited to #547e82 = 4.38:1 (sub-AA — driven
           2026-06-22, the un-fixed content-script sibling of the white-on-teal
           sweep). At 0.10 the chip is #547e7a→5.01:1; the 0.14 hover is 4.58:1.
           Both clear AA — raising the tint past ~0.14 re-sinks the label below
           the floor (lighter bg ⇒ lower white-text contrast). */
        background: rgba(251, 253, 252, 0.10); border: 0; color: inherit;
        font: inherit; padding: 0 8px; border-radius: 999px; cursor: pointer;
      }
      #${PILL_ID} .__trinity_cancel:hover { background: rgba(251, 253, 252, 0.14); }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  function ensurePillEl() {
    let el = document.getElementById(PILL_ID);
    if (el) return el;
    ensurePillStyles();
    el = document.createElement("div");
    el.id = PILL_ID;
    el.hidden = true;
    el.setAttribute("role", "status");
    (document.body || document.documentElement).appendChild(el);
    return el;
  }

  function renderIdle(missingCount, missingIds) {
    const el = ensurePillEl();
    if (missingCount <= 0) { el.hidden = true; return; }
    el.replaceChildren();  // clear (no innerHTML; XSS-safe)
    el.textContent = `⠕ ${missingCount} to sync`;
    el.title = "Trinity: click (or Tab + Enter) to sync missing threads into this tab";
    el.style.cursor = "pointer";
    // Keyboard-operable trigger (WCAG 2.1.1 + 4.1.2): the idle pill is the
    // ONLY way to start a current-tab sync, and a bare <div role=status> with
    // an onclick is MOUSE-ONLY — a keyboard user on claude.ai/chatgpt.com/
    // gemini.google.com can never reach or fire it (driven 2026-06-21: 40 Tabs
    // never land on it, Enter/Space dispatch nothing). Promote the idle state
    // to a real button widget — focusable (tabindex=0), self-describing
    // (role=button + aria-label), and Enter/Space-activatable. (role flips back
    // to status in renderActive/renderJustFinished — those states are a live
    // progress announcement, not a control.)
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("aria-label", `Sync ${missingCount} missing thread${missingCount === 1 ? "" : "s"} into this tab`);
    // Single activation path so click AND keyboard fire the SAME sync — no
    // second source of truth. Disarm both listeners on first fire (one-shot,
    // mirrors the old onclick=null) since the tab navigates away immediately.
    const startSync = async () => {
      el.onclick = null;
      el.onkeydown = null;
      el.removeAttribute("tabindex");
      el.textContent = `⠕ Starting…`;
      await startCurrentTabSync(missingIds);
      // Background will navigate this tab almost immediately; pill
      // re-renders in the next page-load lifecycle.
    };
    el.onclick = startSync;
    el.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
        e.preventDefault();
        startSync();
      }
    };
    el.hidden = false;
  }

  function renderActive(state) {
    const el = ensurePillEl();
    el.replaceChildren();
    el.style.cursor = "default";
    el.removeAttribute("title");
    // Back to a live status announcement (not a control) while sync is in
    // flight — drop the idle button affordances so AT doesn't read the
    // progress text as a focusable button. (Cancel below is a real <button>.)
    el.setAttribute("role", "status");
    el.removeAttribute("tabindex");
    el.removeAttribute("aria-label");
    el.onkeydown = null;
    el.onclick = null;
    el.appendChild(document.createTextNode(
      `⠕ Syncing ${state.landed}/${state.total}…`,
    ));
    const cancelBtn = document.createElement("button");
    cancelBtn.className = "__trinity_cancel";
    cancelBtn.textContent = "Cancel";
    cancelBtn.onclick = (e) => { e.stopPropagation(); cancelCurrentTabSync(); };
    el.appendChild(cancelBtn);
    el.hidden = false;
  }

  function renderJustFinished(state) {
    const el = ensurePillEl();
    el.replaceChildren();
    el.style.cursor = "default";
    el.setAttribute("role", "status");
    el.removeAttribute("tabindex");
    el.removeAttribute("aria-label");
    el.onkeydown = null;
    el.onclick = null;
    // Honest terminal feedback (NO-FEEDBACK / misleading-success guard, driven
    // 2026-06-23): runCurrentTabSync only increments `landed` when a thread's
    // capture actually lands; a nav timeout (12s) or a host that never writes the
    // file leaves it un-incremented but the loop still finishes with finished_at
    // set. So a wholly-FAILED sync (0 of N captured) reaches this state with
    // landed===0 — and the old unconditional "⠕ ✓ Synced 0/N" painted a SUCCESS
    // checkmark (announced verbatim to a screen reader via role=status) when
    // nothing synced. Lead with the answer: 0 landed is a failure, not a ✓.
    //
    // CANCEL is NOT a failure (driven 2026-06-23): a user who clicks Cancel mid-run
    // leaves the SAME terminal shape (active:false, finished_at set, landed<total) as
    // a failure/partial — background.js now exposes `canceled` so we can tell them
    // apart. Without this branch a canceled sync read "⚠ Sync failed — 0/N captured"
    // (a deliberate cancel mislabeled as a FAILURE) or a bare "Synced 2/5" (a cancel
    // framed as a partial sync). Lead with the answer: the user canceled.
    if (state.canceled) {
      el.textContent = state.landed > 0
        ? `⠕ Sync canceled — ${state.landed}/${state.total} captured`
        : `⠕ Sync canceled`;
    } else if (state.landed <= 0) {
      el.textContent = `⠕ ⚠ Sync failed — 0/${state.total} captured`;
    } else if (state.landed === state.total) {
      el.textContent = `⠕ ✓ Synced ${state.landed}`;
    } else {
      // Partial: some landed, some didn't — keep the count but don't imply a
      // clean success. The slash count already tells the user N of M landed.
      el.textContent = `⠕ Synced ${state.landed}/${state.total}`;
    }
    el.hidden = false;
    setTimeout(() => {
      const live = document.getElementById(PILL_ID);
      if (live) live.hidden = true;
    }, SYNCED_FADE_AFTER_MS);
  }

  async function tick() {
    // 1) Sync in flight? Render progress, schedule fast re-tick.
    const syncState = await getCurrentSyncState();
    if (syncState && syncState.active && syncState.provider === provider) {
      renderActive(syncState);
      setTimeout(tick, SYNC_PROGRESS_POLL_MS);
      return;
    }
    // 1b) Recently finished? Show the success banner briefly, once.
    if (
      syncState && !syncState.active && syncState.provider === provider &&
      syncState.finished_at && (Date.now() - syncState.finished_at < 10_000) &&
      syncState.total > 0
    ) {
      renderJustFinished(syncState);
      return;
    }
    // 2) No sync in flight — render the idle "N to sync" pill from
    //    the sidebar-vs-on-disk diff.
    const status = await queryStatus();
    if (!status || !status.ok) return;
    renderIdle(Number(status.missing_count) || 0, status.missing_ids || []);
  }

  setTimeout(() => {
    tick();
    setInterval(tick, POLL_INTERVAL_MS);
  }, FIRST_POLL_DELAY_MS);
})();
