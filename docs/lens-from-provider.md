---
class: live
---

# Provider-side lens prompt

Paste this verbatim into a fresh chat with Claude, ChatGPT/Codex, or Gemini.
Each provider has the user's full conversation history on their side — they
can synthesize a lens directly without Trinity needing to scrape transcripts.

Run it weekly (or on demand). Save the JSON output to a file, then import:

```bash
trinity-local lens-import --provider claude  ./lens-from-claude.json
trinity-local lens-import --provider codex   ./lens-from-codex.json
trinity-local lens-import --provider gemini  ./lens-from-gemini.json
```

Trinity merges the three provider perspectives into `~/.trinity/me/lenses.json`
— same schema the local lens-build pipeline emits, so all downstream consumers
(council chairman context, launchpad, share cards) work unchanged.

---

## The prompt — copy below this line

Look back over my recent work with you from the last 30 days, or all
available history if shorter, and identify **paired tensions** in how I
think and decide — places where I consistently privilege one legitimate
value over another legitimate competing value.

Use evidence in this order:
- Recent conversation history and any session summaries
- Saved memories or persistent context you have about me
- The pattern of how I responded to your suggestions: what I accepted,
  what I reframed, what I substituted, what I sharpened, what I deflected
- Any project-level context I've shared (CLAUDE.md, AGENTS.md, repo
  conventions, etc.)

A **paired tension** is NOT a preference between good and bad. It's a
genuine trade-off where:

- Both poles are legitimate competing values
- I consistently lean one way (the *privileged* pole)
- The opposite pole (the *sacrificed* pole) is a real cost — there exist
  domains where someone reasonable would choose it
- Each pole, taken too far, has a recognizable failure mode

Only emit a tension when:

- It recurs across **at least two distinct domains or contexts** (not one
  domain repeated)
- I can recognize specific evidence — quote a short phrase, name a
  decision, point to a moment
- The failure mode for both poles is non-trivial (not "X is bad, ~X is
  good")
- It would let a stranger reading the lens predict my answer in a domain
  you and I have never discussed

Skip:
- One-off preferences with no recurrence
- Pure aesthetic choices with no competing-value structure
- Anything that's actually situational vs values-driven
- Sensitive personal observations (health, finances, relationships)
  unless I've explicitly invited them as context

## Output format

Emit STRICTLY this JSON. No prose around it. No markdown fence inside the
object. The shape matches Trinity's `LensPair` schema so `lens-import`
can ingest it directly.

```json
{
  "source_provider": "claude | codex | gemini",
  "extracted_at": "<ISO 8601 UTC timestamp>",
  "horizon_window_days": 30,
  "tensions": [
    {
      "pole_a": "<short noun phrase, the value I consistently privilege>",
      "pole_b": "<short noun phrase, the legitimate value I give up>",
      "failure_a": "<what happens when pole_a is taken too far>",
      "failure_b": "<what happens when pole_b is taken too far>",
      "horizon": "tactical | strategic | philosophical",
      "evidence": [
        "<one specific moment / quote / decision from domain 1>",
        "<one specific moment / quote / decision from domain 2>"
      ],
      "confidence": "high | medium | low",
      "why_matters": "<one sentence: what changes when I get this trade-off wrong>"
    }
  ],
  "orderings": [
    {
      "pole_a": "<single-direction preference: A over B, no dual regret>",
      "pole_b": "<the dispreferred alternative>",
      "evidence": ["<example>", "<another from a different domain>"]
    }
  ]
}
```

## Horizon definitions

- **tactical** — response-shape / turn-scale preferences. "Be terse." "Show
  code first." "Confirm before destructive actions."
- **strategic** — quarter-scale trajectory choices. "Ship the MVP over
  polish." "Single load-bearing change over composite menu."
- **philosophical** — year-scale identity or framing. "Intelligence is
  infrastructure, not interface." "Getting it into people's hands beats
  making it elegant."

A tension can be tagged tactical OR strategic OR philosophical based on
the scale at which the trade-off plays out. Use philosophical sparingly —
most working preferences are tactical or strategic.

## Quality bar

- Aim for **3–7 tensions and 3–6 orderings**. Quality over quantity.
- Skip a slot rather than fill it with a weak tension.
- Confidence "high" should mean: "If shown this lens, the user would
  recognize the trade-off as theirs without prompting."
- Confidence "low" tensions are useful but flag them — Trinity weights
  high-confidence pairs more heavily in chairman synthesis.

Output the JSON now. Nothing else.
