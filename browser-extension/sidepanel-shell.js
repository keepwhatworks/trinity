// Side-panel shell — picks the view based on whether the local Trinity host (CLI)
// is reachable:
//   • no host  → the STANDALONE "download your transcripts" view (the extension is
//                a good tool on its own; capture is mirrored to chrome.storage.local
//                by background.js regardless, so there's always something to export)
//   • host     → the full launchpad iframe (councils, lens, routing)
// CSP-safe (external script, addEventListener — no inline handlers / eval). The
// sandboxed launchpad iframe stays untouched; this only chooses what to show.
(function () {
  const $ = (id) => document.getElementById(id);

  // Every host query MUST be bounded — chrome.runtime.sendMessage's callback only
  // fires when background.js responds, but if the native host is slow or wedged
  // and background keeps the message channel open, the callback may NEVER fire,
  // leaving the loading spinner stuck forever (founder-caught: the panel "takes a
  // while to load"; a slow/unreachable host must DEGRADE, not hang). Race every
  // send against a timeout so the shell always resolves to a view.
  const TIMEOUT = Symbol("host-timeout");
  function send(message, timeoutMs = 4000) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
      const timer = setTimeout(() => finish(TIMEOUT), timeoutMs);
      try {
        chrome.runtime.sendMessage(message, (r) => {
          clearTimeout(timer);
          finish(chrome.runtime.lastError ? null : r);
        });
      } catch {
        clearTimeout(timer);
        finish(null);
      }
    });
  }

  // 3-state host detection so the shell distinguishes a CLEAN "no host" (→ the
  // standalone transcript tool) from an ambiguous TIMEOUT. A timeout is more
  // often a present-but-slow host than a missing one (a missing host responds
  // fast with native-host-unavailable), so on timeout we prefer the launchpad —
  // a CLI user who hit host-spawn latency lands on their cockpit, not standalone.
  async function detectHost() {
    // `ping` is a CHEAP reachability check — it does NOT build the launchpad
    // payload. The shell only needs to know the host is up to choose launchpad-
    // vs-standalone; the launchpad iframe builds the real payload ONCE. (This used
    // to query `launchpad_data`, so the host built the FULL payload TWICE per open
    // — shell + iframe — lingering the spinner on a big corpus, founder-caught.)
    const r = await send({ type: "query", query_kind: "ping" });
    if (r === TIMEOUT) return "timeout";
    return (r && r.ok) ? "present" : "absent";
  }

  const PROVIDER_LABELS = { claude: "Claude", chatgpt: "ChatGPT", gemini: "Gemini" };

  function renderProviders(providers) {
    const slot = $("cap-providers");
    slot.textContent = "";
    const keys = Object.keys(providers || {});
    if (!keys.length) {
      const span = document.createElement("span");
      span.className = "empty";
      span.textContent = "Browse Claude, ChatGPT or Gemini to start capturing.";
      slot.appendChild(span);
      return;
    }
    for (const k of keys) {
      const span = document.createElement("span");
      span.className = "prov";
      const b = document.createElement("b");
      b.textContent = String(providers[k]);              // textContent — never innerHTML user/count data
      span.appendChild(b);
      span.appendChild(document.createTextNode(" " + (PROVIDER_LABELS[k] || k)));
      slot.appendChild(span);
    }
  }

  // A tip's `cta` is "the command / action the tip points at" — but it was
  // rendered as a bare, teal+bold <span> that LOOKED like a link/action yet had
  // no href, no handler, no copy: clicking "keepwhatworks.com" (the install-cli
  // tip surfaced to an extension-only user) did NOTHING — a dead pseudo-link on
  // the one conversion CTA the standalone view shows. Found 2026-06-22 driving
  // the real no-host side panel. Make the CTA actually do what it advertises:
  //   • a URL/domain ("keepwhatworks.com") → a real <a href> that OPENS the
  //     install site (the conversion path for a CLI-less user), keyboard- and
  //     pointer-actionable.
  //   • a shell command ("trinity-local lens-setup") → a COPY button with a ✓
  //     ack (the launchpad's copyCodeBlock / popup's copy-setup-cmds pattern),
  //     since the user pastes it into a terminal — there's nothing to navigate.
  // A bare domain (no scheme, no slash) is treated as https://<domain>.
  const _DOMAIN_RX = /^(?:https?:\/\/)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:\/\S*)?$/i;

  function buildCta(text) {
    const cta = String(text || "").trim();
    if (_DOMAIN_RX.test(cta) && !/\s/.test(cta)) {
      const href = /^https?:\/\//i.test(cta) ? cta : "https://" + cta;
      const a = document.createElement("a");
      a.className = "cta cta-link";
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = cta;
      return a;
    }
    // Command-shaped CTA → copy-to-clipboard with a ✓ ack (WCAG 4.1.3: the ack
    // is also pushed through an aria-live region so a screen-reader user hears it).
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cta cta-copy";
    btn.textContent = cta;
    btn.title = "Copy to clipboard";
    btn.setAttribute("aria-label", "Copy command: " + cta);
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(cta);
        const prev = btn.textContent;
        btn.textContent = "✓ Copied";
        announceTip("Copied: " + cta);
        setTimeout(() => { btn.textContent = prev; }, 1800);
      } catch {
        btn.textContent = "Copy blocked — select manually";
        announceTip("Clipboard blocked — select the command manually");
      }
    });
    return btn;
  }

  // WCAG 4.1.3 Status Messages — announce a copy ack the user gets WITHOUT focus
  // moving (the button's own text flipping to "✓ Copied" is mute to AT). Reuses a
  // single persistent visually-hidden region.
  function announceTip(text) {
    let region = document.getElementById("sidepanel-sr-status");
    if (!region) {
      region = document.createElement("div");
      region.id = "sidepanel-sr-status";
      region.style.cssText =
        "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;";
      region.setAttribute("role", "status");
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "true");
      document.body.appendChild(region);
    }
    if (region.textContent === text) region.textContent = "";
    region.textContent = text;
  }

  async function renderTip(state) {
    const slot = $("tip-slot");
    slot.textContent = "";
    const r = await send({ type: "next_tip", state });
    const tip = r && r.tip;
    if (!tip) return;
    const box = document.createElement("div");
    box.className = "tip";
    const x = document.createElement("button");
    x.className = "x"; x.title = "Dismiss tip"; x.setAttribute("aria-label", "Dismiss tip"); x.textContent = "×";
    x.addEventListener("click", async () => {
      await send({ type: "dismiss_tip", key: tip.key });
      renderTip(state);
    });
    box.appendChild(x);
    box.appendChild(document.createTextNode(tip.message + (tip.cta ? "  " : "")));
    if (tip.cta) {
      box.appendChild(buildCta(tip.cta));
    }
    slot.appendChild(box);
  }

  async function downloadTranscripts() {
    const btn = $("download-btn");
    const status = $("dl-status");
    btn.disabled = true;
    status.className = "status";
    status.textContent = "Bundling…";
    const r = await send({ type: "get_transcript_bundle" });
    if (!r || !r.ok || !r.bundle || !r.bundle.count) {
      status.textContent = "Nothing captured yet — browse Claude / ChatGPT / Gemini first.";
      btn.disabled = false;
      return;
    }
    try {
      const blob = new Blob([JSON.stringify(r.bundle, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      chrome.downloads.download({ url, filename: r.filename, saveAs: false }, () => {
        if (chrome.runtime.lastError) {
          status.textContent = "Download failed: " + chrome.runtime.lastError.message;
        } else {
          status.className = "status ok";
          status.textContent = `Saved ${r.bundle.count} conversation(s) → Downloads/${r.filename}`;
        }
        btn.disabled = false;
        // Revoke after a beat so Chrome has finished reading the blob.
        setTimeout(() => { try { URL.revokeObjectURL(url); } catch {} }, 60000);
      });
    } catch (e) {
      status.textContent = "Download failed: " + e;
      btn.disabled = false;
    }
  }

  async function initStandalone() {
    const raw = await send({ type: "get_capture_stats" });
    const stats = (raw && typeof raw === "object") ? raw : { count: 0, providers: {} };
    const count = stats.count || 0;
    $("cap-count").textContent = String(count);
    // Pluralize the headline noun off the SAME count — a static
    // "conversations captured" read "1 conversations captured" on the
    // first-capture path. Mirrors the launchpad's `council{count===1?'':'s'}`.
    $("cap-count-label").textContent =
      (count === 1 ? "conversation" : "conversations") + " captured";
    renderProviders(stats.providers);
    const dl = $("download-btn");
    dl.disabled = !(stats.count > 0);
    dl.addEventListener("click", downloadTranscripts);
    await renderTip({ captured: stats.count || 0, hostPresent: false, lensBuilt: false });
    $("standalone").hidden = false;
  }

  (async function main() {
    // The spinner stays up until the content is ACTUALLY ready — not merely until
    // host-detection resolves. host detection is now a CHEAP `ping` (fast), so the
    // long pole is the launchpad iframe building its payload + mounting petite-vue.
    // If we revealed #app on ping (fast), the v-cloak'd iframe would show BLANK for
    // the seconds it takes to mount (the founder's "still loading" report). So we
    // keep #loading until the iframe posts `__trinityMounted` (v-cloak removed),
    // with a fallback so it can never get stuck (the timeout lesson).
    const loader = $("loading");
    const app = $("app");
    let revealed = false;
    const reveal = () => {
      if (revealed) return;
      revealed = true;
      if (app) app.hidden = false;
      if (loader) loader.hidden = true;
    };
    // The iframe (sandbox/_bridge.js → launchpad_runtime) posts __trinityMounted
    // the moment petite-vue mounts. Reveal then.
    window.addEventListener("message", (e) => {
      if (app && e.source === app.contentWindow && e.data && e.data.__trinityMounted === true) {
        reveal();
      }
    });
    const host = await detectHost();
    if (host === "absent") {
      await initStandalone();        // clean no-host → standalone transcript tool
      if (loader) loader.hidden = true;
      return;
    }
    // present / timeout → the launchpad iframe (already loading since page load) is
    // the view. Hold the spinner until it mounts; fall back after 12s so a wedged
    // mount can never trap the spinner.
    setTimeout(reveal, 12000);
  })();
})();
