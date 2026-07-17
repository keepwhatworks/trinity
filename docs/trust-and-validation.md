# Trust & validation: why you can believe the eval

Trinity's eval makes one claim: *on the kinds of questions you actually ask, model X
beats model Y for **your** taste.* The obvious attacks on that claim are well-known,
and the answer to every one of them is the same: **measure it, don't assert it, and
publish the method and the numbers, never your data.**

This doc is the skeptic's checklist. Every item is either already enforced in code or
a documented validation you can run.

## Grounding in the literature (this isn't a crank idea)

The three pillars (*multi-perspective synthesis*, *learn taste from edits*,
and *score with an LLM judge*) are all established research lines. We build on
their known results rather than rediscovering them.

- **Multi-perspective synthesis with preserved disagreement is independently
  validated.** Gauntlet,
  [*Can LLMs Perform Deep Technical Comprehension of Computer Architecture Papers?*](https://arxiv.org/abs/2607.11859)
  (Aggarwal et al., **2026**, UW–Madison and NVIDIA), runs five independent
  reviewer agents and a synthesizer whose defining instruction is to preserve
  disagreement rather than average it. Human evaluators preferred it over
  trained human analyses in 15 of 20 comparisons. Their ablation isolates the
  gain to the synthesis pass: the pipeline beats the same model run as a
  single rich-persona agent on 96% of papers. That is Trinity's chairman +
  disagreed-claims architecture, measured by another lab with a same-model
  ensemble. Cross-provider member diversity is the axis their limitations
  section names as untested. They also demote their LLM judge to a secondary
  instrument with randomized ordering, the same discipline Trinity's
  judge-validity floor enforces.
- **Learning preference from edits is peer-reviewed.** PRELUDE / CIPHER,
  [*Aligning LLM Agents by Learning Latent Preference from User Edits*](https://arxiv.org/abs/2404.15269)
  (Gao et al., **NeurIPS 2024**), infers a user's latent preferences from their edits and
  represents them as an **interpretable, user-editable text description**. That is exactly
  Trinity's lens: your rewrites → a description of your taste you can read and change. Related:
  [RL from User Conversations](https://arxiv.org/abs/2509.25137) and
  [RLUF](https://arxiv.org/html/2505.14946v1) both summarise per-user latent preferences from
  conversation history. The same move.
- **The judge's failure modes are catalogued. We control for each.** The
  [LLM-as-Judge survey](https://arxiv.org/pdf/2412.05579) and
  [*Justice or Prejudice?*](https://arxiv.org/pdf/2410.02736) enumerate the biases that make a
  naïve judge untrustworthy (position, verbosity/length, self-preference). We address them
  explicitly. See the mapping in *The confounds we control for* below.
- **Implicit feedback is known to be noisy, so we gate on significance.**
  [*User Feedback in Human-LLM Dialogues: …Noisy as a Learning Signal*](https://arxiv.org/html/2507.23158v2)
  and [ImplicitRM](https://arxiv.org/html/2603.23184) find implicit preference data lacks clean
  negatives and carries user-propensity bias. That predicts exactly what we observe at honest
  sample size. That is why the judge pick is **significance-gated** and refuses to crown a
  noise winner rather than overclaiming.

Trinity's distinct contribution is not the idea but the *deployment*: a **per-user** eval built
from **your own** corrections, **cross-provider**, run **locally**. The 2025–26 personalization
benchmarks ([PersonaLens](https://aclanthology.org/2025.findings-acl.927/), PREFEVAL, PersonaMem,
[PersonaFeedback](https://arxiv.org/pdf/2506.12915)) are shared, researcher-built benchmarks of
personalization *capability*. None turns *your own* trace into *your own* answer key.

## The three ways the judge is trusted

A taste eval is only as good as its judge. We never ask you to *trust* that the judge
is aligned. We measure its alignment three independent ways:

1. **Against your own corrections (per-user).** Every time you rewrote a model's
   answer, that's a human-labelled preference: you privileged your version over the
   model's. `eval-judge-check` replays those as position-balanced A/B pairs and measures
   how often each candidate judge picks *the side you actually chose*. The most-aligned
   judge is selected by that number, not by reputation. This is the strongest signal
   because it's *your* taste. But it's private and small-n, so we also do (2) and (3).

2. **Against public human-preference datasets (the mechanism).** The same harness runs
   on datasets where the ground truth is published human preference, so anyone can
   reproduce the mechanism independent of your private data:

   | Dataset | What it is | What it proves |
   |---|---|---|
   | **RewardBench** (Allen AI) | The standard LLM-as-judge benchmark. Human-verified chosen/rejected pairs across chat, safety, reasoning | Our judge agrees with verified human preference at published strong-judge rates (~70–85%) |
   | **Chatbot Arena / LMSYS** | Real head-to-head human votes between model answers | Agreement with crowd preference at scale |
   | **Anthropic HH-RLHF** | Helpfulness/harmlessness chosen-vs-rejected human labels | The judge tracks human preference, not model idiosyncrasy |
   | **MT-Bench (human)** | Per-turn expert judgments | Agreement on multi-turn quality |

   The argument: if the judge agrees with *known* human preference at strong-judge rates,
   the *same* judge applied to your private pairs is trustworthy for the same reason.
   **Shipped:** `eval-judge-check --dataset <file>` runs the *identical* harness
   (position-balancing, length split, shuffle-null, significance gate) against a
   public set you downloaded. It parses the `chosen`/`rejected` (RewardBench, HH-RLHF),
   `response_a`/`response_b`+`winner` (Arena), or `option_a`/`option_b`/`human_side`
   shapes, and asks the *generic* preference question (no lens, because the public label
   is generic human preference, not your taste). It reports the **per-category** breakdown
   (RewardBench's chat / reasoning / safety split, where you see a judge strong on chat
   but weak on safety), not just the aggregate. The public run is saved to a **separate**
   report (`judge_alignment_public.json`), so it never changes the judge `eval-run`
   picks from *your* corrections. Offline: it reads the local file, no network.

3. **Against negative controls (the harness isn't rigged).** A measurement that can't
   fail isn't a measurement. The placebo battery proves the harness *detects* a bad judge:

   - **Random/placebo judge → ~50%.** A judge that answers at random must score a coin
     flip. If it scores high, the measurement is broken. (Position-balancing guarantees
     this: the constant-"A" judge lands at exactly 50%. See `test_position_biased_judge_lands_near_half`.)
   - **Shuffle-labels null → ~50%.** Re-score a real judge's verdicts against *shuffled*
     human sides. Agreement should collapse to chance. If it stays high, the headline
     number is an artifact, not earned. **Shipped + reported:** `eval-judge-check` prints a
     `shuffle-null` line per judge (averaged over 300 seeded permutations). A judge that
     genuinely aligns shows real agreement well above its ~50% null. That *gap* is the proof
     (`shuffle_null_agreement`, guarded by `test_shuffle_null_collapses_to_chance_for_a_real_agreement`).
   - **Identical-response control → no signal** when A == B (degenerate pairs are dropped
     at build time, `build_preference_pairs`).

## The confounds we control for

Beyond "is the judge aligned," the *data* can lie even with a perfect judge. `eval-audit`
scans for the whole class, locally. No dispatch, no quota, no prompt text leaves your machine.
Each row below names the documented bias a reviewer would cite, and our control for it:

| Named bias (literature) | What it is | Trinity's control |
|---|---|---|
| **Position bias** ([survey](https://arxiv.org/pdf/2412.05579)) | judge favours answer A or B by *slot*, not quality | A/B **position-balancing**. A constant-answer judge scores exactly 50% (`test_position_biased_judge_lands_near_half`) |
| **Verbosity / length bias** ([*Justice or Prejudice?*](https://arxiv.org/pdf/2410.02736), measured ~+17%) | judge equates longer with better | `eval-audit` flags the *data* skew **and** `eval-judge-check` reports a **length-controlled split** (agreement when your pick was shorter vs longer). A length prior is disclosed, not hidden |
| **Self-preference bias** ([2410.21819](https://arxiv.org/html/2410.21819v2)) | judge rates its own family higher | one **fixed, validated** judge chosen by *measured* alignment (not rotation). Agreement split by the rejected answer's producer where known |
| **Small-n / noise** ([noisy implicit feedback](https://arxiv.org/html/2507.23158v2)) | a ranking off few pairs is sampling noise | judge "chosen" only at **≥15 pairs, beats random, ≥10-pt lead** (`select_aligned_judge`), else *statistically tied* → heuristic. Aggregate ships with a 95% CI |
| **Axis imbalance** | one rejection axis dominates the aggregate | always report the **per-axis** breakdown. Scanner warns past threshold |
| **Degenerate gold** (#247) | prompt already contains the answer → every model "passes" | built-in drop guard. Scanner re-asserts it's zero |

The through-line: every one of these is a *named, published* way an LLM judge goes wrong.
Each has a control that is either enforced in code or surfaced by `eval-audit`. "Models grading
models" is answered upstream. The **gold is the human's rewrite** (your privileged side), not a
model's output.

## The privacy invariant

Transparency is the **method** and the **numbers**, never the **data**. Your prompts,
your rewrites, and your lens never leave your machine. Every shareable artifact (the eval
card, the launchpad, this validation report) carries counts and rates only:

- `eval-audit --json` emits severities, metrics, and counts. No prompt or response text
  (guarded by `test_methodology_check.py::test_findings_leak_no_raw_text`).
- The judge-alignment report is agreement fractions and pair counts. No pair contents
  (guarded by `test_eval_privacy_no_prompt_leak.py`).
- The per-item ledger (`preference_acts.jsonl`) stays local. Only categorical labels ever
  appear in opt-in telemetry.

So the trust pitch is reproducible without seeing anyone's data: clone the repo, point the
harness at RewardBench, run the negative controls, read the numbers. The method is open.
The corpus is yours.

## Run it yourself

```bash
trinity-local eval-audit                         # scan YOUR eval data for methodology bugs (local, no quota)
trinity-local eval-judge-check                   # measure each judge's agreement with your corrections + length split
trinity-local eval-judge-check --dataset rb.jsonl --dry-run  # check the file parses + see its category coverage (no quota)
trinity-local eval-judge-check --dataset rb.jsonl  # reproduce the mechanism on a PUBLIC set (RewardBench/Arena/HH-RLHF)
trinity-local eval-run                           # score the models on your kind of question, with CI + per-axis
```

`eval-audit` costs no model quota. The judge checks cost a few dispatches each.
The `--dataset` form needs a preference file you downloaded yourself
(`huggingface-cli download allenai/reward-bench`). Trinity never fetches it. All
of them keep every *private* prompt on your machine. The public-set run touches
only the public file.
