"""Guard: the provider-import handlers must not crash on wrong-shape provider JSON.

`lens-import` / `eval-import` parse USER-PASTED provider JSON (the provider-side
loop — a user copies a model's response and pipes it in). Untrusted shape. The
handlers already guarded the common malformed cases (invalid JSON →
JSONDecodeError, non-object top level → isinstance(payload, dict), per-item
isinstance(dict)), but missed two type-siblings found 2026-06-02 (write-path
green-gate sweep):

  - a non-string `source_provider` (a number/list) → `.strip()` AttributeError;
  - a non-list `rejections`/`tensions`/`orderings` (a bare number) → `for`
    TypeError ('int' object is not iterable).

Both crashed the import. Fixed by coercing `source_provider` to str-or-default and
guarding the lists (guard_shape_not_just_parse). A paste-gone-wrong must degrade
to "0 imported", never a stack trace.
"""
from __future__ import annotations

import io
import json
import sys
import types

import pytest

from trinity_local.commands.eval_import import handle_eval_import
from trinity_local.commands.lens_import import handle_lens_import


def _run(handler, payload: dict, monkeypatch) -> int:
    args = types.SimpleNamespace(
        from_json=True, path=None, provider=None, dry_run=True, as_json=False,
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return handler(args)  # must NOT raise


@pytest.mark.usefixtures("patch_trinity_home")
@pytest.mark.parametrize("payload", [
    {"source_provider": 5, "rejections": []},        # non-string provider
    {"source_provider": [1, 2], "rejections": []},   # non-string provider
    {"rejections": 5},                               # non-list rejections (not iterable)
    {"rejections": {"a": 1}},                         # wrong-type rejections
    {},                                              # empty object
])
def test_eval_import_survives_wrong_shape(payload, monkeypatch):
    rc = _run(handle_eval_import, payload, monkeypatch)
    assert rc in (0, 2)  # graceful, not a crash


@pytest.mark.usefixtures("patch_trinity_home")
@pytest.mark.parametrize("payload", [
    {"source_provider": 5, "tensions": []},
    {"source_provider": {"x": 1}, "tensions": []},
    {"tensions": 5},                                 # non-list, not iterable
    {"orderings": 7, "tensions": []},
    {},
])
def test_lens_import_survives_wrong_shape(payload, monkeypatch):
    rc = _run(handle_lens_import, payload, monkeypatch)
    assert rc in (0, 2)


@pytest.mark.usefixtures("patch_trinity_home")
def test_eval_import_still_imports_valid_payload(monkeypatch):
    """The guards must not break the happy path."""
    payload = {"source_provider": "claude", "rejections": [{
        "type": "REFRAME", "model_quote": "x", "user_substitute": "y",
        "why_signal": "z", "confidence": "high",
    }]}
    rc = _run(handle_eval_import, payload, monkeypatch)
    assert rc == 0
