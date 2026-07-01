---
class: live
---

# Provider-side eval prompt

Paste this verbatim into a fresh chat with Claude, ChatGPT/Codex, or Gemini.
Each provider has the user's full conversation history on their side —
they can extract **rejection signals** (turn-pairs where you reframed,
redirected, sharpened, or compressed their suggestion) directly. Trinity
runs those signals as a personal eval suite: any model dispatched
against the suite earns a score based on how close its response gets
to "what you actually asked for instead."

Run weekly (or whenever you want fresh measurement signal). Save the
JSON output to a file, then import:

```bash
trinity-local eval-import --provider claude ./evals-from-claude.json
trinity-local eval-import --provider codex  ./evals-from-codex.json
trinity-local eval-import --provider gemini ./evals-from-gemini.json
```

Trinity merges into `~/.trinity/me/preference_acts.jsonl` (same file the
turn-pair-extraction pipeline writes). Existing `eval-build` /
`eval-run` / `eval-show` consume them unchanged — provider-imported
signals become eval items the next time you build a set.

How measurement works once the loop closes: score the same set against
this week's lens vs last week's. The lens is improving when the
aggregate score on YOUR rejection signal climbs. OpenAI's "eval skills"
pattern is the same shape — a skill (a lens, here) is evaluated against
the suite of cases it claims to handle.

---

## The prompt — copy below this line

Look back over my recent work with you from the last 30 days, or all
available history if shorter, and extract **rejection signals** —
specific moments where your suggestion didn't land and I asked for
something different.

Use evidence in this order:
- Recent conversation history and any session summaries
- Saved memories or persistent context you have about me
- The pattern of how I responded to your suggestions specifically: any
  turn where my next message reframed, redirected, sharpened, or
  compressed what you'd just said

For each rejection, classify it as exactly one of these four axes:

- **REFRAME** — you proposed framing A; I substituted framing B.
  ("Let me explain why X is hard" → "Just give me the SQL.")
- **REDIRECT** — you went down path A; I asked for path B.
  ("Here's how to debug this" → "Skip debugging, just rewrite the
  function.")
- **SHARPENING** — you gave a vague/hedged answer; I asked for precision.
  ("It depends" → "Pick one and defend it.")
- **COMPRESSION** — you gave a long answer; I asked for a shorter one,
  or made the substance you should have led with explicit.
  (300 words of caveats → "Yes or no.")

Only include rejections where:
- The substitution is unambiguous — I clearly didn't accept the original
- The axis is identifiable — if it's two axes blended, pick the
  dominant one
- The signal is non-trivial — not "I asked for a clarification"; the
  REJECTION shows a stable preference about HOW I want answers

Skip:
- Casual reformulations where I just rephrased for myself
- Cases where I accepted the answer but moved on to a follow-up
- Acknowledgment-only ("got it", "thanks") — not a rejection
- Sensitive personal content unless it's load-bearing for the signal

Aim for **10–25 rejection signals**. Quality over quantity. If you
genuinely have fewer, emit fewer — Trinity de-dupes against existing
items, so noise is worse than missing data.

## Output format

Emit STRICTLY this JSON. No prose around it. The shape matches
Trinity's `RejectionSignal` schema so `eval-import` ingests directly.

```json
{
  "source_provider": "claude | codex | gemini",
  "extracted_at": "<ISO 8601 UTC timestamp>",
  "horizon_window_days": 30,
  "rejections": [
    {
      "type": "REFRAME | REDIRECT | SHARPENING | COMPRESSION",
      "original_prompt": "<the question/request of MINE that you were answering — verbatim or near-verbatim>",
      "model_quote": "<verbatim or near-verbatim of what you said, 1–3 sentences>",
      "user_substitute": "<verbatim or near-verbatim of what I said back, 1–3 sentences>",
      "why_signal": "<one sentence: what preference of mine does this reveal>",
      "confidence": "high | medium | low"
    }
  ]
}
```

## Quality bar

- `original_prompt` is **required** — it's the turn-pair anchor that makes a
  rejection a *record of a correction I actually made*, not a preference you're
  asserting on my behalf. Trinity gates every imported rejection on it (the same
  provenance check every other ledger write must pass): supply MY actual request
  (not a paraphrase of the answer), and it enters the ledger AND the benchmark
  Trinity re-runs through each candidate model. If you omit it — or it just echoes
  my `user_substitute` — the rejection has no anchor and is **refused at import**,
  not written. A bare quote/substitute is an assertion, not my ground truth.
- `model_quote` and `user_substitute` should be short enough to read
  at a glance but specific enough that I can recognize them. If the
  original was longer, pull the load-bearing sentence.
- `why_signal` is the load-bearing field — it's what Trinity uses to
  score future responses against. Make it the underlying preference,
  not the surface ("user wanted concrete numbers, not ranges" beats
  "user asked again").
- `confidence` reflects how clean the rejection signal is. "high" means
  the substitution is obviously a different answer; "low" means it
  might be a follow-up rather than a rejection.

Output the JSON now. Nothing else.
