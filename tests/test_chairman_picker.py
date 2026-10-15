"""Chairman auto-selection: a fixed order, Claude then Codex (§ task 32).

The sigmoid blend of personal routing and global benchmarks these tests used to
cover is gone (ranker/chairman_picker.py says why); tests/test_chair_fixed_order.py
covers the installed-build case that blend got wrong.
"""
from __future__ import annotations

import sys
from pathlib import Path

from trinity_local.ranker.chairman_picker import (
    chairman_pick_reason,
    predict_strongest_chairman,
)


class TestPicker:
    def test_empty_available_returns_empty(self):
        assert predict_strongest_chairman("x", available_providers=[]) == ""

    def test_only_available_providers_are_picked(self):
        assert predict_strongest_chairman("fix it", available_providers=["codex"]) == "codex"

    def test_the_task_does_not_move_the_chair(self):
        picks = {predict_strongest_chairman(t, available_providers=["antigravity", "codex", "claude"])
                 for t in ("fix this failing test", "write a blog post", "research X", "hi")}
        assert picks == {"claude"}

    def test_an_unknown_provider_chairs_only_when_alone(self):
        assert predict_strongest_chairman("x", available_providers=["grok", "codex"]) == "codex"
        assert predict_strongest_chairman("x", available_providers=["grok"]) == "grok"


class TestPickReason:
    def test_reports_the_fixed_order(self):
        r = chairman_pick_reason("fix this failing test", available_providers=["codex", "claude"])
        assert r["chairman"] == "claude" and r["source"] == "fixed_order" and r["task_type"]

    def test_reports_none_when_empty_available(self):
        assert chairman_pick_reason("x", available_providers=[]) == {
            "chairman": "", "source": "none", "task_type": ""}


class TestTheChairIsTheSameEveryLaunch:
    """The chair must not depend on per-process hash salting: the blend once
    picked codex under PYTHONHASHSEED=99 and antigravity under 0 for one tie."""

    _INNER = (
        "import sys; sys.path.insert(0, %r)\n"
        "from trinity_local.ranker.chairman_picker import predict_strongest_chairman as p\n"
        "print(p('fix it', available_providers=list({'antigravity', 'codex', 'claude'})))\n"
    )

    def test_same_chair_under_every_hash_seed(self):
        import os
        import subprocess
        src_root = str(Path(__file__).resolve().parents[1] / "src")
        picks = set()
        for seed in ("0", "1", "42", "99", "31337"):
            out = subprocess.run([sys.executable, "-c", self._INNER % src_root],
                                 env=dict(os.environ, PYTHONHASHSEED=seed),
                                 capture_output=True, text=True)
            assert out.returncode == 0, out.stderr
            picks.add(out.stdout.strip())
        assert picks == {"claude"}, picks
