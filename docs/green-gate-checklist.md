# Green-gate checklist: don't ship a green check over degenerate data

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
   a green that was never tied to the right invariant. "ready" meant "process
   ran," never "has content.")
2. **Measure the degeneracy. Don't assume.** Pull the *real distribution*, not the
   aggregate. Eyeball N raw rows. Measure coverage / collapse / fallback-rate /
   skew. (Nearly every instance was found by driving the surface on real or
   degenerate data, not by reading code.)
3. **Gate the green on the invariant. Put the disqualifier IN the gate.**
   `flip = wins AND coverage≥floor`. `ok=True` only if no `fix`. `ready` only if
   `tensions>0`. A disqualifier reported in a sibling field is toothless. A
   consumer polling the boolean gets green.
4. **Pre-register the floor** when "enough" is a judgment (`N_C_FLOOR`,
   `COVERAGE_FLOOR`, `MIN_GAMES_FOR_ELO_CHART`, `_VALUE_PROOF_MIN_COUNCILS`, n≥3).
   Fix the number *before* seeing the data and **echo it in the output** so an
   "abstain, N=4" can't be relitigated into a green by tuning.
5. **Abstain honestly below the floor**: "can't tell, N_c=3", "no tensions yet",
   "coverage-gated, not evidence". A first-class result, not a failure.
6. **Dual-test + mutation-verify.** The test must prove the green FIRES on healthy
   data AND is REFUSED on degenerate data. A happy-path test (or a substring-
   presence assert, or a test that asserts the green on a degenerate fixture)
   *enshrines* the bug. Mutation-verify the gate (delete the gate term → the
   degenerate test must red).

## Ship-time checklist (run when you add or change a green)

A green is a boolean (`ok`/`ready`/`*_recommended`/`flip`), a
"complete"/"healthy" status, or a headline metric a surface displays. When you
add or change one, answer these before shipping:

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
`tests/test_green_gate_registry.py`. A new one can't ship until it's listed here
with its classification and gate.

| Green | File | Classification | Gate |
|---|---|---|---|
| `flip_recommended` | `me/holdout_scorer.py` | **data-directive** (promote geometry to spine) | `wins_all AND coverage ≥ COVERAGE_FLOOR (0.5)` + `N_C_FLOOR=5` + `MIN_DISCORDANT_PAIRS=10`, all pre-registered |
| `auto_iterate_recommended` | `mcp_server.py` | heuristic hint (task *shape*, no data floor) | `polish` — polish-shaped task detection; offers iteration, no data-quality claim |
| `should_auto_council` | `mcp_server.py` | heuristic hint (route *mode*, no data floor) | `mode == "council"` — derived from the routing decision, no data-quality claim |
| `judge_validated` | `evals/runner.py` + `commands/eval.py` | **data-directive** (trust the leaderboard ranking) | `judge_agreement >= JUDGE_VALIDITY_FLOOR (0.70)` **AND** `n_parsed >= MIN_ALIGNMENT_PAIRS (15)`. The pair-count floor was added 2026-07-17 after a fallback judge that abstained on noise still stamped True off a 3-pair 1.0 agreement, silencing the caveat on a coin flip. None = unmeasured/insufficient (thin n reads None, never False, so the caveat still prints). Every ranking surface stamps the caveat when not True. Degenerate tests in `test_judge_validity_gate.py` + `test_eval_judge_integrity.py` |
| `palate accuracy` (prospective) | `me/palate_registry.py` + `lens_health._palate_prospective` | **data-directive** (backs the "lens picks what you'd pick" stand-in claim) | accuracy over DECIDED trials only (abstains disclosed, never counted); reads as a number only at n ≥ EARLY_N (10); WEAK below PALATE_WEAK_FLOOR (0.60); train-on-test walled by the snapshot fit-id set; mutation-proven in `test_palate_registry.py` |
| `baseline_floor.trustworthy` | `evals/baseline_floor.py` + `commands/eval.py` + `launchpad_data.py` + `eval_card.py` | **data-directive** (ship or REFUSE the eval-run headline) | `judge_ok (echo_gold − echo_rejected ≥ JUDGE_RECOGNITION_MARGIN 0.25) AND discriminates (real − worst negative control ≥ DISCRIMINATION_FLOOR 0.15)`, both pre-registered; wired as eval-run's pre-report gate 2026-07-11 — refused runs print the refusal instead of the score, are excluded from the eval-show leaderboard + launchpad hero + share card, with the exclusion disclosed; mutation-proven in `test_baseline_floor.py` |
| `eval_set_available` | `evals/builder.py` (read by `status`'s new-model nudge) | **data-directive** (is there a benchmark eval-run can actually run?) | `stats.dispatchable > 0` (items that are cold-answerable **AND** gold-reachable), NOT raw `stats.items` — a set of all context-bound / gold-unreachable items reads NOT available so the nudge never points at a hollow benchmark; legacy sets without the stamp fall back to `stats.items > 0`; added 2026-07-17 (workflow finding); degenerate tests in `test_eval_builder.py::TestEvalSetAvailable` |
| published test counts (`test_count` / `skipped_count` / `collected_count`) | `scripts/render_docs.py` + `tests/conftest.py` | **data-directive** (the launch-credibility headline "N tests passing + M skipped" on CLAUDE.md / LAUNCH_CHECKLIST / launch-package) | reads `test-run-snapshot.json`, written from a real run's terminal summary. `load_run_snapshot()` REFUSES — raises `UnmeasuredCountError`, never defaults — when the snapshot is missing, unparseable, missing a measured field, from a **red** run (`exit_status != 0` or `failed`/`errors` > 0), degenerate (`passed <= 0`), or **stale** (recorded `collected` ≠ a live `pytest --collect-only` count). Pre-registered floor: `passed > 0` and `collected` must match live collection exactly. Fixed 2026-07-31 — the previous extractor scraped `--collect-only` output for a skip summary that collect-only *never emits* and fell back to a hardcoded `4`, so the docs published `collected - 4` passing / `4` skipped while a real run read 3877/516; 113 doc-consistency guards were green on it because they compare doc-to-generator, never generator-to-reality. Doc-to-reality now runs at the publishing boundary (`render_docs.py --check`, step 5 of `launch-check.sh`, after the suite). Degenerate tests in `test_measured_test_counts.py::TestRefusedOnDegenerateInput` |
| `render_docs --check` exit 0 (and its `--allow-unmeasured` variant) | `scripts/render_docs.py` + `tests/test_doc_count_consistency.py::TestCanonicalPlaceholdersAreRendered` | **decision-directive** (the "published docs match reality" green that gates launch-check step 5) | Two defects found 2026-07-31 by RUNNING the row-above mechanism rather than reading it. (1) `--check` called `render_file()` with the write unconditional, so the read-only verifier **re-rendered every drifted doc and then reported the drift it had just erased** — a second `--check` passed, i.e. the gate could only ever fail once. Now `write=not args.check`; proven by asserting bytes are unchanged after a drifted `--check`. (2) The measured counts come from a snapshot written by the run that is *executing the check*, so gating an in-suite test on the strict refusal made red an **ABSORBING state**: red run → red snapshot → renderer refuses → test fails → red run, forever (observed live; the suite could not return to green without hand-deleting the snapshot). Split by caller: the **publishing boundary** (`--check`, no flag, launch-check.sh step 5, after the suite) still refuses on any unmeasured count — that is the doc-to-reality gate and it is deliberately NOT loosened. The **in-suite** copy passes `--allow-unmeasured`, which checks only placeholders computable from the tree alone and prints the unmeasured ones under a heading that cannot read as agreement, keeping their on-disk value (same contract as a REFUSED evidence claim — an unmeasured count is never planted from a default). Pre-registered floor: tolerance covers *only* `UnmeasuredCountError`; every other extractor failure still exits 1, and real drift in any computable placeholder still exits 1 **even under `--allow-unmeasured`**. Degenerate tests in `test_render_docs_check_modes.py` (`TestCheckIsReadOnly`, `TestUnmeasuredIsNotDrift`); mutation-proven — restore the unconditional write, drop the `--allow-unmeasured` branch, or plant a default for an unmeasured count, and each reds a named test (3/3 observed RED, restore GREEN) |
| `evidence_status` (VERIFIED / REFUSED / ABSENT) | `evidence_claims.py` + `scripts/render_docs.py` (plants the ledger percentages in CLAUDE.md) | **data-directive** (publish or WITHHOLD the measured per-model claims the product rests on — "you side with Opus 4.8 on 68%", "chairman picks your branch 66.1%") | Returns a THREE-state status, never a boolean, because the source artifact (`~/.trinity/disagreement_ledger/summary.json`) is absent in CI and on a fresh clone — a two-state design would make "could not look" indistinguishable from "looked and it agrees", which is this checklist's whole subject. Gate for VERIFIED: `summary["tally_trustworthy"] is True` (the ledger engine's own K3-band + K4-discrimination + `resolved >= K4_MIN_RESOLVED` gate — if the tally is not fit to show a user via `trust`, it is not fit to plant in a doc) **AND** every claim's cell clears `disagreement_ledger.MIN_TALLY_N` (10) **AND** every registered claim resolves. Both floors are the engine's pre-existing pre-registered constants, deliberately not new numbers chosen after seeing the corpus. Any miss ⇒ REFUSED with **zero** values planted (never a partial render); artifact missing **from disk** ⇒ ABSENT. A file that EXISTS but is truncated / empty / not a JSON object is REFUSED, **not** ABSENT — it is degenerate data, not a missing input, so it must fail rather than skip. (The first cut returned `None` for both and so *skipped* on a half-written `trust --build`; mutation testing caught it 2026-07-31. `read_ledger_summary()` now returns `(exists, parsed)` so the two facts cannot re-collapse.) `render_docs.py` plants values only on VERIFIED, prints the state under a "NOT CHECKED" heading otherwise, and `--require-evidence` turns non-VERIFIED into exit 2. Added 2026-07-31 — the renderer machine-checked 9 inventory counts and **zero** of the ~25 evidence percentages, so the trivia was guarded and the evidence was prose; the first run caught a live violation (a pre-contamination-fix `69%` effort figure the file's own never-requote rule forbade), and the SECOND catch was the fix itself: the replacement prose asserted a SHAPE — "the only effort sub-cell that surfaces at all is GPT-5.5·xhigh" — read off the single key the sentence was about, while `effort_breakdown` carried three. That is why two of the claims (`ledger_effort_cells_n`, `ledger_effort_max_levels_per_model`) COUNT the artifact rather than look a key up: the load-bearing fact was never the count but that no model×version has a second effort level, so no cell has a sibling and effort cannot have produced a contrast. Both refuse (never render `0`) when there is no effort evidence at all. Degenerate tests + the absent/refused separation in `test_evidence_claim_guards.py` (`TestRefusalPath`, `TestThreeStatesAreDistinguishable`); mutation-proven (drop the trustworthy gate, collapse ABSENT into VERIFIED, swap half-up for banker's rounding, drift a doc number, unwrap a placeholder — each reds a named test) |
| `consolidate --prune-orphans` `ok` + `classify_basins` `decisive` | `lens_routing.prune_orphan_rules` / `classify_basins` + `commands/cortex.py` + `commands/status.py` + `launchpad_data.py` | **data-directive** (deletes rows from the chairman's accumulated picks; and `decisive` is the count both the status line and the launchpad card publish as "N basins route") | Basin ids are POSITIONAL and re-drawn on every lens build (measured 2026-07-31: median membership Jaccard **0.000** between a base clustering and a 99% subsample), so a rule can outlive its basin and become unreachable — `place_query` only ever returns a LIVE id. `classify_basins(rules, basin_ids)` therefore counts such a rule as `orphan`, never `decisive`: on the founder's corpus 6 of 31 rules were orphaned and one (`b01d`, margin 0.35, effective_n 3.06) CLEARED the routing gate, so both surfaces read 4 decisive where only 3 could fire. `basin_ids=None` OMITS the `orphan` key rather than reporting 0 — not-looked-at must not read as zero. Pruning is a DELETE, so it refuses on degenerate topology against two pre-registered floors: `MIN_BASINS_FOR_PRUNE` (5) and `MAX_ORPHAN_DROP_FRACTION` (0.5, above which the id SCHEME changed and a human decides); a refusal keeps every rule, reports zero drops, and exits **non-zero**. `pick_routes` (margin>=0.15 AND effective_n>=3) is deliberately untouched — orphan-ness is a separate axis applied by the caller that knows the topology. Degenerate tests in `test_picks_orphan_prune.py` (`TestPruneRefusesOnDegenerateInput`); mutation-proven (drop either floor, drop the orphan branch, un-thread the basin ids from the launchpad, break the template's `v-if` — each reds a named test) |
| `semantic_noise_report.ready` | `me/semantic_filter.py` (read by `lens_health._semantic_noise`) | **data-directive** (green/abstain the "signal vs noise" lens-health row) | `ready` requires `total > 0` scored nodes — the zero-guard lives IN the gate, NOT only in the sibling `fraction` (which just dodged the ZeroDivision). A basins-present-but-zero-embedded corpus (backfill-stall) now abstains instead of greening "0% of the corpus reads as noise" off nothing measured; added 2026-07-17 (workflow finding); degenerate test in `test_semantic_filter.py::test_semantic_noise_report_abstains_on_zero_embedded_corpus` |

"Heuristic hint" greens gate on task/route *shape*, not on a *data distribution*,
so they need no degeneracy floor. But they must be classified here (the ratchet
forces the author to make that call explicitly).

## Where the bug class lives (Phase-1 inventory, 2026-06-02)

The launchpad **cards are comprehensively gated and tested**: elo
(`MIN_GAMES_FOR_ELO_CHART`), cortex (demotes below trust threshold), council-value
(`vp.ready`), timeline (`min_prompts`), cold-open (confidence-softens), eval
(mixed-set guard), memory-health (#273 soft-degrade-needs-fix), lens "✓ ready"
(`lensPopulated`). The discipline is fully present there. The recurrences live in
**(a) backend write/compute paths** (the lens clobber, Stage-0 cliff-drop) and
**(b) new code that didn't inherit the card discipline** (the holdout flip). Hunt
those, not the established cards.

## Sibling discipline: the accretion / divergent-duplication guard

The green-gate bug is *one check over degenerate data*. Its sibling is *two checks
over the same data that have drifted apart*. One concept gets implemented in two places,
where a fix lands on one copy and the twin silently keeps the old behavior. Same
root failure (a check that looks fine but isn't). Different shape. This discipline
shipped after the #316 eval-unification work. The 3rd patch on the
rejection→eval seam surfaced ~10 divergent copies at once (cosine in 4 modules, the
fence-stripper in 2, `_write_prompt_node` in 2 test files, `MAX_MISSING_POLLS` in 3
pollers, a real-contest threshold inlined twice).

### Trigger: when to run this

Run the audit when ANY of these fires. Don't wait for a bug report:

- **The 3rd patch on one seam.** Two fixes to "the same area" is a coincidence. The
  third is the signal that the *seam itself* is duplicated, not just buggy.
- **You're about to add robustness to ONE of two similar call sites.** If the twin
  doesn't get the same guard, you've just *created* a divergence.
- **A grep for a literal/constant/helper name returns 2+ hits in non-test code.**

### The divergence checklist: what to look for

- **Two readers of one source diverging.** Two parsers/loaders of the same file or
  field that don't agree on shape (the gemini ingest reading raw frames. Two
  `json.loads` sites guarding the parse but not the resulting *type*).
- **One concept computed twice.** Cosine similarity, "is this the same text?",
  "strip the fence", "is this a real contest?". Each had ≥2 implementations that
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
things that are *coincidentally* similar but *semantically distinct*. A forced
shared helper then grows flags and branches to serve both callers. You've traded
two clear copies for one tangled one. Before extracting, all of:

- [ ] The two copies compute the **same concept**, not just similar-looking code. If
      a kwarg/flag has to switch behavior per caller, they may be distinct. Stop.
- [ ] The unified version is the **superset** each caller uses unchanged (the
      `_write_prompt_node` merge kept the `provider` kwarg the smoke copy lacked.
      Every call site was untouched).
- [ ] It **nets fewer lines and fewer places to change**, with **one obvious home**
      (a shared module / `conftest` / `embeddings.cosine_similarity`), not a new
      dependency edge between two peers that risks an import cycle.
- [ ] **grep `tests/` before deleting any "dead" symbol.** A test may import the
      copy you're removing (deleting `cross_provider_pairs._cosine` red'd a test
      that imported it directly. Re-point the import at the canonical name). And
      after a deletion, `rm -rf` stale `__pycache__` before trusting a green
      orphan/grep guard. Stale bytecode keeps a removed string alive.

When unification would *add* coupling or branches, leave the copies and reach for a
shared **constant** or a guard instead. The goal is fewer places the bug can hide,
not DRY for its own sake.
