---
class: live
---

# MCP Registry Submission Packets

> **Status:** ready-to-use marketing copy for task #114 (Launch-arc
> workstream 1 of 5: MCP-dropdown distribution). When you're ready to
> submit Trinity to each registry, paste the curated pitch text into
> the registry's contribution form. Submission URLs + exact field
> shapes change frequently — verify against each registry's current
> contribution guide before submitting.

## Why these registries

`trinity-local install-mcp` already wires Trinity into Claude Code,
Codex CLI, Antigravity, and Cursor (four harnesses — `install.py`
writes `~/.claude.json`, `~/.codex/config.toml`, `~/.gemini/settings.json`,
and `~/.cursor/mcp.json` per the P16/P92 persona-audit fix). The
remaining funnel-widener is **discoverability** — users who don't
know Trinity exists never run `install-mcp`. Each MCP-server registry
below is a directory users browse looking for tools. Being IN the
dropdown beats being technically perfect.

Order is rough leverage estimate (highest first): user base × overlap
with our power-user-with-multi-CLI persona × ease of approval.

---

## Shared one-paragraph description (canonical pitch)

The text below is the "lede paragraph" most registries ask for. Lift
this verbatim where the registry takes a free-text description.

> **Trinity Local** runs councils across Claude, ChatGPT/Codex, and
> Gemini in parallel from one prompt — the chairman synthesizes the
> verdict and shows you where they split. Free and local: rides your
> existing subscriptions, no API key, no credits, no cloud; prompts
> stay in `~/.trinity/`. Over time it distills a lens from your existing
> transcripts so the chairman returns the answer YOU would have picked —
> the cross-provider layer the labs are commercially prevented from
> building. Score any model against your *actual* rejection signal.
> macOS today; rides on your existing Claude / Gemini / OpenAI
> subscriptions, no API billing.

Tag suite (use the registry's closest matches):

```
ai, llm, claude, gpt, gemini, multi-provider, council, evaluation,
benchmarks, routing, agent, local-first, privacy, mcp
```

Repo: `https://github.com/keepwhatworks/trinity`
Install one-liner: `curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash`

---

## Registry 1: Anthropic / Claude Desktop MCP catalog

**Audience:** Claude users who don't yet use other models. Highest
overlap with our power-user persona because the surface is exactly
where someone goes "I want Claude to be better at X."

**Submission URL:** TODO — check Anthropic's current docs site for
the official MCP-server submission process. The most recent shape was
a PR against an Anthropic-maintained registry repo.

**Tailored pitch** (even for Claude-only users, lead with the
cross-provider council — then the lens; the hook is "ask all three,
keep what works, and Claude learns your voice over time"):

> Trinity sends one prompt to Claude, ChatGPT, and Gemini in parallel
> and the chairman synthesizes the verdict — what they agreed on, where
> they split, which answer wins — free and local, riding the
> subscriptions you already pay for, with prompts staying in
> `~/.trinity/`. Over a handful of councils it distills a personal lens
> from your existing transcripts (CLI + claude.ai web chats) and feeds
> it into the synthesis, so the answer comes back in your voice — the
> tensions you reject vs accept, the subject basins you actually care
> about, your own vocabulary. Comes with the canonical supervision-loop
> tools (`ask` / `run_council` / `get_persona` / `get_council_status`)
> plus a personalized eval harness that benchmarks any provider
> against the user's empirical rejection signal. (Chairman pick is
> the auto-recorded supervision signal — the prior `record_outcome`
> MCP tool was retired 2026-05-21 when chairman-as-verdict landed.)

**Required artifacts:**
- 1024×1024 icon (use `docs/icon.png` if present, or `me-card.png`)
- Short tagline: *"Ask all three. From inside Claude."*

---

## Registry 2: Cursor MCP marketplace

**Audience:** Devs who already pay for Cursor subscriptions, often
running Cursor + Claude Code in parallel. Strong overlap because
they're already polyharness users.

**Submission URL:** TODO — check Cursor docs (`cursor.sh/docs/mcp`).
Their marketplace may require approval rather than self-serve PR.

**Tailored pitch** (lead with "vendor-neutral memory" — Cursor users
are sensitive to lock-in concerns):

> Trinity gives Cursor users a vendor-neutral preference corpus that
> survives the next tool you try. Trinity runs as an MCP server inside
> Cursor and scores the frontier models you dispatch against your own
> rejection history, keeping the preference signal in `~/.trinity/` regardless of
> which IDE wins the next 12 months. Rides on your existing
> subscriptions, no API billing.

**Required artifacts:**
- Same as Anthropic
- Cursor often wants a `cursor-mcp-config.json` snippet showing how
  the server registers — Trinity uses the standard stdio shape.

---

## Registry 3: Cline (autonomous coding agent)

**Audience:** Cline users tend to be experimental — they're already
running an autonomous agent locally and care about reproducibility +
local-first. Strong overlap with the Trinity persona.

**Submission URL:** TODO — Cline's MCP catalog lives in their
GitHub repo (`cline/cline`, look for `mcp-servers.json` or similar).
PR-based.

**Tailored pitch** (lead with the empirical-eval angle — Cline users
care about which model agent loops work best with):

> Trinity scores any model — Claude, GPT, Gemini, plus local Ollama /
> MLX models — against your actual rejection signal mined from prior
> transcripts. Per-rejection-type breakdown (REFRAME / COMPRESSION /
> REDIRECT / SHARPENING) tells you which model handles your kind of
> question, not someone else's synthetic benchmark suite. Plus the
> standard council + personalized lens MCP tools.

**Required artifacts:**
- `mcp-servers.json` entry showing transport=stdio, command, args
- Brief example of a `run_council` call from inside Cline

---

## Registry 4: Continue (open-source coding assistant)

**Audience:** Devs who chose Continue specifically for its
customizability + local-first stance. Strong philosophical alignment
with Trinity's privacy posture.

**Submission URL:** TODO — Continue's contribution flow goes through
their main repo or `continuedev/extensions`.

**Tailored pitch** (lead with privacy + the Preference Corpus Spec —
Continue users will appreciate the schema-standardization play):

> Trinity adds cross-provider councils + a personalized lens + a
> personalized eval harness to Continue. Notably: we ship a CC0
> [`Preference Corpus Spec`](https://github.com/keepwhatworks/trinity/blob/main/docs/PREFERENCE_CORPUS_SPEC.md)
> — a JSON-Schema-validated format for the supervision-signal layer
> that we'd like Continue to adopt too. The format already handles
> council outcomes, labeled rejections, and personalized eval sets.
> Standards outlive products; Trinity is what one looks like when
> someone outside the labs ships it.

**Required artifacts:**
- Link to PREFERENCE_CORPUS_SPEC.md
- The `schemas/*.schema.json` files

---

## Registry 5: Codex CLI ecosystem

**Audience:** OpenAI-CLI users who recently got MCP support. Less
overlap because they're often single-provider — but Trinity's pitch
is exactly to convert single-provider users into multi-provider users.

**Submission URL:** TODO — OpenAI's Codex CLI MCP integration list
location varies; check the `openai/codex` repo or the registry it
links to.

**Tailored pitch** (lead with the council comparison + the fact that
Codex is already a Trinity council member — flip them to the polyharness
side):

> Codex users already have a great coding model. Trinity lets them
> see when Claude or Gemini would do better on a specific task. The
> personal routing table builds
> automatically — after ~10 councils, Trinity tells you which model
> handles your kind of refactor / your kind of debug / your kind of
> architectural call. `install-mcp` registers Trinity in Codex CLI's
> MCP config alongside the existing OpenAI tools.

**Required artifacts:**
- Standard `~/.codex/config.toml` MCP server stanza (Trinity's
  `install-mcp` already writes this; the submission may want a
  reference example)

---

## Cross-registry checklist

Before submitting to any registry:

- [ ] Repo README updated with the registry-acceptance badge if they
      provide one
- [ ] License clearly displayed (MIT for Trinity itself; CC0 for the
      Preference Corpus Spec — many registries care about this distinction)
- [ ] Working install one-liner verified on a fresh macOS install
      (`curl -fsSL .../install.sh | bash` followed by
      `trinity-local status`)
- [ ] CHANGELOG entry noting the registry inclusion (so future
      contributors know it's listed)

After each submission, drop the registry URL into the table below for
attribution + future reference:

| Registry | Submission URL | Status | Listed at |
|---|---|---|---|
| Claude Desktop | TODO | pending | — |
| Cursor | TODO | pending | — |
| Cline | TODO | pending | — |
| Continue | TODO | pending | — |
| Codex CLI | TODO | pending | — |

## Failure modes to guard against in registry copy

- **"Sounds like a wrapper."** Mitigate: every pitch leads with the
  *structural-asymmetry* angle — only Trinity has the cross-provider
  index because the labs are commercially prevented from sharing
  transcripts. Don't describe Trinity as "Claude + GPT + Gemini in one
  place." Describe it as "the layer above them that they can't build."
- **"Yet another MCP server."** Mitigate: lead with the unique tools
  (`get_persona`, `eval-run`, `import_provider_memory`) that no other
  MCP server in the registry offers. Generic council/chat features go
  AFTER the unique ones.
- **"What's MCP?"** Mitigate: don't explain MCP in the pitch — the
  user is browsing an MCP registry, they already know. Save the
  explanation for the README.
- **"Privacy claim disbelief."** Mitigate: link to the install-mcp
  source. The MCP server is a stdio child of the user's harness;
  verify with `lsof`. Don't waste pitch real estate on privacy
  reassurance; let the source code do the talking.

## After submission: feedback loop

Each registry has its own approval cadence (Anthropic: days; Cline /
Continue: hours via PR review; Cursor: variable). Track inbound
issues or feature requests in the Trinity repo with the label
`from-registry:<name>`. The first round of registry feedback usually
exposes real-user confusion patterns — feed those back into the
README + the launchpad onboarding ribbon.
