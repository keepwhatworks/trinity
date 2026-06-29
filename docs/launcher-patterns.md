---
class: historical
---

# Launcher Patterns

This note captures the closest product patterns to `trinity-local`'s planned
macOS architecture:

- file-backed local state
- lightweight or static UI
- native deep links / URL schemes
- local automation execution
- no always-on localhost server

## Closest matches

### Raycast

Useful ideas:

- `raycast://` deeplinks can trigger commands from the browser, terminal, or
  other apps.
- commands remain local and native, not web-hosted.
- confirmations can sit between a deeplink and command execution.

What to copy:

- a stable command address space
- a launcher as control plane, not as transcript store
- command-level deep links

What not to copy:

- requiring a full launcher app as Trinity's main UX

### Alfred

Useful ideas:

- workflows are explicit graphs of triggers, scripts, and actions
- strong support for URL opening, shell scripts, and AppleScript
- imported workflows can carry setup metadata and onboarding

What to copy:

- explicit trigger -> action chain
- installable automation recipe
- human-readable workflow configuration

What not to copy:

- making the user live inside the launcher UI

### LaunchBar

Useful ideas:

- custom URL scheme (`x-launchbar`)
- script-based actions
- background execution of local commands

What to copy:

- URL-driven local automation
- command library that can run in background

### Hookmark

Useful ideas:

- works across many apps through automation surfaces instead of owning the
  documents itself
- supports AppleScript, shell, Shortcuts, and x-callback-url
- uses asynchronous callback patterns when apps cannot answer synchronously

What to copy:

- cross-app context layer
- do not force one storage model across providers
- fallback hierarchy: native automation first, UI scripting last

### Obsidian URI

Useful ideas:

- custom URI protocol mapped to concrete app actions
- useful because the action space is small and stable

What to copy:

- action-oriented URI design
- argument encoding discipline

### Apple Shortcuts

Useful ideas:

- native URL-triggerable dispatch on macOS
- compatible with browser links
- can bridge URL -> shell -> file artifact -> app open

What to copy:

- use one named Shortcut as the native dispatcher
- keep browser pages dumb and static

## Conclusion

The shipped v1 bridge and the next launch target are:

1. **Chrome-extension launch is the v1 bridge:** the Trinity Chrome
   extension hosts the launchpad — click the toolbar icon to open the
   local launchpad/review cockpit without a terminal window. Native
   Messaging carries launchpad button clicks back to `trinity-local`.
   The earlier `Trinity.app` osacompile wrapper was retired pre-launch
   in favor of the cross-platform Chrome extension.
2. **Cowork-style desktop launch is the non-coder target:** the durable
   acquisition surface is a real desktop app with an app icon, menu bar,
   hotkey, first-run setup/status UI, and a local cockpit over
   `~/.trinity/`. The extension remains browser capture and dispatch
   plumbing; it is not the long-term app shell.
3. **Direct prompt → council** is the primary action: launchpad has a textarea + autofill suggestions; user types a prompt or picks a replay candidate; click dispatches `launch_council` through the Chrome extension's Native Messaging host.
4. Trinity writes `PromptBundle` and `CouncilOutcome` files.
5. The static launchpad page renders the personal routing table, the `lens`es card, and recent councils.
6. Launch actions post a JSON message to `trinity-local-capture-host` (the Native Messaging endpoint registered by `install-extension`).
7. The capture host spawns the local CLI as a one-shot subprocess and exits when the council completes — no persistent process. (The earlier macOS Shortcut path through `~/.trinity/bin/trinity-dispatch` was retired pre-launch; an inert `shortcuts_integration` shim survives so older renderers don't break before their JS surgery lands.)
8. Finished councils write to `council_outcomes/`; the next launchpad render reflects them via on-demand `compute_personal_routing_table()` (no durable state file).
9. **Mobile starts as review links**: the phone opens a web/deep link to a
   council review page, then writes ratings through the paired desktop when
   available.
10. **Tool-triggered ingest replaces watchers**: `ingest-recent` is fired by the Chrome extension and by MCP `ask` with a 1s deadline; the legacy `watch-once`/`watch-loop` CLIs were retired pre-launch with the daemon subsystem.

## Action taxonomy

Current dispatch actions (`src/trinity_local/dispatch_registry.py`):

- `launch_council` — primary path; user picks members + task
- `stop_council` — cancel an in-flight council
- `open_review` — open the unified council page
- `start_council` — alternative entry from a prepared bundle
- `council_iterate` — canonical iteration action (replaces continue/refine/auto-chain). Args: `{rounds: int, prompt: str|None}`. Legacy aliases (`council_continue`, `council_refine`, `council_auto_chain`) still accepted as input — they map to `council_iterate` via the dispatch shim — so old launchpad URLs and saved Shortcuts keep working.
- `open_path`, `open_url`, `run_applescript`, `run_command` — generic dispatch helpers

(The `rate_council` dispatch action was retired 2026-05-21/22 alongside the
rest of the rating UX — chairman pick is the auto-recorded supervision signal
now. `council-rate` CLI + MCP `record_outcome` are retired; pick-veto on
extracted cortex rules is the remaining user-side supervision surface.)

If multiple pending actions exist for the same task, priority order:

1. `launch_council` / `start_council`
2. `open_review`
3. `recommendation` / `workflow_suggestion`

This keeps the launchpad from becoming noisy and ensures the primary CTA is always the most actionable one.
