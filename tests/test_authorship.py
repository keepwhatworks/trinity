"""The "only the founder" lens filter (me/authorship.py, amd_0310, res_162).

Pins each drop rule on a hand-built store, the two properties the founder's words depend on
(a later quote of the user is not a paste; the earliest copy of a text must still pass every
other rule), the gate's pass-through behaviour, and a structural ratchet: every lens-side
reader of the prompt store wraps its iterator in lens_only(), except a short allowlist of
reads that are not lens content.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from trinity_local.memory.schemas import PromptNode

LONG_MODEL_ANSWER = ("The streaming parser now reads each line lazily, keeps a bounded buffer of four "
                     "kilobytes, flushes on every newline boundary, and reports malformed rows to stderr "
                     "without stopping, so a single corrupt record can no longer abort an entire ingest run.")


def _n(i, tx, turn, text, ts, *, origin=None, before="", after="", provider="claude"):
    return PromptNode(id=f"p{i}", transcript_id=tx, provider=provider, source_path="", turn_index=turn,
                      text=text, embedding=[], created_at="2026-10-07T00:00:00+00:00", timestamp=ts,
                      preceding_assistant_text=before, following_assistant_text=after, origin=origin)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    nodes = [
        _n(1, "t1", 0, "please make the parser stream instead of loading the whole file", "2026-05-01T10:00:00Z",
           after=LONG_MODEL_ANSWER),
        _n(2, "t2", 0, "run the nightly eval and report the numbers back", "2026-05-01T11:00:00Z", origin="headless"),
        _n(3, "t3", 0, "Another Claude session sent a message: <teammate-message teammate_id=x>done</teammate-message>",
           "2026-05-01T12:00:00Z"),
        _n(4, "t4", 0, "review all md files for consistency and fix what drifted, then test", "2026-05-02T09:00:00Z"),
        _n(5, "t4", 1, "review all md files for consistency and fix what drifted, then test", "2026-05-02T10:00:00Z"),
        _n(6, "t5", 0, "please make the parser stream instead of loading the whole file", "2026-05-03T09:00:00Z"),
        _n(7, "t6", 0, "critique this before I send it: " + LONG_MODEL_ANSWER, "2026-05-04T09:00:00Z"),
        # the assistant quotes the user's earlier words back LATER: the user's prompt is not a paste
        _n(8, "t7", 0, "I want the compression to be solely of me, the directions I push, so that versions of me "
                        "can explore the world and bring back what surprised them; keep the lens honest about it",
           "2026-05-05T09:00:00Z"),
        _n(9, "t7", 1, "ok", "2026-05-05T10:00:00Z",
           before="You said: I want the compression to be solely of me, the directions I push, so that versions of me "
                  "can explore the world and bring back what surprised them; keep the lens honest about it"),
        # an earliest copy that is itself headless stays dropped as headless, and its later copy is a copy
        _n(10, "t8", 0, "summarise the overnight council results into a short status note", "2026-05-06T09:00:00Z",
           origin="headless"),
        _n(11, "t9", 0, "summarise the overnight council results into a short status note", "2026-05-06T10:00:00Z"),
    ]
    return nodes


def test_each_drop_rule(store):
    from trinity_local.me.authorship import classify
    dropped, own = classify(store)
    assert "p1" not in dropped                                       # the user's own request
    assert dropped["p2"] == "headless"
    assert dropped["p3"] == "machine"
    assert "p4" not in dropped and dropped["p5"] == "repeat"       # the first copy in a session stays
    assert dropped["p6"] == "copy"                                   # a later copy elsewhere
    assert dropped["p7"] == "paste" and own["p7"].startswith("critique this before I send it")
    assert "p8" not in dropped                                       # quoted back later is NOT a paste
    assert dropped["p10"] == "headless" and dropped["p11"] == "copy"


def test_a_one_word_framing_survives_the_paste(store):
    from trinity_local.me.authorship import classify
    store.append(_n(12, "t10", 0, "Thoughts?\n" + LONG_MODEL_ANSWER, "2026-05-07T09:00:00Z"))
    dropped, own = classify(store)
    assert dropped["p12"] == "paste" and own["p12"] == "Thoughts?"


def test_palate_epoch_follows_the_map_not_the_flag(store):
    from trinity_local.me import authorship
    from trinity_local.me.palate_registry import LEGACY_EPOCH, _epoch_now
    assert _epoch_now() == LEGACY_EPOCH                              # flag on, no map: not filtered
    authorship.authorship_map_path().parent.mkdir(parents=True, exist_ok=True)
    authorship.authorship_map_path().write_text(json.dumps(
        {"version": authorship.MAP_VERSION, "built_at": "x", "nodes": 1, "dropped": 0, "share_kept": 1.0,
         "counts": {}, "classes": {}, "own_text": {}}))
    assert _epoch_now() == f"only-me-{authorship.MAP_VERSION}"     # a new filter version is a new epoch


def _form(i, tx, name, score, issue, ts):
    return _n(i, tx, 0, f'Fix the visual issue for plan "{name}" ({score}/10). ISSUE: {issue} PRIORITY: high', ts)


def test_a_form_filled_in_across_sessions_is_a_template(tmp_path, monkeypatch):
    """Found 2026-10-08: an automated loop's prompts, unique text but one skeleton, were kept as the
    founder (their sessions predate the entrypoint field). Later instances are templates; the
    earliest stays, as for copies."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.me.authorship import TEMPLATE_MIN_SESSIONS, classify
    forms = [_form(i, f"s{i}", f"plan-{i}", 5 + i % 5, f"issue number {i} in the {i}th room", f"2026-05-0{1 + i}T09:00:00Z")
             for i in range(TEMPLATE_MIN_SESSIONS)]
    dropped, _ = classify(forms)
    assert "p0" not in dropped
    assert all(dropped.get(f"p{i}") == "template" for i in range(1, TEMPLATE_MIN_SESSIONS))


def test_a_skeleton_in_fewer_sessions_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.me.authorship import TEMPLATE_MIN_SESSIONS, classify
    forms = [_form(i, f"s{i}", f"plan-{i}", 7, f"issue {i}", f"2026-05-0{1 + i}T09:00:00Z")
             for i in range(TEMPLATE_MIN_SESSIONS - 1)]
    assert classify(forms)[0] == {}


def test_dispatch_ledger_copy(store, tmp_path):
    from trinity_local.dispatch_ledger import _norm_hash, ledger_path
    from trinity_local.me.authorship import classify
    ledger_path().parent.mkdir(parents=True, exist_ok=True)
    ledger_path().write_text(json.dumps({"h": _norm_hash(store[5].text), "ts": "2026-05-03T09:00:00Z"}) + "\n")
    dropped, _ = classify(store)
    assert dropped["p6"] == "dispatched" and "p1" not in dropped     # the original stays the user's


def test_gate_filters_and_passes_through(store, monkeypatch):
    from trinity_local.me import authorship
    dropped, own = authorship.classify(store)
    authorship.authorship_map_path().parent.mkdir(parents=True, exist_ok=True)
    authorship.authorship_map_path().write_text(json.dumps(
        {"version": authorship.MAP_VERSION, "classes": dropped, "own_text": own}))
    kept = {n.id: n.text for n in authorship.lens_only(iter(store))}
    assert set(kept) == {"p1", "p4", "p7", "p8", "p9"}
    assert kept["p7"].startswith("critique this before I send it") and LONG_MODEL_ANSWER not in kept["p7"]
    monkeypatch.setenv("TRINITY_LENS_ONLY_ME", "0")
    assert len(list(authorship.lens_only(iter(store)))) == len(store)
    monkeypatch.delenv("TRINITY_LENS_ONLY_ME")
    authorship.authorship_map_path().unlink()
    assert len(list(authorship.lens_only(iter(store)))) == len(store)  # no map yet: unchanged


@pytest.mark.parametrize("payload", ["[1, 2]", "42", '{"version": 1, "classes": [], "own_text": {}}'])
def test_a_wrong_shaped_map_reads_as_no_map(store, payload):
    from trinity_local.me import authorship
    authorship.authorship_map_path().parent.mkdir(parents=True, exist_ok=True)
    authorship.authorship_map_path().write_text(payload)
    assert len(list(authorship.lens_only(iter(store)))) == len(store)
    assert authorship.authorship_status()["state"] == "absent"


def test_ensure_rebuilds_only_when_the_corpus_changed(store, monkeypatch):
    from trinity_local.me import authorship
    calls = []
    monkeypatch.setattr(authorship, "build_authorship_map",
                        lambda fp=None: (calls.append(fp), authorship.authorship_map_path().parent.mkdir(
                            parents=True, exist_ok=True), authorship.authorship_map_path().write_text(json.dumps(
                                {"version": authorship.MAP_VERSION, "fingerprint": fp, "classes": {}, "own_text": {}})))[0])
    authorship.ensure_authorship_map("10:a")
    authorship.ensure_authorship_map("10:a")
    authorship.ensure_authorship_map("11:b")
    assert calls == ["10:a", "11:b"]


# ---- the structural ratchet --------------------------------------------------------------

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
READERS = {"iter_prompt_nodes", "iter_prompt_nodes_no_embedding", "_iter_nodes"}
# Reads that are not lens content, each with its reason. Anything else must wrap in lens_only().
ALLOW = {
    ("me/authorship.py", "build_authorship_map"): "classifies the whole store",
    ("me/authorship.py", "iter_lens_nodes"): "is the gate",
    ("me/chapters.py", "corpus_month_span"): "store health: is the time field intact",
    ("me_builder.py", "_corpus_fingerprint"): "detects corpus change, must see everything",
    ("me_builder.py", "build_me_via_lens_pipeline"): "the arcs id -> position lookup",
}


def _unwrapped_reads():
    files = sorted((SRC / "me").glob("*.py")) + [SRC / "me_builder.py", SRC / "vocabulary.py"]
    out = []
    for f in files:
        rel = str(f.relative_to(SRC))
        if rel == "me/import_verification.py":          # the import boundary, not the lens
            continue
        tree = ast.parse(f.read_text())
        parents = {ch: node for node in ast.walk(tree) for ch in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in READERS:
                continue
            p = parents.get(node)
            if isinstance(p, ast.Call) and (getattr(p.func, "id", None) or getattr(p.func, "attr", None)) == "lens_only":
                continue
            fn = node
            while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = parents[fn]
            if (rel, getattr(fn, "name", "<module>")) not in ALLOW:
                out.append(f"{rel}::{getattr(fn, 'name', '<module>')}")
    return out


def test_every_lens_read_goes_through_the_gate():
    bad = _unwrapped_reads()
    assert not bad, f"lens-side prompt reads that bypass lens_only(): {bad}"


GATE_CALLS = {"lens_only", "lens_view", "is_lens_node_id"}
FILE_READ_ALLOW = {("me/exposures.py", "_node_providers"): "an id -> provider lookup"}


def test_direct_store_file_readers_use_the_gate_too():
    """Some lens-side modules parse prompt_nodes.jsonl themselves instead of calling the store.
    Each such function must apply the gate (lens_view / is_lens_node_id) or be allowlisted."""
    files = sorted((SRC / "me").glob("*.py")) + [SRC / "core_gate.py", SRC / "me_builder.py", SRC / "vocabulary.py"]
    bad = []
    for f in files:
        rel = str(f.relative_to(SRC))
        if rel in ("me/authorship.py", "me/import_verification.py"):
            continue
        tree = ast.parse(f.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            consts = {c.value for c in ast.walk(fn) if isinstance(c, ast.Constant) and isinstance(c.value, str)}
            if "prompt_nodes.jsonl" not in consts:
                continue
            calls = {getattr(c.func, "id", None) or getattr(c.func, "attr", None) for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if not calls & GATE_CALLS and (rel, fn.name) not in FILE_READ_ALLOW:
                bad.append(f"{rel}::{fn.name}")
    assert not bad, f"functions parsing prompt_nodes.jsonl without the gate: {bad}"


def test_the_lens_build_refreshes_the_map():
    tree = ast.parse((SRC / "me_builder.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "build_me_via_lens_pipeline")
    calls = {getattr(c.func, "id", None) or getattr(c.func, "attr", None) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "ensure_authorship_map" in calls


def test_a_map_from_an_older_filter_reads_as_absent_and_is_rebuilt(tmp_path, monkeypatch):
    """verify (codex, 2026-10-08): bumping MAP_VERSION must retire v1 maps, or a lens build keeps
    reading a map without the template class and the palate epoch stays wrong."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.me import authorship
    authorship._CACHE = None
    authorship.authorship_map_path().parent.mkdir(parents=True, exist_ok=True)
    authorship.authorship_map_path().write_text(json.dumps(
        {"version": authorship.MAP_VERSION - 1, "built_at": "x", "fingerprint": "fp", "nodes": 1, "dropped": 0,
         "share_kept": 1.0, "counts": {}, "classes": {}, "own_text": {}}))
    assert authorship.authorship_status()["state"] == "absent"
    rebuilt = []
    monkeypatch.setattr(authorship, "build_authorship_map", lambda fp=None: rebuilt.append(fp) or {})
    authorship.ensure_authorship_map("fp")          # same fingerprint, older version: still rebuilt
    assert rebuilt == ["fp"]
