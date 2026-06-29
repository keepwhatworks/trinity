// Trinity Local — adapters/claude.js (MAIN world)
//
// Normalizes Anthropic's SSE delta stream from
//   POST /api/organizations/<org>/chat_conversations/<conv_id>/completion
// into a single payload the capture host can write.
//
// Per spec-v1.6: the CANONICAL state fetch (GET /chat_conversations/<id>)
// is preferred — it returns the full message tree directly. This adapter
// handles the streamed-only case (when the canonical fetch hasn't fired
// yet, or as a self-contained verification path). When both arrive, the
// canonical write wins via overwrite-by-conv_id.
//
// Loaded BEFORE page-hook.js (manifest content_scripts order). Registers
// itself at window.__TRINITY_ADAPTERS.claude so page-hook.js can find it
// without explicit imports (MV3 content scripts can't import each other).

(() => {
  const G = typeof window !== "undefined" ? window : globalThis;
  if (G.__TRINITY_ADAPTERS_CLAUDE_INSTALLED__) return;
  G.__TRINITY_ADAPTERS_CLAUDE_INSTALLED__ = true;

  function extractConvId(url) {
    try {
      const base = typeof location !== "undefined" ? location.href : "https://claude.ai/";
      const u = new URL(url, base);
      const m = u.pathname.match(/\/chat_conversations\/([^/]+)/);
      return m ? m[1] : null;
    } catch {
      return null;
    }
  }

  // Parse the streamed body text into discrete SSE events.
  // SSE event = consecutive non-empty lines, separated by blank lines.
  // Each event has optional "event:" name and one or more "data:" lines.
  function parseSSE(bodyText) {
    const events = [];
    if (!bodyText) return events;
    // Use \r?\n\r?\n to be robust to Windows-style line endings.
    const blocks = bodyText.split(/\r?\n\r?\n/);
    for (const block of blocks) {
      if (!block.trim()) continue;
      let eventName = null;
      const dataLines = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
        // Other SSE fields (id:, retry:) ignored — Anthropic doesn't use them.
      }
      if (!dataLines.length && !eventName) continue;
      // Multiple data: lines on one event are joined with \n per the SSE spec.
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
          // Skip malformed JSON; Anthropic shouldn't emit any but the
          // adapter must not crash if the stream is interrupted.
          continue;
        }
      }
      events.push({ event: eventName, data });
    }
    return events;
  }

  // Walk events and concatenate assistant text. Two delta shapes are
  // observed (both supported because Anthropic's API has both lived
  // in production):
  //   - content_block_delta with delta.type === "text_delta"
  //   - older "completion" events with a top-level completion string
  function accumulateText(events) {
    let text = "";
    for (const ev of events) {
      const d = ev.data;
      if (!d) continue;
      if (d.type === "content_block_delta" && d.delta && d.delta.type === "text_delta") {
        text += d.delta.text || "";
      } else if (d.type === "completion" && typeof d.completion === "string") {
        text += d.completion;
      }
    }
    return text;
  }

  // Extract the assistant message uuid if the stream contains a
  // message_start event. Useful for downstream joining when the
  // canonical fetch arrives.
  function extractMessageUuid(events) {
    for (const ev of events) {
      const d = ev.data;
      if (!d) continue;
      if (d.type === "message_start" && d.message && d.message.uuid) {
        return d.message.uuid;
      }
    }
    return null;
  }

  function adapt(input) {
    const url = input.url || "";
    const body_text = input.body_text || "";
    const events = parseSSE(body_text);
    return {
      provider: "claude",
      kind: "adapter_stream",
      conv_id: extractConvId(url),
      message_uuid: extractMessageUuid(events),
      url,
      method: input.method || "POST",
      captured_at: input.captured_at,
      events_count: events.length,
      assistant_text: accumulateText(events),
    };
  }

  if (typeof window !== "undefined") {
    window.__TRINITY_ADAPTERS = window.__TRINITY_ADAPTERS || {};
    window.__TRINITY_ADAPTERS.claude = {
      adapt,
      parseSSE,
      accumulateText,
      extractConvId,
      extractMessageUuid,
    };
  }

  // For node-based unit tests.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { adapt, parseSSE, accumulateText, extractConvId, extractMessageUuid };
  }
})();

