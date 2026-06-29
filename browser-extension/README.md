---
class: live
---

# Trinity Local — browser extension

Captures `claude.ai`, `chatgpt.com`, and `gemini.google.com` conversations
to `~/.trinity/conversations/<provider>/<conv_id>.json`. No listening
server, no daemon — Chrome spawns the local capture host on demand via
Native Messaging.

## Architecture (60-second version)

```
claude.ai page  ─→  adapters/claude.js + page-hook.js  (MAIN world: wraps fetch)
                              │
                              │  window.postMessage
                              ▼
                       content-script.js              (ISOLATED world: relay)
                              │
                              │  chrome.runtime.sendMessage
                              ▼
                        background.js                  (service worker)
                              │
                              │  chrome.runtime.connectNative
                              ▼
                  trinity-local-capture-host          (Python process Chrome spawns)
                              │
                              │  atomic file write
                              ▼
       ~/.trinity/conversations/claude/<conv_id>.json
```

The MAIN-world script is what wraps the page's `fetch()` — the ISOLATED
world cannot see page-level network calls (MV3 invariant). The two
worlds communicate via `window.postMessage` because they don't share a
`window` object.

## 60-second install (manual, one-time)

```bash
# 1. Make sure the trinity-local package + capture host are installed.
pip install -e .
which trinity-local-capture-host   # should print a path

# 2. In Chrome, load the extension UNPACKED (browser-extension/ here):
#    a. Open chrome://extensions
#    b. Toggle "Developer mode" on (top right)
#    c. Click "Load unpacked" → select this browser-extension/ directory
#    d. Copy the 32-character ID Chrome assigns (looks like "abcdef...")

# 3. Wire up the Native Messaging manifest with that ID:
trinity-local install-extension --extension-id <PASTE_THE_ID_HERE>

# 4. Smoke test: visit claude.ai, send one message, then check:
ls -la ~/.trinity/conversations/claude/
# expected: <conv_id>.json (canonical state) + <conv_id>.stream.json (adapter output)
```

The `--extension-id` is the Chrome-assigned hash. It is the security
primitive: only that exact extension can invoke the local capture host.
Any other extension hitting the host gets `Specified native messaging
host not found.` (enforced by Chrome itself, not Trinity).

## What gets written

**Canonical fetch (preferred):** when claude.ai's UI calls
`GET /api/organizations/<org>/chat_conversations/<conv_id>` for re-render,
page-hook captures the full conversation tree as returned by Anthropic.
This lands at `~/.trinity/conversations/claude/<conv_id>.json` and is
the canonical state — overwrites on each turn.

**Streamed fallback:** when only the `POST /completion` SSE stream is
visible (e.g. the canonical fetch didn't fire), `adapters/claude.js`
accumulates the `content_block_delta` events into a single
`assistant_text` string. Lands at `<conv_id>.stream.json` so it doesn't
clobber the canonical file when both arrive.

## How to know it's working

The launchpad (run `trinity-local portal-html`) has a "Browser capture ·
last 24h" card. To inspect on disk, `ls ~/.trinity/conversations/`. If
empty after sending a claude.ai message:

1. **Check the extension's service worker console** — `chrome://extensions`,
   click "service worker" link under Trinity Local Capture. Should see
   `[trinity-bg] service worker started`. Errors like
   `Specified native messaging host not found` mean the manifest is
   missing or has the wrong extension ID — rerun
   `trinity-local install-extension --extension-id <ID>`.
2. **Check the page console** — F12 on claude.ai. Should see
   `[trinity-hook] fetch + xhr wrappers installed on <host>` and adapter
   chunks logged on each turn.
3. **Check the native host process** — when the service worker is
   connected, `ps aux | grep capture-host` shows the Python process
   Chrome spawned. It exits when the service worker disconnects.

## Optional Stagehand smoke

The gated real-Chrome test uses Stagehand only as a local browser
launcher. It does not call `act()`, `extract()`, Browserbase, or any LLM;
the assertion is a deterministic `chrome.runtime.sendMessage` ping from
the launchpad to `background.js`.

```bash
cd browser-extension
npm install
cd ..
export TRINITY_CHROME_SMOKE=1
export TRINITY_EXTENSION_ID=<ID_FOR_THIS_BROWSER_EXTENSION_PATH>
trinity-local install-extension --extension-id "$TRINITY_EXTENSION_ID"
trinity-local portal-html
pytest tests/test_chrome_extension_smoke.py -v
```

By default the smoke launches a visible local Chrome window because MV3
extension debugging is easier headed. Set `TRINITY_STAGEHAND_HEADLESS=1`
to request headless mode.

## Files in this directory

- `manifest.json` — MV3 manifest. Two `content_scripts` entries cover
  ISOLATED and MAIN worlds; the `world: "MAIN"` field requires Chrome 111+.
- `page-hook.js` (MAIN world) — wraps `window.fetch` AND
  `XMLHttpRequest.prototype.open/send` (gemini.google.com dispatches
  batchexecute via XHR, not fetch — XHR interception landed v1.7.7).
  Tees streamed response bodies, dispatches through
  `window.__TRINITY_ADAPTERS`. Also installs a content-script context-
  invalidation guard so reloading the extension doesn't spam page
  consoles with thrown `chrome.runtime.sendMessage` errors.
- `adapters/claude.js` + `adapters/chatgpt.js` + `adapters/gemini.js`
  (MAIN world, loaded before page-hook) — register Anthropic + OpenAI
  SSE + Google batchexecute/StreamGenerate parsers on the adapter
  registry. Gemini support shipped v1.7.6 (task #135) with the
  StreamGenerate URL pattern added v1.7.7 via `trinity-local extension
  repair --har` council dogfood (commit 2f37681).
- `content-script.js` (ISOLATED world) — bridges `window.postMessage`
  events to `chrome.runtime.sendMessage`. Guards against
  "Extension context invalidated" throws on hot-reload.
- `background.js` (service worker) — receives captured payloads,
  forwards via `chrome.runtime.connectNative("local.trinity.capture")`.

Structural validity of this directory is asserted by
`tests/test_browser_extension_manifest.py` (11 checks: file existence,
load-order, host permissions, MV3 version, fetch + XHR wrapping
present, content-script context guard present). The Anthropic SSE
parser is validated against a saved fixture by
`tests/test_browser_extension_claude_adapter.py` (8 cases). The
stdio wire protocol round-trip is covered by
`tests/test_capture_host_stdio.py` (17 cases).

## Privacy invariants

- **No outbound network from the capture host.** Pinned by AST
  scanner in `tests/test_capture_host_no_network.py`.
- **`allowed_origins` in the Native Messaging manifest** restricts the
  host to invocations from Trinity's extension ID only.
- **Atomic writes** (`tmp.replace(target)`) — readers never see
  partial files.
- **Data is plaintext on disk** — same as the rest of `~/.trinity/`.
