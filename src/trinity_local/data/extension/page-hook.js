// Trinity Local — page-hook.js (MAIN world)
//
// Wraps window.fetch so we can observe the page's own API calls to
// Anthropic / OpenAI / Google streaming endpoints. Per the v1.6 spec
// validation log (T-1, 2026-05-14): EventSource is NOT used by
// claude.ai — all streaming goes through fetch() + response.body
// .getReader() because the completion endpoints are POST and
// EventSource is GET-only per the W3C spec.
//
// This file runs in the MAIN world (per manifest.json content_scripts
// entry with "world": "MAIN", Chrome 111+). The ISOLATED-world
// content-script.js cannot intercept page-level network calls.
//
// Capture strategy: wrap fetch. On streaming responses, tee the body
// so the page sees the original stream AND we accumulate a copy.
// When the stream completes, postMessage the accumulated payload to
// the ISOLATED-world content script via window.postMessage. Content
// script forwards to the background service worker.

(() => {
  if (window.__TRINITY_HOOK_INSTALLED__) return;
  window.__TRINITY_HOOK_INSTALLED__ = true;

  const PROVIDER_PATTERNS = [
    { provider: "claude", host: "claude.ai", streamPath: "/completion" },
    // chatgpt.com streaming endpoint moved to /backend-api/f/conversation
    // (the /f/ segment was added 2026-05 — live DevTools probe caught it
    // returning 503 then 200 in capture-host audit log). Keep the legacy
    // /backend-api/conversation entry so older accounts on the prior
    // rollout still capture; the canonical GET regex below stays
    // anchored to /backend-api/conversation/<id> which is untouched.
    { provider: "chatgpt", host: "chatgpt.com", streamPath: "/backend-api/f/conversation" },
    { provider: "chatgpt", host: "chatgpt.com", streamPath: "/backend-api/conversation" },
    { provider: "chatgpt", host: "chat.openai.com", streamPath: "/backend-api/f/conversation" },
    { provider: "chatgpt", host: "chat.openai.com", streamPath: "/backend-api/conversation" },
    { provider: "gemini", host: "gemini.google.com", streamPath: "/_/BardChatUi/data/batchexecute" },
    // StreamGenerate is the actual conversation streaming endpoint.
    // batchexecute carries telemetry/control RPCs (bard_activity_enabled,
    // etc.); the real prompt+response flows through StreamGenerate. Added
    // 2026-05-23 after `trinity-local extension repair --har` council
    // (bundle extrepair_a6688c43b62bbbe7): Claude + Codex + Antigravity
    // chairman-synthesized this as the missing pattern explaining why
    // gemini captures landed with empty assistant_text. classifyRequest's
    // includes() check matches without regex changes — same adapter.
    { provider: "gemini", host: "gemini.google.com", streamPath: "/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate" },
  ];

  function classifyRequest(url, method) {
    try {
      const u = new URL(url, location.href);
      for (const pat of PROVIDER_PATTERNS) {
        if (u.hostname === pat.host || location.hostname === pat.host) {
          if (u.pathname.includes(pat.streamPath) || u.pathname === pat.streamPath) {
            return { provider: pat.provider, kind: "stream" };
          }
          // Canonical fetch endpoints — non-streaming GETs that return
          // the full conversation tree. claude.ai: /chat_conversations/<id>
          // (no trailing /completion). chatgpt.com: /backend-api/conversation/<id>.
          if (pat.provider === "claude" && /\/chat_conversations\/[^/]+$/.test(u.pathname)) {
            return { provider: "claude", kind: "canonical" };
          }
          if (pat.provider === "chatgpt" && /\/backend-api\/conversation\/[^/]+$/.test(u.pathname) && method === "GET") {
            return { provider: "chatgpt", kind: "canonical" };
          }
          // Sidebar-list endpoints — return arrays of {conv_id, title}
          // for the recent-conversations sidebar. Used by the auto-sync
          // diff pipeline: compare this list against on-disk captures
          // → mobile-to-desktop "what's missing" count. claude.ai:
          // GET /api/organizations/<org>/chat_conversations?limit=N
          // (no /<id> suffix). chatgpt.com:
          // GET /backend-api/conversations?limit=N. Both are paginated
          // listing endpoints distinct from the canonical single-thread
          // fetch above. Captured under kind="sidebar_list" so the
          // capture host writes them to a sentinel filename
          // (_sidebar.json) without conflating with per-thread state.
          // Claude.ai upgraded from /chat_conversations to
          // /chat_conversations_v2 (caught live 2026-05-23: original
          // pattern matched nothing, v2 is the current endpoint).
          // Match both — legacy left in case Anthropic does a rolling
          // cohort migration and some accounts still hit v1.
          // EXCLUDE ?starred=true: claude.ai fires a SEPARATE v2 call for
          // the "Starred" section (a filtered subset), and an account with
          // no stars returns data:[] — capturing that as the sidebar wiped
          // the real recent-conversations list, so the sync pill read 0 and
          // never showed (found live 2026-05-30). Only the unfiltered list
          // is the true sidebar.
          if (pat.provider === "claude"
              && (u.pathname.endsWith("/chat_conversations") || u.pathname.endsWith("/chat_conversations_v2"))
              && method === "GET"
              && u.searchParams.get("starred") !== "true") {
            return { provider: "claude", kind: "sidebar_list" };
          }
          if (pat.provider === "chatgpt" && u.pathname.endsWith("/backend-api/conversations") && method === "GET") {
            return { provider: "chatgpt", kind: "sidebar_list" };
          }
        }
      }
    } catch (e) {
      // URL parse failed; ignore
    }
    return null;
  }

  function emit(payload) {
    // Send to ISOLATED-world content script via window.postMessage.
    // The 'source' field is the discriminator the content script uses
    // to filter out unrelated postMessages (which are common on these
    // sites — they all use postMessage internally).
    window.postMessage({ source: "trinity-hook", payload }, location.origin);
  }

  const originalFetch = window.fetch;
  async function trinityFetch(input, init) {
    const url = typeof input === "string" ? input : input?.url;
    const method = (init?.method || (typeof input !== "string" && input?.method) || "GET").toUpperCase();
    const classification = classifyRequest(url, method);

    // Snapshot request body BEFORE awaiting the fetch — Gemini's
    // batchexecute RPC is reply-only on the response side, so the
    // user's prompt only lives in the request body. Best-effort:
    // serialize init.body if it's a string or URLSearchParams. Other
    // shapes (FormData, Blob, ReadableStream) are skipped — adapter
    // handles missing request_body gracefully.
    let request_body = null;
    if (classification && init && init.body !== undefined && init.body !== null) {
      try {
        if (typeof init.body === "string") {
          request_body = init.body;
        } else if (init.body instanceof URLSearchParams) {
          request_body = init.body.toString();
        }
      } catch {
        request_body = null;
      }
    }

    const response = await originalFetch.apply(this, arguments);

    if (!classification || !response.ok) {
      return response;
    }

    // Tee the body so the page reads the original; we read a clone.
    // response.clone() is the standard way; the clone has its own
    // independent reader pulling from the same underlying source.
    let captured;
    try {
      captured = response.clone();
    } catch (e) {
      // Some browsers refuse to clone once consumed; bail silently.
      return response;
    }

    // Don't block the page. Read the captured body in the background
    // and emit when it finishes. The page's response is returned
    // immediately and reads from its own buffered copy.
    (async () => {
      try {
        if (classification.kind === "stream") {
          const reader = captured.body.getReader();
          const decoder = new TextDecoder("utf-8");
          let buf = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
          }
          const captured_at = new Date().toISOString();
          // Dispatch through the provider's adapter if loaded
          // (adapters/<provider>.js runs before page-hook.js and
          // registers on window.__TRINITY_ADAPTERS). The adapter
          // normalizes the SSE delta stream into a structured payload
          // the host can write directly. Fallback: emit raw if no
          // adapter is registered for this provider.
          const adapter = window.__TRINITY_ADAPTERS?.[classification.provider];
          if (adapter?.adapt) {
            // page_href lets gemini.js extract conv_id from the user's
            // open conversation (the batchexecute URL itself doesn't
            // carry one). request_body carries the user prompt for
            // gemini (whose response is reply-only). Other adapters
            // ignore extra fields.
            //
            // An adapter MAY return null to SUPPRESS the capture — gemini's
            // adapter does this for telemetry/control batchexecute RPCs that
            // carry no prompt or answer (the over-capture noise fix). Skip the
            // emit entirely so nothing is written for a non-conversation RPC.
            const adapted = adapter.adapt({ url, body_text: buf, method, captured_at, page_href: location.href, request_body });
            if (adapted) emit(adapted);
          } else {
            emit({
              provider: classification.provider,
              kind: "stream",
              url,
              method,
              body_text: buf,
              captured_at,
            });
          }
        } else if (classification.kind === "canonical") {
          const json = await captured.json();
          emit({
            provider: classification.provider,
            kind: "canonical",
            url,
            method,
            conversation: json,
            captured_at: new Date().toISOString(),
          });
        } else if (classification.kind === "sidebar_list") {
          // Recent-conversations list endpoint. Carries an array of
          // {conv_id, title, updated_at, ...} entries the host renders
          // in the sidebar. We capture it under a sentinel filename
          // (`_sidebar.json`) so the auto-sync diff pipeline can
          // compare sidebar conv_ids vs on-disk capture conv_ids and
          // surface a "N new to sync" count. Overwrites on each fetch
          // — latest snapshot is what matters.
          const json = await captured.json();
          emit({
            provider: classification.provider,
            kind: "sidebar_list",
            url,
            method,
            sidebar: json,
            captured_at: new Date().toISOString(),
          });
        }
      } catch (e) {
        // Capture must never break the page. Swallow.
        console.warn("[trinity-hook] capture failed", e);
      }
    })();

    return response;
  }

  // Install the wrapper using Object.defineProperty with writable:false +
  // configurable:false so chatgpt's bundle can't reassign window.fetch to
  // its own minified version (which was the 100% capture-fail bug on
  // chatgpt.com — chatgpt does `window.fetch = o` after page-hook
  // installs at document_start; with writable:false that assignment
  // silently no-ops and trinity's wrapper stays live).
  //
  // We still expose `originalFetch` to ourselves via closure so trinityFetch
  // can delegate to the real network call. chatgpt's bundle, if it tries
  // `window.fetch = ourFetch`, hits the immutable property — the page
  // continues to call `window.fetch(...)` which is trinityFetch, which
  // delegates to originalFetch. No observable behavior change for the
  // page; trinity captures the request.
  try {
    Object.defineProperty(window, "fetch", {
      value: trinityFetch,
      writable: false,
      configurable: false,
    });
  } catch (e) {
    // Fallback for environments where defineProperty fails (some old
    // browsers / iframes with strict CSP). Falls back to plain assignment
    // which is what the v0.2 code did before this fix; chatgpt will
    // re-patch but at least claude.ai keeps working.
    window.fetch = trinityFetch;
    console.warn("[trinity-hook] defineProperty failed, falling back to writable assignment", e);
  }

  // XMLHttpRequest wrapper. gemini.google.com's batchexecute RPCs go
  // through XHR, not fetch — verified 2026-05-23 in live DevTools probe
  // (window.fetch.name was "trinityFetch" but 17 batchexecute POSTs hit
  // the network panel with zero adapter calls). Wrapping only fetch
  // misses gemini entirely. We mirror the fetch path: open captures
  // the URL/method on the XHR instance, send snapshots the request
  // body and registers a 'load' listener that reads responseText and
  // dispatches through the same adapter pipeline. Same emit() / same
  // adapters / same payload shape downstream.
  const OrigOpen = XMLHttpRequest.prototype.open;
  const OrigSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function trinityXhrOpen(method, url) {
    try {
      this.__trinity_url = url;
      this.__trinity_method = (method || "GET").toUpperCase();
      this.__trinity_classification = classifyRequest(url, this.__trinity_method);
    } catch (e) {
      // Capture must never break the page; classifier errors are silent.
    }
    return OrigOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function trinityXhrSend(body) {
    if (this.__trinity_classification) {
      // Snapshot the request body where possible. Gemini's batchexecute
      // sends application/x-www-form-urlencoded strings; sufficient for
      // adapter to extract the user's prompt.
      let request_body = null;
      try {
        if (typeof body === "string") {
          request_body = body;
        } else if (body instanceof URLSearchParams) {
          request_body = body.toString();
        }
      } catch {
        request_body = null;
      }
      this.__trinity_request_body = request_body;
      this.addEventListener("load", function trinityXhrOnLoad() {
        try {
          if (this.status < 200 || this.status >= 300) return;
          const classification = this.__trinity_classification;
          const url = this.__trinity_url;
          const method = this.__trinity_method;
          const buf = this.responseText || "";
          const captured_at = new Date().toISOString();
          const adapter = window.__TRINITY_ADAPTERS?.[classification.provider];
          if (classification.kind === "stream") {
            if (adapter?.adapt) {
              emit(adapter.adapt({
                url, body_text: buf, method, captured_at,
                page_href: location.href,
                request_body: this.__trinity_request_body,
              }));
            } else {
              emit({ provider: classification.provider, kind: "stream", url, method, body_text: buf, captured_at });
            }
          } else if (classification.kind === "canonical") {
            let json;
            try { json = JSON.parse(buf); } catch { return; }
            emit({ provider: classification.provider, kind: "canonical", url, method, conversation: json, captured_at });
          } else if (classification.kind === "sidebar_list") {
            let json;
            try { json = JSON.parse(buf); } catch { return; }
            emit({ provider: classification.provider, kind: "sidebar_list", url, method, sidebar: json, captured_at });
          }
        } catch (e) {
          console.warn("[trinity-hook] xhr capture failed", e);
        }
      });
    }
    return OrigSend.apply(this, arguments);
  };

  console.log("[trinity-hook] fetch + xhr wrappers installed on", location.hostname,
              "— window.fetch.name:", window.fetch.name);

  // For node-based unit tests (no-op in the page MAIN world, where `module`
  // is undefined). Exposes classifyRequest so the capture-DECISION logic —
  // which endpoint maps to canonical/stream/sidebar, and the order-dependent
  // over-match avoidance (chatgpt's plural /backend-api/conversations sidebar
  // must NOT be swallowed by the singular /backend-api/conversation stream
  // includes() match) — is testable without a live browser. Mirrors the
  // adapters/<provider>.js module.exports guard.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { classifyRequest, PROVIDER_PATTERNS };
  }
})();
