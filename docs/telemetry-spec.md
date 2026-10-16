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
ships **on by default** to close the feedback loop. Trinity can't improve routing
without seeing which providers win which task types. The guarantee is not *off*, it
is *provably no-PII*: only categorical labels (`task_type` from a fixed list, anything else
sent as `other`; `winner` folded to a known provider or `other`; `member_count`;
`mode`; and once per install `activation_source` + `surface`) ever leave the machine, for
Google Analytics, from the CLI and MCP server only, carrying the rotating id, never the local
one. The launchpad page sends nothing. Enforced structurally by `telemetry.build_outbound_event_payload`'s `DISCLOSED_EVENT_PARAMS`
allowlist (a param outside the set is dropped at the wire boundary) and asserted by
`tests/test_telemetry_no_pii.py`.

**Channel attribution (founder decision 2026-10-11).** The install command may carry
`TRINITY_CHANNEL=<channel>`. `trinity-local install` stores it locally (allowlisted in
`telemetry.CHANNELS`, anything else becomes `other`) and sends nothing. The first completed
council or verify on a build that can send sends one `activation` event (`activation_source`,
`surface`), marked sent so it never repeats; a use while sending is impossible (no credentials,
sharing off) is not marked, so the event goes on the first use that can send
(`telemetry.record_activation`).

Where the key lives (founder decision 2026-10-11: ship it in public builds):

- the GA4 key is **never in git**. A release built with it in the maintainer's shell attaches
  a wheel that carries it (`scripts/inject_ga4_credentials.py`, `internal/sync_public.sh`),
  and `install.sh` installs that wheel. Anyone can extract the key from the wheel; it can only
  add events to the property, never read them, and it can be rotated.
- an install without the key (a source checkout, the tag tarball, or a release built without
  it) has no credentials, and both the CLI and the launchpad silently no-op.
- the browser send path is withheld an `endpoint` unless credentials are present, so it can't
  bypass the Python no-op.

Control:

- `trinity-local telemetry-disable` turns it off. Data stops immediately.
- adjustable later from Launchpad settings.

## 2. Summary-level sharing only

Telemetry sends two events, both categorical: `council_complete` (task type, winner, member
count, mode) and, once per install, `activation` (install channel, first-use surface).

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
action via `trinity-local download-embedder` (or `HF_HUB_OFFLINE=0 huggingface-cli download nomic-ai/modernbert-embed-base`).
MCP child processes inherit the env so the guarantee propagates through
every spawn. Override per-invocation when explicitly pulling fresh weights.

---

## Consent Flow

## Install-time prompt

During setup, ask:

> Help improve Trinity by sharing anonymous usage and model-rating summaries?

The copy should clearly distinguish:

### Shared

- per council: task type (a fixed list), winner, member count, mode
- once per install: the install channel and whether the first use was a council or a verify
- a monthly-rotating anonymous id

### Not shared

- transcripts
- prompts
- outputs
- code
- file paths

## Launchpad settings

Users can:

- enable and disable sharing (`telemetry-enable` / `telemetry-disable`, and the launchpad
  settings modal)
- reset the anonymous identity (`telemetry-reset-id` and the modal button)
- see where events go (the modal's "Sent to" row: Google Analytics from the CLI and MCP server,
  or "Not configured" for a source build with no analytics key)
- see the exact outbound payload (`telemetry-show --wire`)

The launchpad's own events (`launchpad_view` and an `elo_snapshot` of provider win-rates), the
self-configured collector they could be sent to (`TRINITY_TELEMETRY_ENDPOINT`), the
`telemetry-endpoint` command and the `share_elo_summaries` setting were removed on 2026-10-15
(founder-approved): without a configured collector the page sent nothing.

---

## Data Model

## Anonymous identity

`share_install_id`, generated on opt-in: unique per install, not derived from prompts or provider
content, resettable. It never leaves the machine; events carry a monthly-rotating id derived
from it (`outbound_client_id`).

## Local settings record

`~/.trinity/settings/telemetry.json`:

```json
{
  "sharing_enabled": true,
  "share_usage_events": true,
  "share_install_id": "share_xxx",
  "consented_at": "2026-04-28T12:00:00Z"
}
```

plus, once per install, `activation_source`, `activation_sent_at` and `activation_attempted_at`.

---

## Event Types

Both are sent from Python (`record_event`, `record_activation`) through
`build_outbound_event_payload`, which drops any parameter outside `DISCLOSED_EVENT_PARAMS`.

- `council_complete`: `task_type` (a fixed list; anything else is `other`), `winner` (a known
  provider or `other`), `member_count`, `mode`. Sent when a council finishes.
- `activation`: `activation_source` (an allowlisted channel or `other`) and `surface`
  (`council` or `verify`). Sent once, on the first completed council or verify.

---

## Public Aggregate Model (deferred)

**Status: not implemented in code today.** Current `telemetry.py` sends the allowlisted categorical events to Google Analytics, on by default, under a monthly-rotating id. There is no aggregation backend, no `last_upload_at` round-trip, no public-Elo response shape.

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

- a monthly-rotating anonymous id
- the categorical parameters of the two events above

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

The settings page includes: the sharing toggle, where events go ("Sent to"), and resetting the
anonymous id. `trinity-local telemetry-show --wire` prints the exact payload.

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
