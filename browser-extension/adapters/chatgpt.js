// Trinity Local — adapters/chatgpt.js (MAIN world)
//
// Normalizes OpenAI's SSE stream from
//   POST /backend-api/conversation
// into a single payload the capture host can write.
//
// Same module shape as adapters/claude.js. Loaded BEFORE page-hook.js
// (manifest content_scripts order). Registers at
// window.__TRINITY_ADAPTERS.chatgpt so page-hook reads it without
// imports (MV3 content scripts can't import each other).
//
// Per spec-v1.6: chatgpt.com's canonical state lives at
// GET /backend-api/conversation/<id> and returns a `mapping` graph
// keyed by node id with `current_node` pointer (branching tree, not
// linear). The canonical fetch is preferred when it fires; this
// adapter handles the streamed-only case + extracts conv_id from
// the stream for cross-file dedup.
//
// OpenAI's SSE event shape evolved over time. Common patterns:
//   - `message.content.parts: [str]` (cumulative text per event)
//   - `delta` field on newer responses (incremental)
//   - `v` field on oldest responses (JSON Patch ops)
// This adapter handles the first two; the third logs and is ignored.

(() => {
  const G = typeof window !== "undefined" ? window : globalThis;
  if (G.__TRINITY_ADAPTERS_CHATGPT_INSTALLED__) return;
  G.__TRINITY_ADAPTERS_CHATGPT_INSTALLED__ = true;

  function parseSSE(bodyText) {
    const events = [];
    if (!bodyText) return events;
    const blocks = bodyText.split(/\r?\n\r?\n/);
    for (const block of blocks) {
      if (!block.trim()) continue;
      const dataLines = [];
      let eventName = null;
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
      if (!dataLines.length && !eventName) continue;
      const joined = dataLines.join("\n");
      if (joined === "[DONE]") {
        events.push({ event: eventName, data: null, raw: joined });
        continue;
      }
      let data = null;
      if (joined) {
        try {
          data = JSON.parse(joined);
        } catch {
          continue;
        }
      }
      events.push({ event: eventName, data });
    }
    return events;
  }

  // Extract the conversation_id from events. OpenAI puts it on most
  // payloads at the top level OR nested in `message.metadata`.
  function extractConvId(events) {
    for (const ev of events) {
      const d = ev.data;
      if (!d) continue;
      if (typeof d.conversation_id === "string" && d.conversation_id) {
        return d.conversation_id;
      }
      if (d.message && typeof d.message === "object") {
        const m = d.message;
        if (m.metadata && typeof m.metadata.conversation_id === "string") {
          return m.metadata.conversation_id;
        }
      }
    }
    return null;
  }

  // Extract the assistant message id from events.
  function extractMessageId(events) {
    for (const ev of events) {
      const d = ev.data;
      if (!d || !d.message) continue;
      const role = d.message.author && d.message.author.role;
      if (role === "assistant" && typeof d.message.id === "string") {
        return d.message.id;
      }
    }
    return null;
  }

  // Accumulate assistant text. OpenAI's "parts" field is cumulative
  // (each event carries the FULL text so far), so we take the last
  // observed value rather than concatenating.
  //
  // The newer "delta" shape ships partial chunks per event — concat
  // those. The adapter handles both by tracking the last-observed
  // parts string AND a delta accumulator, returning whichever is
  // longer (the partial delta should be a strict prefix of the
  // cumulative parts, but if parts events don't fire the delta is
  // the only signal).
  function accumulateText(events) {
    let lastCumulative = "";
    let deltaAccum = "";
    for (const ev of events) {
      const d = ev.data;
      if (!d) continue;
      // Newer shape: delta.content
      if (d.delta && typeof d.delta === "object") {
        const dc = d.delta.content;
        if (typeof dc === "string") {
          deltaAccum += dc;
        } else if (Array.isArray(dc)) {
          for (const part of dc) {
            if (typeof part === "string") deltaAccum += part;
          }
        }
      }
      // Standard shape: message.content.parts[]
      if (d.message && d.message.content && Array.isArray(d.message.content.parts)) {
        const role = d.message.author && d.message.author.role;
        if (role === "assistant") {
          const joined = d.message.content.parts
            .filter((p) => typeof p === "string")
            .join("");
          if (joined.length >= lastCumulative.length) {
            lastCumulative = joined;
          }
        }
      }
    }
    return lastCumulative.length >= deltaAccum.length ? lastCumulative : deltaAccum;
  }

  function adapt(input) {
    const url = input.url || "";
    const body_text = input.body_text || "";
    const events = parseSSE(body_text);
    return {
      provider: "chatgpt",
      kind: "adapter_stream",
      conv_id: extractConvId(events),
      message_id: extractMessageId(events),
      url,
      method: input.method || "POST",
      captured_at: input.captured_at,
      events_count: events.length,
      assistant_text: accumulateText(events),
    };
  }

  if (typeof window !== "undefined") {
    window.__TRINITY_ADAPTERS = window.__TRINITY_ADAPTERS || {};
    window.__TRINITY_ADAPTERS.chatgpt = {
      adapt,
      parseSSE,
      accumulateText,
      extractConvId,
      extractMessageId,
    };
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { adapt, parseSSE, accumulateText, extractConvId, extractMessageId };
  }
})();
