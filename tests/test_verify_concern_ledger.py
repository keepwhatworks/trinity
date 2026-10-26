"""verify keeps a local ledger of its readers' concerns (council_4d1588a7251a43c5): what was reviewed, the criteria,
and each concern with its lab and the reader's one-line why. It is what a later label (did the author act on it;
does a test reproduce it) joins to; the run log keeps counts only, so no concern could be followed before.
The rows hold diff text and model output, so they stay on this machine, outside analytics/."""
from __future__ import annotations

import json
import subprocess

from trinity_local import verify as V
from trinity_local.verify_rule import Panel


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, capture_output=True, text=True, check=True).stdout


def _repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    (r / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "before")
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    _git(r, "add", "-A")
    return r, _git(r, "diff", "--cached")


def _panel(monkeypatch, votes: dict[str, dict[str, bool]], why: dict[str, str]):
    reads = [V.Read(provider=p, lab=V.LAB_OF.get(p, p), model=f"{p}-m", votes=v, passed=all(v.values()), seconds=1.0,
                    why=why.get(p, "")) for p, v in votes.items()]
    panel = Panel(ran=True, votes={p: all(v.values()) for p, v in votes.items()})
    monkeypatch.setattr(V, "read_panel", lambda *a, **k: (panel, reads))


CRIT = [{"id": "j_sum", "kind": "judgment", "statement": "add still adds"}]


def test_a_concern_is_recorded_with_what_was_reviewed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    r, diff = _repo(tmp_path)
    _panel(monkeypatch, {"codex": {"j_sum": False}, "antigravity": {"j_sum": True}},
           {"codex": "add now subtracts"})
    V.verify(CRIT, diff, "ctx", r, exclude_lab="claude")

    (row,) = [json.loads(l) for l in (home / "verify_concerns.jsonl").read_text().splitlines()]
    assert row["concerns"] == [{"lab": "openai", "provider": "codex", "model": "codex-m", "criterion": "j_sum",
                                "why": "add now subtracts"}]
    head = _git(r, "rev-parse", "HEAD").strip()
    assert (row["diff"], row["base"], row["repo"]) == (diff, head, str(r.resolve()))
    # a replay gives its readers exactly what these readers saw, and knows whose work they read
    assert (row["context"], row["exclude_lab"]) == ("ctx", "claude")
    assert row["criteria"] == [{"id": "j_sum", "kind": "judgment", "statement": "add still adds", "advisory": False}]
    assert row["readers"] == [{"lab": "openai", "provider": "codex", "model": "codex-m"},
                              {"lab": "google", "provider": "antigravity", "model": "antigravity-m"}]
    # the counts-only run log stays free of the text the ledger holds
    assert "add now subtracts" not in (home / "analytics" / "verify_runs.jsonl").read_text()


def test_a_run_no_reader_flagged_is_recorded_and_the_switch_turns_it_off(tmp_path, monkeypatch):
    """amd_0611: a run every reader passed is a row too. Without it a defect all the readers missed never
    reaches the ledger, and the replay's population would be chosen by the panel's own flags."""
    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    r, diff = _repo(tmp_path)
    _panel(monkeypatch, {"codex": {"j_sum": True}, "antigravity": {"j_sum": True}}, {})
    V.verify(CRIT, diff, "ctx", r)
    (row,) = [json.loads(l) for l in (home / "verify_concerns.jsonl").read_text().splitlines()]
    assert (row["concerns"], [x["provider"] for x in row["readers"]]) == ([], ["codex", "antigravity"])
    assert row["diff"] == diff

    _panel(monkeypatch, {"codex": {"j_sum": False}}, {"codex": "no"})
    monkeypatch.setenv("TRINITY_VERIFY_CONCERNS", "0")
    V.verify(CRIT, diff, "ctx", r)
    assert len((home / "verify_concerns.jsonl").read_text().splitlines()) == 1


def test_a_reader_that_failed_is_not_a_concern(tmp_path, monkeypatch):
    """A read that errored (a usage cap, a timeout) carries no judgment; its votes are not FAIL votes, it is not
    a reader, and a run with no reader left is not recorded. Nor is a read that returned no votes at all (only
    advisory questions, none answered): concern_replay would not count it, so it is no reader here either."""
    home = tmp_path / "home"
    monkeypatch.setenv("TRINITY_HOME", str(home))
    r, diff = _repo(tmp_path)
    reads = [V.Read(provider="codex", lab="openai", model=None, votes={"j_sum": False}, passed=False, seconds=1.0,
                    error="usage limit"),
             V.Read(provider="antigravity", lab="google", model=None, votes={}, passed=False, seconds=1.0)]
    monkeypatch.setattr(V, "read_panel", lambda *a, **k: (Panel(ran=False), reads))
    V.verify(CRIT, diff, "ctx", r)
    assert not (home / "verify_concerns.jsonl").exists()


def test_recording_never_fails_a_verify(monkeypatch):
    monkeypatch.setattr("trinity_local.state_paths.concerns_path",
                        lambda: (_ for _ in ()).throw(OSError("read-only home")))
    V.record_concerns({"panel": {"reads": [{"provider": "codex", "lab": "openai", "votes": {"j": False}}]}})
