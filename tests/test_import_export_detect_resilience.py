"""Guard: import-export auto-detect must survive a non-UTF8 file in the walk.

`trinity-local import-export <dir>` walks a user-supplied export directory and
probes every file to auto-detect ChatGPT / Claude.ai / Gemini-Takeout. The probe
`_detect_conversations_json` read the head with `encoding="utf-8"` under
`except OSError` — but a non-UTF8 file NAMED conversations.json (a coincidental
name, a corrupt export) raises `UnicodeDecodeError` (a `ValueError`, NOT an
`OSError`), which crashed the whole `detect_exports` walk on it. Found 2026-06-02
(write-path green-gate sweep — the same guard_shape_not_just_parse class as the
ingest parsers). Reproduced empirically. A single un-decodable file must be
skipped, not abort the import. (The parsers downstream already degrade malformed
JSON to 0 sessions — verified — so only the detect probe was unguarded.)
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from trinity_local.commands.import_export import (
    _detect_conversations_json,
    detect_exports,
)


def test_detect_conversations_json_returns_none_on_non_utf8():
    d = Path(tempfile.mkdtemp())
    f = d / "conversations.json"
    f.write_bytes(b"\xff\xfe[{not even close to utf-8 \x80\x81")
    assert _detect_conversations_json(f) is None  # skip, not raise


def test_detect_exports_survives_non_utf8_among_valid():
    """One un-decodable conversations.json must not abort the whole auto-detect —
    the valid export in the same tree is still found."""
    d = Path(tempfile.mkdtemp())
    (d / "conversations.json").write_bytes(b"\xff\xfe[{\x80" * 8)  # non-UTF8
    sub = d / "chatgpt_export"
    sub.mkdir()
    (sub / "conversations.json").write_text('[{"mapping": {"x": 1}}]')  # valid chatgpt
    detected = detect_exports(d)  # must NOT raise
    assert any(x["source"] == "chatgpt" for x in detected)
