"""End-to-end privacy guard: the REAL `import-export` CLI must not write a ChatGPT
hidden-node's PII to the on-disk prompt corpus.

`test_ingest_chatgpt_hidden_node_not_prompt` pins the fix at PARSE time
(`parse_chatgpt_export` → `_is_user_facing_prompt`). But the privacy guarantee that
actually matters is about what gets *written to disk* — the `prompts/prompt_nodes.jsonl`
corpus every downstream surface reads (memory viewer, share cards, lens-build). A user
imports a ChatGPT export by running `trinity-local import-export <path>`, which is a
DIFFERENT code path: `handle_import_export` → `detect_exports` → `_parse_for_source`
→ `stage_session` → `flush_chunk` → on-disk write. The parser test exercises none of
that staging/indexing layer. A future regression there — a fast-path that re-derives
prompts from `session.messages` including dropped nodes, a staging change that stops
honoring the parser's drop, or a revert of the parser skip itself — would pass the
parser unit test while writing the user's custom-instructions PII (name, address,
profession) straight into the corpus. That is the "privacy nightmare" class the
product guards hardest, so it deserves a guard at the surface a user actually runs.

This builds a faithful ChatGPT `conversations.json` with two hidden role=user nodes
(an `is_user_system_message` custom-instructions node and an
`is_visually_hidden_from_conversation` memory node, both carrying distinct PII
markers) interleaved with two genuinely-typed prompts, runs the real
`handle_import_export` into an isolated TRINITY_HOME, then reads the prompt corpus
BACK OFF DISK and asserts: exactly the two typed prompts were indexed, and neither
PII marker appears anywhere in the written corpus.

Mutation-proven: revert the `is_visually_hidden_from_conversation` /
`is_user_system_message` skip in `_chatgpt_conversation_dict_to_session` → both
hidden nodes index → the PII-absence assertion reds. (Verified by hand against the
real CLI 2026-06-09: import wrote 1 prompt, the typed one; no PII on disk or in any
rendered surface.)

Runs under the TF-IDF embedding fallback (no `[mlx]` needed) — two prompts embed
instantly and the backend choice is irrelevant to WHICH texts get indexed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from trinity_local.commands.import_export import handle_import_export

# Distinct markers so a leak is unambiguous and grep-able.
_PII_SYS = "SYSPII the user is Jordan Vasquez of 4417 Pinecrest Ave, a tax attorney"
_PII_MEM = "MEMPII user memory: SSN ends 8842, spouse Riley, prefers terse replies"
_TYPED_1 = "How do I structure a recursive descent parser for arithmetic expressions?"
_TYPED_2 = "Now how would I add operator precedence climbing to it?"
_ASSISTANT = "ASSISTREPLY you write one function per precedence level"


def _node(nid, role, text, parent, children, *, hidden=False, user_system=False):
    meta: dict = {}
    if hidden:
        meta["is_visually_hidden_from_conversation"] = True
    if user_system:
        meta["is_user_system_message"] = True
    return nid, {
        "id": nid, "parent": parent, "children": children,
        "message": {
            "id": nid, "author": {"role": role}, "create_time": 1718000000.0,
            "content": {"content_type": "text", "parts": [text]}, "metadata": meta,
        },
    }


def _write_export(path: Path) -> None:
    # root → [hidden custom-instructions] → typed1 → assistant → [hidden memory] → typed2
    mapping = dict([
        _node("root", "system", "", None, ["nsys"]),
        _node("nsys", "user", _PII_SYS, "root", ["nu1"], user_system=True),
        _node("nu1", "user", _TYPED_1, "nsys", ["nast"]),
        _node("nast", "assistant", _ASSISTANT, "nu1", ["nvis"]),
        _node("nvis", "user", _PII_MEM, "nast", ["nu2"], hidden=True),
        _node("nu2", "user", _TYPED_2, "nvis", []),
    ])
    conv = {
        "conversation_id": "conv-e2e-pii", "title": "Parser design",
        "create_time": 1718000000.0, "update_time": 1718000100.0,
        "current_node": "nu2", "default_model_slug": "gpt-5.5", "mapping": mapping,
    }
    path.write_text(json.dumps([conv]), encoding="utf-8")


def _args(path: Path) -> argparse.Namespace:
    # Mirror the register() defaults so this is the exact arg shape the CLI builds.
    return argparse.Namespace(
        path=str(path), path_flag=None, source=None, dry_run=False,
        limit=None, batch_size=64, dim=768, progress=False,
    )


def test_import_export_never_writes_chatgpt_hidden_pii_to_corpus(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    export_dir = tmp_path / "export"
    export_dir.mkdir()
    _write_export(export_dir / "conversations.json")

    # Run the REAL command path: detect → parse → stage → flush → on-disk write.
    handle_import_export(_args(export_dir))
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True, f"import-export failed: {out}"
    assert out["totals"]["prompts_indexed"] == 2, (
        f"expected exactly the 2 typed prompts indexed (the 2 hidden PII nodes "
        f"dropped) — a 4 would mean the hidden nodes leaked in; "
        f"got {out['totals']['prompts_indexed']}"
    )

    # Read the corpus BACK OFF DISK — the artifact every surface consumes.
    from trinity_local.memory.store import iter_prompt_nodes_no_embedding

    texts = [n.text for n in iter_prompt_nodes_no_embedding(limit=None)]
    assert sorted(texts) == sorted([_TYPED_1, _TYPED_2]), (
        f"the written corpus is not exactly the two typed prompts: {texts!r}"
    )
    blob = "\n".join(texts)
    for marker, what in ((_PII_SYS, "custom-instructions"), (_PII_MEM, "memory")):
        assert marker not in blob, (
            f"ChatGPT {what} PII reached the on-disk prompt corpus via the real "
            f"import-export CLI ({marker!r}) — a personal-data leak + lens poison "
            "at the layer the parser unit test doesn't cover"
        )
    assert "ASSISTREPLY" not in blob, "assistant text leaked into the corpus"

    # Belt-and-suspenders: the PII must not be anywhere under the home, not just the
    # prompt corpus — no scoreboard / index / cursor file echoed it either.
    home = tmp_path / "trinity"
    for f in home.rglob("*"):
        if f.is_file():
            try:
                body = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            assert "SYSPII" not in body and "MEMPII" not in body, (
                f"hidden-node PII leaked into {f.relative_to(home)}"
            )
