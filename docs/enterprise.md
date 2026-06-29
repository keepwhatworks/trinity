---
class: aspirational
---

# Trinity for teams

> **One product. Free for individuals; the same MCP runs in your org. You pay
> for support, not a different app.**

Trinity convenes Claude, ChatGPT, and Gemini in a structured council so your team
sees where the models agree and where they split — then learns which model wins
which kind of work.

Trinity is not a separate enterprise SKU. The plugin, the MCP server, the Chrome
extension, the `~/.trinity/` data contract — a platform team deploys the *exact
same artifact* an individual installs with one curl line. There is no
"Trinity Enterprise" fork to maintain, no feature wall, no second codebase. What
an enterprise buys is **support**, not software (an open-core / Red-Hat shape).

This page is written for the platform owner, eng leader, and security reviewer.
The README and the website lead with the audience-neutral benefit — *proof of
which model wins **your** kind of work* — because that benefit reads identically
for an individual choosing a model and a platform team routing per-token spend.
This page is for the platform owner, the eng leader, and the security reviewer.

---

## The same benefit, two scales

The core artifact is the **personalized eval** — Trinity scores providers on your
own *rejection signal*: the times someone on your team **rewrote** a model's
answer (REFRAME / REDIRECT / COMPRESSION / SHARPENING). It is the one benchmark a
model vendor and a request-router (OpenRouter / Portkey / LiteLLM / Bedrock)
**cannot** reconstruct — the correction happens in the editor, never as an API
call a gateway could log.

- **For an individual**, that proof answers *"which model should I ask?"* — within
  the flat-rate subscription they already pay for.
- **For an enterprise**, the *same* proof answers *"which model should we route to,
  per token?"* — where it lands directly on the bill.

Same artifact, same wording, two scales. That's why there's one product.

---

## Why routing is the lever at org scale

Individuals dispatch through their own flat-rate consumer subscriptions
(Claude Pro / Code, ChatGPT Plus, etc.), so their marginal cost is ~$0 — there is
no per-token bill, and that is exactly why the consumer surfaces carry **no** cost
framing.

Enterprises are different: they dispatch against **per-token API pricing**, with no
subscription flat rate to hide behind. And the per-token price of frontier models
spans a wide band — the most expensive frontier tier can run **20–40×** the
price of a strong high-volume model. At a billion tokens a month, sending work to
the flagship that a cheaper model wins on *your* rejection signal is the single
largest avoidable line item.

Trinity is the layer that decides **which model gets which task**, learned from
your team's own corrections rather than a vendor's leaderboard. The chairman picks
the model your team would have picked; the per-basin routing tally (`picks.json`)
turns that into a reusable rule. Routing to the cheapest model that *still wins
your work* is where the spend story and the quality story become the same story.

> Trinity does **not** add a hosted API tier or a per-call billing controller.
> Dispatch rides your own provider credentials. There is no Trinity-operated
> inference path and no per-call markup — that would destroy both the cost basis
> and the privacy guarantee.

---

## What's free, what's paid

| | Individuals | Enterprises |
|---|---|---|
| The app + MCP + extension | Free, forever | The **same** artifact |
| Dispatch | Your own flat-rate subscriptions (~$0 marginal) | Your own per-token API credentials |
| Data | Stays on the machine | Stays on the machine |
| **What you pay for** | Nothing | **Support** — deployment, SLAs, upgrade guidance, security review, roadmap input |

There is no feature gate between the two columns. An enterprise that never buys
support runs the identical free product; support is the commercial relationship,
not a license key.

---

## Security & privacy posture

The privacy properties are not an enterprise add-on — they are architectural, and
they hold for every user:

- **Transcripts never leave the machine.** Ingest, embedding, theme assignment,
  search, and clustering run locally on pure embeddings + heuristics. The only
  outbound calls are the council member/chairman dispatches, which ride *your*
  provider credentials.
- **No LLM calls outside councils.** No background model calls, no hosted controller.
- **Prompt content never uploads.** Optional, opt-in telemetry is categorical-only
  (routing labels — `task_type`, `provider_scores`, `winner`); never prompt text.
  The public build ships **no analytics credentials**, so telemetry is a no-op by
  default; it can be disabled explicitly with `trinity-local telemetry-disable`.
- **Local-first inference.** The embedding model (~600 MB, one-time) runs on your
  hardware; the Hugging Face Hub is pinned offline after the first download.
- **At rest:** `~/.trinity/` lives on the user's disk under normal filesystem
  permissions; deploy with a restrictive umask (e.g. `077`) per your standard for
  developer machines. (At-rest encryption is the host's responsibility today, not
  Trinity's — call this out in your review.)

See [`SECURITY.md`](../SECURITY.md) for the threat model and disclosure process.

---

## Deployment

- **Install** is the same curl-bash flow or `install-mcp`, run per developer
  machine. The MCP server registers into **Claude Code, Codex CLI, Antigravity, and
  Cursor** (`trinity-local install-mcp` writes all four configs). Cursor is an
  install target — Trinity runs as an MCP server inside it — and is not yet an
  ingest source (it stores chats in SQLite `state.vscdb`, which no parser reads
  yet).
- **Dispatch mode is per developer:** the same Trinity routes through whatever
  provider credential a given machine has — a consumer subscription on a laptop, or
  an org API key in a managed environment. The product doesn't change; the credential
  does.
- **Platform support** (Linux / Windows beyond WSL2, fleet telemetry pre-seed,
  managed install) is exactly the kind of work the support relationship covers.

---

## Status & honesty

Trinity is **consumer-shipped today** and pre-PyPI (install via the curl-bash
script; `~/.trinity/code` + a local venv). The enterprise **support offering**
described here is the forward-looking commercial model — we onboard design partners
first. If you're evaluating Trinity for an org, the technical claims above are live
and verifiable in the repo; the support contract is a conversation, not a checkout.

**Talk to us:** open an issue on
[github.com/keepwhatworks/trinity](https://github.com/keepwhatworks/trinity).
