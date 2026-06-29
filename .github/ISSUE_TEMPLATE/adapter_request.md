---
name: Adapter request
about: Trinity should ingest from a harness/source it doesn't yet
title: "[adapter] "
labels: adapter, enhancement
---

## Which source

<!-- Cursor sessions / Windsurf / Aider / Cline / OpenCode / claude.ai
export / chatgpt export / Gemini takeout / something else. Link to the
on-disk path or export format if you can. -->

## Format

- File extension(s):
- Storage location on disk (the canonical `~/.harness-name/...` path):
- Roughly how many turns per file:

## Sample (optional, anonymized)

<!-- A single redacted message is enough to characterize the shape. Don't
paste real transcripts; we don't need them. -->

```json

```

## Priority 1 reminder

Per `CONTRIBUTING.md`, adapters are priority 1 — the first PR shape
maintainers will engage with. If you can ship the adapter yourself,
even better: `parse_<source>_session()` in `src/trinity_local/ingest.py`
+ 2-3 tests in `tests/test_ingest.py` against the included fixture
shape. Look at `parse_gemini_takeout_html()` for reference.
