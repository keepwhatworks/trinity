# Green-gate checklist — don't ship a green check over degenerate data

> The single most recurring bug shape in Trinity is **a green check (ok / ready /
> healthy / complete / `*_recommended` / a headline metric) passing while the
> underlying data is degenerate.** It even recurred *inside the validator built
> to catch it* (the holdout scorer's `flip_recommended`, 2026-06-02). This is the
> standing discipline for any code that emits a green. See principle **#35** +
> corollary in [`historical/principles.md`](historical/principles.md) and the
> [`data_sampling_principle`] memory.

## The contract (one line)

**A green must gate on the INVARIANT it attests, not a cheap PROXY. The
disqualifier goes IN the gate, never a sibling field. Below a pre-registered
floor, abstain.**

The bug is always the same: the green attests to a proxy that's cheap to check
(process ran / file present / JSON parsed / a test string is present / the sign
test was won) instead of the invariant it's supposed to mean (the output has
content / the data covers enough / the number is comparable / the distribution
isn't collapsed). Degenerate data passes the proxy and fails the invariant, so
the check stays green.

## The 6-step protocol

1. **Name the invariant** the green is supposed to attest. (Most of these bugs are
   a green that was never tied to the right invariant — "ready" meant "process
   ran," never "has content.")
2. **Measure the degeneracy — don't assume.** Pull the *real distribution*, not the
   aggregate; eyeball N raw rows. Measure coverage / collapse / fallback-rate /
   skew. (Nearly every instance was found by driving the surface on real or
   degenerate data, not by reading code.)
3. **Gate the green on the invariant — put the disqualifier IN the gate.**
   `flip = wins AND coverage≥floor`; `ok=True` only if no `fix`; `ready` only if
   `tensions>0`. A disqualifier reported in a sibling field is toothless — a
   consumer polling the boolean gets green.
4. **Pre-register the floor** when "enough" is a judgment (`N_C_FLOOR`,
   `COVERAGE_FLOOR`, `MIN_GAMES_FOR_ELO_CHART`, `_VALUE_PROOF_MIN_COUNCILS`, n≥3).
   Fix the number *before* seeing the data and **echo it in the output** so an
   "abstain, N=4" can't be relitigated into a green by tuning.
5. **Abstain honestly below the floor** — "can't tell, N_c=3", "no tensions yet",
   "coverage-gated, not evidence". A first-class result, not a failure.
6. **Dual-test + mutation-verify.** The test must prove the green FIRES on healthy
   data AND is REFUSED on degenerate data. A happy-path test (or a substring-
   presence assert, or a test that asserts the green on a degenerate fixture)
   *enshrines* the bug. Mutation-verify the gate (delete the gate term → the
   degenerate test must red).

## Ship-time checklist (run when you add or change a green)

When you add or change anything that returns/sets a green — a boolean
(`ok`/`ready`/`*_recommended`/`flip`), a "complete"/"healthy" status, or a
headline metric a surface displays — answer these before shipping:

- [ ] **Invariant, not proxy:** does the green gate on the thing it actually
      claims, or on a cheap proxy (process finished / field present / parsed /
      string present / won a test)?
- [ ] **Gate, not sibling:** is *every* disqualifier folded into the single
      decision, or is one sitting in a sibling field a consumer could ignore?
- [ ] **Pre-registered floor:** if "enough" is a judgment, is the floor a named
      constant set before seeing the data, and echoed in the output?
- [ ] **Honest abstain:** below the floor, does it abstain with a truthful
      message (no soft pass, no misleading "ready"/"evidence")?
- [ ] **Dual test, mutation-verified:** does a test assert the green is REFUSED on
      degenerate input (not just that the disqualifier is reported), and does
      deleting the gate red it?
- [ ] **Drove the real + degenerate data:** did you pull the real distribution and
      eyeball raw rows, and drive the surface on degenerate synthetic data?

## Registry of decision-directive greens

Decision-directive greens (booleans/metrics that tell a consumer to take an action
based on data) carry the highest risk and are ratcheted by
`tests/test_green_gate_registry.py` — a new one can't ship until it's listed here
with its classification and gate.

| Green | File | Classification | Gate |
|---|---|---|---|
| `flip_recommended` | `me/holdout_scorer.py` | **data-directive** (promote geometry to spine) | `wins_all AND coverage ≥ COVERAGE_FLOOR (0.5)` + `N_C_FLOOR=5` + `MIN_DISCORDANT_PAIRS=10`, all pre-registered |
| `auto_iterate_recommended` | `mcp_server.py` | heuristic hint (task *shape*, no data floor) | `polish` — polish-shaped task detection; offers iteration, no data-quality claim |
| `should_auto_council` | `mcp_server.py` | heuristic hint (route *mode*, no data floor) | `mode == "council"` — derived from the routing decision, no data-quality claim |

"Heuristic hint" greens gate on task/route *shape*, not on a *data distribution*,
so they need no degeneracy floor — but they must be classified here (the ratchet
forces the author to make that call explicitly).

## Where the bug class lives (Phase-1 inventory, 2026-06-02)

The launchpad **cards are comprehensively gated and tested** — elo
(`MIN_GAMES_FOR_ELO_CHART`), cortex (demotes below trust threshold), council-value
(`vp.ready`), timeline (`min_prompts`), cold-open (confidence-softens), eval
(mixed-set guard), memory-health (#273 soft-degrade-needs-fix), lens "✓ ready"
(`lensPopulated`). The discipline is fully present there. The recurrences live in
**(a) backend write/compute paths** (the lens clobber, Stage-0 cliff-drop) and
**(b) new code that didn't inherit the card discipline** (the holdout flip). Hunt
those, not the established cards.

## Sibling discipline — the accretion / divergent-duplication guard

The green-gate bug is *one check over degenerate data*. Its sibling is *two checks
over the same data that have drifted apart* — one concept implemented in two places,
where a fix lands on one copy and the twin silently keeps the old behavior. Same
root failure (a check that looks fine but isn't); different shape. This discipline
shipped after the #316 eval-unification work, where the 3rd patch on the
rejection→eval seam surfaced ~10 divergent copies at once (cosine in 4 modules, the
fence-stripper in 2, `_write_prompt_node` in 2 test files, `MAX_MISSING_POLLS` in 3
pollers, a real-contest threshold inlined twice).

### Trigger — when to run this

Run the audit when ANY of these fires; don't wait for a bug report:

- **The 3rd patch on one seam.** Two fixes to "the same area" is a coincidence; the
  third is the signal that the *seam itself* is duplicated, not just buggy.
- **You're about to add robustness to ONE of two similar call sites.** If the twin
  doesn't get the same guard, you've just *created* a divergence.
- **A grep for a literal/constant/helper name returns 2+ hits in non-test code.**

### The divergence checklist — what to look for

- **Two readers of one source diverging.** Two parsers/loaders of the same file or
  field that don't agree on shape (the gemini ingest reading raw frames; two
  `json.loads` sites guarding the parse but not the resulting *type*).
- **One concept computed twice.** Cosine similarity, "is this the same text?",
  "strip the fence", "is this a real contest?" — each had ≥2 implementations that
  could (and did) drift. The **narrower** copy is the latent bug: the scorer's
  `(?:json)?` fence-stripper silently left a ```` ```text ```` fence in and broke
  the parse, while its twin handled any language tag.
- **A consumer routing around a pipeline.** A reader using a *direct* path instead
  of the canonical accessor (the launchpad reading `prompts/` directly, bypassing
  the lazy migration → "0 prompts" for upgrading users).
- **X has robustness Y lacks.** Two copies where one got a shape-guard / floor /
  dedup and the twin didn't.
- **Band-aids piled on a seam.** Each patch routes *around* the previous one rather
  than fixing the shared cause.

### The net-simplicity guardrail (so the cure isn't worse than the disease)

Unify **only when it nets simpler.** The failure mode of a DRY pass is merging two
things that are *coincidentally* similar but *semantically distinct* — a forced
shared helper then grows flags and branches to serve both callers, and you've traded
two clear copies for one tangled one. Before extracting, all of:

- [ ] The two copies compute the **same concept**, not just similar-looking code. If
      a kwarg/flag has to switch behavior per caller, they may be distinct — stop.
- [ ] The unified version is the **superset** each caller uses unchanged (the
      `_write_prompt_node` merge kept the `provider` kwarg the smoke copy lacked;
      every call site was untouched).
- [ ] It **nets fewer lines and fewer places to change**, with **one obvious home**
      (a shared module / `conftest` / `embeddings.cosine_similarity`) — not a new
      dependency edge between two peers that risks an import cycle.
- [ ] **grep `tests/` before deleting any "dead" symbol** — a test may import the
      copy you're removing (deleting `cross_provider_pairs._cosine` red'd a test
      that imported it directly; re-point the import at the canonical name). And
      after a deletion, `rm -rf` stale `__pycache__` before trusting a green
      orphan/grep guard — stale bytecode keeps a removed string alive.

When unification would *add* coupling or branches, leave the copies and reach for a
shared **constant** or a guard instead — the goal is fewer places the bug can hide,
not DRY for its own sake.
