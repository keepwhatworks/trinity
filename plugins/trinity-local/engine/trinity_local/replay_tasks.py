"""Replay tasks from your own bugs: release-day eval items, Harbor-shaped.

A replay task is a defect-fix commit from your repository whose OWN test goes red when
the fix is taken away (hq_099: 92% of fix commits carry one). Each task is the code as
it was just before the fix, plus the fix's tests; the agent's job is to make them pass;
the grade is the test's exit code, never a judge.

Written in Harbor's task layout (docs.harborframework.com/tasks/overview):

    <task>/instruction.md        neutral wrapper + the exact command + failing test ids
                                 + the ordinary failure output. Never the commit message.
    <task>/task.toml             schema 1.3; metadata (fix commit, date, remotes holding it);
                                 agent network allowlisted to model APIs, verifier offline
    <task>/environment/Dockerfile + repo.tar.gz   the code before the fix (no .git)
    <task>/tests/test.sh         restores the original test files, runs them, writes
                                 /logs/verifier/reward.txt (1 or 0)
    <task>/tests/files/...       the immutable test files
    <task>/solution/solve.sh + fix.patch   the real fix (for oracle runs only)

Harbor consumes these tasks: on 2026-10-05, Harbor 0.24.0's oracle agent scored 1 and
its nop agent 0 on an exported task (council_67f8f7d672701f61's bar). One caveat from that
run: Docker on macOS cannot enforce Harbor's network policies, and Harbor REFUSES an
allowlisted or offline task there rather than ignore the policy; the check ran with
`--agent-network public`. Strict policies need a sandbox that enforces them (Linux Docker
with nftables, or a cloud sandbox). Tasks run here are executed by Trinity's
local runner on the user's subscriptions; local runs do NOT enforce Harbor's network
policy, so a task whose fix is on a public remote can only support a pilot, never a
causal claim about a model or a harness.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

TEST_PATH = re.compile(r"(^|/)(tests?)/.*\.py$|(^|/)test_[^/]*\.py$")
MODEL_API_HOSTS = ["api.anthropic.com", "claude.ai", "api.openai.com", "chatgpt.com",
                   "generativelanguage.googleapis.com", "cloudcode-pa.googleapis.com"]
DOCKERFILE = """FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ADD repo.tar.gz /app
RUN if [ -f pyproject.toml ]; then pip install --no-cache-dir -e '.[test]' || pip install --no-cache-dir -e . || true; fi
"""


@dataclass
class ReplayTask:
    name: str
    fix: str
    parent: str
    date: str
    test_files: list[str]
    failing: list[str]
    failure_tail: str
    command: str
    remotes: list[str] = field(default_factory=list)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:200]}")
    return r.stdout


def _snapshot(repo: Path, rev: str, dest: Path, paths: list[str] | None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(["git", "archive", "--format=tar", rev, *(paths or [])], cwd=repo,
                          capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest, filter="data")


def _pytest(python: str, tree: Path, files: list[str], timeout: int) -> tuple[str, str]:
    """('green'|'failed'|'invalid'|'timeout', output). Uses the same classification as verify."""
    from .untested import _INVALID
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(tree / ".home"),
           "PYTHONPATH": str(tree / "src") if (tree / "src").is_dir() else str(tree),
           "TRINITY_HOME": str(tree / ".home" / ".trinity"), "TRINITY_DISABLE_MLX": "1",
           # A fix can leave a file the same size within the same second (a - b -> a + b);
           # Python's mtime+size bytecode cache would then run the OLD code and drop a real
           # task as "the fix does not pass". No bytecode, no stale reads.
           "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], cwd=tree,
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    out = r.stdout + r.stderr
    if r.returncode == 0:
        return "green", out
    if r.returncode in (2, 3, 4) or _INVALID.search(out):
        return "invalid", out
    return "failed", out


def _failing_ids(output: str) -> list[str]:
    return sorted(set(re.findall(r"^FAILED (\S+)", output, re.M)))


def find_tasks(repo: Path, since: str, python: str | None = None, limit: int = 20,
               paths: list[str] | None = None, timeout: int = 300) -> list[ReplayTask]:
    """Fix commits since `since` whose own test files are RED on the code before the fix
    and GREEN once the fix's source changes are applied. Deterministic; no model call."""
    repo = Path(repo).resolve()
    python = python or (str(repo / ".venv" / "bin" / "python") if (repo / ".venv" / "bin" / "python").exists()
                        else sys.executable)
    shas = _git(repo, "log", "--no-merges", f"--since={since}", "--format=%H").split()
    tasks: list[ReplayTask] = []
    for sha in shas:
        if len(tasks) >= limit:
            break
        changed = _git(repo, "show", "--name-status", "--format=", sha).splitlines()
        tests = [l.split("\t")[-1] for l in changed if l[:1] in ("A", "M") and TEST_PATH.search(l.split("\t")[-1])]
        sources = [l.split("\t")[-1] for l in changed if not TEST_PATH.search(l.split("\t")[-1])]
        if not tests or not any(s.endswith(".py") for s in sources):
            continue
        parent = _git(repo, "rev-parse", f"{sha}^", check=False).strip()
        if not parent:
            continue
        with tempfile.TemporaryDirectory(prefix="trinity-replay-") as tmp:
            before = Path(tmp) / "before"
            _snapshot(repo, parent, before, paths)
            for t in tests:                       # the fix's tests, on the code before the fix
                (before / t).parent.mkdir(parents=True, exist_ok=True)
                (before / t).write_text(_git(repo, "show", f"{sha}:{t}"))
            state, out = _pytest(python, before, tests, timeout)
            if state != "failed":
                continue                          # green: never tested the fix; invalid: does not load
            patch = _git(repo, "diff", parent, sha, "--", *sources)
            p = Path(tmp) / "fix.patch"
            p.write_text(patch)
            if subprocess.run(["git", "apply", str(p)], cwd=before, capture_output=True).returncode:
                continue
            if _pytest(python, before, tests, timeout)[0] != "green":
                continue                          # the fix alone does not make its own tests pass
        date = _git(repo, "show", "-s", "--format=%cI", sha).strip()
        remotes = [r.strip() for r in _git(repo, "branch", "-r", "--contains", sha, check=False).splitlines() if r.strip()]
        tasks.append(ReplayTask(name=f"replay-{sha[:10]}", fix=sha, parent=parent, date=date, test_files=tests,
                                failing=_failing_ids(out), failure_tail=out[-2500:], remotes=remotes,
                                command="python -m pytest -q " + " ".join(tests)))
    return tasks


_LOCAL_PATH = re.compile(r"(/private)?/(tmp|var/folders)/\S+|" + re.escape(str(Path.home())) + r"\S*")


def _scrub(text: str) -> str:
    """Failure output carries local paths (and the user's name in them). Not in a task."""
    return _LOCAL_PATH.sub("<path>", text)


def _instruction(t: ReplayTask) -> str:
    failing = "\n".join(f"- `{f}`" for f in t.failing) or "- (see the output below)"
    return f"""# Make the failing tests pass

The command below fails in this repository. Change the code so it passes.
Do not edit the test files: they are restored to their original contents before grading.

```bash
{t.command}
```

Failing tests:
{failing}

Output when it was run:

```text
{_scrub(t.failure_tail).strip()}
```
"""


def _task_toml(t: ReplayTask, repo_name: str, agent_network: str = "allowlist") -> str:
    hosts = ", ".join(json.dumps(h) for h in MODEL_API_HOSTS)
    agent_net = (f'network_mode = "allowlist"\nallowed_hosts = [{hosts}]' if agent_network == "allowlist"
                 else f'network_mode = "{agent_network}"')
    verifier_net = "public" if agent_network == "public" else "no-network"
    return f"""schema_version = "1.3"

[task]
name = "trinity-replay/{repo_name}-{t.fix[:10]}"
description = "Make a repository's failing tests pass (replayed defect fix)."
keywords = ["replay", "bugfix", "python"]

[metadata]
category = "bugfix"
source_commit = "{t.fix}"
parent_commit = "{t.parent}"
fix_date = "{t.date}"
failing_tests = {json.dumps(t.failing)}
remotes_containing_fix = {json.dumps(t.remotes)}

[agent]
timeout_sec = 1800.0
{agent_net}

[verifier]
timeout_sec = 600.0
network_mode = "{verifier_net}"

[environment]
cpus = 2
memory_mb = 4096
"""


def _test_sh(t: ReplayTask) -> str:
    restore = "\n".join(f'mkdir -p "/app/$(dirname {f})" && cp "/tests/files/{f}" "/app/{f}"' for f in t.test_files)
    return f"""#!/bin/bash
# Restore the original test files (an agent may not change what grades it), then run them.
set -u
mkdir -p /logs/verifier
{restore}
cd /app
if python -m pytest -q -p no:cacheprovider {' '.join(t.test_files)}; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
"""


def write_task(repo: Path, t: ReplayTask, out: Path, paths: list[str] | None = None,
               agent_network: str = "allowlist") -> Path:
    repo = Path(repo).resolve()
    d = out / t.name
    if d.exists():
        shutil.rmtree(d)
    (d / "environment").mkdir(parents=True)
    (d / "tests" / "files").mkdir(parents=True)
    (d / "solution").mkdir(parents=True)
    (d / "instruction.md").write_text(_instruction(t))
    (d / "task.toml").write_text(_task_toml(t, repo.name, agent_network))
    (d / "environment" / "Dockerfile").write_text(DOCKERFILE)
    with tempfile.TemporaryDirectory(prefix="trinity-replay-") as tmp:
        snap = Path(tmp) / "repo"
        _snapshot(repo, t.parent, snap, paths)
        for f in t.test_files:                    # the agent sees the failing tests too
            (snap / f).parent.mkdir(parents=True, exist_ok=True)
            (snap / f).write_text(_git(repo, "show", f"{t.fix}:{f}"))
        with tarfile.open(d / "environment" / "repo.tar.gz", "w:gz") as tf:
            for p in sorted(snap.rglob("*")):
                tf.add(p, arcname=str(p.relative_to(snap)), recursive=False)
    for f in t.test_files:
        (d / "tests" / "files" / f).parent.mkdir(parents=True, exist_ok=True)
        (d / "tests" / "files" / f).write_text(_git(repo, "show", f"{t.fix}:{f}"))
    (d / "tests" / "test.sh").write_text(_test_sh(t))
    (d / "tests" / "test.sh").chmod(0o755)
    sources = [s for s in _git(repo, "diff", "--name-only", t.parent, t.fix).split() if not TEST_PATH.search(s)]
    (d / "solution" / "fix.patch").write_text(_git(repo, "diff", t.parent, t.fix, "--", *sources))
    (d / "solution" / "solve.sh").write_text("#!/bin/bash\nset -eu\ncd /app\ngit apply /solution/fix.patch\n")
    (d / "solution" / "solve.sh").chmod(0o755)
    return d


def export(repo: Path, out: Path, since: str, limit: int = 20, paths: list[str] | None = None,
           python: str | None = None, agent_network: str = "allowlist") -> dict:
    tasks = find_tasks(repo, since, python=python, limit=limit, paths=paths)
    out.mkdir(parents=True, exist_ok=True)
    written = [str(write_task(repo, t, out, paths, agent_network)) for t in tasks]
    public = sum(1 for t in tasks if t.remotes)
    return {"tasks": len(written), "dir": str(out), "on_a_remote": public, "local_only": len(written) - public,
            "agent_network": agent_network,
            "note": "Tasks whose fix is on a remote can only support a pilot, not a causal claim."}
