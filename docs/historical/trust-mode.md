---
class: historical
---

# Trust mode + audit log

> **Historical record — retired 2026-05-22.** The `trinity_local.trust`
> library + `trust.schema.json` + `trust-init` / `trust-show` / `audit-show`
> CLIs were sunset alongside the rating UX (#134). Trinity now ships with
> NO unified gating UX; the Chrome extension's `ACTION_ALLOWLIST` in
> `capture_host.py` is the current gating surface. Whatever shape v1.1
> rebuilds, this doc is the v1.0 design context — not live config.
>
> Council-ratified by `council_c18f739a0234aa58` (2026-05-16) as the
> Phase 6 design of the three-tier architecture. Preserved verbatim
> below so the next iteration can read the original intent.

Trinity's trust substrate is what makes "we respect your choices" a
credible claim instead of a marketing line. Two coupled mechanisms:

  1. **~/.trinity/trust.toml** — declarative grants. What Trinity is
     allowed to do without prompting.
  2. **~/.trinity/audit.log** — every operation Trinity ran. Append-
     only JSONL. You can `grep` it.

## The 30-second model

By default, every Trinity operation either prompts (skill tier: the
prompt is Claude Code's permission dialog; pip tier: a stderr y/N) or
silently audit-logs. You can pre-grant operations you trust:

```toml
# ~/.trinity/trust.toml
schema_version = 1

[trust]
default = "ask"

[trust.operations]
embed_batch = "trust"       # always proceed
launch_council = "ask"      # always prompt
```

Bootstrap a starter file by running the library helper or copying
the example below. The `trust-init` CLI surface was deferred to v1.1
in the pre-launch simplification pass (commit 66e5c3e) — the trust
library at `trinity_local.trust` still ships in v1.7.4, but the user-
facing CLI returns in v1.1.

Until then, write `~/.trinity/trust.toml` manually with the structure
shown above.

## Resolution order

Most specific wins:

1. `--dangerously-trust-all` flag OR `TRINITY_DANGEROUSLY_TRUST_ALL=1`
   env var → "trust" for everything
2. `[trust.rules]` exact `tier.operation` override (e.g.
   `"extension.launch_council" = "ask"`)
3. `[trust.operations]` per-operation grant
4. `[trust.tiers]` per-tier default
5. `[trust]` global default (defaults to "ask")

Inspect the resolved level programmatically until the v1.1 CLI returns:

```python
from trinity_local.trust import resolve_trust
result = resolve_trust(operation="embed_batch", tier="pip")
# {"operation": "embed_batch", "tier": "pip", "level": "trust", ...}
```

## --dangerously-trust-all

Matches Claude Code's convention. When set (flag or env var):

- All operations proceed without prompting.
- The audit log STILL fires. Bypassing prompts is not bypassing
  accountability — that's the whole point of the substrate.

The flag is loud on purpose. Trinity prints a stderr warning on every
top-level invocation when trust-all is active. If you don't see the
warning, the flag isn't taking effect.

## Audit log

Every Trinity operation appends one JSONL line to
`~/.trinity/audit.log`:

```json
{"ts":"2026-05-16T20:23:42","script":"embed","operation":"embed_batch","tier":"skill","trust_mode":"trust:toml:operation","outcome":"ok","args":{"n_texts":3}}
```

Read it directly (the `audit-show` CLI is deferred to v1.1):

```bash
tail -20 ~/.trinity/audit.log         # raw JSONL
tail -20 ~/.trinity/audit.log | jq    # pretty-printed
```

The audit log is append-only. Atomic single-write per operation
(POSIX O_APPEND on a regular file). If the log can't be written —
disk full, permissions wrong — Trinity emits a loud stderr warning
on first failure so you notice immediately. Silent gaps in the
audit log would defeat the trust substrate.

## Cross-tier propagation

When the Chrome extension's native host spawns `trinity-local` as a
subprocess, the audit log needs to record the originating tier
(`extension`), not the subprocess tier (`pip`). The native host sets
three env vars before spawning:

```
TRINITY_ORIGIN_TIER=extension
TRINITY_ORIGIN_ACTION=launch-council
TRINITY_INVOCATION_ID=<uuid>
```

Trinity's `audit_log()` reads these and stamps the record
accordingly. So when you grep `audit.log` for extension-originated
operations, you see them as such regardless of where they ran.

## What v1.0 shipped

- `~/.trinity/trust.toml` schema (`schema_version = 1`)
- `~/.trinity/audit.log` JSONL append + atomic single-write
- `--dangerously-trust-all` flag + env var
- `trinity_local.trust` library (CLI surface deferred to v1.1)
- Cross-tier `TRINITY_ORIGIN_TIER` env propagation
- Loud-fail on audit-write errors (stderr warning, rate-limited)

## What v1.1 ships

Per the council verdict (`council_c18f739a0234aa58`):

- Automatic audit rotation (v1.0 warns above 50 MB in `status`)
- Visible trust indicators in launchpad header + extension popup
- `--tier`, `--operation`, `--outcome` filter flags on audit-show
- Global `--dangerously-trust-all` flag on `trinity-local` (v1.0
  has only the env var)

## See also

- `docs/three-tier-architecture.md` — full architecture spec
- `schemas/trust.schema.json` — JSON Schema for trust.toml (canonical); `skills/trinity/schemas/trust.schema.json` is the byte-identical bundled copy
- `docs/launch_councils/council_c18f739a0234aa58.json` — Phase 6
  council outcome
