"""verify's readers are told what they did not see, and a partial read is never agreement
(coherence review 2026-10-15).

read_panel sent `diff[:60000]` with no word to the readers or the user. A 135 KB diff whose
first 60 KB were four deleted files reached both readers without the code under review; they
voted "cannot verify", but a cut just past a defect would have produced "panel agreed" on the
half they saw.
"""
from __future__ import annotations

from types import SimpleNamespace

from trinity_local import verify as V
from trinity_local.verify import Criterion
from trinity_local.verify_rule import Kernel, Panel, triage


def _file(path: str, body_lines: int, *, deleted: bool = False) -> str:
    if deleted:
        return (f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n--- a/{path}\n+++ /dev/null\n"
                + "".join(f"-old line {i}\n" for i in range(body_lines)))
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            + "".join(f"+new line {i}\n" for i in range(body_lines)))


def test_a_diff_that_fits_reaches_readers_whole_deleted_files_included():
    diff = _file("gone.py", 5, deleted=True) + _file("kept.py", 3)
    shown, partial = V.reader_diff(diff, cap=10_000)
    assert partial == "" and "-old line 4" in shown, "a removed check under the cap must stay visible"


def test_over_the_cap_a_deleted_file_reaches_readers_as_one_line():
    diff = _file("gone.py", 5000, deleted=True) + _file("kept.py", 3)
    shown, partial = V.reader_diff(diff, cap=2000)
    assert partial == ""
    assert "deleted file (5000 lines removed; contents not shown)" in shown
    assert "+new line 2" in shown and "-old line 1" not in shown


def test_an_oversized_diff_names_what_the_readers_did_not_see():
    diff = _file("a.py", 50) + _file("b.py", 50) + _file("c.py", 50)
    shown, partial = V.reader_diff(diff, cap=len(_file("a.py", 50)) + 10)
    assert "a.py" in shown and "+new line" in shown
    assert partial and "b.py" in partial and "c.py" in partial
    assert f"[verify: {partial}]" in shown, "the readers must be told too"


def test_one_oversized_file_is_cut_never_dropped():
    shown, partial = V.reader_diff("x" * 5000, cap=1000)
    assert len(shown.split("\n[verify:")[0]) == 1000 and "cut at" in partial


def test_a_partial_read_is_never_consensus_and_the_verdict_says_so():
    votes = {"claude": True, "codex": True, "antigravity": True}
    assert Panel(ran=True, votes=votes).consensus_pass() is True
    partial = Panel(ran=True, votes=votes, partial="2 of 5 files not shown")
    assert partial.consensus_pass() is False
    t = triage(Kernel(ran=True, relevant=True, green=True), partial)
    assert t.outcome == "READ" and "only part of the diff" in t.reason


def test_read_panel_reports_the_partial_read(monkeypatch, tmp_path):
    seen = []

    def fake(name, prompt, cwd, config, effort):
        seen.append(prompt)
        return SimpleNamespace(stdout='{"votes": {"j": "PASS"}, "why": ""}', stderr="",
                               returncode=0, usage={}), config.providers[name]
    monkeypatch.setattr(V, "_dispatch", fake)
    monkeypatch.setattr(V, "READER_DIFF_CHARS", 500)
    monkeypatch.setattr(V.reader_diff, "__defaults__", (500,))
    crit = [Criterion.from_dict({"id": "j", "kind": "judgment", "statement": "s"})]
    panel, _ = V.read_panel(crit, _file("a.py", 20) + _file("b.py", 20), "ctx", tmp_path)
    assert panel.partial and "b.py" in panel.partial
    assert seen and all("[verify:" in p for p in seen)


def test_code_reaches_the_readers_before_docs_and_tests():
    diff = _file("README.md", 40) + _file("tests/test_x.py", 40) + _file("src/pkg/core.py", 40)
    shown, partial = V.reader_diff(diff, cap=len(_file("src/pkg/core.py", 40)) + 10)
    assert "src/pkg/core.py" in shown.split("[verify:")[0]
    assert "README.md" in partial and "tests/test_x.py" in partial
