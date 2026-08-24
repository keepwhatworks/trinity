"""A member timeout that selects on SPEED manufactures a fake council.

council_2797722d0cf6e1e3: a long premise-review task killed both claude and
codex at 480.1s while Gemini 3.7 Flash finished. The council reported one
member and named it the winner. The two that "failed" had not failed — they
were still thinking — so the surviving verdict was selected by latency and
nothing else.

The default stays at 8 minutes on purpose: raising it globally lets a genuinely
stuck member hold up every council. What changes is that a task known to be hard
can buy headroom.
"""

from __future__ import annotations

import importlib


def _reload():
    import trinity_local.providers as P

    return importlib.reload(P)


class TestTheCeilingIsOverridable:
    def test_env_var_raises_it(self, monkeypatch):
        monkeypatch.setenv("TRINITY_PROVIDER_TIMEOUT_SECONDS", "1500")
        assert _reload().DEFAULT_PROVIDER_TIMEOUT_SECONDS == 1500.0

    def test_default_is_unchanged_when_unset(self, monkeypatch):
        monkeypatch.delenv("TRINITY_PROVIDER_TIMEOUT_SECONDS", raising=False)
        assert _reload().DEFAULT_PROVIDER_TIMEOUT_SECONDS == 480.0

    def test_garbage_falls_back_rather_than_crashing_the_council(self, monkeypatch):
        monkeypatch.setenv("TRINITY_PROVIDER_TIMEOUT_SECONDS", "")
        assert _reload().DEFAULT_PROVIDER_TIMEOUT_SECONDS == 480.0


class TestASoloCouncilIsNotAContest:
    def test_the_ledger_cannot_admit_a_one_member_council(self):
        """The trust tally requires both sides occupied by DISTINCT labs.

        This is what kept the fake council out of the per-model rates: with one
        member there are no disagreed_claims, so nothing reaches the key. Pinned
        because it is the invariant that made a timeout bug harmless instead of
        silently moving a published number.
        """
        import pathlib

        src = (pathlib.Path(__file__).resolve().parent.parent / "src" / "trinity_local"
               / "disagreement_ledger.py").read_text()
        assert "single lab arguing with itself" in src, (
            "the both-sides-occupied guard is the thing standing between a "
            "degraded council and the trust ledger — do not remove it"
        )
