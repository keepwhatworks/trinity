---
class: live
---

# Trinity Local — architecture

> Long-form companion to the README. The README covers what Trinity does
> and how to install it; this file covers *how* it works under the hood.

## Trinity reads what you've already typed

Three subscriptions, three tabs, three half-answers. Trinity sends one question
to every model you use in parallel and runs a synthesis pass that returns one
verdict — what they agreed on, where they disagreed and why it matters, which
one was right.

It also looks back. Two transcript sources feed Trinity's lens: CLI sessions
that live on disk by default (`~/.claude/`, `~/.codex/`, `~/.gemini/`)
and web chats the Chrome extension auto-captures locally to
`~/.trinity/conversations/` as you use claude.ai / chatgpt.com /
gemini.google.com. Trinity finds questions you asked multiple providers
separately, turns each cross-provider pair into a synthetic council, and
bootstraps your context from your own history before you run a single fresh
council.

## Where the repo is now

`v<!-- canonical:version -->1.7.396<!-- /canonical -->` is launch-hardened around
the MCP-first path: `lens`, `council`, `dream`, `status`, and `install` are the
advertised CLI verbs, while older names stay registered for compatibility with
launchpad dispatch and existing scripts. The core mechanics are in place:
<!-- canonical:mcp_tool_count -->8<!-- /canonical --> MCP tools, MCP Resources,
schema migrations, Chrome Native Messaging dispatch, provider-side memory
imports, real ModernBERT embeddings when installed, and abstain-gates when only
the TF-IDF fallback is available.

The verification surface holds a comprehensive passing test suite,
<!-- canonical:doc_consistency_guards -->113<!-- /canonical --> doc-consistency
guards, and a <!-- canonical:smoke_surface_count -->35<!-- /canonical -->-surface
browser smoke gate. The public repo and the v1.7 release shipped 2026-07-02;
the remaining launch risks are not core council mechanics: Chrome Web Store
publish + extension-ID pinning, native Windows beyond WSL2, fresh-machine
install honesty, and the gated real-Chrome smoke.

Two verbs share one engine: `lens` runs the tension pipeline directly (plus
the routing freeze and the `core.md` distill) for the common "refresh my
taste" case; `dream` wraps that same pipeline as its Phase 4, adding the
quota-heavier halo — discover cross-provider question pairs in your history,
synthesize each as a virtual council, re-consolidate routing basins, and
refresh `vocabulary.md`.

## Councils are a GPS — broad when you need coverage, deep when you need conviction

You ask one question; Trinity hands you the right mode. **Broad councils** run
every model you use in parallel — chairman synthesizes the spread, you see where
the labs agree and where they fight. **Deep councils** run a chain — each round
refines the previous round's answer, the chairman steers toward conviction
instead of coverage. Same primitive, two zoom levels. You're never lost in the
answer space because the mechanic moves with you.

The same GPS shape applies inside your own data. Broad: `topics.json` holds the
basin topology of everything you've asked — k-means over real embeddings, each
basin labeled and evidenced. Deep: autofill and k-NN rank past prompts by
**replay value** — not pure cosine similarity but usefulness-to-re-run
(similar + repeated + uncertain + stale + not-recently-replayed), so the
threads that resurface are the ones still worth your attention. (An earlier
"depth score" geometry module shipped without a production consumer and was
retired 2026-05-27 by the orphan finder — see `retired_names.py`.)

## Context is the durable asset, not the prompts

Prompts are transient strings; *context* is the durable asset that shapes how
every model answers. Trinity treats your context as a first-class object —
indexed, embedded, yours. The labs are commercially prevented from helping you
use a competitor, which means none of them can build the layer that holds
context across them. Someone outside them has to.

## One-paragraph wire diagram

The chairman model synthesizes member outputs, emitting structured Routing JSON
over every council. Members run in parallel or in `chain` mode for sequential
refinement. The personal routing table is computed on demand from
`~/.trinity/council_outcomes/*.json` — the chairman's pick per council is the
supervision signal (the user-verdict override layer, `council_feedback.jsonl`,
was retired 2026-06-05; the user never picks, rates, or vetoes). `consolidate`
additionally tallies the recency-weighted chairman winner per lens basin into
`scoreboard/picks.json`, and `ask` routes future queries through the same
basin centroids on that winner.

The `lens` pipeline now centers on the unified
`~/.trinity/me/preference_acts.jsonl` ledger: Stage 0 mines model-miss acts
(REFRAME / COMPRESSION / REDIRECT / SHARPENING), explicit decisions and
provider imports join the same store, and later stages build basins, paired
tensions, trajectories, correction vectors, and recency-aware registry support.
Two walls hold the loop honest. Every ledger write passes a provenance gate —
an act must anchor to a real transcript turn, so the ledger stays a projection
of ground truth (councils, evals, and chairman outcomes never feed the lens;
that edge is deliberately unwired). And lens writes are clobber-guarded — a
degenerate extraction quarantines to a `.degenerate` sidecar instead of wiping
a live lens, with the stage-4 verdict distribution logged before every guarded
write. Real ModernBERT embeddings power semantic geometry when available;
TF-IDF is kept as a lexical fallback and semantic flows abstain when real
embeddings are not loaded.

Cross-provider continuity flows via MCP Resources — agents read
`trinity://memories/lens.md` at session handshake, so any harness can pick up
the user's voice without an explicit hand-off step. (The earlier `handoff` CLI
+ MCP tool were retired 2026-05-26 after 0 production usage; see
`retired_names.py`.) The `evals/` package consumes preference acts + `lens.md`
to produce replayable personal benchmarks (`eval-build` / `eval-run`, with
`--effort`/`--model` per-run target overrides for fair cross-lab comparisons
and `--regrade` to re-judge saved responses at zero dispatch cost; results
stamp the model + effort that actually ran, and partial runs carry a
survivorship warning). All
artifact shapes are JSON-Schema-validated and documented in
[`PREFERENCE_CORPUS_SPEC.md`](PREFERENCE_CORPUS_SPEC.md) — adoptable by other
tools (Aider / Cline / Continue) under CC0 to interop with Trinity's preference
corpus.
