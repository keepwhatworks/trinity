"""verify's duplicated-value check (src/trinity_local/duplicates.py, hq_125)."""
from __future__ import annotations

import subprocess

import pytest

from trinity_local import duplicates as dup


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "r"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "a.py").write_text('URL = "https://example.com/install.sh | sh"\nDEP = "mcp>=1.0,<2"\n')
    (root / "tests" / "test_a.py").write_text('X = "a value that only a test asserts here"\n')
    _git(root, "init", "-q"); _git(root, "add", "-A"); _git(root, "commit", "-qm", "base")
    return root


def _change(root, rel, text):
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)
    _git(root, "add", "-A")
    return subprocess.run(["git", "diff", "--cached"], cwd=root, capture_output=True, text=True).stdout


def test_flags_a_value_pasted_into_a_second_file(repo):
    diff = _change(repo, "src/b.py", 'CMD = "https://example.com/install.sh | sh"\nREQ = ["mcp>=1.0,<2"]\n')
    r = dup.duplicated_values(diff, repo)
    vals = {d["value"]: d["also_in"] for d in r["duplicated"]}
    assert vals.get("https://example.com/install.sh | sh") == ["src/a.py"]
    assert vals.get("mcp>=1.0,<2") == ["src/a.py"]


def test_new_values_and_short_strings_are_not_flagged(repo):
    diff = _change(repo, "src/c.py", 'NEW = "a brand new long string nobody else has"\nS = "short"\n')
    assert dup.duplicated_values(diff, repo)["duplicated"] == []


def test_tests_comments_and_generated_mirrors_are_ignored(repo):
    # a test asserting an existing value is not a second source
    d1 = _change(repo, "tests/test_b.py", 'Y = "https://example.com/install.sh | sh"\n')
    assert dup.duplicated_values(d1, repo)["duplicated"] == []
    # a comment mentioning it is not a copy
    d2 = _change(repo, "src/d.py", '# see "https://example.com/install.sh | sh"\n')
    assert dup.duplicated_values(d2, repo)["duplicated"] == []
    # a byte-identical generated mirror of the changed file is not a second source
    (repo / "mirror").mkdir()
    text = 'NEW = "a value living in the file and its mirror"\n'
    (repo / "mirror" / "e.py").write_text(text)
    d3 = _change(repo, "src/e.py", text)
    assert dup.duplicated_values(d3, repo)["duplicated"] == []


def test_refuses_outside_a_git_checkout(tmp_path):
    assert dup.duplicated_values("", tmp_path)["ran"] is False


def test_editing_around_an_existing_value_is_not_a_new_copy(repo):
    """A line rewritten while keeping the same value nets to zero: no copy was added."""
    (repo / "src" / "b.py").write_text('OTHER = "https://example.com/install.sh | sh"\n')
    _git(repo, "add", "-A"); _git(repo, "commit", "-qm", "second copy already exists")
    diff = _change(repo, "src/a.py", 'URL = ("https://example.com/install.sh | sh")\nDEP = "mcp>=1.0,<2"\n')
    assert dup.duplicated_values(diff, repo)["duplicated"] == []


def test_categories_are_reported():
    assert dup.category("mcp>=1.0,<2", 'DEP = "mcp>=1.0,<2"') == "spec"
    assert dup.category("https://example.com/install.sh", "") == "url/path"
    assert dup.category("could not reach the server at all", 'raise RuntimeError("...")') == "log/error"
    assert dup.category("Restart Claude Code to load the tools", "print(x)") == "log/error"
    assert dup.category("Restart Claude Code to load the tools", "MSG = x") == "text"


def test_verify_reports_duplicates_only_when_enabled(repo, monkeypatch):
    from trinity_local import verify as v
    from trinity_local.commands.verify import render_card
    diff = _change(repo, "src/b.py", 'CMD = "https://example.com/install.sh | sh"\n')
    crit = [{"id": "T1", "kind": "test", "statement": "s", "command": "true"}]
    monkeypatch.delenv(dup.FLAG, raising=False)
    assert v.verify(crit, diff, "", repo, run_panel=False)["duplicates"] is None
    monkeypatch.setenv(dup.FLAG, "1")
    on = v.verify(crit, diff, "", repo, run_panel=False)
    assert on["duplicates"]["duplicated"] and "duplicated: 1 value(s)" in render_card(on)
