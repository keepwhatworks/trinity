---
class: aspirational
---

# Trinity field notes

First-person, shareable write-ups of what Trinity is and why it exists. Built to be
posted (dev.to / Hashnode / Reddit / your own blog). Each is self-contained and
true to what ships. The narrative across all three is the same one the
[README](../../README.md) and [website](https://keepwhatworks.com) lead with:
*ask all three, keep what works. The cross-provider layer only an outsider can build.*

| Post | Angle | The one line |
|---|---|---|
| [I asked three AIs at once](i-asked-three-ais-at-once.md) | The council. The action | Ask Claude, GPT, and Gemini in parallel. A chairman shows you where they split and which one to trust. |
| [I kept score on the disagreements my own work settled](i-benchmarked-the-models-on-my-own-corrections.md) | The disagreement ledger. The proof. | The labs can't say which competitor to trust. This keeps score on model disagreements, settled by what you actually did next. Includes the death of the judge-scored version. |
| [Your AI chats are training data](your-ai-chats-are-training-data.md) | Data sovereignty. The why | They get a model of you for free. This keeps it local and makes it yours. |

**To publish:** see [SUBMISSION.md](SUBMISSION.md). Each post carries import-ready
front-matter (title / description / tags / canonical_url / cover_image) and the guide
maps the high-traffic targets (dev.to, Hashnode, Hacker News, Reddit, Medium, …) +
the one `canonical_url` rule that protects your SEO when you cross-post.

**Voice:** each article was rewritten in the founder's voice by running it through a
real Trinity council against the live lens (the dogfood: `council_94dfe5466e3e3b9f`
chose Claude's terse rewrite for the council piece, `council_85247df76a7d46d2` chose
GPT's for the eval piece, and the sovereignty piece applies the same validated voice).

**House rules for these (so they stay credible):**
- First person, honest to what actually ships, no overclaims. Cursor is an
  install target (Trinity runs inside it as an MCP server), not yet an ingest
  source. Install is the one-line `curl` script (pre-PyPI).
- Never include a real prompt, transcript, or lens excerpt. The whole product
  promise is that your data stays on your machine, and that applies to the
  marketing too. Show the mechanism and the numbers, never the data.
