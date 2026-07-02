---
class: live
---

# Install Trinity (Chrome Extension — Tier 3, optional)

> The Chrome extension is the optional third tier. It does two things
> the skill + pip tiers can't: (1) the launchpad + council UI in a
> side panel that opens from the browser toolbar, (2) cross-surface
> capture of web-chat conversations from claude.ai, chatgpt.com, and
> gemini.google.com — captures land in ~/.trinity/conversations/ and
> the lens picks them up on the next MCP-triggered ingest cycle.

## When to install this tier

- You want the launchpad and councils accessible from any tab via
  the toolbar icon — it opens a side panel, no terminal.
- You want web chats (claude.ai, chatgpt.com, gemini.google.com)
  ingested into `~/.trinity/conversations/` so Trinity sees ALL your
  conversations, not just the CLI tools.
- You're on Linux or Windows and the macOS-only Shortcut dispatcher
  doesn't apply.

Tier 3 is **opt-in**. Skill + pip work fully without it.

## Prerequisites

Tier 2 (pip) must already be installed — the extension talks to
the engine via Native Messaging.

```bash
trinity-local --version    # should print the wheel version
trinity-local status       # should be mostly green
```

## Install the extension

The extension is **unpacked** in v1.0 (loaded directly from the repo,
not from the Chrome Web Store). Web Store publish is post-launch.

1. Clone the repo if you haven't already:
   ```bash
   git clone <!-- canonical:github_repo_url -->https://github.com/keepwhatworks/trinity<!-- /canonical --> trinity-local
   cd trinity-local
   ```

2. Open `chrome://extensions` in Chrome (or `edge://extensions` in
   Edge; both work — same MV3 manifest).

3. Toggle **"Developer mode"** on (top-right).

4. Click **"Load unpacked"** and select the `browser-extension/` folder
   in this repo.

5. Copy the 32-character extension ID Chrome assigns (visible under
   the extension card).

6. Register the Native Messaging manifest so the extension can talk
   to the local engine:

   ```bash
   trinity-local install-extension --extension-id <ID>
   ```

   This writes the manifest into Chrome's Native Messaging directory
   (`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`
   on macOS; `~/.config/google-chrome/NativeMessagingHosts/` on
   Linux; registry on Windows). Edge gets the same manifest in its
   own NM directory.

7. Verify:

   ```bash
   trinity-local status
   # dispatch_ready should now be green
   ```

   Or click the Trinity toolbar icon — the launchpad opens in a
   side panel; the "Launch Council" composer should fire end-to-end.

## What v0.2 ships (current — `browser-extension/manifest.json` version `<!-- canonical:chrome_extension_version -->0.2.22<!-- /canonical -->`)

- Toolbar icon → opens the side panel (`sidepanel.html`), which hosts
  the launchpad + council composer (the manifest sets
  `side_panel.default_path` and has NO `default_popup`, so the action
  click opens the panel via `openPanelOnActionClick`)
- Action dispatch via Native Messaging (replaces the macOS Shortcut
  dispatcher; works cross-platform). <!-- canonical:chrome_action_allowlist_count -->17<!-- /canonical -->-entry `ACTION_ALLOWLIST` in
  `src/trinity_local/capture_host.py` gates which CLI surfaces are
  callable — defense in depth.
- Conversation capture content-scripts loaded on claude.ai /
  chatgpt.com / gemini.google.com (writes to
  `~/.trinity/conversations/<provider>/`). The gemini.google.com
  adapter shipped 2026-05-22 as part of v0.2 (task #135).

## What's deferred (post-v0.2)

Per the council-ratified roadmap (see
`docs/three-tier-architecture.md`):

- Per-site permission opt-in flow (user grants capture per origin
  rather than at install)
- Chrome Web Store listing
- Audit-log read surface in the popup ("last 10 operations")

(Trust-indicator badges in the popup were on the original v0.2
pickup list but the underlying substrate was retired 2026-05-22 —
see [`historical/trust-mode.md`](historical/trust-mode.md). Whatever
gating UX v1.1 rebuilds will dictate the indicator shape.)

## Audit log

When the extension fires `launch-council` (or any other allowlisted
action), the Native Messaging host stamps the audit log with
`tier=extension` so cross-tier provenance is preserved. Inspect:

```bash
grep '"tier": "extension"' ~/.trinity/audit.log | tail -20
```

The trust-*gating* library (`trinity_local.trust`) was retired
2026-05-22; the `trust.toml` config is no longer consulted by any
tier. The extension's allowlist (`background.js`
`ACTION_ALLOWLIST`) is its current gating surface. A unified gating
config + the `trust-init` / `trust-show` / `audit-show` CLI lands in
v1.1 as a fresh build. See [`historical/trust-mode.md`](historical/trust-mode.md) for the
original design — preserved as the historical record of the
substrate Trinity moved away from.

## Limitations and what to expect

- **File-URL access**: opening the file:// launchpad with the
  extension wired up requires the extension's "Allow access to file
  URLs" toggle (chrome://extensions). v0.1 doesn't auto-enable
  this — the user has to flip the toggle once.
- **Conversation capture is read-only**: the extension reads web-chat
  DOM via content scripts; it never injects user messages or modifies
  the provider UI.
- **Real-Chrome smoke test is gated**: see
  `tests/test_chrome_extension_smoke.py`. It uses the optional
  Stagehand driver in `browser-extension/` to launch local Chrome with
  the unpacked extension, then verifies the launchpad can receive
  `trinity-pong` through `onMessageExternal`.

## See also

- [`INSTALL-skill.md`](INSTALL-skill.md) — primary install path
  (skill tier via Claude Code)
- [`INSTALL-pip.md`](INSTALL-pip.md) — engine-only install
- [`MIGRATION.md`](MIGRATION.md) — for users coming from the
  macOS Shortcut dispatcher era
- [`three-tier-architecture.md`](three-tier-architecture.md) — full
  architecture spec
- `browser-extension/README.md` — extension-internal architecture
  notes
