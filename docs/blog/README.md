---
class: aspirational
---

# Trinity — field notes

First-person, shareable write-ups of what Trinity is and why it exists. Built to be
posted (dev.to / Hashnode / Reddit / your own blog) — each is self-contained and
true to what ships. The narrative across all three is the same one the
[README](../../README.md) and [website](https://keepwhatworks.com) lead with:
*own your taste; the cross-provider layer only an outsider can build.*

| Post | Angle | The one line |
|---|---|---|
| [I asked three AIs at once](i-asked-three-ais-at-once.md) | The council — the action | Ask Claude, GPT, and Gemini in parallel; a fourth model hands you the answer you'd have picked. |
| [I benchmarked the models on my own corrections](i-benchmarked-the-models-on-my-own-corrections.md) | The eval moat — the proof | The labs can't say which competitor wins; this scores them on the answers *you* rewrote, with a judge measured against your own corrections. |
| [Your AI chats are training data](your-ai-chats-are-training-data.md) | Data sovereignty — the why | They get a model of you for free; this keeps it local and makes it yours. |

**To publish:** see [SUBMISSION.md](SUBMISSION.md) — each post carries import-ready
front-matter (title / description / tags / canonical_url / cover_image) and the guide
maps the high-traffic targets (dev.to, Hashnode, Hacker News, Reddit, Medium, …) +
the one `canonical_url` rule that protects your SEO when you cross-post.

**Voice:** each article was rewritten in the founder's voice by running it through a
real Trinity council against the live lens (the dogfood — `council_94dfe5466e3e3b9f`
chose Claude's terse rewrite for the council piece; `council_85247df76a7d46d2` chose
GPT's for the eval piece; the sovereignty piece applies the same validated voice).

**House rules for these (so they stay credible):**
- First person, honest to what actually ships — no overclaims. Cursor is an
  install target (Trinity runs inside it as an MCP server), not yet an ingest
  source. Install is the one-line `curl` script (pre-PyPI).
- Never include a real prompt, transcript, or lens excerpt — the whole product
  promise is that your data stays on your machine, and that applies to the
  marketing too. Show the mechanism and the numbers, never the data.
