---
description: Put a plan in front of Claude, Codex and Gemini before you commit to it.
argument-hint: <the plan or decision, with what you know>
---

Call the `mcp__trinity-local__run_council` tool on this:

$ARGUMENTS

Send the goal, the plan, the evidence you have, and one alternative. Then report
the council's `decision`, what would change it (`flip_condition`), what all three
agreed on, and each split: who was on which side, which side survived, and the
`check` that would settle it later. If the argument is empty, ask what decision
to put to the council first.
