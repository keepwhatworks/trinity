"""The shared front-end for the provider-side import loops (lens-import +
eval-import). Both loops duplicated this input handling byte-for-byte; pin the
unified `read_provider_import` so a shape-guard fix stays in one place.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from trinity_local.commands.provider_import import read_provider_import


def _args(**kw):
    base = dict(from_json=False, path=None, provider=None, as_json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _stdin(monkeypatch, text):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_success_returns_payload_and_source_provider(monkeypatch):
    _stdin(monkeypatch, json.dumps({"source_provider": "Claude", "tensions": [1]}))
    out = read_provider_import(_args(from_json=True), list_fields=("tensions",))
    assert out == ({"source_provider": "Claude", "tensions": [1]}, "claude")  # lowercased


def test_cli_provider_overrides_payload(monkeypatch):
    _stdin(monkeypatch, json.dumps({"source_provider": "claude"}))
    payload, sp = read_provider_import(_args(from_json=True, provider="codex"), list_fields=())
    assert sp == "codex"


def test_missing_source_provider_defaults_to_unknown(monkeypatch):
    _stdin(monkeypatch, json.dumps({"tensions": []}))
    _, sp = read_provider_import(_args(from_json=True), list_fields=("tensions",))
    assert sp == "unknown"


def test_non_string_source_provider_does_not_crash(monkeypatch):
    _stdin(monkeypatch, json.dumps({"source_provider": 42}))
    _, sp = read_provider_import(_args(from_json=True), list_fields=())
    assert sp == "unknown"


def test_no_input_is_exit_2():
    assert read_provider_import(_args(), list_fields=()) == 2


def test_missing_file_is_exit_1(tmp_path):
    assert read_provider_import(_args(path=str(tmp_path / "nope.json")), list_fields=()) == 1


def test_bad_json_is_exit_2(monkeypatch):
    _stdin(monkeypatch, "{not json")
    assert read_provider_import(_args(from_json=True), list_fields=()) == 2


def test_non_dict_top_level_is_exit_2(monkeypatch):
    _stdin(monkeypatch, json.dumps([1, 2, 3]))
    assert read_provider_import(_args(from_json=True), list_fields=()) == 2


def test_present_but_wrong_type_list_field_fails_loud(monkeypatch, capsys):
    # The silent-data-loss guard: a JSON string where an array was expected must
    # NOT be coerced to [] — both loops depend on this.
    _stdin(monkeypatch, json.dumps({"rejections": "oops a string"}))
    assert read_provider_import(_args(from_json=True), list_fields=("rejections",)) == 2
    assert "must be a list" in capsys.readouterr().err


def test_wrong_type_list_field_as_json(monkeypatch, capsys):
    _stdin(monkeypatch, json.dumps({"tensions": 5}))
    assert read_provider_import(_args(from_json=True, as_json=True), list_fields=("tensions",)) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "must be a list" in out["error"]


def test_absent_list_field_is_fine(monkeypatch):
    _stdin(monkeypatch, json.dumps({"source_provider": "claude"}))
    out = read_provider_import(_args(from_json=True), list_fields=("tensions", "orderings"))
    assert isinstance(out, tuple)  # success — absent fields are legitimately empty


# --- print_provider_prompt: `lens-prompt` and `eval-prompt` share it --------------

def test_prompt_prints_only_the_body_after_the_marker(tmp_path, monkeypatch, capsys):
    from trinity_local.commands.provider_import import PROMPT_BODY_MARKER, print_provider_prompt

    doc = tmp_path / "p.md"
    doc.write_text(f"# Title\nintro\n{PROMPT_BODY_MARKER}\n\nLook back\n", encoding="utf-8")
    monkeypatch.setenv("TRINITY_TEST_PROMPT_DOC", str(doc))
    assert print_provider_prompt("p.md", "TRINITY_TEST_PROMPT_DOC", with_instructions=False) == 0
    assert capsys.readouterr().out == "Look back\n"


def test_prompt_without_marker_prints_whole_doc_and_warns(tmp_path, monkeypatch, capsys):
    from trinity_local.commands.provider_import import print_provider_prompt

    doc = tmp_path / "p.md"
    doc.write_text("# Title\nno marker here\n", encoding="utf-8")
    monkeypatch.setenv("TRINITY_TEST_PROMPT_DOC", str(doc))
    assert print_provider_prompt("p.md", "TRINITY_TEST_PROMPT_DOC", with_instructions=False) == 0
    out = capsys.readouterr()
    assert out.out == "# Title\nno marker here\n"
    assert "marker not found" in out.err and "p.md" in out.err


def test_prompt_override_to_a_missing_file_exits_1(tmp_path, monkeypatch, capsys):
    from trinity_local.commands.provider_import import print_provider_prompt

    monkeypatch.setenv("TRINITY_TEST_PROMPT_DOC", str(tmp_path / "absent.md"))
    assert print_provider_prompt("p.md", "TRINITY_TEST_PROMPT_DOC", with_instructions=False) == 1
    err = capsys.readouterr().err
    assert "p.md" in err and "TRINITY_TEST_PROMPT_DOC" in err
