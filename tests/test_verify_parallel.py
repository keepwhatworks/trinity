"""verify's reads go out at once and the tests run during them (2026-10-09: a review took
the SUM of the reads plus the tests; Gemini 236 s + Codex 14 s + tests 28 s). Barriers make
the concurrency deterministic: run sequentially, the barrier times out and the test fails."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from trinity_local import verify as V

J = {"id": "j", "kind": "judgment", "statement": "addresses the context", "blocking": True}


def _cfg(*names):
    return SimpleNamespace(providers={n: SimpleNamespace(enabled=True) for n in names})


def _vote(name):
    out = json.dumps({"votes": {"j": "PASS"}, "why": ""})
    return SimpleNamespace(stdout=out, stderr="", returncode=0, usage={}), SimpleNamespace(model=name, effort=None)


def test_the_reads_are_dispatched_concurrently(monkeypatch, tmp_path):
    """MUTATION: dispatch the readers in a plain loop again and the barrier breaks."""
    barrier = threading.Barrier(2, timeout=5)

    def fake(name, prompt, cwd, config, effort):
        barrier.wait()
        return _vote(name)
    monkeypatch.setattr(V, "_dispatch", fake)
    crit = [V.Criterion.from_dict(J)]
    panel, reads = V.read_panel(crit, "diff", "ctx", tmp_path, providers=("codex", "antigravity"),
                                config=_cfg("codex", "antigravity"))
    assert [r.provider for r in reads] == ["codex", "antigravity"]   # provider order kept
    assert all(r.error is None for r in reads) and panel.votes == {"codex": True, "antigravity": True}


def test_the_tests_run_while_the_panel_reads(monkeypatch, tmp_path):
    """MUTATION: run the kernel after the panel again and both barrier waits break."""
    barrier = threading.Barrier(2, timeout=5)
    seen = []

    def fake(name, prompt, cwd, config, effort):
        barrier.wait()
        seen.append(prompt)
        return _vote(name)
    real_kernel = V.run_kernel

    def kernel(criteria, cwd, timeout=600, env=None):
        barrier.wait()
        return real_kernel(criteria, cwd, timeout=timeout, env=env)
    monkeypatch.setattr(V, "_dispatch", fake)
    monkeypatch.setattr(V, "run_kernel", kernel)
    crit = [J, {"id": "t", "kind": "test", "statement": "s", "command": "true"}]
    out = V.verify(crit, "diff", "ctx", tmp_path, providers=("codex",), config=_cfg("codex"),
                   differential=False, quality=False)
    assert out["kernel"]["green"] is True and out["panel"]["votes"] == {"codex": True}
    assert "true" not in seen[0].split("THE CHANGE")[0]   # the prompt carries no kernel output
