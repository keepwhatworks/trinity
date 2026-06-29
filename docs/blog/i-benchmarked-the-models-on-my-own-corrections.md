---
class: aspirational
title: "Three AI labs can't tell you which one is best. So I built a benchmark that scores them on the answers I rewrote."
published: true
description: "Public leaderboards say which model is best on average. I wanted which is best for MY work. So I scored Claude, GPT, and Gemini on the answers I actually rewrote, with a judge I measured against my own corrections. Local, open, private."
tags: ai, opensource, machinelearning, privacy
canonical_url: https://github.com/keepwhatworks/trinity/blob/main/docs/blog/i-benchmarked-the-models-on-my-own-corrections.md
cover_image: ""
voice_pass: council_85247df76a7d46d2 (winner=codex, your lens)
---

# Three AI labs can't tell you which one is best. So I built a benchmark that scores them on the answers I rewrote.

Every leaderboard answers the wrong question. HumanEval, MMLU, LMArena. Strongest *on average, across everyone*.
I don't want the average. I want this: for my work, on the questions I actually ask, which model is best for *me*?

The labs can't answer that. Anthropic can't say "use ChatGPT." OpenAI can't say "Claude won." Permanent conflict of interest.
The most useful comparison in AI is the one with no honest source.

So I built the source. It scores Claude, GPT, and Gemini on the one answer key only I have. The times I rewrote their answers.
Then I spent the hardest part of the build making it un-poke-able. Because the day you publish a benchmark, people show up to break it.

## The benchmark nobody can publish

A model 2% better on HumanEval can still be the wrong model for me. Because I don't write HumanEval problems.
I ask for short and get a lecture. I ask for a spec and get a story. I ask for the actual identifiers and get hand-waving.
Best-for-me is the model that makes *those* mistakes least. No public benchmark measures it, because it's different for every person.

The labs are the obvious people to build a personalized cross-model benchmark. And the exact people who can't.
"Claude wins your refactors, GPT wins your compression" is a scorecard each of them could only ever publish the flattering half of. It has to come from outside all three.

## My corrections are the answer key

When a model answers and I rewrite it in my next message, I've told you which was better. Not in theory. With my own hands.
That rewrite is a human-labeled preference. I privileged my version over the model's. A few dozen of those and you have a graded test where the answer key is *real human judgment, not a model's opinion of "good."*

That's the whole game. Almost every "LLM benchmark" is a model grading a model. Turtles all the way down. Mine is anchored to a human.
The eval reads my transcripts, finds the moments I rewrote an answer, classifies what I was reaching for (shorter? a different frame? more precision?), and turns each into a scored question. Given this prompt, does this model avoid the mistake I had to fix?

First run: one model won my compression-heavy prompts, a different one won my "give me the actual numbers" prompts.
There is no single best model. There's a best model for each kind of thing I do.

## Making the judge impossible to argue with

Scoring needs a judge, and a judge is a model. The objection writes itself. *You used a model to grade models, of course it's biased.*
So I don't assert the judge is fair. I measure it.

I already have a pile of cases where I told you which answer I preferred. My own rewrites.
I hand the judge those exact pairs, randomized A/B so it can't game position, and check how often it picks the side *I* actually chose. That's a number. This judge agrees with my own corrections N% of the time.
I run it for every candidate and the most-aligned one gets the job. Chosen by measurement, not by my preference. One fixed judge, so every model is scored the same way.

Then the smaller holes, closed one by one. Because a benchmark dies by a thousand "well, actually"s:

- **Every score carries its own error bar.** `0.79 ± 0.04, n = 23`. Below a sample floor it's labeled *preliminary*. A number that admits its uncertainty is more trustworthy, not less.
- **A failed judgment is "no score," never a sneaky 0.5** dragging the average to the middle.
- **Trivially-passable questions get dropped.** And the drop is shown, not hidden.
- **The method is open source.** It's adversarial-proof *because* you don't have to trust me. You can read every gate.

None of this is me freelancing. Learning taste from your edits is a NeurIPS 2024 paper ([PRELUDE](https://arxiv.org/abs/2404.15269)). It even lands on the same interpretable-text representation I call the lens.
The judge's failure modes, position, length, self-preference, are the exact ones the [LLM-as-judge surveys](https://arxiv.org/pdf/2410.02736) catalogue, and I close each one.
The fact that the signal goes thin at honest sample size is itself a [known result](https://arxiv.org/html/2507.23158v2) about implicit feedback. Which is why the judge refuses to crown a winner instead of pretending.
The idea is settled science. What's new is running it across three labs, on your own machine, and declining to overclaim.

## And it never leaves my laptop

The card is built to share. "Which model wins my work" is fun to post. The card has scores, model names, axes, sample size. Nothing else.
The raw prompts, the responses, my corrections, those never appear in anything that leaves the machine. They live in a local file I can audit and you can't.
The transparency is in the *method*, which is fully open. The data is yours.

So I can't tell you which AI is best. Nobody honestly can. But I can tell you which is best for *me*, backed by the only answer key that was ever going to be right. My own work, graded by a judge I measured, on a machine nobody else can see.
The method is sitting right there for you to run on yours.

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```
