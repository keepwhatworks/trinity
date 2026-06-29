---
class: aspirational
title: "Your AI chats are someone else's training data. I built a local layer that keeps mine, and turns them into a model of my taste."
published: true
description: "Every correction you make teaches a model what you want, and you give that signal away. I built a local layer that reads the same chats on your own machine and builds a model of your taste that you own. Nothing uploads."
tags: ai, privacy, opensource, productivity
canonical_url: https://github.com/keepwhatworks/trinity/blob/main/docs/blog/your-ai-chats-are-training-data.md
cover_image: ""
voice_pass: applied from council_94dfe5466e3e3b9f + council_85247df76a7d46d2 (your lens; 3rd council hit a transient 500)
---

# Your AI chats are someone else's training data. I built a local layer that keeps mine, and turns them into a model of my taste.

I've sent thousands of messages to Claude, ChatGPT, and Gemini. Every correction, every "no, shorter," every "give me the spec not the story" is a map of how I think and decide.
It lives on three companies' servers, as a model of me I don't own and can't see.

So I built the local version. It reads those same conversations on my own machine. And instead of shipping a model of me to a lab, it builds one *for* me that stays here.
It's called **Trinity**, it's free and open source, and the entire point is that nothing leaves your laptop.

## They get a model of you, for free

Look at what you hand a frontier model in a week. Not just questions. *Judgments.*
You reject its first answer and rewrite it. You push back on the framing. You ask for precision it didn't give. Each one is you teaching it what you want, with real stakes.

That signal is worth more than the prompts. Prompts are transient. The pattern of how you correct, rephrase, and decide is durable. It's the closest thing there is to a model of your taste.
You produce it constantly and give it away to whichever tab is open.

I didn't want to stop using the models. I wanted to stop being the only one in the relationship who doesn't keep the data.

## So I built the local version

The constraint was absolute. Nothing uploads.

Trinity reads the transcripts already on your disk. Claude Code, Codex, and Antigravity write session logs locally, and a small browser extension captures your claude.ai, chatgpt.com, and Gemini chats *to your own machine* as you use them. No server, no account, no API key.

Then it does what the labs do, except the output is yours. It mines the corrections, the moments you rewrote, redirected, rejected, and builds a *lens*. A small, readable model of how you think.
Every piece runs on your hardware. The embedding model downloads once and never phones home. There are no LLM calls anywhere except the ones you explicitly trigger, and those ride your own subscriptions.
The lens is derived from your data, on your machine, and it stays there.

## What the lens actually knows

The first time I read my own lens back it was unsettling, in a good way. Like seeing my handwriting described. It had noticed things I'd never have written down.
That I take a working mechanism over an elegant abstraction almost every time. That I ask for the concrete identifier the second an answer gets vague. That my "make it shorter" is really "lead with the action, demote the explanation."

None of that came from a survey. It came from how I'd actually behaved across thousands of turns.
The labs see all of it too. The difference: my copy is mine. A file I can open, audit, edit, delete. And because it's mine, it's portable. When the next frontier model ships, my lens still describes me.

## The model of you should be yours

The industry got this backwards. Every AI product assumes the personalization layer, the memory, the model of your preferences, belongs to the company whose model you're talking to.
So you get a different, siloed, invisible "you" inside each provider, none of which you control.

It doesn't have to be that way. The data is already on your machine, or capturable to it. The compute to distill it is cheap and local.
The only thing missing was someone outside the labs building the layer. Because the labs have every incentive to keep that model of you on their side of the wire.

So I built it on my side. My transcripts never leave my machine. The model of my taste is a file I own.
And the models I pay for are finally the thing I rent. Not the thing that quietly owns the most valuable artifact in the whole exchange.

```bash
curl -fsSL https://raw.githubusercontent.com/keepwhatworks/trinity/main/scripts/install.sh | bash
```
