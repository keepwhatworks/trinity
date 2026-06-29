"""The memory viewer's copy-a-shell-command chips must produce a command that is
SAFE to paste into a real bash shell.

Both the per-rep "Replay" chip (memory_viewer.py:2627) and the basin-level
"Launch council on this topic" chip (:2826) build
`trinity-local council --task "<escapeBashArg(headline)>"` and copy it to the
clipboard. The seed is a basin REPRESENTATIVE HEADLINE — a verbatim user prompt,
which routinely contains the four bash double-quote metacharacters: dollar,
backtick, double-quote, backslash.

`escapeBashArg` (:2602) escapes exactly those four (backslash FIRST, then " ` $)
so that, inside the double-quoted `--task "..."`, bash neither expands $HOME,
executes `git log`, nor drops the literal quote/backslash. A regression to that
escaping (wrong order, a dropped metachar) ships a BROKEN or COMMAND-INJECTING
copy SILENTLY — the chip still flashes "✓ Copied" and the prior chip tests
(test_memory_viewer_topics_rep_expand_browser /
test_memory_viewer_copy_announces_to_screen_reader_browser) only assert the
visual ✓ swap, the SR announcement, and stopPropagation with a BENIGN headline.
None exercises `escapeBashArg` with the metacharacters it exists to escape.

This is the shell analog of the renderMarkdown XSS-sanitizer guards (#287/#288):
a security-sensitive escaper on attacker-influenceable corpus content, mutation-
proven so a broken escape REDS here instead of shipping a copy that runs
`git log` (or worse) on paste.

The guard drives the REAL file:// memory viewer, clicks both chips, reads the
clipboard, and round-trips the copied command through a REAL bash shell with a
stub `trinity-local` on PATH — asserting the recovered --task argv is BYTE-EXACT
the original headline.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parent.parent

# A realistic ORGANIC prompt headline carrying all four bash double-quote
# metacharacters: $ (env var / price), ` (inline code), " (literal quote),
# \ (windows path / regex). Real corpus prompts have these.
SEED = r'''how do I print $HOME and run `git log` for the path C:\Users with a "quoted" arg?'''

# The four metacharacters escapeBashArg must neutralize inside `--task "..."`.
_METACHARS = ("$", "`", '"', "\\")


def _render_portal(home: Path) -> Path:
    """Seed topics.json with the metachar-headline basin and render the REAL
    portal-html (publishes the d3/font vendor files so the topology graph mounts).
    Returns the portal_pages dir holding memory.html."""
    (home / "memories").mkdir(parents=True)
    topics = {
        "basins": [
            {
                "id": "b00",
                "label": "shell scripting questions",
                "size": 4,
                "thread_count": 1,
                "top_terms": ["shell", "bash", "path", "quote"],
                "representatives": [
                    {
                        "headline": SEED,
                        "transcript_id": "tx1",
                        "turn_count": 1,
                        "turns": [],
                    }
                ],
                "centroid": [0.1, 0.2, 0.3],
            },
            {
                "id": "b01",
                "label": "second basin",
                "size": 2,
                "thread_count": 1,
                "top_terms": ["foo", "bar"],
                "representatives": [
                    {
                        "headline": "a plain headline",
                        "transcript_id": "tx2",
                        "turn_count": 1,
                        "turns": [],
                    }
                ],
                "centroid": [0.4, 0.5, 0.6],
            },
        ],
        "edges": [{"source": "b00", "target": "b01", "weight": 0.5}],
    }
    (home / "memories" / "topics.json").write_text(
        json.dumps(topics), encoding="utf-8"
    )
    (home / "memories" / "lens.md").write_text("# lens\n\n- a vs b\n", encoding="utf-8")

    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


def _recover_task_arg_via_real_bash(copied_command: str, scratch: Path) -> str:
    """Paste `copied_command` into a REAL bash shell with a stub `trinity-local`
    on PATH that prints its --task argv byte-exact, and return what bash passed.

    This is the load-bearing assertion: if the escaping is wrong, bash will
    expand $HOME / execute the backtick / drop the quote, and the recovered
    string will DIFFER from the original headline (or the stub's marker proves
    injected execution)."""
    bindir = scratch / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "trinity-local"
    # The stub walks argv, finds --task, and prints its value on a marked line.
    stub.write_text(
        '#!/usr/bin/env bash\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "--task" ]; then shift; printf "TASK_ARG=[%s]\\n" "$1"; fi\n'
        '  shift\n'
        'done\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    # Run the copied command verbatim in bash. If a backtick were unescaped,
    # `git log` would execute here (and could fail / emit output) — but with
    # correct escaping it is a literal inside the quoted --task value.
    proc = subprocess.run(
        ["bash", "-c", copied_command],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = proc.stdout
    marker = "TASK_ARG=["
    assert marker in out, (
        "the stub trinity-local did not receive a --task argument when the copied "
        f"command was pasted into a real bash shell (stdout={out!r}, stderr={proc.stderr!r}). "
        "A broken escape can mangle the command so --task never reaches the binary."
    )
    start = out.index(marker) + len(marker)
    end = out.rindex("]")
    return out[start:end]


def test_replay_and_launch_chip_commands_are_shell_safe():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    # Non-vacuous precondition: the seed actually carries every metachar.
    for ch in _METACHARS:
        assert ch in SEED, f"test seed is missing the metacharacter {ch!r} — guard would be vacuous"

    scratch = Path(tempfile.mkdtemp(prefix="trin-shellsafe-"))
    home = scratch / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(target, wait_until="load")
        page.wait_for_timeout(1200)

        # Click the b00 basin node circle to open the detail panel (showDetail).
        circle_count = page.evaluate(
            """() => {
              const circles = Array.from(document.querySelectorAll('circle'));
              let target = circles[0];
              for (const c of circles) {
                const id = c.getAttribute('data-id') || (c.__data__ && c.__data__.id);
                if (id === 'b00') { target = c; break; }
              }
              if (target) target.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
              return circles.length;
            }"""
        )
        assert circle_count >= 1, "no basin nodes rendered in the topology graph"
        page.wait_for_timeout(900)

        chips = page.evaluate(
            """() => ({
              replay: document.querySelectorAll('.topics-rep-replay').length,
              launch: document.querySelectorAll('.topics-launch-chip').length,
            })"""
        )
        assert chips["replay"] >= 1, f"no per-rep Replay chip rendered after opening basin b00: {chips}"
        assert chips["launch"] >= 1, f"no basin Launch-council chip rendered after opening basin b00: {chips}"

        # Read the copied command from EACH chip.
        page.click(".topics-launch-chip")
        page.wait_for_timeout(200)
        launch_clip = page.evaluate("() => navigator.clipboard.readText()")

        page.click(".topics-rep-replay")
        page.wait_for_timeout(200)
        replay_clip = page.evaluate("() => navigator.clipboard.readText()")

        assert not [e for e in errs if "woff2" not in e and "404" not in e], (
            f"the memory viewer threw a JS error while driving the copy chips: {errs}"
        )
        browser.close()

    for label, clip in (("Launch", launch_clip), ("Replay", replay_clip)):
        assert clip, f"the {label} chip copied an empty command to the clipboard"

        # 1) Shape: exactly `trinity-local council --task "<escaped>"`.
        prefix = 'trinity-local council --task "'
        assert clip.startswith(prefix) and clip.endswith('"'), (
            f"the {label} chip copied an unexpected command shape: {clip!r}"
        )

        # 2) Escaping actually happened: the copied command must DIFFER from the
        #    raw headline embedded verbatim (else escapeBashArg was a no-op and
        #    the round-trip would only accidentally pass on metachar-free input).
        assert f'--task "{SEED}"' != clip, (
            f"the {label} chip embedded the raw headline with NO escaping — "
            f"escapeBashArg did not fire: {clip!r}"
        )

        # 3) The load-bearing safety assertion: paste into a REAL bash shell and
        #    recover the --task argv. It must be BYTE-EXACT the original headline
        #    — $HOME NOT expanded, `git log` NOT executed, " and \ preserved.
        recovered = _recover_task_arg_via_real_bash(clip, scratch)
        assert recovered == SEED, (
            f"the {label} chip's copied command, pasted into a real bash shell, did NOT "
            f"round-trip to the original prompt — escapeBashArg shipped a broken/injecting "
            f"escape.\n  expected --task: {SEED!r}\n  bash recovered : {recovered!r}\n"
            f"  (if $HOME expanded, a backtick executed, or a quote/backslash was dropped, "
            f"the user who copy-pastes the 'Replay'/'Launch council' command runs the WRONG "
            f"command — or an injected one.)"
        )
