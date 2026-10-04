---
description: Check a change before calling it done - your tests first, the other labs as a second read.
argument-hint: "[optional: what the change is supposed to do]"
---

Call the `mcp__trinity-local__verify` tool on the change you just made.

1. `diff`: the unified diff of the change (`git diff`, or `git diff HEAD~1` if it is committed).
2. `criteria`: for each test that exercises the changed files, an item
   `{id, kind: "test", statement, command}` with the command this project already
   uses (pytest, npm test, cargo test...), never one you invented; plus the claim
   under test as `{id, kind: "judgment", statement}`.
3. `exclude_lab`: your own lab, so another lab reads your work.

Relay the result exactly. STOP: a relevant test is red; say which. READ: a person
still reads; name what is unverified. The other labs' read is a second opinion,
not approval. What the change is for: $ARGUMENTS
