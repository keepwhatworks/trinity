// Trinity Local — adapters/gemini.js (MAIN world)
//
// Normalizes Google's `batchexecute` RPC response from
//   POST https://gemini.google.com/_/BardChatUi/data/batchexecute?...
// into a payload the capture host can write under a stable conv_id.
//
// Why this adapter is different from claude.js / chatgpt.js:
//
//   gemini.google.com does NOT use SSE. It uses Google's `batchexecute`
//   RPC — a chunked HTTP body framed as:
//
//     )]}'                                            # JSON-hijacking prefix
//     <decimal_length>\n                              # frame length
//     [[...frame array...]]\n                         # frame (JSON)
//     <decimal_length>\n                              # next frame length
//     [[...frame array...]]\n
//     ...
//
//   Each frame is a JSON array. The interesting entries are `wrb.fr`
//   rows whose 3rd element is a JSON-encoded STRING containing the
//   actual response payload (Google double-encodes here so the outer
//   JSON parses fast on slow connections). That inner payload's
//   shape is what *moves* across Google's frontend releases — IDs
//   rotate, field positions shift, candidate slots get reorganized.
//
// Per the v1.6 / v1.8 trade-off (claude.md "gemini.google.com adapter
// deferred to v1.7 per protocol-fragility risk"): we ship a
// `kind: "adapter_stream"` payload because that's what the capture
// host writes at `<conv_id>.stream.json` (instead of the
// `stream-<urlhash>.json` orphan produced by `kind: "stream"`).
//
// Best-effort: we extract conv_id (from URL referer / `?c=` query)
// and best-effort assistant_text (longest plausible text run inside
// the wrb.fr payload). If the inner shape moves, the raw body is
// still preserved in the captured file so downstream ingest can
// re-parse with an updated extractor without re-capturing.
//
// Loaded BEFORE page-hook.js (manifest content_scripts order).
// Registers itself at window.__TRINITY_ADAPTERS.gemini so page-hook
// can find it.

(() => {
  const G = typeof window !== "undefined" ? window : globalThis;
  if (G.__TRINITY_ADAPTERS_GEMINI_INSTALLED__) return;
  G.__TRINITY_ADAPTERS_GEMINI_INSTALLED__ = true;

  // Extract a conversation id from the user's Gemini URL.
  // Gemini's web app uses three URL shapes for conversations:
  //   1. https://gemini.google.com/app/<conv_id>
  //   2. https://gemini.google.com/?c=<conv_id>
  //   3. https://gemini.google.com/chat/<conv_id>
  //
  // The batchexecute RPC URL itself does NOT carry the conv_id
  // (it's a generic data endpoint), so we fall back to the page's
  // own location.href, which IS scoped to the user's open conversation.
  function extractConvId(streamUrl, pageHref) {
    const candidates = [];
    if (typeof pageHref === "string" && pageHref) candidates.push(pageHref);
    if (typeof streamUrl === "string" && streamUrl) candidates.push(streamUrl);
    const base = "https://gemini.google.com/";
    for (const candidate of candidates) {
      try {
        const u = new URL(candidate, base);
        // /app/<id> or /chat/<id>
        const m = u.pathname.match(/\/(?:app|chat)\/([A-Za-z0-9_-]{6,})/);
        if (m) return m[1];
        // ?c=<id>
        const q = u.searchParams.get("c");
        if (q && /^[A-Za-z0-9_-]{6,}$/.test(q)) return q;
      } catch {
        continue;
      }
    }
    return null;
  }

  // Split the chunked batchexecute body into the discrete JSON frames.
  // Wire format (length-prefixed JSON, NOT SSE):
  //
  //   )]}'\n
  //   123\n               # decimal byte length of next frame
  //   [[...]]\n           # JSON frame of that length
  //   45\n
  //   [[...]]\n
  //   ...
  //
  // Real-world bodies sometimes ship without the leading `)]}'`
  // prefix (depends on the Google frontend variant), so we accept
  // both. Returns parsed JSON arrays — frames that don't parse are
  // skipped silently.
  // Find the index just past the end of the JSON value starting at `start`.
  // Tracks brace/bracket depth + string escape state. Returns -1 if no
  // complete value is found before end-of-text. Treats this byte-level
  // structural scan as more authoritative than Google's declared length
  // prefix because the prefix is unreliable: live captures (2026-05-23)
  // showed prefixes off by ±2 chars from the actual JSON value length —
  // unknown whether it's a UTF-8/UTF-16 mismatch or a Google-side count
  // semantic that includes trailing separators. Brace-depth scan
  // sidesteps the question.
  function findJsonValueEnd(text, start) {
    let depth = 0;
    let inString = false;
    let escape = false;
    let started = false;
    for (let i = start; i < text.length; i++) {
      const c = text[i];
      if (escape) { escape = false; continue; }
      if (inString) {
        if (c === "\\") escape = true;
        else if (c === "\"") inString = false;
        continue;
      }
      if (c === "\"") { inString = true; started = true; }
      else if (c === "[" || c === "{") { depth++; started = true; }
      else if (c === "]" || c === "}") {
        depth--;
        if (started && depth === 0) return i + 1;
      } else if (!started && (c === "\n" || c === "\r" || c === " " || c === "\t")) {
        // skip leading whitespace before the value starts
        continue;
      } else if (!started && (c >= "0" && c <= "9")) {
        // bare-number value (rare for our use case but handle anyway)
        started = true;
      }
    }
    return -1;
  }

  function parseFrames(bodyText) {
    if (!bodyText) return [];
    let text = bodyText;
    // Strip XSSI prefix if present
    if (text.startsWith(")]}'")) {
      text = text.replace(/^\)\]\}'\s*/, "");
    }
    const frames = [];
    let i = 0;
    while (i < text.length) {
      // Skip whitespace between frames
      while (i < text.length && (text[i] === "\n" || text[i] === "\r" || text[i] === " ")) {
        i++;
      }
      if (i >= text.length) break;
      // The conventional gemini framing is `<length>\n<json>`. We READ the
      // length prefix (to skip past it) but DON'T trust its value — see
      // findJsonValueEnd above. The prefix is purely a hint that "a JSON
      // value follows"; the scan finds the actual end.
      while (i < text.length && text[i] >= "0" && text[i] <= "9") {
        i++;
      }
      // Skip the newline(s) after the length
      while (i < text.length && (text[i] === "\n" || text[i] === "\r")) {
        i++;
      }
      if (i >= text.length) break;
      // Brace-depth scan to find the actual end of the JSON value.
      const end = findJsonValueEnd(text, i);
      if (end < 0) {
        // Truncated body — best effort: try parsing the rest as one frame.
        const tail = text.slice(i).trim();
        if (tail) {
          try {
            frames.push(JSON.parse(tail));
          } catch { /* skip */ }
        }
        break;
      }
      try {
        frames.push(JSON.parse(text.slice(i, end)));
      } catch {
        // Shape-shifted frame — skip silently; don't crash on partials.
      }
      i = end;
    }
    return frames;
  }

  // Walk frames and pull out the `wrb.fr` rows. Each frame is roughly:
  //   [
  //     ["wrb.fr", "<rpc_id>", "<json_encoded_payload>", null, null, "<msg_id>"],
  //     ...
  //   ]
  // The interesting field is element [2] — a JSON string carrying the
  // actual model response. Parsing is best-effort; non-`wrb.fr` rows
  // are ignored.
  function extractWrbPayloads(frames) {
    const payloads = [];
    for (const frame of frames) {
      if (!Array.isArray(frame)) continue;
      for (const row of frame) {
        if (!Array.isArray(row)) continue;
        if (row[0] !== "wrb.fr") continue;
        const raw = row[2];
        if (typeof raw !== "string" || !raw) continue;
        try {
          payloads.push(JSON.parse(raw));
        } catch {
          // Some frames carry non-JSON ack rows; skip.
          continue;
        }
      }
    }
    return payloads;
  }

  // Best-effort assistant-text extractor.
  //
  // The inner payload from Gemini's batchexecute is a deeply nested
  // array. The model's reply text typically lives in a candidate slot
  // a few levels deep. Rather than hard-code positions (which Google
  // rotates), we walk the payload and pick the LONGEST plain-text
  // leaf that looks like prose (≥30 chars, has whitespace, isn't
  // base64-ish). This is robust to shape rotation: even if Google
  // re-orders candidate slots, the longest prose leaf is the reply.
  //
  // Returns "" if no plausible text is found — the captured raw body
  // is still preserved so a future extractor can do better.
  function accumulateText(payloads) {
    let longest = "";
    const seen = new WeakSet();
    function looksLikeProse(s) {
      if (typeof s !== "string") return false;
      if (s.length < 30) return false;
      if (!/\s/.test(s)) return false;
      // Filter out base64-ish blobs (long runs of [A-Za-z0-9+/=] with no spaces between).
      // Prose has punctuation + spaces; serialized blobs don't.
      const wordCount = s.split(/\s+/).length;
      if (wordCount < 5) return false;
      return true;
    }
    function walk(node) {
      if (node === null || node === undefined) return;
      if (typeof node === "string") {
        if (looksLikeProse(node) && node.length > longest.length) {
          longest = node;
        }
        return;
      }
      if (typeof node !== "object") return;
      if (seen.has(node)) return;
      seen.add(node);
      if (Array.isArray(node)) {
        for (const child of node) walk(child);
      } else {
        for (const key of Object.keys(node)) walk(node[key]);
      }
    }
    for (const p of payloads) walk(p);
    return longest;
  }

  // Best-effort user-prompt extractor from the batchexecute REQUEST
  // body. Gemini's RPC response is reply-only — the user's prompt
  // lives in the outbound POST body, form-encoded as:
  //
  //   f.req=<url-encoded JSON array>&at=<token>
  //
  // The JSON array's shape (when the rpcid is the chat-send RPC):
  //
  //   [[["<rpcid>","<json-encoded RPC args>",null,"generic"]]]
  //
  // The inner RPC args (also JSON) carry the user's prompt at
  // [0][0] for the StreamGenerate RPC. Position can rotate across
  // releases — we apply the same longest-prose-leaf heuristic on
  // the decoded args.
  function extractUserPrompt(requestBody) {
    if (!requestBody || typeof requestBody !== "string") return "";
    // Pull the f.req value out of the form-encoded body.
    let fReq = null;
    for (const part of requestBody.split("&")) {
      const eq = part.indexOf("=");
      if (eq < 0) continue;
      const key = part.slice(0, eq);
      if (key !== "f.req") continue;
      const rawVal = part.slice(eq + 1);
      try {
        fReq = decodeURIComponent(rawVal.replace(/\+/g, " "));
      } catch {
        fReq = rawVal;
      }
      break;
    }
    if (!fReq) return "";
    let outer;
    try {
      outer = JSON.parse(fReq);
    } catch {
      return "";
    }
    // Walk the outer array and JSON.parse any string children — those
    // are the double-encoded RPC arg blobs.
    const decoded = [];
    function walk(node) {
      if (node === null || node === undefined) return;
      if (typeof node === "string") {
        // Try parsing as JSON; if it parses to an object/array,
        // include its decoded form in the candidate set.
        try {
          const inner = JSON.parse(node);
          decoded.push(inner);
        } catch {
          // Do NOT treat a raw, unparseable top-level string as a prompt
          // candidate. In the batchexecute envelope these are STRUCTURAL tokens
          // — the rpcid ("Bsxleb"/"VxUbXb"/…) and the literal "generic"
          // source-path marker at [0][0][3]. The user's prompt is ALWAYS inside
          // the double-encoded inner RPC args, which DO parse (pushed above) and
          // get recursed by walk2. Pushing the structural tokens let "generic"
          // win the longest-prose contest for telemetry/control RPCs that carry
          // no real prose — so user_text became "generic" (non-empty), which
          // defeated the over-capture gate below (`!user_text && !assistant_text`)
          // and emitted a junk capture for every control batchexecute (the
          // ~98%-garbage captures dir). Skipping them is loss-free for real
          // prompts (which live in the parseable inner args, even 1-word ones).
        }
        return;
      }
      if (typeof node !== "object") return;
      if (Array.isArray(node)) {
        for (const child of node) walk(child);
      } else {
        for (const key of Object.keys(node)) walk(node[key]);
      }
    }
    walk(outer);
    // Same longest-prose-leaf trick as accumulateText, but the
    // threshold is lower (user prompts are often short, e.g. "fix
    // this bug" — 13 chars). Use a more forgiving threshold + only
    // require ≥2 whitespace-separated tokens.
    let longest = "";
    const seen = new WeakSet();
    function looksLikePrompt(s) {
      if (typeof s !== "string") return false;
      if (s.length < 3) return false;
      if (s.length > 50000) return false;  // discard giant blobs
      const trimmed = s.trim();
      if (!trimmed) return false;
      // Filter base64-ish: long runs of [A-Za-z0-9+/=] without spaces or punctuation.
      if (trimmed.length > 60 && !/[\s.,?!]/.test(trimmed)) return false;
      return true;
    }
    function walk2(node) {
      if (node === null || node === undefined) return;
      if (typeof node === "string") {
        if (looksLikePrompt(node) && node.length > longest.length) {
          longest = node;
        }
        return;
      }
      if (typeof node !== "object") return;
      if (seen.has(node)) return;
      seen.add(node);
      if (Array.isArray(node)) {
        for (const child of node) walk2(child);
      } else {
        for (const key of Object.keys(node)) walk2(node[key]);
      }
    }
    for (const d of decoded) walk2(d);
    return longest;
  }

  // Best-effort assistant message id. Gemini's batchexecute frames
  // commonly carry a message identifier at row[5] of the wrb.fr row
  // (when present — it's the message-finished marker), but the inner
  // payload also embeds candidate ids. We scan for the first short
  // hex-like id deep in the payload. Returns null on miss.
  function extractMessageId(frames) {
    for (const frame of frames) {
      if (!Array.isArray(frame)) continue;
      for (const row of frame) {
        if (!Array.isArray(row)) continue;
        if (row[0] !== "wrb.fr") continue;
        // row[5] is sometimes a string message id in production frames.
        const cand = row[5];
        if (typeof cand === "string" && /^[A-Za-z0-9_-]{8,}$/.test(cand)) {
          return cand;
        }
      }
    }
    return null;
  }

  function adapt(input) {
    const url = input.url || "";
    const body_text = input.body_text || "";
    // page_href is set by page-hook.js (location.href at capture time)
    // when running in the extension. In node tests it's read from input.
    const pageHref = input.page_href || (typeof location !== "undefined" ? location.href : "");
    const frames = parseFrames(body_text);
    const payloads = extractWrbPayloads(frames);
    const conv_id = extractConvId(url, pageHref);
    const user_text = extractUserPrompt(input.request_body || "");
    const assistant_text = accumulateText(payloads);
    const message_id = extractMessageId(frames);

    // OVER-CAPTURE GATE (the gemini-capture noise fix). `/batchexecute` is a
    // GENERIC Google RPC endpoint — every turn fires it for telemetry/control
    // RPCs (bard_activity_enabled, config, sidebar, …) alongside the real
    // conversation RPC. classifyRequest can't tell them apart (the rpcids that
    // ARE the conversation aren't stable across releases, so a hardcoded
    // allowlist would silently break capture), so it lets all of them through
    // and the captures dir filled with ~98% garbage (one file per RPC frame).
    // Decide it HERE, by CONTENT not by rpcid: a batchexecute frame that yields
    // neither a user prompt NOR assistant prose carries no conversation — it is
    // pure telemetry. Return null so page-hook skips the emit entirely (nothing
    // is written). This is loss-free: a capture with empty user_text AND empty
    // assistant_text contributes zero PromptTurns to the corpus regardless, so
    // dropping it at the source only removes disk noise. Real captures — the
    // chat-send RPC (has user_text) and the StreamGenerate reply (has
    // assistant_text) — always carry content and pass.
    if (!user_text && !assistant_text) {
      return null;
    }

    // Per-call discriminator emitted as a separate `file_stem` field
    // so the capture host can land each RPC capture distinctly.
    // conv_id keeps its semantic meaning (the gemini thread ID);
    // file_stem is what capture_host prefers for the on-disk filename
    // when present, falling back to conv_id otherwise.
    //
    // Without this discriminator, gemini fires multiple RPCs per user
    // turn (telemetry batchexecute + StreamGenerate + others), all
    // sharing the same conv_id, so each overwrites the previous.
    // Across turns in the same thread, the SAME StreamGenerate URL
    // fires again with the same conv_id, also overwriting. Effect:
    // only the trailing RPC of the latest turn survives on disk.
    //
    // Discriminator preference: message_id (genuinely identifies the
    // assistant message; stable across re-fetches of the same turn)
    // → captured_at compact timestamp (unique per wrapper invocation
    // modulo millisecond collisions, vanishingly rare).
    let file_stem = null;
    if (conv_id) {
      if (message_id) {
        file_stem = `${conv_id}__${message_id}`;
      } else if (input.captured_at) {
        const compact = String(input.captured_at).replace(/[^0-9]/g, "").slice(0, 17);
        if (compact) {
          file_stem = `${conv_id}__${compact}`;
        }
      }
    }

    return {
      provider: "gemini",
      // adapter_stream (not "stream") — capture host writes this under
      // `<file_stem || conv_id>.stream.json` instead of the urlhash-
      // orphan path. When conv_id can't be extracted (rare; user is
      // on the gemini.google.com root with no thread loaded), file_stem
      // is null and the capture host's conv_id requirement still
      // gates writes — better to drop one orphan than to flood the
      // captures dir with hash-keyed files.
      kind: "adapter_stream",
      conv_id,
      file_stem,
      message_id,
      url,
      method: input.method || "POST",
      captured_at: input.captured_at,
      frames_count: frames.length,
      events_count: payloads.length,
      // user_text: best-effort extraction of the user prompt from the
      // batchexecute REQUEST body. Without this, gemini captures only
      // contain the assistant reply (Google's RPC is reply-only on the
      // response side) and contribute zero PromptTurn entries to the
      // corpus — assistant text is recorded as context but the lens
      // pipeline only consumes user-facing turns.
      user_text,
      assistant_text,
      // Preserve the raw bodies so a future extractor can re-parse without
      // re-capturing. The capture host writes whatever we return as JSON,
      // and Gemini's frame shape is unstable enough that the inner-payload
      // parser will likely need to evolve — keeping the raw bodies
      // decouples ingest from adapter shape.
      _raw_body: body_text,
      _raw_request_body: input.request_body || null,
    };
  }

  if (typeof window !== "undefined") {
    window.__TRINITY_ADAPTERS = window.__TRINITY_ADAPTERS || {};
    window.__TRINITY_ADAPTERS.gemini = {
      adapt,
      parseFrames,
      extractWrbPayloads,
      accumulateText,
      extractConvId,
      extractMessageId,
      extractUserPrompt,
    };
  }

  // For node-based unit tests.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      adapt,
      parseFrames,
      extractWrbPayloads,
      accumulateText,
      extractConvId,
      extractMessageId,
      extractUserPrompt,
    };
  }
})();
