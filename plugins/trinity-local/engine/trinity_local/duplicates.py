"""Did the change copy a value that already lives somewhere else?

The say-do gap: a model will say "one source of truth" and then paste the same
spec into a second file. On this repo the mcp version spec ended up in 8 install
paths and the install command in 25 files before each was consolidated. This
check is content-blind and deterministic: it lists every long string literal
(>= MIN_LEN characters) or version spec (e.g. mcp>=1.0,<2) that the diff ADDS to a
source file and that also appears in another tracked file.

Ignored: test files (tests assert literals on purpose), comments, and byte-identical
copies of the changed file (a generated mirror is not a second source).

MEASURED, KILLED (hq_125, res_153): on 103 agent-written commits, flagged commits were
later fixed at lift 1.43, no better than picking the same number of commits by how many
values they add or by churn (both 1.59). It stays OFF (TRINITY_VERIFY_DUPLICATES=1) as a
descriptive aid: it says where a value now lives twice, not that the change is risky.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .untested import _TEST_PATH, parse_hunks

FLAG = "TRINITY_VERIFY_DUPLICATES"
MIN_LEN = 24
_STRING = re.compile(r"""(["'])((?:(?!\1)[^\\\n]|\\.){%d,}?)\1""" % MIN_LEN)
_SPEC = re.compile(r"\b[A-Za-z][\w.-]*\s*(?:>=|==|<=|~=)\s*\d[\w.]*(?:\s*,\s*(?:<|<=|>|>=|==|!=)\s*\d[\w.]*)*")
_COMMENT = re.compile(r"^\s*(#|//)")


def enabled() -> bool:
    return os.environ.get(FLAG) == "1"


_LOGLIKE = re.compile(r"\b(log|logger|logging|print|raise|warn|warning|error|Error|Exception|echo|fail|stderr)\b")


def category(value: str, line: str) -> str:
    """For stratified reporting only (council_39db12c038cf80ca): nothing is excluded by it."""
    if _SPEC.fullmatch(value):
        return "spec"
    if "://" in value or value.startswith(("/", "~/", "./")):
        return "url/path"
    if _LOGLIKE.search(line):
        return "log/error"
    return "text"


def _values(text: str) -> list[str]:
    vals = [m.group(2) for m in _STRING.finditer(text)]
    vals += [m.group(0) for m in _SPEC.finditer(text) if len(m.group(0)) >= 6]
    return vals


def added_values(diff: str) -> list[tuple[str, str, str]]:
    """(path, value, category) for each long literal or version spec the diff adds to a source
    file MORE times than it removes it: a line that is merely edited around an existing value
    does not count as a new copy (council_39db12c038cf80ca)."""
    added: list[tuple[str, str, str]] = []
    net: dict[str, int] = {}
    for h in parse_hunks(diff):
        if _TEST_PATH.search(h.path):
            continue
        for line in h.body:
            sign, text = line[:1], line[1:]
            if sign not in ("+", "-") or _COMMENT.match(text):
                continue
            for v in _values(text):
                net[v] = net.get(v, 0) + (1 if sign == "+" else -1)
                if sign == "+":
                    added.append((h.path, v, category(v, text)))
    seen, uniq = set(), []
    for path, v, cat in added:
        if net.get(v, 0) > 0 and (path, v) not in seen:
            seen.add((path, v))
            uniq.append((path, v, cat))
    return uniq


def _files_containing(value: str, cwd: Path) -> list[str]:
    r = subprocess.run(["git", "grep", "-l", "-I", "-F", "-e", value], cwd=cwd, capture_output=True, text=True)
    return [p for p in r.stdout.splitlines() if p]


def duplicated_values(diff: str, cwd: Path, max_values: int = 60) -> dict:
    cwd = Path(cwd)
    if not (cwd / ".git").exists():
        return {"ran": False, "reason": "not a git checkout"}
    values = added_values(diff)
    rows = []
    for path, value, cat in values[:max_values]:
        own = (cwd / path).read_bytes() if (cwd / path).is_file() else None
        others = []
        for f in _files_containing(value, cwd):
            if f == path or _TEST_PATH.search(f):
                continue
            if own is not None and (cwd / f).is_file() and (cwd / f).read_bytes() == own:
                continue                                  # a generated mirror, not a second source
            others.append(f)
        if others:
            rows.append({"value": value, "added_in": path, "category": cat,
                         "also_in": others[:10], "copies": len(others)})
    return {"ran": True, "values_checked": min(len(values), max_values),
            "values_capped": max(0, len(values) - max_values), "duplicated": rows}
