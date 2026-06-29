---
class: aspirational
---

# Trinity Telemetry Spec

## Goal

Trinity needs a telemetry model that supports:

- opt-in public benchmarking
- public Elo / radar aggregation
- basic launchpad usage measurement
- strong privacy boundaries
- compatibility with a local-first product

Telemetry is **not** the source of truth for the product.

The source of truth remains local state:

- councils
- user choices
- switching behavior
- recommendation outcomes

Telemetry exists to support:

- aggregate public Elo
- social comparison
- hosted growth metrics

---

## Principles

## 1. Default-on, provably no-PII, one command off

**Superseded 2026-05-27** (this section originally read "Opt-in only"): telemetry
ships **on by default** to close the feedback loop — Trinity can't improve routing
without seeing which providers win which task types. The guarantee is not *off*, it
is *provably no-PII*: only categorical labels (`task_type`, `winner`, `member_count`,
`mode`) and an anonymous numeric Elo snapshot ever leave the machine, enforced
structurally by `telemetry.build_outbound_event_payload`'s `DISCLOSED_EVENT_PARAMS`
allowlist (a param outside the set is dropped at the wire boundary) and asserted by
`tests/test_telemetry_no_pii.py`.

Two further gates make a stock install send **nothing**:

- the public build ships **no GA4 credentials** — absent `TRINITY_GA4_MEASUREMENT_ID`
  + `TRINITY_GA4_API_SECRET`, both the CLI and the launchpad silently no-op;
- the browser send path is withheld an `endpoint` unless those creds are present, so
  it can't bypass the Python no-op.

Control:

- `trinity-local telemetry-disable` turns it off; data stops immediately.
- adjustable later from Launchpad settings.

## 2. Summary-level sharing only

Telemetry should send:

- anonymous usage events
- Elo summaries
- council counts
- coarse benchmark signals

Telemetry should **not** send by default:

- raw transcripts
- raw prompts
- raw model outputs
- file paths
- repo names
- local code

## 3. Local-first truth

The product continues to learn locally first.

Telemetry is a derived export layer, not the operational core.

## 3a. No surprise outbound calls

Trinity makes zero outbound network calls during normal operation outside
the (default-on, opt-out) telemetry path. `main()` pins `HF_HUB_OFFLINE=1` +
`TRANSFORMERS_OFFLINE=1` + `HF_HUB_DISABLE_TELEMETRY=1` at startup via
`setdefault`, so the embedding model loads from `~/.cache/huggingface/hub/`
without contacting the Hub. The one-time download is a deliberate user
action via `HF_HUB_OFFLINE=0 huggingface-cli download nomic-ai/modernbert-embed-base`.
MCP child processes inherit the env so the guarantee propagates through
every spawn. Override per-invocation when explicitly pulling fresh weights.

## 4. Split "views" from "ratings"

Launchpad opens and Elo snapshots are different event types.

Do not treat “viewed the page” as equivalent to “generated new benchmark data.”

---

## Consent Flow

## Install-time prompt

During setup, ask:

> Help improve Trinity by sharing anonymous usage and model-rating summaries?

The copy should clearly distinguish:

### Shared

- launchpad views
- council counts
- anonymous Elo summaries
- app version

### Not shared

- transcripts
- prompts
- outputs
- code
- file paths

## Launchpad settings

Users must be able to:

- enable sharing (shipped — launchpad settings modal)
- disable sharing (shipped)
- reset anonymous share identity (shipped — `telemetry-reset-id` CLI; modal button)
- configure endpoint URL (shipped)

**Future / not yet implemented:**
- preview shared payload (UI not built; the shared event shape is documented below in this file, but no in-launchpad preview exists)
- see last successful upload time (no `last_upload_at` field tracked in `~/.trinity/settings/telemetry.json` today)

Recommended settings fields:

- `sharing_enabled`
- `share_usage_events`
- `share_elo_summaries`
- `last_upload_at`
- `share_install_id`

---

## Data Model

## Anonymous identity

Generate a stable random ID on opt-in:

- `share_install_id`

Properties:

- unique per local install
- not derived from prompts or provider content
- resettable by the user

## Local settings record

Recommended local structure:

```json
{
  "sharing_enabled": true,
  "share_usage_events": true,
  "share_elo_summaries": true,
  "share_install_id": "share_xxx",
  "consented_at": "2026-04-28T12:00:00Z",
  "last_view_upload_at": "2026-04-28T12:10:00Z",
  "last_elo_upload_at": "2026-04-28T12:10:00Z",
  "last_elo_hash": "sha1:...",
  "last_upload_status": "ok"
}
```

---

## Event Types

## 1. `launchpad_view`

Sent when the user views the Launchpad and sharing is enabled.

Purpose:

- usage / retention measurement
- rough heartbeat for active installs

### Payload

```json
{
  "event": "launchpad_view",
  "version": 1,
  "share_install_id": "share_xxx",
  "app_version": "0.1.0",
  "timestamp": "2026-04-28T12:10:00Z",
  "surface": "launchpad",
  "council_count_bucket": "10-49",
  "provider_count": 3
}
```

### Cadence

- send on each Launchpad view if sharing is enabled
- optionally dedupe views closer than a short threshold if needed later

---

## 2. `elo_snapshot`

Sent only when the Elo/radar state has changed materially or gone stale.

Purpose:

- public aggregate provider Elo
- public task-kind comparison
- future radar percentile / benchmark calculations

### Payload

```json
{
  "event": "elo_snapshot",
  "version": 1,
  "share_install_id": "share_xxx",
  "app_version": "0.1.0",
  "timestamp": "2026-04-28T12:10:00Z",
  "window": "last_30_days",
  "council_count": 42,
  "providers": {
    "claude": {
      "elo": 1672,
      "writing": 1730,
      "research": 1580
    },
    "antigravity": {
      "elo": 1615,
      "writing": 1500,
      "research": 1710
    },
    "codex": {
      "elo": 1548,
      "coding": 1695
    }
  },
  "matchups": {
    "claude_vs_gemini": { "claude_wins": 12, "gemini_wins": 8 },
    "claude_vs_codex": { "claude_wins": 7, "codex_wins": 10 }
  }
}
```

### Cadence

Send only if one of these is true:

- first upload ever
- council count changed
- Elo summary hash changed
- last Elo upload is older than 24 hours

Do **not** post the full Elo payload on every page view if nothing changed.

---

## 3. `settings_update`

Optional event when sharing settings change and sharing is still enabled at the
moment of update.

Purpose:

- understand opt-in / opt-out behavior

### Payload

```json
{
  "event": "settings_update",
  "version": 1,
  "share_install_id": "share_xxx",
  "timestamp": "2026-04-28T12:12:00Z",
  "sharing_enabled": true,
  "share_usage_events": true,
  "share_elo_summaries": false
}
```

If the user turns sharing off, Trinity should simply stop sending future events.

---

## Upload Timing

## Recommended flow on Launchpad view

When the user opens the Launchpad:

1. render local page as usual
2. if sharing disabled:
   - do nothing
3. if sharing enabled:
   - send `launchpad_view`
   - check whether `elo_snapshot` is stale or changed
   - send `elo_snapshot` only if needed

This produces:

- a clean usage heartbeat
- fresh public Elo without duplicate spam

---

## Local Snapshot State

To support dedupe, store:

- `last_view_upload_at`
- `last_elo_upload_at`
- `last_elo_hash`
- `last_upload_status`

Recommended Elo hash input:

- window
- provider Elo values
- task-kind Elo values
- matchup summary
- council count

If the hash is unchanged, the snapshot does not need to be re-uploaded.

---

## Public Aggregate Model (deferred)

**Status: not implemented in code today.** Current `telemetry.py` only supports a configurable endpoint URL with default-off; there is no aggregation backend, no `last_upload_at` round-trip, no public-Elo response shape.

When/if a backend is built, the natural shape is:

The backend computes:

- public provider Elo
- task-kind public Elo
- matchup win/loss aggregates
- percentile ranges

The backend returns:

- public Elo
- public task-kind Elo
- percentile / benchmark data for future leaderboard surfaces

This allows Trinity to render:

- your Elo
- public Elo
- deltas
- percentile labels

without ever uploading raw local transcripts.

---

## Privacy Boundaries

## Explicitly allowed to upload

- anonymous install id
- app version
- page view event
- council counts
- Elo summaries
- matchup counts
- coarse provider / task-kind rating summaries

## Explicitly not uploaded by default

- raw prompt text
- transcript text
- raw council outputs
- code snippets
- file paths
- repo names
- local environment details beyond coarse app/provider counts

If Trinity later adds optional public battle-card sharing, that must be a
separate explicit action, not covered by this telemetry consent.

---

## UI Copy Requirements

## Install copy

The install prompt should say:

- what gets shared
- what does not get shared
- that sharing is optional
- that settings can be changed later

## Launchpad settings copy

The settings page should include:

- `Sharing enabled`
- `Last upload`
- `Preview shared data`
- `Turn off sharing`
- `Reset anonymous ID`

Trust depends on reversibility and clarity.

---

## Why This Model Fits Trinity

This telemetry design matches the architecture:

- local-first truth
- static local pages
- council-derived learning
- optional public benchmarking

It also matches the product strategy:

- council creates the data
- local Elo creates the value
- anonymous shared summaries make everyone's results better together
- public aggregate comes back as:
  - radar normalization
  - public Elo
  - comparison layer

That is the cleanest path to “your stack vs the public stack” without turning
Trinity into a transcript-collection service.
