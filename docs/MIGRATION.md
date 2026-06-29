---
class: live
---

# Migration

> Effective with the v1.0 launch wave. Existing installs keep working;
> nothing breaks. This document covers two migration arcs that landed
> together in this release:
>
> 1. **Three-tier architecture** (Skill / Pip / Extension) — the new
>    framing for *how* you interact with Trinity. Existing users see
>    the engine they always had, plus a skill orchestration layer on
>    top.
> 2. **macOS Shortcut → Chrome extension dispatcher** — the launchpad
>    button-click implementation. The Shortcut is now the legacy
>    tier-2 fallback; the extension is the cross-platform default.

---

## Arc 1 — Three-tier architecture (Phase 1, council `ff3da1fa84906791`)

Pre-v1.0, Trinity was a single pip wheel: `trinity-local` the CLI did
everything, the launchpad opened via macOS Shortcut, the Chrome
extension only captured web-chat transcripts. Three concerns smushed
into one shape.

v1.0 splits this into three tiers, each independently functional:

- **Tier 1 — MCP server** (primary, 2026-05-19 pivot). Trinity
  registers as an MCP server in every harness that supports MCP
  (Claude Code, Codex CLI, Antigravity, Cursor, Claude Desktop). The
  agent calls tools like `mcp__trinity-local__run_council` inline.
  Tool docstrings are the contract — no `/skill` invocation required.
  The skill at `~/.claude/skills/trinity/` is kept as a **back-compat
  symlink alias** so users who already type `/trinity` in Claude Code
  keep working; new users never need to know it exists.
- **Tier 2 — Engine** (`~/.trinity/code/src/trinity_local/`): the
  Python engine MCP tools (and the legacy skill) call. The
  `trinity-local` shell wrapper at `~/.local/bin/` resolves to it via
  `launcher_path_resolver.sh` — probes browser extension dirs first
  (Web Store auto-update path), then `~/.trinity/code/`, then the
  legacy skill location.
- **Tier 3 — Chrome extension** (discovery + capture sidecar). Two
  jobs: (1) browser capture — streams web chats from claude.ai /
  chatgpt.com / gemini.google.com into `~/.trinity/conversations/`
  via Native Messaging, (2) cross-platform launchpad dispatcher.
  The popup's "Copy install brief" button is the **non-technical-user
  entry point**: install the extension → paste brief into Claude
  Code / Desktop → agent runs install.sh + register-extension +
  install-mcp end-to-end. Zero terminal expertise required.

Data in `~/.trinity/` is invariant across tiers — same files, same
schemas. The tiers differ in *how you invoke Trinity*, not *what
Trinity computes*. The falsifiable invariant: cosine similarity
≥ 0.9999 between any two backends on the same input under pinned
tokenizer + model hash, identical k-means cluster assignments under
the same seed.

**What existing users do**: nothing required. Your existing `trinity-
local` install IS the Tier 2 wheel. Type `/trinity` in Claude Code if
you want the orchestration; skip it if you prefer driving the CLI by
hand. Both work.

**What changed under the hood**: the `scripts/` directory ships six
shebang-runnable Python scripts (`embed.py`, `cluster.py`, `pca.py`,
`descriptor.py`, `signature.py`, `anchor.py`) that the pip tier
imports. The dependency inversion (pip imports from scripts/ exclusively)
ships in v1.1; v1.0 has scripts/ wrapping the pip tier with the same
output guarantees. See [`three-tier-architecture.md`](three-tier-architecture.md).

**Audit log**: `~/.trinity/audit.log` ships in v1.0 — every Trinity
operation that flows through the scripts cluster writes an entry via
`scripts/_runtime.audit_log()`. Inspect with `tail -20`.

The trust-*gating* library (`trinity_local.trust`) was retired
2026-05-22 in the post-launch consistency sweep (iter #117) — nothing
currently reads `~/.trinity/trust.toml`. The gating config + the
user-facing CLI (`trust-init` / `trust-show` / `audit-show`) rebuild
fresh in v1.1. See [`historical/trust-mode.md`](historical/trust-mode.md) for the
original design — preserved as the historical record of the
substrate Trinity moved away from.

---

## Arc 2 — macOS Shortcut → Chrome extension dispatcher (Phase 4b)

## TL;DR

If you're on macOS today and `~/.trinity/portal_pages/launchpad.html`
already opens and dispatches: **you're fine, do nothing right now.**
The Shortcut is now the legacy tier-2 fallback. Whenever you have ten
minutes, install the browser extension to gain Linux/Windows support,
faster click → council latency, and structured error responses.

If you're on Linux or Windows: the extension is the only working path.
The Shortcut never worked off macOS — silent breakage was the bug.

## What changed

Before the transition, every launchpad button click fired a
`shortcuts://run-shortcut?name=Trinity%20Dispatch&input=…` URL that
only macOS Shortcuts could intercept. The URL handler was invisible
on Linux/Windows; clicks went nowhere with no error.

After this release the launchpad routes each click through three tiers
in priority order:

1. **Chrome extension** — `chrome.runtime.sendMessage(<id>, …)` to a
   local extension that forwards to a Native Messaging helper
   (`trinity-local-capture-host`) on every platform Chromium runs on.
2. **macOS Shortcut** — the legacy `shortcuts://` URL. Unchanged from
   the prior release; kept so existing macOS installs don't break.
3. **Inline install banner** — when neither tier is wired up, the
   launchpad surfaces a one-paragraph banner that links here.

The dispatch chooser cached the detection state in `sessionStorage`
so normal clicks pay no probe latency (verdict
`council_fb374b01311885cc`).

## Upgrade path

### macOS (Shortcut user today)

```bash
# 1. Load the extension manually (one-time).
#    Open chrome://extensions in Chrome, enable Developer mode,
#    click "Load unpacked", and select browser-extension/ in this repo.
#    Copy the 32-character ID Chrome assigns.

# 2. Register the Native Messaging manifest with Trinity:
trinity-local install-extension --extension-id <ID>

# 3. (Optional) keep the Shortcut around for tier-2 fallback — it
#    costs nothing and means the launchpad still works if the
#    extension is disabled.
```

After step 2 every click goes through tier 1. The Shortcut never
fires unless the extension fails. You can keep your existing macOS
Shortcut around as a safety net — but note that the `shortcut-install`
CLI no longer exists (retired pre-launch in commit ab36d86, per the
section below); you can't reinstall the Shortcut from scratch, only
preserve what's already there.

### Linux

```bash
# 1. Load the extension manually in Chromium / Brave / Edge
#    (chrome://extensions → Developer mode → Load unpacked).

# 2. Register the Native Messaging manifest:
trinity-local install-extension --extension-id <ID>

# 3. Install a desktop launcher entry so the launchpad appears
#    in your application menu:
trinity-local install-launcher
```

The macOS Shortcut dispatcher was retired pre-launch (commit ab36d86);
the Chrome extension is now the canonical dispatch path on all OSes.
If you previously ran `shortcut-install`, the CLI no longer exists —
just install the Chrome extension instead.

### Windows

```bash
# 1. Load the extension manually in Chrome / Edge.
# 2. Register the Native Messaging manifest:
trinity-local install-extension --extension-id <ID>
# 3. Install a Start Menu shortcut:
trinity-local install-launcher
```

Same shape as Linux. If you used Trinity on WSL with the Shortcut,
the Shortcut never reached your Windows host — it stayed inside the
WSL filesystem. The extension is the first cross-host path Trinity
has ever shipped on Windows.

## Why both paths still exist

The macOS Shortcut path predates the extension. Removing it would
break every existing macOS user who hasn't yet loaded the unpacked
extension. The transition pattern is the same one Chrome itself uses
for new APIs: the new path becomes the default, the old path stays as
a fallback, and the old path eventually retires once telemetry shows
nobody uses it.

For Trinity specifically:

- The Shortcut still works on macOS for 100% of installs that already
  ran `shortcut-install`. Don't break what works.
- The extension is platform-portable and returns structured errors
  (`native-host-unavailable`, etc.) that the launchpad surfaces as a
  precise install hint instead of a silent no-op.
- The two paths now have **full button parity** as of Phase 4b:
  every launchpad control has a narrow action-allowlist entry in
  `capture_host.ACTION_ALLOWLIST` (see the table in the
  "Cross-platform parity" section below). The macOS Shortcut path
  stays as tier-2 fallback so existing installs keep working,
  but no button is Shortcut-only anymore.

## When the Shortcut path actually retires

Shortcut retirement is gated on measurement infrastructure we don't
have yet. Trinity ships v1.0 without dispatch-tier telemetry; any
retirement commitment is a future gate, not a calendar date. Concretely:
the macOS Shortcut path will not begin a deprecation-warning release
until (a) an opt-in measured release lands that reports per-install
dispatch-tier usage and (b) that release shows the extension carrying
the dispatch load on macOS for an extended sample. One release after
the warning lands, the path goes away.

Until then: it's a load-bearing fallback for the installs that already
have the Shortcut wired. The `shortcut-install` CLI was retired
pre-launch (commit ab36d86); existing macOS users keep their Shortcut
where it already lives in macOS Shortcuts.app, but no new installs can
be bootstrapped via Trinity itself.

## FAQ

### Cross-platform parity (as of Phase 4b)

Every launchpad control maps to a narrow action-allowlist entry,
so every button works on Linux + Windows when the extension is
wired. The macOS Shortcut path remains as tier-2 fallback for
existing installs.

The allowlist is `capture_host.ACTION_ALLOWLIST` — each entry maps a
launchpad button kind to exactly one CLI subcommand (council launch /
stop, ingest-recent, telemetry enable/disable/reset-id, render-me-card,
extension repair, import-export, and the rest). Each entry is the
narrowest possible surface — no `run_command`, no arbitrary shell.
Spoofed payloads can't trigger anything beyond the specific CLI
subcommand named in the allowlist.

### Will my existing macOS install break when I upgrade?

No. The launchpad detects whether `chrome.runtime.sendMessage` is
reachable to a configured extension ID; if not, it falls back to the
Shortcut URL just like before. The only thing that changes is the
order of tiers — extension is tried first, and the result reaches the
chairman with structured `{ok, error, hint}` instead of just firing
and hoping.

### What if I don't want to install a browser extension?

Don't. The Shortcut path keeps working on macOS. If you're on Linux
or Windows and want to avoid the extension entirely, `trinity-local
serve` opens the launchpad over `http://localhost:8765` and you can
copy/paste commands from there into your terminal.

### What about Safari / Firefox users?

Safari doesn't ship Native Messaging, so there's no extension path
there today. Safari users on macOS fall through to the Shortcut tier
automatically. Firefox does ship Native Messaging with a different
manifest schema; `trinity-local install-extension --firefox` writes
the manifest, but the extension itself needs the Firefox-specific
`applications.gecko.id` field — see browser-extension/README.md.

### How do I check which tier is wired up?

```bash
trinity-local portal-html
# Prints the launchpad path AND the dispatch readiness snapshot:
#   {
#     "dispatch": {
#       "extension_configured": true,
#       "host_on_path": true,
#       "shortcut_applicable": true,
#       "shortcut_installed": true,
#       "ready": true,
#       "recommended_action": null
#     }
#   }
```

`ready: true` plus `recommended_action: null` means at least one tier
is fully wired. When `ready` is false, `recommended_action` tells you
exactly what to fix.

## See also

- `browser-extension/README.md` — extension architecture + Phase 4
  three-tier dispatch logic
- `trinity-local status` — pre-flight diagnostics + dispatch readiness
  check at session start
