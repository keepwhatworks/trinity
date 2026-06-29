---
class: aspirational
title: "I run my hardest questions through Claude, GPT, and Gemini at once. Then one of them picks the answer I'd have picked."
published: true
description: "I pay for three AI subscriptions and never knew which to trust. So I built a tool that asks all three at once. Then one of them reads all three and picks the answer I'd have chosen. Local, free, no API key."
tags: ai, opensource, productivity, privacy
canonical_url: https://github.com/keepwhatworks/trinity/blob/main/docs/blog/i-asked-three-ais-at-once.md
cover_image: ""
voice_pass: council_94dfe5466e3e3b9f (chairman=claude, your lens)
---

# I run my hardest questions through Claude, GPT, and Gemini at once. Then one of them picks the answer I'd have picked.

I pay for three frontier subscriptions. Claude, ChatGPT, Gemini.
For the questions that matter, the architecture call, the security trade-off, rewrite-or-patch, I'd ask all three. Then sit there comparing three half-answers, guessing which to trust.
Most expensive way to stay uncertain I've found.

So I built the asking, comparing, and deciding into one tool.
It lives inside the CLIs I already use. It runs on the subscriptions I already pay for. And it gets more like *me* the more I use it.
It's called **Trinity**. It's free, open source, and my transcripts never leave the machine.

Stop copy-pasting prompts between tabs like an animal. Ask once.

## Three tabs, no way to know who's right

Nobody can tell you which model to believe. And it's structural, not an accident.
Anthropic won't recommend ChatGPT. OpenAI won't tell you Gemini won. Google can't point at either.
**The labs are commercially barred from comparing themselves to each other.** So the one comparison I actually want, for my work, which model is right, is the one no vendor is allowed to give me.

So I did it by hand. Paste into Claude, ChatGPT, Gemini. Read three answers. Notice two agree and one's in the weeds. Average them in my head. Pick. Move on, vaguely unsure.

The signal was sitting in those three answers. I just had no layer holding them side by side and telling me where they split.

## So I built a council

The idea is small. Ask all three at once, then have a chairman synthesize.
A run is a *council*. Three models answer in parallel, the *members*. Then one of them steps up as *chairman*, reads all three, and returns one verdict. What they agreed on. Where they disagreed and why it matters. The answer.

The parts I care about are the parts it refuses to do:

- **No API key.** Trinity dispatches through the CLIs I'm already logged into. `claude`, `codex`, `agy`. Every council rides my flat-rate subscriptions, so the marginal cost is $0.
- **No server.** It installs as an MCP server inside the tools I already work in. Claude Code, Codex CLI, Antigravity, Cursor.
- **No phone-home.** One curl line, MIT-licensed, reads and writes only my own disk.

First real council I ran was a database-migration question. Back came: all three agree the online migration is safe. They split on whether to batch the backfill. And here's why that split matters for your lock contention.
I stopped tab-switching that day. One decision, with the disagreement surfaced instead of buried.

One honesty note, since people ask. Cursor is an **install target**, not an ingest source yet. Trinity runs as an MCP server inside Cursor, but Cursor keeps its chats in SQLite, not the JSONL the rest read. So it's not in the corpus until I ship a reader for it. I'd rather say that than fake the checkbox.

## Then it started learning my taste

Every time a model answers and I rewrite it, cut the lecture, ask for a spec instead of a story, push for the actual numbers, I'm leaving a signal and then throwing it away.
Trinity reads the transcripts already on my machine. Claude Code, Codex, Antigravity, plus my web chats on claude.ai, chatgpt.com, Gemini. It mines exactly those moments.
It builds a *lens*, a small model of how I think, and hands it to the chairman on every council.

So the chairman doesn't pick the universally "best" answer. It picks the one that fits me. It enforces my constraints.

It also turns those rewrites into a benchmark. Which model wins the questions I actually corrected? Not HumanEval. My corrections. Only I can see that signal, because only I have the cross-provider rejection history.

The discipline that makes this safe: **no LLM call happens outside a council.**
Ingest, embedding, lens-building, ranking, all local heuristics and embeddings, zero model calls. My prompts are never uploaded. The only things that ever leave the machine are the council member calls themselves, riding my own subscriptions.

## What no single lab can build

Any one lab could ship a smarter council tomorrow. None of them can read your transcripts across all three providers and tell you, neutrally, which one wins your work. The moment they do, they're recommending a competitor.
The cross-provider view has to come from outside all three.

Turned out that someone was me. On a weekend, with a curl command and the subscriptions I was already paying for.

I still pay for three. I just don't tab-switch anymore. Ask once, all three answer, and what comes back is the answer I'd have picked.

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```
