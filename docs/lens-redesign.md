---
class: aspirational
---

# Lens redesign — robust lenses that stand the test of time

Design doc. Goal stated by the user: **build lenses that represent me
and stand the test of time.** This evaluates the entire lens pipeline
against that goal, identifies the one structural gap, and specifies the
change — including where #182 (trajectory lens) plugs in.

Status: design only. Nothing here is built. Supersedes the standalone
#182 plan, which this folds into a larger picture.

## The goal, made falsifiable

"Robust + stands the test of time" decomposes into four testable
properties a lens should have:

1. **Stability** — the lens doesn't swing between rebuilds. Two
   rebuilds a day apart over the same corpus produce ~the same lens.
2. **Accretion** — confirming evidence *strengthens* a tension; it
   doesn't just re-derive it from scratch each time.
3. **Graceful decay** — a tension that stops being supported fades
   over time rather than vanishing on the first rebuild that misses it.
4. **Provenance** — every tension traces to the evidence that supports
   it, with a confidence the chairman can weigh.

The current pipeline has (4) and partially (1-via-the-≥3-basin-rule).
It fails (2) and (3) outright, and (1) is fragile — demonstrated this
session: one chairman run produced **0 tensions**, another produced 3.

## The one structural gap: the lens is stateless

Today `lens-build` is a pure function of `(current corpus, a stochastic
chairman)`, regenerated from zero each run. `save_lenses` overwrites
`lenses.json` wholesale. A LensPair carries no identity across rebuilds,
no support score, no last-confirmed timestamp. Consequences:

- A tension the chairman misses this run **vanishes** — no memory it
  was strongly supported last week.
- A single bad chairman call can **reshape the whole lens** (we watched
  it zero out; the #194 clobber guard catches only the all-empty case,
  not subtler drift).
- There is **no decay curve** — it's all-or-nothing per rebuild.

The irony: the accumulator primitive we want is the Beta-Binomial
posterior we *deleted* with the moves substrate (#184). We removed it
from the layer that didn't need it (moves — the chairman derives those
at inference time) when the layer that genuinely needs it is **the lens
itself**.

And the substrate is already half-there: `merges.jsonl` is an
append-only ledger of "every user-expressed-preference act" (turn-pair
overwrites, council winners, cortex overrides). **lens-build writes to
it but never reads it back.** The durable evidence is being recorded and
ignored.

## The design principle

> The lens is a **slowly-evolving accumulator**, not a per-run redraw.
> Each rebuild proposes *candidate* tensions; a reconcile step matches
> them against the existing lens and updates confidence — reinforce on
> confirmation, decay on absence. `merges.jsonl` is the durable evidence
> ledger that survives any single rebuild.

This is the seed-kernel recursion applied where it belongs: "evidence
reinforces, absence decays," on the lens.

## Council verdict (2026-05-28, `council_476703aafb5f71a8`)

Ran a cross-provider council on three candidate architectures
(A = deterministic accumulator, B = chairman-maintains-lens fixpoint
`lens_{n+1}=chairman(corpus+lens_n)`, C = status quo). Chairman codex/
gpt-5.5, winner claude, confidence high. Unanimous agreed claims:

- **A is the best architecture; C rejected; B lacks a structural
  stability guarantee.**
- **No extra LLM calls needed.**
- **Reuse the existing routing-accumulator *pattern*** — not a
  lens-specific reinvention.
- **Lens state tracks support + demotion over time.**

The load-bearing disagreement (claude vs codex): **B isn't just weaker
— it's error lock-in.** A wrong tension the chairman writes into
`lens_n` is re-fed as context every rebuild and *reinforced*. The
recursion that makes B elegant is exactly what compounds its mistakes.
A can *decay* a wrong tension; B *entrenches* it. The chairman's
eval_seed stated the principle: **"chairman output is evidence, not
authority over persistent lens state."** The chairman proposes; the
accumulator disposes — and must be able to overrule the chairman by
decay. → **B rejected.**

## Complexity audit — the v1 doc over-built A

The session's own lesson (we just deleted the moves Bayesian gate for
being over-engineered) applies to A. Grounding "reuse the routing
pattern" in what routing *actually* does:
`aggregate_routing_table()` keeps **simple `{wins, n}` counts,
recomputed from the append-only `council_outcomes` ledger every read.
No stateful Beta-Binomial. No mutable counters.** That is the pattern
to copy. Audit of the v1 design's pieces:

| v1 proposed | Verdict | Why |
|---|---|---|
| `alpha`/`beta` Beta-Binomial per tension | **CUT** | Routing uses simple counts; Beta-Binomial is the exact over-engineering we deleted from moves. Use `support_count`. |
| stored `status` (active/dormant) | **CUT** | Derive at render from (support, recency). One less mutable field to corrupt. |
| `first_seen` timestamp | **CUT** | Observability, not load-bearing. (Same dead-field trim as dream_calibration.) |
| two-floor decay (ACTIVE + DORMANT hysteresis) | **CUT → one rule** | Hysteresis is premature; add only if flapping is observed on real rebuilds. |
| `basins_spanned` union-growth | **DEFER** | Nice (coverage accretes) but adds merge logic; start with latest, add later if needed. |
| cold-start Bayesian priors (MBTI seeds) | **CUT** | Designing for a hypothetical cold user; this user has a corpus. Evidence-first. |
| `tension_id` (cosine identity) | **KEEP** | The core — without stable identity there's no accretion. |
| `last_confirmed` timestamp | **KEEP** | The one load-bearing timestamp; drives decay. |
| reconcile step | **KEEP** | The load-bearing new logic. |

**Bigger simplification the audit surfaced:** support should be
**recomputed from the evidence ledger, not stored as a mutable
counter** — exactly how the routing table is recomputed from
`council_outcomes`. The only *new persisted state* is a tiny **tension
registry** (`tension_id → canonical poles + embedding`) so identity is
stable across rebuilds. `support_count` and `last_confirmed` are
*derived* from how many ledger records map to each tension and when the
latest one landed. This eliminates the mutable-counter corruption class
entirely — the same category as the clobber incident (#194). The lens
becomes a **view over an append-only ledger**, not stateful counters.

## Every stage, evaluated against the goal

| Stage | Today | Keep / Change | Why |
|---|---|---|---|
| **0 — turn-pair rejections** | Chairman extracts REFRAME/REDIRECT/COMPRESSION/SHARPENING; now chunked (#195) | **Keep**, but append each kept signal to `merges.jsonl` with a timestamp (already happens) | Rejections are raw evidence — they belong in the durable ledger, not just the rebuildable rejections.jsonl |
| **1 — basins** | k-means topology, 20 basins | **Keep** — but pin basin identity (see Risk 1) | Basins are the semantic coordinate system tension-identity matching relies on; they must be stable across rebuilds |
| **2 — decisions** | Chairman extracts privileged/sacrificed | **Keep** as extraction | Decisions are evidence; unchanged |
| **3 — pair-mining** | Chairman proposes paired tensions | **Reframe**: output is *candidate* tensions, not "the lens" | The chairman proposes; the accumulator disposes |
| **4 — basin post-filter (+T2)** | ≥3-basin count + cosine membership | **Keep** as the gate on candidates | A candidate must still clear the structural bar before entering reconcile |
| **4.25 — canonicalize to user vocab (NEW)** | — | **Add**: rephrase each tension pole in the user's own words, drawn from the supporting evidence | Legibility + compression (your words carry your connotations) + stability (your phrasing is consistent run-to-run; the chairman's drifts) |
| **4.5 — reconcile (NEW)** | — | **Add**: match candidates to registry by cosine identity; accrue evidence ids | This is the load-bearing new stage — turns the lens from stateless to accumulating |
| **5 — distill (core.md)** | Reads tensions | **Change**: read *high-support* tensions first | The distillation should reflect the durable core, not this-run's noise |
| **2.5 — vocabulary** | Homonyms/synonyms | **Keep** — and use it in 4.5 identity matching | Two phrasings of one tension ("refine"/"tighten") must resolve to one identity; vocabulary is that bridge |

## The accumulation primitive (Stage 4.5) — post-audit lean version

**Tension identity** (the one piece of new persisted state). The
chairman phrases the same tension differently across runs ("mechanism
inspection ↔ speculative inference" one day, "rigor ↔ speed" another).
Surface-hashing won't match. Use **semantic identity**: a candidate
matches an existing tension if
`cosine(probe(candidate), probe(existing)) ≥ MATCH_THRESHOLD` (~0.80).
Same T2 embedding primitive as #186 — the embedder bridging two
phrasings of one idea. (Requires MLX; under TF-IDF fall back to
surface-pole overlap — #185's lesson.) Persist a tiny **registry**:
`tension_id → {canonical_poles, probe_embedding, supporting_evidence_ids}`.

**Support is DERIVED, not stored** (the routing-table pattern):
- `support_count(t)` = number of distinct evidence records in
  `merges.jsonl` linked to `t` (via supporting_evidence_ids, unioned
  across rebuilds).
- `last_confirmed(t)` = timestamp of the newest such record.
- `active(t)` = `support_count ≥ ACTIVE_MIN` **and** `last_confirmed`
  within the recency window. Computed at render. No stored status.

**The reconcile step** (deterministic, no LLM):
```
for candidate in stage4_accepted:
    match = best_registry_tension_by_cosine(candidate)   # ≥ MATCH_THRESHOLD
    if match:
        match.supporting_evidence_ids |= candidate.evidence_ids   # accrue
    else:
        register new tension (id, canonical poles, embedding, evidence_ids)
# nothing to "decay" actively — support + recency are recomputed from the
# ledger each render. A tension whose newest evidence ages out simply
# stops being active. Absence IS decay; no beta counter needed.
```

**Render** (lens.md): include only `active` tensions, **sorted by
support_count descending** — the MBTI "function stack" insight: the
dominant (highest-support) tensions lead; the chairman weights them
heaviest. Inactive tensions stay in the registry (revivable), unrendered.

**Why this is robust AND simpler than v1:**
- **Stability**: a single bad chairman run adds no confirming evidence
  to existing tensions — it can't erase them (their support is recomputed
  from the ledger, untouched). Reshaping requires *sustained* new
  evidence, not one call.
- **No mutable counters** → no counter-corruption class (the clobber
  category). The ledger is the single source of truth; the lens is a
  view.
- **Graceful decay** for free: recency-of-newest-evidence, computed at
  render. No floors-hysteresis, no stored status, no Beta-Binomial.

## Optional, separate from the core: blind-spot surfacing (MBTI)

NOT part of the accumulator — a render-only insight feature, own task.
MBTI's "inferior function" = each tension's **underweighted pole** =
your blind spot. Trinity already extracts `pole_a_failure` /
`pole_b_failure` (the cost of over-indexing a pole). Surface it as a
self-awareness line: *"your lens leans hard toward mechanism-inspection;
the cost you pay is speed."* That's the most genuinely useful thing a
personality lens can say — not "you're type X" but "here's the edge you
keep choosing and what it costs." Cheap (uses existing fields), high
insight-value, but orthogonal to robustness — ship after the core.

## Where #182 (trajectory lens) plugs in

#182's arc-pairs become **another candidate source feeding the same
Stage 4.5 reconcile.** The recursive design from the earlier sketch
still holds (arc = sequence of synchronic rejections in one thread,
mined by the existing Stage 3 primitive). The only change: arc-derived
candidates don't get their own storage or gate — they flow into the
accumulator alongside synchronic candidates and earn support the same
way. A diachronic tension that recurs across threads accrues support
and goes active; one seen once stays provisional and ages out.

This is why #182 *should not* ship before the accumulator: built the
old from-scratch way, a trajectory lens is just as fragile. Built on the
accumulator, it's durable by construction.

## Build sequence (post-audit)

1. **Lens accumulation core** (load-bearing). Add the tension registry
   (`tension_id → canonical poles + probe embedding +
   supporting_evidence_ids`); the Stage 4.5 reconcile step (cosine-match
   candidates, union evidence ids); derive `support_count` +
   `last_confirmed` from the ledger at render; render only `active`
   tensions, sorted by support_count. ~80 LOC + 1 small registry file
   (down from the v1 doc's ~150 LOC + per-tension Beta-Binomial state).
2. **Migration** — existing 3-tension lens.md → register each with its
   current supporting decisions as the seed evidence set, so the first
   reconciled rebuild reinforces rather than replaces.
3. **#182 trajectory arc-pairs** — as a candidate source into 4.5.
4. **Threshold tuning** — pick `MATCH_THRESHOLD` + `ACTIVE_MIN` +
   recency window from real rebuild cadence (a few real rebuilds to
   calibrate — the "run on real data" discipline this session kept
   proving).
5. **(Optional, later) blind-spot surfacing** — render the
   underweighted-pole cost per tension. Separate task; orthogonal to
   robustness.

## Risks / open decisions

1. **Basin identity across rebuilds.** k-means with a fixed seed is
   *mostly* stable but not guaranteed as the corpus grows. Mitigation
   (already the plan): tension identity is **probe-text cosine**, which
   is basin-independent; basins_spanned is evidence, not identity.
2. **MATCH_THRESHOLD calibration.** Too high → the same tension splits
   into duplicates each rebuild (no accretion). Too low → distinct
   tensions merge (lost resolution). Start 0.80, watch duplicate-vs-merged
   on real rebuilds.
3. **Cold-start.** First-ever build registers every tension fresh with a
   single evidence record → all low-support. Correct: a new user's lens
   *should* be low-confidence until confirmed across rebuilds.
4. **TF-IDF fallback** (no MLX) — identity matching degrades to
   surface-pole overlap (#185's constraint). Support/recency are
   backend-independent; only matching is coarser.

## What the audit removed (and why it's safe)

- **Beta-Binomial alpha/beta → simple derived support_count.** Routing
  uses simple counts; the moves Beta-Binomial we deleted (#184) is the
  exact over-engineering to avoid. Absence-as-decay (recency of newest
  evidence) replaces the beta counter entirely.
- **Stored `status` → derived at render.** No mutable field to corrupt.
- **`first_seen`, two-floor hysteresis, basins union-growth, cold-start
  priors → cut/deferred.** None load-bearing for the four robustness
  properties; each addable later if real rebuilds show a need.

## What this does NOT change

- The "no LLM outside councils" commitment — reconcile is deterministic.
- The extraction stages (0,2,3) — they still propose; we only changed
  what happens to their output.
- Privacy — everything stays in `~/.trinity/`, append-only ledger
  included.

The net: the chairman keeps *proposing* tensions (good at that); the
**ledger** decides what *persists* (the part that makes a lens stand the
test of time). The lens is a **view over append-only evidence**, not
stateful counters — which is both simpler than the v1 design and immune
to the counter-corruption class that produced this session's incidents.

## Is it beautiful? An honest audit

Asked directly. The answer is split: **the core is beautiful; the
surface still shows its accretion seams.**

### What's beautiful (the core idea)

- **One idea.** "The lens is a stable, self-owned, evidence-backed
  view that speaks in your voice." Every decision flows from that one
  sentence.
- **One primitive, three jobs.** Embedding cosine does basin-membership
  gating (Stage 4 / #186), tension *identity* matching (4.5), AND
  vocabulary canonicalization (4.25). The kernel applied at every layer
  — the recursion isn't decoration, it's the same operation reused. That
  is the mark of a design that found its primitive.
- **One ledger.** `merges.jsonl` is the single source of truth; the
  lens, the routing table, and (formerly) moves all want the same shape:
  *accumulate over an append-only ledger, viewed through cosine
  identity.* Trinity is, at its core, one accumulator.
- **The chairman proposes; the ledger disposes.** Authority sits with
  the evidence, not the stochastic model. That's why B (chairman
  fixpoint) was rejected and why this is robust by construction.

### What's NOT beautiful yet (the seams)

1. **Fractional stage numbers.** 0, 1, 2, 2.5, 3, 4, 4.25, 4.5, 5 — the
   `.5` and `.25` stages are tells that the pipeline grew by *insertion*,
   not design. A beautiful pipeline has clean phases. The honest reframe
   collapses to **four verbs**:
   - **EXTRACT** (today's 0,2,3): chairman reads turns → preference
     evidence → ledger.
   - **GATE** (1,4): basin membership + cross-domain (≥3) filter.
   - **ACCUMULATE** (4.5): cosine-match to registry, accrue evidence,
     derive support.
   - **RENDER** (4.25 + 5): canonicalize to user vocab, order by support,
     distill.
   One primitive (cosine) threads GATE+ACCUMULATE+RENDER; one ledger
   underlies all four. *That* is the beautiful shape; the current
   numbering hides it.

2. **Two extraction passes that are really one.** Stage 0 (rejections:
   REFRAME/REDIRECT/COMPRESSION/SHARPENING) and Stage 2 (decisions:
   privileged↔sacrificed) are both "chairman reads turns, emits a
   preference act." A **rejection IS a decision** — the user privileged
   their substitute's value over the model's offering. Decisions ⊇
   rejections (rejections are the model-miss-triggered subset). The
   beautiful form: **one evidence type — a "preference act" with a
   `trigger` field** (model-miss vs self-expressed) — one extraction
   pass, one ledger schema. `merges.jsonl`'s own docstring already
   reaches for this ("every user-expressed-preference act"). The design
   should lean in: collapse 0+2 into one EXTRACT pass over one evidence
   type. That removes a whole chairman call AND a schema.

3. **Canonicalize-after-academic-phrasing is slightly backwards.** The
   chairman invents academic poles (Stage 3), then 4.25 translates them
   back to your words. More direct: feed the chairman the evidence
   verbatim and ask for poles *in your language* up front. The catch —
   the chairman drifts (the instability we measured), so a deterministic
   canonicalization pass is the stabilizer. Resolution: do **both** —
   nudge the chairman toward user-words in the prompt *and* keep the
   deterministic canonicalization as the guarantee. Belt and suspenders,
   but the suspenders (deterministic) are what hold.

### Verdict

The **idea** is beautiful — one ledger, one cosine primitive doing
triple duty, evidence-over-authority, your-voice rendering. If we ship
the lean A as-is it's *correct and robust* but carries visible accretion
seams (fractional stages, the rejection/decision split).

The **beautiful build** is one more step: collapse to the four verbs and
unify rejections+decisions into one preference-act evidence type. That's
not gold-plating — it *removes* a chairman call and a schema while making
the structure legible. It's the same "simpler is the higher bar" move
that retired moves and audited A.

**Recommendation:** build the lean A core first (it's correct and
unblocks everything), but do it *inside* the four-verb structure from
the start — EXTRACT / GATE / ACCUMULATE / RENDER as the actual module
boundaries — so we don't add a 4.5 to a fractional pile. Treat
rejection+decision unification as the first EXTRACT-phase task, not a
later refactor. Build the beautiful shape, not the seamed one we'd have
to clean up later.

## EXTRACT unification — the sequence (authorized 2026-05-28)

The beauty audit's "first EXTRACT task": collapse rejections (Stage 0,
model-miss) and decisions (Stage 2, self-expressed) into one
**`PreferenceAct`** evidence type with a `trigger` discriminator. High
risk because `rejections.jsonl` is a persisted contract read by the eval
harness and written by provider-import. Sequenced Strangler-Fig so each
stage is independently shippable + reversible and the risky storage
migration comes last:

- **Stage 1 — type + read-layer (SHIPPED v1.7.30).** New
  `me/preference_acts.py`: `PreferenceAct` (round-trip), `from_rejection`
  / `from_decision` adapters, `iter_preference_acts()` unifying the two
  existing on-disk stores. `load_decisions()` added (symmetric to
  save). Render surfaces BOTH triggers as one "Preference acts" section
  in lens.md (decisions now reach the chairman context for the first
  time). Writers, eval harness, provider-import, storage all UNCHANGED.
  Browser-verified on the real lens (49 model-miss + 49 self-expressed).
- **Stage 2 — unify the extraction pass.** Merge the Stage 0 + Stage 2
  chairman calls into one "extract preference acts" prompt that emits
  PreferenceActs with `trigger` set. Removes one chairman call (the
  headline cost win). Keep writing both legacy stores for back-compat.
- **Stage 3 — feed pair-mining from the unified stream.** Stage 3 reads
  `iter_preference_acts()` instead of decisions-only, so rejections also
  become tension candidates (a real lens improvement, not just a
  refactor). Re-tune on real data.
- **Stage 4 — migrate storage + retire the split.** Introduce
  `preference_acts.jsonl`; shim eval-build + provider-import + the
  clobber guard to read/write through it; deprecate `rejections.jsonl` +
  `decisions.jsonl` via the retirement registry. The last + riskiest
  step, taken only after the type is proven across Stages 1–3.

### Environment blocker on the remaining EXTRACT stages (2026-05-28)

Stages 1–3 + the ledger clobber guard shipped (v1.7.30–v1.7.33): the
unified `PreferenceAct` type, the unified read API, eval-build on it, and
the `preference_acts.jsonl` ledger (canonical export, gutting-proof). The
unification is **substantively complete** — every consumer is either
migrated or dual-fed, and the system runs unified.

The remaining work is **blocked in this dev environment**, not by design:

- **Live end-to-end validation, the prompt-merge, and #182 arc-pairs** all
  need real chairman calls. A `lens-build` run here hangs at Stage 0 —
  spawning `claude -p` as a subprocess from *inside* a `claude` session
  doesn't return (auth / TTY / recursion). Confirmed: a real build sat at
  Stage 0 with ~20s CPU over 5.5 min and no active chairman child, then
  was killed; the #194 + ledger clobber guards meant it mutated nothing
  (49 rejections / 98 acts intact).
- **The physical retirement** (flip the read path off the legacy union;
  retire `rejections.jsonl` + `decisions.jsonl`) requires changing
  lens-build's Stage 0/2 *save* path to write the unified store directly.
  That write-path change can't be validated end-to-end without a working
  live build — so it's deferred to a non-nested environment rather than
  shipped blind.

Net: finish the retirement (and the optional prompt-merge / #182) from a
shell where `trinity-local lens-build` can actually reach a provider.

### Stage 4 — storage migration + legacy retirement (the final EXTRACT step)

The capstone of #202: make `preference_acts.jsonl` the single source of
truth and retire `rejections.jsonl` + `decisions.jsonl`. This is the
highest-blast-radius step, so it's sequenced dual-write → flip-reads →
retire, with a rollback window at each stage.

**Dependency map (verified 2026-05-28):**

- *Real disk consumers of the legacy stores:*
  - `commands/eval_import.py` — provider-import APPENDS to rejections.jsonl
    (`_append_signals`) + dedup-reads it (`_load_existing_ids`).
  - `launchpad_data._load_decisions_by_id` — reads decisions.jsonl for the
    lens-card "justified by" backref map.
  - Stage 0 (`me_builder` → `save_rejections`) + Stage 2
    (`save_decisions`) writers.
  - `iter_preference_acts()` — the unified reader (currently unions both
    legacy stores).
- *NOT dependencies (docstring mentions only):* `council_runtime.py`
  (describes rejections.jsonl as conceptual input; data actually flows via
  lens.md + the prompt) and `me/pair_mining.py` (Stage 3 takes
  `decisions: list[Decision]` IN MEMORY from Stage 2, no disk read).

**Sequenced migration:**

1. **Dual-write** (reversible; nothing reads the new file as truth yet):
   lens-build/lens-resync already export `preference_acts.jsonl` via
   `save_preference_acts`. Add the missing writer: `eval_import` appends a
   `PreferenceAct` (trigger=model_miss) to the ledger alongside its
   rejections.jsonl append. Now every writer keeps both stores current.
2. **Backfill**: a one-time pass (`iter_preference_acts()` reads the legacy
   union → `save_preference_acts`) guarantees the ledger is complete before
   any reader trusts it. `lens-resync` already does exactly this.
3. **Flip reads** (one PR, easily reverted): point the two real readers at
   the ledger — `iter_preference_acts()` reads `load_preference_acts()`
   instead of unioning the legacy stores; `_load_decisions_by_id` reads the
   self_expressed acts from the ledger. Validate on real data: a build + a
   provider-import + a launchpad render all agree.
4. **Retire** (only after #3 proves out): stop the legacy writers (Stage
   0/2 save only the ledger; eval_import appends only the ledger), then add
   `rejections.jsonl` / `decisions.jsonl` + `save_rejections` /
   `load_rejections` / `save_decisions` / `load_decisions` to
   `retired_names.py`, and delete the now-dead writers/readers. Keep the
   clobber guard (#194 pattern, already on `save_preference_acts` per
   v1.7.33).

**Rollback safety:** through step 3 both stores stay populated, so any
step reverts by flipping the read back. Step 4 is the point of no return —
take it only once a real build + import + launchpad cycle has confirmed
the ledger is authoritative. Blocked on nothing now (dispatch works);
gated only on wanting a green real-data cycle first.
