---
class: live
---

# Trinity Local architecture

> Long-form companion to the README. The README covers what Trinity does
> and how to install it. This file covers *how* it works under the hood.

## Trinity reads what you've already typed

Three subscriptions, three tabs, three half-answers. Trinity sends one question
to every model you use in parallel and runs a synthesis pass that returns one
verdict: what they agreed on, where they disagreed and why it matters, which
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

`v<!-- canonical:version -->1.7.397<!-- /canonical -->` is launch-hardened around
the MCP-first path: `lens`, `council`, `status`, and `install` are the
advertised CLI verbs, while older names stay registered for compatibility with
launchpad dispatch and existing scripts. The core mechanics are in place:
<!-- canonical:mcp_tool_count -->7<!-- /canonical --> MCP tools, MCP Resources,
schema migrations, Chrome Native Messaging dispatch, provider-side memory
imports (verify-at-import with a quarantine sidecar, covered in the four walls
below), real ModernBERT embeddings when installed, and abstain-gates when only
the TF-IDF fallback is available. The runtime footprint is two dependencies
(mcp, numpy). PNG share cards live in the optional `[share]` extra
(council-ratified demotion of measured-dormant machinery, 2026-07-10).

The verification surface holds a comprehensive passing test suite,
<!-- canonical:doc_consistency_guards -->113<!-- /canonical --> doc-consistency
guards, and a <!-- canonical:smoke_surface_count -->35<!-- /canonical -->-surface
browser smoke gate. The public repo and the v1.7 release shipped 2026-07-02.
The remaining launch risks are not core council mechanics: Chrome Web Store
publish + extension-ID pinning, native Windows beyond WSL2, fresh-machine
install honesty, and the gated real-Chrome smoke.

One verb owns the memory layer. `lens` runs the tension pipeline and then
refreshes every thinking memory: the routing freeze, the LLM-free
`vocabulary.md` scan (folded in 2026-07-04 so the staleness warning's own
advice can clear it), and the `core.md` distill. `lens --deep` first mines
your history (discover cross-provider question pairs, synthesize each as a
virtual council, re-consolidate routing basins), then runs the same build:
the one-command cold start. (`dream` survives only as a compatibility alias
for `--deep`, folded 2026-07-04. `lens --only-distill` is the ~20s core.md
refresh.) Rule of thumb: `lens` = refresh what you have. `lens --deep` = also
mine new signal from your history.

## Councils are a GPS: broad when you need coverage, deep when you need conviction

You ask one question. Trinity hands you the right mode. **Broad councils** run
every model you use in parallel. The chairman synthesizes the spread, and you
see where the labs agree and where they fight. **Deep councils** run a chain.
Each round refines the previous round's answer, and the chairman steers toward
conviction instead of coverage. Same primitive, two zoom levels. You're never
lost in the answer space because the mechanic moves with you.

The same GPS shape applies inside your own data. Broad: `topics.json` holds the
basin topology of everything you've asked. It's k-means over real embeddings,
each basin labeled and evidenced. Deep: autofill and k-NN rank past prompts by
**replay value**. That is not pure cosine similarity but usefulness-to-re-run
(similar + repeated + uncertain + stale + not-recently-replayed), so the
threads that resurface are the ones still worth your attention. (An earlier
"depth score" geometry module shipped without a production consumer and was
retired 2026-05-27 by the orphan finder. See `retired_names.py`.)

## Context is the durable asset, not the prompts

Prompts are transient strings. *Context* is the durable asset that shapes how
every model answers. Trinity treats your context as a first-class object:
indexed, embedded, yours. The labs are commercially prevented from helping you
use a competitor, which means none of them can build the layer that holds
context across them. Someone outside them has to.

## One-paragraph wire diagram

The chairman model synthesizes member outputs, emitting structured Routing JSON
over every council. Members run in parallel (`chain` mode is parked dormant,
measured at zero real uses ever, and is wire-compat only). The personal routing
table is computed on demand from `~/.trinity/council_outcomes/*.json`. The
chairman's pick per council is the supervision signal. (The user-verdict
override layer, `council_feedback.jsonl`, was retired 2026-06-05. The user
never picks, rates, or vetoes.) `consolidate` additionally tallies the
recency-weighted chairman winner per lens basin into `scoreboard/picks.json`.
`ask` then routes future queries through the same basin centroids on that
winner.

The `lens` pipeline now centers on the unified
`~/.trinity/me/preference_acts.jsonl` ledger. Stage 0 mines model-miss acts
(REFRAME / COMPRESSION / REDIRECT / SHARPENING). Explicit decisions and
provider imports join the same store. Later stages build basins, paired
tensions, trajectories, correction vectors, and recency-aware registry support.
Four walls hold the loop honest. Every ledger write passes a provenance
gate. An act must anchor to a real transcript turn, so the ledger stays a
projection of ground truth. (Councils, evals, and chairman outcomes never feed
the lens. That edge is deliberately unwired.) Provider imports go further:
an anchor must not merely exist but RESOLVE against the local prompt index.
A claimed prompt that doesn't match anything the user actually typed waits in
a quarantine sidecar (never touching acts, lenses, orderings, routing, or
lens-build) until a later ingest lands its transcript and promotes it. The
health meter counts what's waiting. Lens writes are clobber-guarded.
A degenerate extraction quarantines to a `.degenerate` sidecar instead of
wiping a live lens, with the stage-4 verdict distribution logged before every
guarded write. And because Trinity's own agent-loop output is itself a real
transcript turn (provenance guards fabrication, not reflection), a
self-reflection meter (`lens-health`'s ledger-contamination check,
pre-registered floor 25%) measures the fraction of ledger acts that are
agent-loop-shaped. So a lens quietly converging on the machine's own
vocabulary reads WEAK instead of green (founder-corpus baseline: 4–9%).
Real ModernBERT embeddings power semantic geometry when available.
TF-IDF is kept as a lexical fallback, and semantic flows abstain when real
embeddings are not loaded.

The lens's stand-in claim is measured, continuously. At every build the taste
direction freezes with the exact set of acts it was fit on. Every correction
that arrives afterward is a prospective trial the direction never saw
(`me/palate_registry.py`). Train-on-test is structurally impossible there, and
honest abstains are disclosed. The running number lives in `lens-health` (81.6%
over 354 decided trials as of 2026-08-01, with a further 55 honestly
abstained and never scored, against a kill-floor of 60%). `choose(options)`, the ninth
MCP tool, productizes the validated half. It ranks any concrete options on the
frozen direction, LLM-free and instant, with the live accuracy in every
payload and an abstain under the same noise floor. Generation-side
conditioning measured null (16/30, pre-registered) and ships dormant. Eval
leaderboards are gated by a judge-validity floor: a judge must agree with the
user's own held-out corrections at ≥70% before rankings read as decisive.
The floor has teeth both ways: a 2026-07-07 axis-instruction fix lifted the
best judge to 77% on that day's pair set, and a 2026-07-17 re-measurement on a
cleaned pair source (unwinnable context-reveal acts gated out) dropped it back
to 60%. Every ranking surface currently stamps "directional, not decisive",
which is exactly what the floor is for. The behavioral disagreement ledger
needs no judge and is unaffected.

## Deliberately absent

Three subsystems are missing on purpose. Treat proposals to add them as
regressions until the evidence changes. **User ratings**: retired 2026-05-21
(residual veto 2026-06-05). The chairman's pick is the entire supervision
signal, and the user just chats. **Council→lens learning loops**: the lens
learns from raw transcripts only. Feeding the optimizer's outcomes into its
own objective converges taste to a flattering mirror (founder-locked
2026-07-02). **A goals/OKR layer**: considered and declined 2026-07-04. The
coupling pattern it would formalize (fast proxies, slow truth, a loop that
audits whether they still agree) is already instantiated where it earns rent:
the provenance-gated ledger, the lens/routing wall, the preference-collapse
meter, judge-alignment. And the retirement log (moves substrate, handoff,
decision-log) shows what happens to substrates built before their signal
exists. The operating discipline stays manual until the manual loop
demonstrably hurts.

The audit discipline that keeps this document honest: every staleness or
health warning that recommends a command carries an **advice-closure guard**
(`tests/test_advice_closure.py`). The recommended command's writers must
touch the signal the checker reads, so no warning can outlive its own advice.
(Two live instances of that bug shipped and were fixed 2026-07-03/04.)

Cross-provider continuity flows via MCP Resources. Agents read
`trinity://memories/lens.md` at session handshake, so any harness can pick up
the user's voice without an explicit hand-off step. (The earlier `handoff` CLI
+ MCP tool were retired 2026-05-26 after 0 production usage. See
`retired_names.py`.) The `evals/` package consumes preference acts + `lens.md`
to produce replayable personal benchmarks (`eval-build` / `eval-run`, with
`--effort`/`--model` per-run target overrides for fair cross-lab comparisons
and `--regrade` to re-judge saved responses at zero dispatch cost). Results
stamp the model + effort that actually ran, and partial runs carry a
survivorship warning. All
artifact shapes are JSON-Schema-validated and documented in
[`PREFERENCE_CORPUS_SPEC.md`](PREFERENCE_CORPUS_SPEC.md). They are adoptable
by other tools (Aider / Cline / Continue) under CC0 to interop with Trinity's
preference corpus.
