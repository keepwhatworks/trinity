# Principles

> The load-bearing ideas from the [essays](https://keepwhatworks.com/#articles), made executable.

The essays argue *why* certain things stay durable once AI commoditizes the rest. This file is the *how*: each principle is written so it can be **enforced during development** and **used to seed a new product** built the same way.

Trinity Local is the first system built on these principles — so every rule below is something Trinity holds itself to, with a pointer to where it lives in the code. The pattern is deliberately portable: the principle stands on its own, Trinity is just the first use case.

**How to read it.** Each entry has the principle, the essay it comes from, an **Enforce (dev)** rule you can turn into a review checklist or a CI guard, and a **Seed (new app)** note for applying it to something new.

**One rule about this file itself:** never let it describe a capability the code doesn't yet ship. A principle whose enforcement is still aspirational says so (e.g. a guard that's flag-gated default-OFF is named as such). Describing an unshipped lock as live is the exact "green while degraded" failure these principles exist to prevent.

---

## I. Foundation — why anything lasts

### 1. Knowledge is the process of being less wrong, not a settled state
*From Utopia is a Mechanism.* There is no final authority; a system that emits a permanent "done" stops correcting and starts rotting.

- **Enforce (dev):** No surface may emit a permanent `done` / `correct` / `ready` green that isn't continuously re-checked against ground truth. Every green gates on the **invariant** it attests (not a proxy), carries the disqualifier *in* the gate, has a pre-registered floor, and ships with a degenerate-data test asserting the green is **refused**. A "build succeeded" marker must reflect ground-truth freshness, not a stale self-report.
- **Seed (new app):** Bake permanent self-doubt into the architecture — no status is ever "finished." Every green is a claim re-verified on a cadence that abstains honestly when it can't verify. Build the error-hunting loop before the success dashboard.

### 2. Distributed error correction with cheap exit
*From Utopia is a Mechanism.* If a provider stops correcting errors, you unsubscribe. Compete over the best small corner, not central truth — and keep the user's data local and portable so the cost of leaving stays near zero.

- **Enforce (dev):** The preference/outcome ledger lives locally (`~/.trinity/`), is exportable, and no single provider's verdict is hard-wired as authority. Reject any design that makes one provider the mandatory arbiter or makes the user's data non-portable. A hosted-API tier that centralizes cost basis or privacy is a hard no.
- **Seed (new app):** Make the user's accumulated signal local, portable, and provider-neutral so the cost of leaving is near zero — that constraint forces the product to keep earning its place. Design for plural, swappable backends on a level field.

### 3. The durable moat is the learning loop, not the rules in code
*From The AI-Native Way.* Capture production failures as high-value signal, aggregate, verify the next run is better, repeat. The loop is what compounds; the hand-written rules are what you throw away.

- **Enforce (dev):** Every shipped feature that produces a verdict (council, route, eval) must persist its structured outcome to an append-only ledger that a downstream aggregator consumes. Reject any new decision surface that emits a result but writes nothing to `~/.trinity/council_outcomes/` or `analytics/`. Guard that the dispatch → outcome → aggregate path is actually wired (no empty dispatch callbacks).
- **Seed (new app):** Architect loop-first: before any UI, define what counts as a production failure, where it's stored, what aggregates it, and how the next run is *verifiably* better. The captured signal is the moat.

### 4. Build for verifiability — exceptions are first-class
*From The AI-Native Way.* Failures must be observable and become training data, never silently swallowed.

- **Enforce (dev):** No feature ships without a degenerate-data / edge-case test proving its failure mode is **captured** (honest abstain or recorded failure), not green-passed. Every analytics/outcome writer has a test that corrupts or starves its input and asserts the exception surfaces.
- **Seed (new app):** Give the app a dedicated, queryable "the system was wrong here" channel that feeds refinement. The success metric is how fast captured failures convert into measurable improvement — not uptime.

### 5. A 10x-better unbundling wins — parallelize, and keep the verifier cheaper than the act
*From The AI-Native Way + The Architecture of Endurance.* Parallelize everything that isn't a true serial dependency, and never let a check cost more than the work it checks.

- **Enforce (dev):** Default council mode is parallel; only true data dependencies run serially (chain mode). Any new serial step must justify a real dependency. A verifier (eval judge, abstain-gate, lens-health) must cost materially less than the act it guards — flag any verification whose latency or quota approaches the dispatch.
- **Seed (new app):** Map the critical path first: which steps are irreducibly serial vs parallelizable. Ship the unbundled parallel version that hits 10x on one painful axis, and make every quality-check cheaper than the work it inspects.

---

## II. Architecture — how you build on it

### 6. Behavior is a structural affordance, not motivation
*From Design the Affordance.* People do what the structure makes cheap, legible, and default. Boundaries live in the substrate (call graph / schema), not in prompt text.

- **Enforce (dev):** A separation between roles enforced only by a prompt instruction is a suggestion, not a wall — require structural enforcement, tested from both sides. Each distinct signal (`agreed_claims`, `disagreed_claims`, `why_matters`, `provider_scores`) gets its own field, never collapsed into prose. (Note: "members don't see each other's reasoning" holds structurally only in the default parallel mode; chain mode deliberately lets each member refine the prior — qualify any copy accordingly.)
- **Seed (new app):** Before features, design the substrate: name typed roles, draw boundaries into the API/file/permission layout, give every signal its own channel, make the desired behavior the path of least resistance. Don't ship an incentive layer to nudge behavior — change the affordance underneath.

### 7. The cheap path is the path taken
*From Design the Affordance.* Flip the gradient so the right way is also the default and laziest way.

- **Enforce (dev):** The recommended workflow must be the lowest-friction one (one-paste MCP install, no API key, no new app). If doing the right thing costs more steps than the wrong thing, fix the affordance (add the default, the alias, the auto-fire) rather than documenting a discipline users must remember.
- **Seed (new app):** Audit every desired user action: is it the cheapest path? Engineer defaults so the right action is also the laziest — installation, happy path, and safe choice are the same single gesture.

### 8. Narrow waist between layers
*From Design the Affordance.* A thin, stable interface in the middle with rich diversity above and below, so layers evolve independently and new species attach to either side.

- **Enforce (dev):** `~/.trinity/` (its files and schemas) is the invariant contract across the MCP server, the pip engine, and the Chrome extension. A new backend/surface must read and write the existing schemas rather than inventing a parallel store. Schema changes go through versioned migrations and guard the **shape/type**, not just `json.loads`.
- **Seed (new app):** Define one thin, stable data contract (file format + schema) as the system's waist; make every surface a thin client of it. Version the waist explicitly and migrate, never fork it.

### 9. The lever is one layer deeper than the symptom
*From Design the Affordance.* Fix the affordance underneath, not the symptom layer — and grep every sibling surface for the same shape.

- **Enforce (dev):** When a value/label/sort ships wrong, fix the root-cause data binding **at the surface that ships it**, not the visible symptom. Every load-bearing value is value-asserted at that surface, with a discriminating fixture, and all sibling surfaces are patched together. A value tested at the primitive but not at the surface binding is a guard-gap — the recurring bug class.
- **Seed (new app):** Institute root-cause discipline: when a number is wrong or a metric gamed, look one layer below the symptom for the affordance producing it. Periodically prune declared-but-unenforced rules; keep only the structurally durable ones.

### 10. Separation of powers — the optimizer never holds the pen on its own objective
*From The Architecture of Endurance.* No agent sits on a loop that authorizes itself; the definition of "better" lives outside the components it judges.

- **Enforce (dev):** The supervision target (the lens — the definition of "better") must be unwritable by the components it judges. Enforce the airgap **by type**: the miner/proposer cannot reach the validator's accept/reject; the optimizer (chairman/router) reads the lens but has no path to mutate the definition of "better." *Status today:* the lens is sourced from the user's own transcripts (taste anchored outside any reward objective), but the chairman re-authors `lens.md` each build and the held-out **regression-gate validator ships flag-gated default-OFF** (`TRINITY_REGRESSION_GATE`, shadow mode) pending shadow-log validation. Copy that asserts the airgap as a *running* invariant must be softened until that flag defaults ON — and the flag is armed only by an external action, never by internal code.
- **Seed (new app):** Type the roles as a graph from day one — proposer, checker, judge — and verify no node has an edge to its own objective. The success criterion lives in a module the working components can read but cannot write; build the self-authorization check as a structural test.

### 11. Don't widen — repeat
*From The Architecture of Endurance.* Scale by nesting the same small, checked unit (fractal), never by gathering everyone into one flat O(n²) room.

- **Enforce (dev):** Scaling to more providers/basins reuses the same bounded unit (a small council, a per-basin tally), not one ever-larger all-to-all comparison. Reject designs that grow O(n²) in members talking to each other; re-instantiate the invariants at each level rather than stretching one mechanism.
- **Seed (new app):** Pick a small, bounded, self-checking unit as the atom and compose copies hierarchically. Identify the 2–3 invariants that must hold at every level and rebuild the organ enforcing them on each floor.

### 12. Decorrelated checkers
*From The Architecture of Endurance.* A quorum of copies of one model is one mind in disguise; a majority is only trustworthy if the checkers fail independently.

- **Enforce (dev):** Gate "solo / quorum" status on **distinct-provider** count, not raw member count. Three calls to the same provider are not a trustworthy majority; a multi-voice council must contain decorrelated providers (Claude / ChatGPT / Gemini), enforced in the surface-rendering test.
- **Seed (new app):** When aggregating opinions for reliability, enforce provider/model diversity as a hard precondition for trusting consensus. Record the source of each vote; refuse to compute a "majority" over correlated sources.

### 13. Freeze the core, free the skin
*From The Architecture of Endurance + Free You More.* A hard, deliberately-unreachable invariant (the definition of "better") wrapped in a fast, freely-rewritable surface — only an outside human may touch the core.

- **Enforce (dev):** The definition of "better" is changeable only via an external action (env flag / human / out-of-band deploy), never by internal code. Trinity's self-improving-lens regression gate is arm-able only via `TRINITY_REGRESSION_GATE=1`, defaults OFF / byte-identical; freeze that default-OFF byte-identity in a test. Reject any change that lets an internal component alter the scoring criterion, the lens basin definition, or the acceptance rule. (And: marketing copy must not describe this lock as a running invariant while the gate is default-OFF.)
- **Seed (new app):** Split the codebase into a small constitutional core (definition of success + the change-rule) and a freely-evolving body. Make the core changeable only from outside the running system; the app can rewrite almost all of itself precisely because the one rule it cannot touch is the one that says what "better" means.

### 14. Fail small, with a stake
*From The Architecture of Endurance.* Errors stay local, proportional, reversible; an append-only ledger is the memory that catches drift first and makes a checker's track record revocable.

- **Enforce (dev):** Every checker/judge writes an append-only record of its approvals; routing/eval outcomes are never destructively overwritten. A failure must flood one room (one basin / one task type), not the whole system — reject features whose failure mode is global (e.g. one bare total instead of per-axis skip counters).
- **Seed (new app):** Build in chambers: a fault in one module floods its room, not the hull. Keep an append-only ledger of every automated judgment so any component's trust is auditable and revocable. Default to graded, reversible responses.

---

## III. Becoming — what it's for

### 15. Time to Wow < 5s, Time to Mastery = infinity
*From The Gravity of Becoming.* Deliver an instant, prerequisite-free first win and a bottomless deepening curve.

- **Enforce (dev):** A cold/first-run path must produce a visible result with no prerequisites (the one-paste install, the first-run continuity demo), **and** there must be a deepening surface rewarding repeated use (routing sharpens per council; the lens lift). A cold-start call-to-action must gate on the **prerequisite** of the command it shows — never lead a new user with a command that errors on an empty ledger.
- **Seed (new app):** Design two curves explicitly: an instant, prerequisite-free first win (<5s, no setup) and a bottomless mastery curve that compounds with use. Test the empty/cold state as rigorously as the populated one.

### 16. Hide the hands, but never hide the seams
*From The Gravity of Becoming.* Bury the machinery so the user feels like the author; always surface where the answer was contested and which call was close.

- **Enforce (dev):** Hide machinery (embeddings, token plumbing, parameter counts) from the default view, but **always** surface the decision seams (which provider won, where they disagreed, the margin). A surface that hides the seam — presents a verdict as uncontested — is as wrong as one dumping raw machinery. Council surfaces show the split and the winner in human brand terms, not raw slugs.
- **Seed (new app):** Two UX rules: (1) abstract the technical machinery so the user feels like author of the output; (2) never abstract away the *choice* — show where it was close, what it cost, and that the human decides. The interface disappears; the agency does not.

### 17. Disagreement is signal, not noise
*From Utopia is a Mechanism + The Architecture of Becoming + Free You More.* Surface the consensus **and** the contested residual with why-it-matters; the disagreements are where the live edge is.

- **Enforce (dev):** Every synthesis output must structurally separate consensus from contested and preserve **why** each disagreement matters; never collapse to a single confident answer that hides the split. The Routing JSON schema requires `agreed_claims` and `disagreed_claims`-with-`why_matters` as distinct fields. Confidence-honesty: n<3 or mixed evidence suppresses or demotes the claim.
- **Seed (new app):** When aggregating sources, treat their disagreement as the most valuable output, not an embarrassment to smooth over. Build a first-class "where they split and why it matters" view; surface uncertainty proportional to evidence.

### 18. Pull, don't push
*From The Gravity of Becoming.* Build enough mass that demand falls toward it; lead with the painkiller users already want, not the upsell you have to teach.

- **Enforce (dev):** Lead with the painkiller (the cross-provider council — "ask all three, keep what works"); demote the educational upsell (the lens) to retention. Onboarding that has to *teach* demand before delivering value is pushing — restructure so the obvious latent want (a free, no-API-key second opinion across models) is the front door.
- **Seed (new app):** Find the latent demand people already feel but can't articulate and build the riverbed for it; lead with that. If acquisition requires heavy education, the value prop is being pushed — redesign until the core benefit pulls on contact.

### 19. Bedrock is a question good enough to outlast every answer
*From The Gravity of Becoming.* Persist the durable artifact (the contested question, the tension), not the perishable answer.

- **Enforce (dev):** The system's long-lived state should be the questions and preference-tensions (the lens's paired tensions, `disagreed_claims`); treat any specific model answer as expiring snapshot data, never as canonical truth baked into the schema or hard-coded into logic.
- **Seed (new app):** Architect long-term memory around questions, tensions, and open problems rather than answers. Answers are cache entries that expire as models improve; the durable layer is the well-posed problem. Make the question the first-class persisted object.

### 20. Distributed error correction — move errors from fatal to theoretical
*From The Architecture of Becoming + Utopia is a Mechanism.* The goal is not a flawless foundation; it's a place that metabolizes error, so wrong theories die in testing, not in production.

- **Enforce (dev):** Any routing/pick rule promoted to "use directly" must clear a pre-registered floor (e.g. a winner-margin floor) with the disqualifier **in** the gate; coin-flip-margin rules fall back to advisory, never harden into a shipped directive. Wrong picks must be falsifiable against the ledger before they reach a user.
- **Seed (new app):** Design so every confident decision is recorded and later checkable against reality, and a bad decision degrades locally and reversibly. Build the place that metabolizes error: cheap retraction, graded consequences, an audit trail.

### 21. Elegant compression — perfection is when there's nothing left to take away
*From The Architecture of Becoming.* A dead or structurally-inactive path must be retired, not left dormant.

- **Enforce (dev):** Track user-facing verb count and MCP tool count as canonical numbers (doc-consistency guards). Adding a tool/verb requires showing it is not a redundant projection of an existing one. Periodically run a subtraction pass; delete projections and paths that never fire on real data.
- **Seed (new app):** Start from the minimal verb set that delivers the core value; refuse to add a surface until an existing one provably cannot cover the need. Keep what works, throw the scaffolding out.

---

## Keystone — the synthesis

### 22. Your judgment is the single scarce, non-replenishing input
*From Free You More.* Spend cheap things freely; harvest the human's verdict from work already done, never from a bolt-on rating chore.

- **Enforce (dev):** The lens forms from transcripts already on disk — verdicts the user already rendered while working — never from a mandatory rating/picking session. (The user-pick layer was fully removed; the chairman's pick is the sole supervision signal.) Reject any feature that adds a mandatory user-rating/veto/pick step; a new prompt to the user must justify that it targets the live edge, not settled ground.
- **Seed (new app):** Treat the human's judgment as the one input that doesn't scale and budget it ruthlessly. Learn from verdicts users already produce as a byproduct of real work; every interruption must be justified by being on the frontier.

### 23. Two regimes, one membrane
*From Free You More.* Hold settled ground silently and unsupervised; spend interaction loudly and adaptively on the live edge. A correct system reallocates contact, it doesn't minimize it.

- **Enforce (dev):** Routing/lens behavior must be bimodal: settled basins (high margin, stable taste) answer silently; live-edge cases (low-margin basins, where the labs split) surface loudly. A design that uniformly "asks less everywhere" is wrong — guard that the system gets quieter on settled ground **and** louder on the frontier, never flat.
- **Seed (new app):** Build two explicitly different modes: a silent, unsupervised hold on what's settled and an expensive, awake probe on what's still forming. The membrane deciding settled-vs-live is the core engineering problem. Never optimize for fewer interactions globally — only for spending interaction where it's still informative.

### 24. Manufacture the probe for the blind spot — on purpose, and again
*From Free You More + The Architecture of Endurance.* A judge that only recognizes itself has stopped judging. Build the test that exposes where the model is confidently wrong, and keep rebuilding it because the blindness reforms.

- **Enforce (dev):** When a new model lands, don't widen the council — score the newcomer against the user's own hardest questions (the personal cross-provider rejection set). Lens-health abstains rather than greening a self-recognizing/collapsed lens. An eval that only confirms what the lens already prefers (no contrast/discrimination gate) is a recording, not a judge — require a discrimination metric and adversarial confident-mistake items. *Status today:* the lens-health preference-collapse meter ships this blind-spot **detector**; the auto-relearning loop that would feed confident-wrong cases back into the lens does **not** ship — don't advertise it as live.
- **Seed (new app):** Build an adversarial self-test into the learning loop: continuously generate the probe that exposes where the model is confidently wrong by your own later reflection. Never let personalization converge to a flattering mirror — its discrimination on out-of-distribution-better options is the metric that matters, re-manufactured as the blind spot reforms.

### 25. Don't build the thing that needs you less — build the thing that frees you more
*From Free You More.* The right shape never finishes, because there is no final you. A permanently-static lens is a failure signal, not completion.

- **Enforce (dev):** Do **not** optimize for or advertise "converged / finished learning the user." The lens is recency-biased and never frozen on an oldest-N snapshot; surface staleness from ground truth and treat a long-frozen lens as a defect, never as "done."
- **Seed (new app):** Define the product's job as freeing the user's attention for new ground by reliably holding the ground they've won — not as reaching a final model of them. Build it to run perpetually a step behind a moving person; treat any state where it stops learning as a bug to investigate.

---

*These principles were extracted from the essays and checked against the shipped code in a bidirectional consistency audit. Where a principle's enforcement is still in progress, it says so — because the first principle (#1) forbids claiming a green that isn't real.*
