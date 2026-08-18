"""Does the JavaScript we GENERATE actually parse?

Trinity emits its launchpad and memory-viewer pages as one large inline <script>
built from Python format strings. Nothing checked that the result parses, and on
2026-08-18 that cost the whole memory viewer: a removal commit (res_022) deleted the
opening of a block -- `if (focusTask && ...) {` through a click handler -- and left the
trailing `});` plus two appendChild calls behind. Unbalanced delimiters, SyntaxError at
load, every file view stuck on "Loading…" forever.

It shipped because the guard that catches it is a browser test, and browser tests skip
without Chrome on the gate command this repo mandates. Measured the same day: ~30% of
this repo's fix commits put their ENTIRE guard in that tier.

So this checker is deliberately DEPENDENCY-FREE and runs in the default tier. It does not
parse JavaScript; it tracks delimiter balance while skipping the four contexts where a
brace is not a brace -- line comments, block comments, quoted strings, and template
literals (including nested `${}`) -- plus regex literals, which are distinguished from
division by the token that precedes them.

That is weaker than a parser and strong enough for the failure it exists to catch: a
partial deletion leaves delimiters unbalanced, and unbalanced delimiters are exactly what
this counts. `node --check` runs too when node is present, as a strictly additional
check, never as a replacement -- a guard that silently degrades to nothing when a tool is
missing is the same bug class as the browser tier it is compensating for.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

PAIRS = {")": "(", "]": "[", "}": "{"}
OPENERS = set(PAIRS.values())
# A `/` starts a regex (not a division) when the last meaningful token is one of these.
REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {"return", "typeof", "case", "in", "of"}


def strip_noncode(js: str) -> str:
    """Blank out comments, strings, template literals and regex literals.

    Replaces their contents with spaces rather than deleting, so reported offsets stay
    meaningful and the delimiter scan sees only real code.
    """
    out = list(js)
    i, n = 0, len(js)
    last_sig = ""
    while i < n:
        c = js[i]
        nxt = js[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = js.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and nxt == "*":
            j = js.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and js[j] != c:
                j += 2 if js[j] == "\\" else 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
            last_sig = "x"
            continue
        if c == "`":
            j, depth = i + 1, 0
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "$" and j + 1 < n and js[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if js[j] == "}" and depth:
                    depth -= 1
                elif js[j] == "`" and not depth:
                    break
                j += 1
            for k in range(i, min(j + 1, n)):
                out[k] = " "
            i = j + 1
            last_sig = "x"
            continue
        if c == "/" and (last_sig in REGEX_PRECEDERS or last_sig == ""):
            j, cls = i + 1, False
            while j < n and (cls or js[j] != "/"):
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "[":
                    cls = True
                elif js[j] == "]":
                    cls = False
                elif js[j] == "\n":
                    break
                j += 1
            if j < n and js[j] == "/":
                for k in range(i, j + 1):
                    out[k] = " "
                i = j + 1
                last_sig = "x"
                continue
        if not c.isspace():
            last_sig = c
        i += 1
    return "".join(out)


def unbalanced(js: str) -> str | None:
    """None when delimiters balance, else a human-readable description."""
    code = strip_noncode(js)
    stack: list[tuple[str, int]] = []
    for idx, ch in enumerate(code):
        if ch in OPENERS:
            stack.append((ch, idx))
        elif ch in PAIRS:
            if not stack:
                return f"stray closing {ch!r} at offset {idx} (line {code[:idx].count(chr(10)) + 1})"
            open_ch, _ = stack.pop()
            if open_ch != PAIRS[ch]:
                return (f"mismatched {open_ch!r} closed by {ch!r} at offset {idx} "
                        f"(line {code[:idx].count(chr(10)) + 1})")
    if stack:
        ch, idx = stack[-1]
        return f"unclosed {ch!r} opened at offset {idx} (line {code[:idx].count(chr(10)) + 1})"
    return None


def node_check(js: str, tmp: Path) -> str | None:
    """`node --check`, when node exists. Additional evidence, never a substitute."""
    if not shutil.which("node"):
        return None
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(tmp)], capture_output=True, text=True)
    return None if r.returncode == 0 else r.stderr.strip()[:400]


def inline_scripts(html: str) -> list[str]:
    return re.findall(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>", html, re.S)


def main() -> int:
    import tempfile
    bad = 0
    for path in [Path(p) for p in sys.argv[1:]]:
        for i, js in enumerate(inline_scripts(path.read_text(encoding="utf-8"))):
            for label, problem in (("balance", unbalanced(js)),
                                   ("node", node_check(js, Path(tempfile.mkdtemp()) / "s.js"))):
                if problem:
                    print(f"{path.name} script[{i}] {label}: {problem}")
                    bad += 1
    print("OK" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
