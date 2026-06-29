---
class: aspirational
---

# Where to submit these (and how)

Each article in this folder is a self-contained Markdown post with **import-ready
front-matter** (`title`, `description`, `tags`, `canonical_url`, `cover_image`).
Paste the file into any of the platforms below and it lands formatted. Goal: hit
places that already have the foot traffic so you're not building an audience from
zero.

## The one rule that protects your SEO: pick ONE home, set `canonical_url` everywhere else

Cross-posting the same text to five sites *without* a canonical URL splits your
Google rank five ways and can get you flagged as duplicate. Fix: choose a single
**home** for each post, publish it there first, then syndicate everywhere else with
`canonical_url` pointing back at the home. Every article here already has a
`canonical_url` line — change it to wherever you decide the home is (your own site,
or the dev.to post once it's live). Default is the GitHub copy, which is a fine,
stable home if you don't have a blog yet.

## High-traffic targets, by effort

| Platform | Traffic | How to post | Notes |
|---|---|---|---|
| **dev.to** | Huge dev audience, great SEO | Dashboard → "Create Post" → paste the `.md` *with* its front-matter | Reads `title`/`tags`/`canonical_url`/`cover_image` directly. `tags` max 4, already set. Set `published: true` when ready. Best default home. |
| **Hashnode** | Large, SEO-strong | New post → import Markdown; map canonical in settings | Free, custom domain support; pairs well with dev.to syndication. |
| **Hacker News** | Spiky, enormous when it hits | Submit the *link* to the live post (not the text) | Title matters more than anywhere. Lead with the contrarian one ("Three AI labs can't tell you which is best"). Post 8–10am ET weekday. Don't editorialize the title. |
| **Reddit** | Targeted, native fit | Submit link + a short honest first comment | r/LocalLLaMA (privacy/local angle), r/ClaudeAI, r/OpenAI, r/programming (be careful — anti-promo), r/SideProject. Read each sub's self-promo rules first. |
| **Lobsters** | Small but high-signal devs | Link submission (needs an invite) | `ai`/`privacy` tags. Quality-sensitive crowd; the eval-rigor post fits best. |
| **Medium** | Broad, SEO via Google | Import via URL (Stories → Import a story) keeps canonical | Use Import (not paste) so canonical is preserved automatically. |
| **HackerNoon / freeCodeCamp** | Editorial reach | Submit via their contributor flow | Slower (editor review) but big distribution if accepted. |
| **Indie Hackers** | Founder audience | Post the build story + link | The "I built it on a weekend" framing is native here. |

## Which post leads where

- **Hacker News / Lobsters →** [Three AI labs can't tell you which one is best](i-benchmarked-the-models-on-my-own-corrections.md). The contrarian, rigor-heavy one survives a skeptical crowd best.
- **dev.to / Hashnode / Medium →** [I asked three AIs at once](i-asked-three-ais-at-once.md). The clearest "I built X" story; widest appeal.
- **r/LocalLLaMA / privacy crowds →** [Your AI chats are training data](your-ai-chats-are-training-data.md). The sovereignty angle is native there.

## Per-post launch checklist

1. Set `canonical_url` to your chosen home (or leave the GitHub default).
2. Add a `cover_image` (1200×630 PNG — Trinity already renders share cards; reuse one).
3. Flip `published: false → true` on the home platform only.
4. Syndicate to the rest with `canonical_url` pointing at the home.
5. Submit the *live link* to HN / Reddit / Lobsters with a tight, non-clickbait title.
6. Reply to the first comments fast — early engagement is what ranks these.

## House rule (don't break it)

These are honest to what ships and contain **no raw prompts, transcripts, or lens
excerpts** — the product promise is that your data stays on your machine, and that
applies to the marketing too. If you edit, keep it that way: show the mechanism and
the numbers, never the data.
