#!/usr/bin/env python3
"""Install the wheel a release ships into an empty venv and run what a new user is shown.

WHY (council_a68965439ef6ad3e, amd_0394). v1.7.403 shipped commands that failed only when
INSTALLED: `lens-prompt` and `eval-prompt` read docs/ files the wheel does not carry, and the
`plan` a new user is told to run died on an argparse error. The suite runs from a source
checkout and passed through all three, and nothing in launch-check built and ran the wheel.
Pointed at the 1.7.403 wheel this script fails exactly those three checks. It runs from an
empty HOME and TRINITY_HOME:

  1. every advertised verb's --help, and the read-only onboarding commands, exit 0;
  2. every `run:` line `status` prints, and every health check's `fix`, PARSES with the
     installed CLI's own parser; the ones that are local and cheap, with no `<placeholder>`
     to fill in, are also RUN;
  3. the MCP server answers over stdio: the 4 tools, the council and verify prompts, a
     `trust` call, and an empty lens resource that says what to run.

Nothing here dispatches a model or sends telemetry (sharing is turned off first).

Usage:  .venv/bin/python scripts/wheel_smoke.py [--wheel PATH] [--keep]
Exit 0 only when every check passed.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Fixes that are local, quick and harmless in an isolated home: run them for real.
RUNNABLE_FIXES = {"lens-health", "telemetry-disable", "telemetry-show", "install-extension",
                  "lens-prompt", "eval-prompt", "status", "install"}


def _base_python() -> str:
    for name in (f"python{sys.version_info.major}.{sys.version_info.minor}", "python3"):
        cand = Path(sys.base_prefix) / "bin" / name
        if cand.exists():
            return str(cand)
    return sys.executable


def build_wheel(out: Path) -> Path:
    subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir",
                    str(out), "."], cwd=REPO, check=True, capture_output=True, text=True)
    for leftover in (REPO / "build", *REPO.glob("*.egg-info"), *(REPO / "src").glob("*.egg-info")):
        shutil.rmtree(leftover, ignore_errors=True)
    wheels = sorted(out.glob("*.whl"))
    if not wheels:
        raise SystemExit("no wheel was built")
    return wheels[-1]


def make_venv(root: Path, wheel: Path) -> Path:
    venv = root / "venv"
    subprocess.run([_base_python(), "-m", "venv", str(venv)], check=True)
    subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "-q", str(wheel)],
                   check=True, capture_output=True, text=True)
    return venv


def fix_commands(text: str) -> list[str]:
    """The runnable part of each `└─ run:` / fix line: drop a trailing `# comment`, split on
    a bare `#` and on ` or `, keep the pieces that start with `trinity-local`."""
    out: list[str] = []
    for raw in text.split("# or:"):
        cmd = raw.split("   #")[0].split(" # ")[0].strip()
        for part in cmd.split(" && "):
            part = part.strip()
            if part.startswith("trinity-local "):
                out.append(part)
    return out


class Smoke:
    def __init__(self, venv: Path, home: Path):
        self.venv, self.home = venv, home
        self.env = {"PATH": f"{venv / 'bin'}:/usr/bin:/bin", "HOME": str(home),
                    "TRINITY_HOME": str(home / ".trinity"), "TRINITY_AUTOSCAN_DISABLED": "1",
                    "LANG": "en_US.UTF-8"}
        self.results: list[tuple[bool, str, str]] = []

    def run(self, argv: list[str], *, expect: int = 0, timeout: int = 180, stdin: str = ""):
        r = subprocess.run(argv, cwd=self.home, env=self.env, capture_output=True, text=True,
                           timeout=timeout, input=stdin)
        return r, r.returncode == expect and "Traceback (most recent call last)" not in r.stderr

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        self.results.append((ok, label, detail))

    def cli(self, label: str, args: list[str], *, expect: int = 0, must_contain: str = ""):
        r, ok = self.run([str(self.venv / "bin" / "trinity-local"), *args], expect=expect)
        if ok and must_contain:
            ok = must_contain in (r.stdout + r.stderr)
        self.check(label, ok, "" if ok else f"exit {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        return r

    def parses(self, command: str) -> None:
        """Parse a shown command with the INSTALLED package's own parser, without running it."""
        argv = shlex.split(command)[1:]
        code = ("import sys\nfrom trinity_local.main import build_parser\n"
                f"build_parser().parse_args({argv!r})\n")
        r, ok = self.run([str(self.venv / "bin" / "python"), "-c", code])
        self.check(f"parses: {command}", ok, "" if ok else (r.stderr or r.stdout)[-300:])

    def packaged_data(self) -> None:
        """Files the installed code reads must be in the wheel: a path into the repo (docs/,
        data/, browser-extension/) resolves to nothing on an install."""
        code = ("from trinity_local.commands import extension_repair as e\n"
                "from trinity_local import global_benchmarks as g\n"
                "assert e._PAGE_HOOK_PATH.is_file(), e._PAGE_HOOK_PATH\n"
                "assert g._reference_evals_path().is_file(), g._reference_evals_path()\n")
        r, ok = self.run([str(self.venv / "bin" / "python"), "-c", code])
        self.check("the data files installed code reads are in the wheel", ok,
                   "" if ok else (r.stderr or r.stdout)[-300:])

    def mcp(self) -> None:
        proc = subprocess.Popen([str(self.venv / "bin" / "trinity-local"), "--mcp"], cwd=self.home,
                                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        assert proc.stdin is not None and proc.stdout is not None
        stdin, stdout = proc.stdin, proc.stdout

        def call(msg):
            stdin.write(json.dumps(msg) + "\n")
            stdin.flush()
            while True:
                line = stdout.readline()
                if not line:
                    return None
                data = json.loads(line)
                if data.get("id") == msg.get("id"):
                    return data
        try:
            init = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "wheel-smoke", "version": "1"}}})
            self.check("mcp: initialize", bool(init and "result" in init))
            stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            stdin.flush()
            tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = sorted(t["name"] for t in (tools or {}).get("result", {}).get("tools", []))
            self.check("mcp: the four tools", names == ["get_council_status", "run_council",
                                                        "trust", "verify"], str(names))
            prompts = call({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"})
            pnames = [p["name"] for p in (prompts or {}).get("result", {}).get("prompts", [])]
            self.check("mcp: prompts are council and verify", sorted(pnames) == ["council", "verify"],
                       str(pnames))
            trust = call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "trust", "arguments": {}}})
            res = (trust or {}).get("result", {})
            self.check("mcp: trust answers", bool(res) and not res.get("isError"), str(res)[:300])
            lens = call({"jsonrpc": "2.0", "id": 5, "method": "resources/read",
                         "params": {"uri": "trinity://memories/lens.md"}})
            text = ((lens or {}).get("result", {}).get("contents") or [{}])[0].get("text", "")
            self.check("mcp: an empty lens says what to run", "trinity-local lens" in text, text[:200])
        finally:
            proc.kill()
            proc.wait()

    def all(self) -> None:
        self.cli("telemetry-disable", ["telemetry-disable"])
        self.cli("telemetry-show --wire says OFF", ["telemetry-show", "--wire"],
                 must_contain="Sharing is currently: OFF")
        self.cli("--help", ["--help"])
        for verb in ("lens", "plan", "verify", "trust", "status", "install", "download-embedder",
                     "uninstall", "lens-setup"):
            self.cli(f"{verb} --help", [verb, "--help"])
        self.cli("install --check", ["install", "--check"])
        self.cli("lens-prompt prints the prompt", ["lens-prompt"], must_contain="## ")
        self.cli("eval-prompt prints the prompt", ["eval-prompt"], must_contain="## ")
        self.cli("plan without a question says how", ["plan"], expect=2,
                 must_contain='trinity-local plan "')
        self.cli("uninstall dry run", ["uninstall"])
        human = self.cli("status", ["status"])
        shown = [line.split("run:", 1)[1].strip() for line in human.stdout.splitlines()
                 if "└─ run:" in line]
        js = self.cli("status --json", ["status", "--json"])
        try:
            data = json.loads(js.stdout)
            checks = (data.get("health") or data).get("checks") or []
            shown += [c["fix"] for c in checks if isinstance(c, dict) and c.get("fix")]
        except (json.JSONDecodeError, AttributeError) as exc:
            self.check("status --json is JSON", False, str(exc))
        for command in sorted({c for line in shown for c in fix_commands(line)}):
            self.parses(command)
            verb = shlex.split(command)[1]
            if verb in RUNNABLE_FIXES and "<" not in command:
                self.cli(f"runs: {command}", shlex.split(command)[1:])
        self.packaged_data()
        self.mcp()


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--wheel", help="test this wheel instead of building one from the tree")
    ap.add_argument("--keep", action="store_true", help="keep the temp venv and home")
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="trinity-wheel-smoke-"))
    try:
        wheel = Path(args.wheel) if args.wheel else build_wheel(root / "dist")
        venv = make_venv(root, wheel)
        home = root / "home"
        home.mkdir()
        smoke = Smoke(venv, home)
        smoke.all()
        failed = [r for r in smoke.results if not r[0]]
        for ok, label, detail in smoke.results:
            print(f"{'ok  ' if ok else 'FAIL'} {label}" + (f"\n       {detail}" if detail else ""))
        print(f"\n{len(smoke.results) - len(failed)} of {len(smoke.results)} passed ({wheel.name})")
        return 1 if failed else 0
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
