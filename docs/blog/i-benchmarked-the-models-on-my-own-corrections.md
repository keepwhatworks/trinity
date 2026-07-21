---
class: aspirational
title: "Three AI labs can't tell you which one to trust. So I kept score on the disagreements my own work settled."
published: true
description: "Public leaderboards say which model is best on average. I wanted which model to trust for MY work. I built a judge-scored benchmark first, measured the judge, and killed it. What survived is better: a ledger of model disagreements, settled by what I actually did next. Local, open, private."
tags: ai, opensource, machinelearning, privacy
canonical_url: https://github.com/keepwhatworks/trinity/blob/main/docs/blog/i-benchmarked-the-models-on-my-own-corrections.md
cover_image: ""
voice_pass: rewritten 2026-07-15 around the disagreement ledger (the judge-scored instrument was retired by its own measurement)
---

# Three AI labs can't tell you which one to trust. So I kept score on the disagreements my own work settled.

Every leaderboard answers the wrong question. HumanEval, MMLU, LMArena. Strongest *on average, across everyone*.
I don't want the average. I want this: for my work, on the questions I actually ask, which model should I trust?

The labs can't answer that. Anthropic can't say "use ChatGPT." OpenAI can't say "Claude won." Permanent conflict of interest.
The most useful comparison in AI is the one with no honest source.

So I built the source. Twice. The first version died by its own measurement. That story is the point of this post.

## The benchmark nobody can publish

A model 2% better on HumanEval can still be the wrong model for me. Because I don't write HumanEval problems.
I ask for short and get a lecture. I ask for a spec and get a story. I ask for the actual identifiers and get hand-waving.
Best-for-me is the model that makes *those* mistakes least. No public benchmark measures it, because it's different for every person.

The labs are the obvious people to build a personalized cross-model benchmark. And the exact people who can't.
"Claude wins your refactors, GPT wins your compression" is a scorecard each of them could only ever publish the flattering half of. It has to come from outside all three.

## The benchmark I built first, and why I killed it

Version one was the obvious design. Take the answers I rewrote, turn each into a scored question, and have a judge model grade every candidate against my corrections.
I even measured the judge before trusting it. I handed it pairs where I had already picked a side, randomized A/B, and checked how often it agreed with me.

Then I ran the experiment that mattered. Swap the judge and hold everything else still.
The score moved about four times more than the gap between the models.

Read that again. The number I wanted to publish was mostly a measurement of the judge, wearing the models as a costume.
So the leaderboard died. I deleted the shareable score card the same week. A benchmark that measures its own grader is not a benchmark.

## What survived: the disagreement ledger

Here is what I noticed while burying version one. I never needed a judge to grade *quality*. I needed to know *which side to take when the models split*.
And I already had thousands of splits on disk.

I run my hard questions through Claude, GPT, and Gemini at once. A chairman synthesizes the verdict and records exactly where they disagreed.
Weeks later, my own work settles those disagreements. I shipped the design one model argued for. I deleted the abstraction another one defended. I renamed the thing a third one insisted was fine.

That settling is the answer key. Not a model's opinion of "good." What I actually did, with my own hands, once reality weighed in.
An extractor reads my later work blind. It does not know which model said what. It marks each disputed claim followed or contradicted. Credit lands on whichever models took the side my work took.

No judge grades an answer. No one rates anything. The scoreboard is behavior.

## The numbers, with their teeth

On 119 disagreements my later work settled, as of mid-2026. The models below are the ones I ran then. Newer ones, the Claude 5 and GPT-5.6 generation, accrue their own rows as I use them. The number never stops moving.

| Model | Record | Win rate |
|---|---|---|
| Claude Opus 4.8 | 37 wins, 11 losses | **77%** |
| Claude Opus 4.7 | 20 wins, 19 losses | 51% |
| GPT-5.5 | 48 wins, 47 losses | 51% |
| Gemini 3.1 Pro | 30 wins, 62 losses | 33% |

The Opus 4.8 confidence interval excludes a coin flip. The Opus 4.7 record *is* a coin flip.

That gap is the finding. One version apart, same lab, same brand. A generic "Claude" score would blend them to about 62% and hide the only thing worth knowing.
Model, size, and version. Or the number means nothing.

## How it earns the right to be believed

A benchmark dies by a thousand "well, actually"s. So the gates were registered before the run, and the run had to clear every one:

- **Coverage floor.** The extractor had to find real evidence for at least 40% of disagreements. It found 94%.
- **Reliability floor.** Same evidence, extracted twice, had to agree. Kappa 0.70 against a 0.60 floor.
- **Blind extraction.** The extractor never sees which model held which position. It cannot flatter anyone.
- **A coin-flip kill.** If no model's interval had excluded 50%, the whole instrument would have died, and I would have said so. Two did: one above the coin flip, one below it.
- **Honest scope.** This is my corpus. 119 resolved disagreements, one user. Your number will be yours, and it accrues from your own councils. I am not publishing a universal ranking. I am publishing a method.

And the standing proof of all of it: the first instrument failed these standards, and it is dead. The graveyard is the credential.

## And it never leaves my laptop

The raw material is my own transcripts and councils. They live in a local folder I can audit and you can't.
Nothing uploads. The method is fully open source instead. You don't have to trust my number. You can run yours.

So I still can't tell you which AI is best. Nobody honestly can. But I can tell you which one my own settled work keeps siding with, at the exact model version, on my kind of question.
The method is sitting right there for you to run on yours.

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```
