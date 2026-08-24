"""A run of synthesis failures stops the pass; isolated ones do not.

res_081, diagnosed free from the run log: on 2026-08-24 the provider hit its
session limit at cluster ~378 of 433. Every later chairman call returned the
quota notice, which carries no routing-json fence, so all 47 were correctly
refused — and the loop kept dispatching for another 55 clusters, spending quota
it no longer had on calls that could not succeed. The same error string then
reached the distill stage and became `core.md`.

The distribution is the whole diagnosis: clusters 0-377 failed twice (0.53%),
clusters 378-432 failed 47 times. That is not a rate, it is a regime change.

Isolated failures must NOT stop a three-hour run. Five in a row must.
"""
from __future__ import annotations

from trinity_local.commands import dream


class _Rig:
    """Drives _synthesize_all with a scripted failure pattern."""

    def __init__(self, monkeypatch, tmp_path, pattern):
        self.calls = 0
        self.pattern = pattern          # list[bool]: True = this call fails
        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        monkeypatch.setattr(dream, "_cluster_fingerprint", lambda c: f"fp{c}")
        monkeypatch.setattr(dream, "cluster_to_synthesis_args",
                            lambda c: {"responses": []}, raising=False)
        import trinity_local.cross_provider_pairs as cpp
        monkeypatch.setattr(cpp, "cluster_to_synthesis_args", lambda c: {"responses": []})
        import trinity_local.mcp_server as ms

        async def _synth(args, responses):
            i = self.calls
            self.calls += 1
            if i < len(self.pattern) and self.pattern[i]:
                raise ValueError("save_council_outcome refused: routing_label is None")
            return {}

        monkeypatch.setattr(ms, "_synthesize_responses", _synth)


class TestTheBreaker:
    def test_isolated_failures_do_not_stop_the_run(self, monkeypatch, tmp_path):
        # the real pre-event pattern: rare, scattered
        pattern = [False] * 40
        pattern[7] = pattern[23] = True
        rig = _Rig(monkeypatch, tmp_path, pattern)
        done, failed = dream._synthesize_all(list(range(40)), None)
        assert rig.calls == 40, "an isolated failure must not abort a long run"
        assert done == 38 and failed == 2

    def test_a_run_of_failures_aborts(self, monkeypatch, tmp_path):
        # quota exhaustion: healthy, then everything fails
        pattern = [False] * 10 + [True] * 30
        rig = _Rig(monkeypatch, tmp_path, pattern)
        done, failed = dream._synthesize_all(list(range(40)), None)
        assert failed == dream._CONSECUTIVE_FAILURE_ABORT
        assert rig.calls == 10 + dream._CONSECUTIVE_FAILURE_ABORT, (
            f"kept dispatching after {failed} consecutive failures — that is the "
            "2026-08-24 defect: 55 clusters of quota spent on calls that could "
            "not succeed"
        )
        assert done == 10, "completed work must still be reported and kept"

    def test_the_counter_resets_on_success(self, monkeypatch, tmp_path):
        """Four failures, a success, four more — must NOT abort."""
        pattern = [True] * 4 + [False] + [True] * 4 + [False] * 10
        rig = _Rig(monkeypatch, tmp_path, pattern)
        dream._synthesize_all(list(range(19)), None)
        assert rig.calls == 19, "consecutive means consecutive, not cumulative"

    def test_the_threshold_is_far_above_the_measured_isolated_rate(self):
        # 2 failures in 378 clusters before the event; five in a row is ~1e-11
        assert dream._CONSECUTIVE_FAILURE_ABORT >= 3
        assert dream._CONSECUTIVE_FAILURE_ABORT <= 10, (
            "too high and the breaker never fires before the quota is gone")
