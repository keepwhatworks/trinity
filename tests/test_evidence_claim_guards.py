"""Guards for the EVIDENCE-claim half of the canonical-placeholder mechanism.

`scripts/render_docs.py` has always machine-checked the inventory counts (test
count, CLI subcommand count, version…) and nothing else. The ~25 bare
percentages in CLAUDE.md — the numbers the whole product rests on — were prose.
`src/trinity_local/evidence_claims.py` closes that gap by recomputing them from
`~/.trinity/disagreement_ledger/summary.json`.

TWO LAYERS, and the split is the point
--------------------------------------
The ledger artifact lives in the user's ~/.trinity. It does not exist in CI, on
a fresh clone, or under an isolated `TRINITY_HOME`. So:

  Layer 1 (this file, unconditional): every extractor, the whole refusal path,
  and the doc↔registry wiring are exercised against a COMMITTED SYNTHETIC
  FIXTURE. These run everywhere. A rotted registry, a broken extractor, an
  unwrapped placeholder, or a loosened floor is RED in CI with no ~/.trinity at
  all.

  Layer 2 (`TestLiveLedgerAgreement`, conditional): the values actually written
  into CLAUDE.md are compared against the live ledger. This is the only test
  that can skip, it skips ONLY in the `absent` state, and its skip reason says
  UNVERIFIED out loud. In the `refused` state it FAILS — an artifact that
  exists but cannot back the numbers the doc is asserting is a red condition,
  not a missing-input condition.

That is what stops a skip from reading as a pass: the mechanism is proven
unconditionally, and only the live-corpus agreement is skippable.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE = _ROOT / "tests" / "fixtures" / "disagreement_ledger_summary.sample.json"
_CLAUDE_MD = _ROOT / "CLAUDE.md"

# The values the fixture must produce. Written out longhand (not recomputed in
# the test) so a bug in the extractor cannot agree with itself.
_EXPECTED_FROM_FIXTURE = {
    "ledger_opus48_win_pct": "75%",
    "ledger_opus48_win_pct_1dp": "75.0%",
    "ledger_opus48_record": "30-10",
    "ledger_gemini31_win_pct": "20%",
    "ledger_gpt55_win_pct": "53%",
    "ledger_gpt55_win_pct_1dp": "52.5%",
    "ledger_gpt55_xhigh_win_pct": "65%",
    "ledger_gpt55_xhigh_win_pct_1dp": "65.0%",
    "ledger_gpt55_xhigh_record": "13W-7L",
    # Counted, not looked up: 4 cells clear MIN_TALLY_N in the fixture (gpt55
    # xhigh n=20 + high n=15, opus high n=24 + low n=12) and `medium` at n=5
    # does not. Both numbers differ from the live corpus (3 cells, max 1 level)
    # on purpose — a fixture mirroring the live shape could not tell a real
    # counter from a hardcoded constant.
    "ledger_effort_cells_n": "4",
    "ledger_effort_max_levels_per_model": "2",
    "ledger_chairman_agreement_pct": "72.5%",
    "ledger_resolved_n": "90",
}

# Claims that MUST stay wrapped in CLAUDE.md. Unwrapping one back to a bare
# number silently returns it to the unguarded class this whole mechanism exists
# to eliminate, and nothing else would notice.
_MUST_BE_WRAPPED_IN_CLAUDE_MD = (
    "ledger_opus48_win_pct",
    "ledger_opus48_record",
    "ledger_gemini31_win_pct",
    "ledger_gpt55_win_pct",
    "ledger_chairman_agreement_pct",
    "ledger_resolved_n",
)

_PLACEHOLDER = re.compile(
    r"<!--\s*canonical:(\w+)\s*-->(.*?)<!--\s*/canonical\s*-->", re.DOTALL
)


def _fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _doc_placeholders(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(text)]


def _docs_with_ledger_placeholders() -> list[Path]:
    out: list[Path] = []
    for pattern in ("*.md", "*.html"):
        for path in _ROOT.rglob(pattern):
            if any(
                skip in str(path)
                for skip in (".venv", "node_modules", "/build/", ".egg-info", ".pytest_cache")
            ):
                continue
            if "canonical:ledger_" in path.read_text(encoding="utf-8", errors="ignore"):
                out.append(path)
    return out


# ───────────────────────────────────────────────────────────────────────
# Layer 1 — unconditional, fixture-driven. Runs in CI with no ~/.trinity.
# ───────────────────────────────────────────────────────────────────────

class TestExtractorsAgainstFixture:
    def test_fixture_yields_verified_with_every_claim(self):
        from trinity_local.evidence_claims import CLAIM_NAMES, VERIFIED, evidence_status

        state, values, reason = evidence_status(_fixture())
        assert state == VERIFIED, reason
        assert set(values) == set(CLAIM_NAMES), (
            "every registered claim must render from a healthy summary; "
            f"missing={sorted(set(CLAIM_NAMES) - set(values))}"
        )

    def test_each_claim_renders_the_expected_string(self):
        from trinity_local.evidence_claims import evidence_status

        _, values, _ = evidence_status(_fixture())
        assert values == _EXPECTED_FROM_FIXTURE

    def test_rounding_is_half_up_not_bankers(self):
        """21/40 = 52.5%. Python's round() gives 52 (banker's rounding to even);
        the correct published figure is 53. A win rate landing exactly on a half
        must not render one point low and then be frozen there by this guard."""
        from trinity_local.evidence_claims import evidence_status

        _, values, _ = evidence_status(_fixture())
        assert values["ledger_gpt55_win_pct"] == "53%"
        assert round(21 * 100 / 40) == 52, "the banker's-rounding trap this asserts against"

    def test_percentages_recompute_from_raw_counts_not_stored_win_rate(self):
        """`records[...].win_rate` is stored rounded to 3dp. Corrupt it and the
        rendered percent must not move — the extractor reads w and l."""
        from trinity_local.evidence_claims import evidence_status

        summary = _fixture()
        summary["records"]["claude · opus · 4.8"]["win_rate"] = 0.111
        _, values, _ = evidence_status(summary)
        assert values["ledger_opus48_win_pct"] == "75%"


class TestRefusalPath:
    """The degenerate-data tests: each proves the green is REFUSED, not
    downgraded, not silently partial. `refused` must plant ZERO values —
    a half-rendered doc is worse than an unrendered one."""

    def test_refuses_when_tally_not_trustworthy(self):
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["tally_trustworthy"] = False
        state, values, reason = evidence_status(summary)
        assert state == REFUSED
        assert values == {}
        assert "tally_trustworthy" in reason

    def test_refuses_when_trustworthy_flag_is_missing_entirely(self):
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        del summary["tally_trustworthy"]
        state, values, _ = evidence_status(summary)
        assert state == REFUSED
        assert values == {}

    def test_refuses_when_a_source_key_disappears(self):
        """A renamed model key must not quietly drop a guarded number back to
        unguarded prose — it must stop the whole render."""
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["records"].pop("claude · opus · 4.8")
        state, values, reason = evidence_status(summary)
        assert state == REFUSED
        assert values == {}
        assert "ledger_opus48_win_pct" in reason

    def test_refuses_when_a_cell_falls_below_the_engine_floor(self):
        from trinity_local.disagreement_ledger import MIN_TALLY_N
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["records"]["claude · opus · 4.8"] = {"w": MIN_TALLY_N - 6, "l": 5}
        assert (MIN_TALLY_N - 6) + 5 < MIN_TALLY_N, "fixture must land under the floor"
        state, values, _ = evidence_status(summary)
        assert state == REFUSED
        assert values == {}

    def test_refuses_when_an_EFFORT_subcell_falls_below_the_engine_floor(self):
        """The sibling of the records[] floor above, and the one the first cut
        left unguarded: mutation testing showed `_effort_cell`'s MIN_TALLY_N
        check could be deleted with every test still green. The effort sub-cell
        is the THINNEST number CLAUDE.md quotes (the xhigh record is 17W-12L),
        so it is the one most likely to fall under the floor after a rebuild —
        and it must take the whole render down with it, not publish quietly."""
        from trinity_local.disagreement_ledger import MIN_TALLY_N
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["effort_breakdown"]["openai · flagship · 5.5"]["xhigh"] = {
            "w": 3, "l": 4,
        }
        assert 3 + 4 < MIN_TALLY_N, "fixture must land under the engine floor"
        state, values, reason = evidence_status(summary)
        assert state == REFUSED
        assert values == {}
        assert "ledger_gpt55_xhigh_win_pct" in reason

    def test_refuses_when_resolved_below_the_engine_k4_floor(self):
        from trinity_local.disagreement_ledger import K4_MIN_RESOLVED
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["resolved"] = K4_MIN_RESOLVED - 1
        state, values, _ = evidence_status(summary)
        assert state == REFUSED
        assert values == {}

    def test_refuses_on_an_empty_summary(self):
        from trinity_local.evidence_claims import REFUSED, evidence_status

        state, values, _ = evidence_status({})
        assert state == REFUSED
        assert values == {}

    def test_refuses_when_an_extractor_throws(self):
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["records"]["claude · opus · 4.8"] = {"w": "not-a-number", "l": 3}
        state, values, _ = evidence_status(summary)
        assert state == REFUSED
        assert values == {}

    def test_refuses_when_there_is_no_effort_evidence_at_all(self):
        """An empty `effort_breakdown` must REFUSE, not render a confident "0".

        A corpus with no effort cells cannot back a sentence ABOUT the effort
        cells. Returning 0 would let CLAUDE.md publish a count that reads like a
        measurement while nothing was measured — the green-over-degenerate shape
        one layer up from the bug that motivated these two claims."""
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["effort_breakdown"] = {}
        state, values, reason = evidence_status(summary)
        assert state == REFUSED
        assert values == {}
        assert "ledger_effort_cells_n" in reason

    def test_refuses_when_every_effort_cell_is_under_the_floor(self):
        """Same refusal when cells exist but all are thinner than the engine's
        own MIN_TALLY_N — present-but-degenerate, not present-and-countable."""
        from trinity_local.disagreement_ledger import MIN_TALLY_N
        from trinity_local.evidence_claims import REFUSED, evidence_status

        summary = _fixture()
        summary["effort_breakdown"] = {"openai · flagship · 5.5": {"xhigh": {"w": 2, "l": 3}}}
        assert 2 + 3 < MIN_TALLY_N, "fixture must land under the engine floor"
        state, values, _ = evidence_status(summary)
        assert state == REFUSED
        assert values == {}


class TestEffortShapeIsCountedNotAssumed:
    """The two shape claims exist because CLAUDE.md, both `mcp_server.py`
    comment blocks and a guard docstring asserted the clean tally surfaces
    "exactly ONE" effort sub-cell. That was read off the single key the sentence
    was about; the live artifact carried three. These pin the counting."""

    def test_thin_cells_are_excluded_from_the_count(self):
        """The engine filters sub-cells under MIN_TALLY_N, so a thin cell can
        only arrive by hand-edit — and must still not inflate the count."""
        from trinity_local.evidence_claims import evidence_status

        _, values, _ = evidence_status(_fixture())
        before = int(values["ledger_effort_cells_n"])

        summary = _fixture()
        summary["effort_breakdown"]["claude · opus · 4.8"]["medium"] = {"w": 20, "l": 9}
        _, promoted, _ = evidence_status(summary)
        assert int(promoted["ledger_effort_cells_n"]) == before + 1, (
            "fattening the below-floor `medium` cell past MIN_TALLY_N must "
            "change the count — otherwise the count is not reading that cell"
        )

    def test_the_count_tracks_the_artifact_rather_than_a_constant(self):
        from trinity_local.evidence_claims import evidence_status

        summary = _fixture()
        summary["effort_breakdown"].pop("claude · opus · 4.8")
        _, values, _ = evidence_status(summary)
        assert values["ledger_effort_cells_n"] == "2", (
            "dropping a two-cell model must drop the count to the gpt55 pair"
        )

    def test_max_levels_is_per_model_not_a_repo_wide_total(self):
        """The load-bearing fact is that no model has a SIBLING level. Spreading
        the same number of cells across more models must drive it to 1 — a
        repo-wide total would stay put and the "no contrast" sentence would keep
        rendering while a contrast existed."""
        from trinity_local.evidence_claims import evidence_status

        _, values, _ = evidence_status(_fixture())
        assert values["ledger_effort_max_levels_per_model"] == "2"

        summary = _fixture()
        summary["effort_breakdown"] = {
            "openai · flagship · 5.5": {"xhigh": {"w": 13, "l": 7}},
            "claude · opus · 4.8": {"high": {"w": 18, "l": 6}},
            "google · pro · 3.1": {"high": {"w": 30, "l": 57}},
        }
        _, spread, _ = evidence_status(summary)
        assert spread["ledger_effort_cells_n"] == "3"
        assert spread["ledger_effort_max_levels_per_model"] == "1", (
            "three cells across three models is the LIVE shape: no siblings"
        )


class TestThreeStatesAreDistinguishable:
    """`absent` and `refused` must never collapse into each other or into a
    pass. Conflating "we could not look" with "we looked and it is fine" is the
    bug class this module was written against."""

    def test_states_are_three_distinct_strings(self):
        from trinity_local.evidence_claims import ABSENT, REFUSED, VERIFIED

        assert len({ABSENT, REFUSED, VERIFIED}) == 3

    def test_absent_when_no_artifact_on_disk(self, tmp_path, monkeypatch):
        from trinity_local.evidence_claims import ABSENT, evidence_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        state, values, reason = evidence_status()
        assert state == ABSENT
        assert values == {}
        assert "UNVERIFIED" in reason
        assert str(tmp_path) in reason, "the skip reason must name the missing path"

    def test_absent_is_not_reported_as_refused(self, tmp_path, monkeypatch):
        from trinity_local.evidence_claims import ABSENT, REFUSED, evidence_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        state, _, _ = evidence_status()
        assert state == ABSENT and state != REFUSED

    def test_unreadable_artifact_reads_absent_not_verified(self, tmp_path, monkeypatch):
        from trinity_local.evidence_claims import VERIFIED, evidence_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        d = tmp_path / "disagreement_ledger"
        d.mkdir()
        (d / "summary.json").write_text("{not json", encoding="utf-8")
        state, values, _ = evidence_status()
        assert state != VERIFIED
        assert values == {}

    @pytest.mark.parametrize(
        "corrupt",
        ["{not json", "", "[1, 2, 3]", '"a string"', "null"],
        ids=["truncated", "empty", "json-array", "json-string", "json-null"],
    )
    def test_a_corrupt_artifact_REFUSES_it_does_not_skip(
        self, tmp_path, monkeypatch, corrupt
    ):
        """THE distinction, found by mutation-testing the first cut of this
        module: a summary.json that EXISTS but is truncated / empty / not an
        object is PRESENT AND DEGENERATE. The first cut folded it into the same
        `None` as "no file" and returned ABSENT — and ABSENT is the one state
        that SKIPS. So an interrupted `trust --build` that half-wrote the file
        would have made the live agreement check skip while CLAUDE.md went on
        asserting numbers nothing had verified. That is the exact
        skip-that-reads-as-a-pass this module exists to prevent, reproduced
        inside the mechanism meant to prevent it.
        """
        from trinity_local.evidence_claims import ABSENT, REFUSED, evidence_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        d = tmp_path / "disagreement_ledger"
        d.mkdir()
        (d / "summary.json").write_text(corrupt, encoding="utf-8")
        state, values, reason = evidence_status()
        assert state == REFUSED, (
            f"a corrupt ledger returned {state!r}; if that is ABSENT the live "
            "guard SKIPS on a corrupt artifact"
        )
        assert state != ABSENT
        assert values == {}
        assert "EXISTS" in reason

    def test_missing_and_corrupt_do_not_share_a_state(self, tmp_path, monkeypatch):
        """Pins the pair, not just each side — the bug was that one code path
        produced both answers."""
        from trinity_local.evidence_claims import ABSENT, REFUSED, evidence_status

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        assert evidence_status()[0] == ABSENT
        d = tmp_path / "disagreement_ledger"
        d.mkdir()
        (d / "summary.json").write_text("[]", encoding="utf-8")
        assert evidence_status()[0] == REFUSED

    def test_read_ledger_summary_reports_existence_separately(self, tmp_path, monkeypatch):
        """The two facts must be separately observable, so no future caller can
        re-collapse them behind a single `None`."""
        from trinity_local.evidence_claims import read_ledger_summary

        monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
        assert read_ledger_summary() == (False, None)
        d = tmp_path / "disagreement_ledger"
        d.mkdir()
        (d / "summary.json").write_text("{oops", encoding="utf-8")
        assert read_ledger_summary() == (True, None)
        (d / "summary.json").write_text('{"a": 1}', encoding="utf-8")
        assert read_ledger_summary() == (True, {"a": 1})


class TestRendererWiring:
    """The renderer must plant evidence values ONLY in the verified state, and
    must leave the placeholders byte-identical otherwise."""

    def _renderer(self):
        path = _ROOT / "scripts" / "render_docs.py"
        spec = importlib.util.spec_from_file_location("render_docs_evidence_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_renderer_exposes_the_three_state_hook(self):
        module = self._renderer()
        assert hasattr(module, "evidence_state")

    def test_unknown_placeholder_is_left_untouched(self, tmp_path):
        """This is the mechanism that makes `absent` safe: with no evidence
        values in hand, render_file must not blank or rewrite the placeholder."""
        module = self._renderer()
        doc = tmp_path / "doc.md"
        original = "score <!-- canonical:ledger_opus48_win_pct -->68%<!-- /canonical --> here"
        doc.write_text(original, encoding="utf-8")
        changed, count = module.render_file(doc, {})
        assert changed is False
        assert count == 0
        assert doc.read_text(encoding="utf-8") == original

    def test_require_evidence_exits_nonzero_on_both_unverified_states(self):
        """`--require-evidence` is the publishing-boundary control: it converts
        "nothing confirmed these numbers" into a failing exit. Exit 2 is kept
        distinct from `--check`'s exit 1 so a caller can tell "docs drifted"
        from "the evidence was never checked"."""
        module = self._renderer()
        assert module.evidence_exit_code("absent", True) == 2
        assert module.evidence_exit_code("refused", True) == 2
        assert module.evidence_exit_code("verified", True) is None

    def test_require_evidence_is_opt_in_and_off_by_default(self):
        """Default OFF is deliberate — the artifact is absent in CI, so a
        default-on flag would red every clone. Pin the default so the reason it
        is off stays a decision rather than becoming an accident."""
        module = self._renderer()
        for state in ("absent", "refused", "verified"):
            assert module.evidence_exit_code(state, False) is None
        parser_default = None
        import argparse as _argparse

        p = _argparse.ArgumentParser()
        p.add_argument("--require-evidence", action="store_true")
        parser_default = p.parse_args([]).require_evidence
        assert parser_default is False

    def test_verified_values_do_get_planted(self, tmp_path):
        module = self._renderer()
        doc = tmp_path / "doc.md"
        doc.write_text(
            "score <!-- canonical:ledger_opus48_win_pct -->OLD<!-- /canonical --> here",
            encoding="utf-8",
        )
        changed, count = module.render_file(doc, {"ledger_opus48_win_pct": "75%"})
        assert changed is True and count == 1
        assert "-->75%<!--" in doc.read_text(encoding="utf-8")


class TestDocRegistryWiring:
    def test_every_ledger_placeholder_in_docs_is_registered(self):
        """A `canonical:ledger_*` placeholder with no registry entry never
        renders — it would sit in the doc looking guarded while being frozen
        prose. That is strictly worse than an unwrapped number."""
        from trinity_local.evidence_claims import CLAIM_NAMES

        unregistered: list[str] = []
        for path in _docs_with_ledger_placeholders():
            for name, _ in _doc_placeholders(path):
                if name.startswith("ledger_") and name not in CLAIM_NAMES:
                    unregistered.append(f"{path.relative_to(_ROOT)}: {name}")
        assert not unregistered, (
            "these ledger placeholders have no entry in evidence_claims.CLAIMS, "
            "so they will never be re-rendered:\n  " + "\n  ".join(unregistered)
        )

    def test_core_claims_are_still_wrapped_in_claude_md(self):
        names = {name for name, _ in _doc_placeholders(_CLAUDE_MD)}
        missing = [n for n in _MUST_BE_WRAPPED_IN_CLAUDE_MD if n not in names]
        assert not missing, (
            "these evidence claims were unwrapped back to bare prose in "
            f"CLAUDE.md: {missing}. A bare number is unguarded by construction."
        )

    def test_the_gate_reuses_the_engines_pre_registered_floors(self):
        """The floors must stay the disagreement ledger's own constants. A
        future edit that swaps in a private, looser floor would let a thin cell
        publish — so pin the wiring, not just the behaviour."""
        source = (_ROOT / "src" / "trinity_local" / "evidence_claims.py").read_text(
            encoding="utf-8"
        )
        assert "MIN_TALLY_N" in source
        assert "K4_MIN_RESOLVED" in source
        assert "tally_trustworthy" in source


# ───────────────────────────────────────────────────────────────────────
# Layer 2 — live corpus. The ONLY skippable test in this file.
# ───────────────────────────────────────────────────────────────────────

class TestLiveLedgerAgreement:
    def test_claude_md_numbers_agree_with_the_live_ledger(self):
        from trinity_local.evidence_claims import (
            ABSENT,
            REFUSED,
            evidence_status,
            ledger_summary_path,
        )

        state, values, reason = evidence_status()

        # A SYNTHETIC ledger is not a live one. Browser tests run
        # scripts/seed_synthetic_home.py into the shared isolated TRINITY_HOME,
        # and this guard would then read that FIXTURE as if it were the user's
        # corpus and compare CLAUDE.md's published numbers against it. A fixture
        # can never back those numbers, so the REFUSED assertion below fired on
        # POLLUTION rather than on drift — and only in the slow shard, because
        # the polluting test is slow-marked, so no gate anyone ran ever saw it
        # (found 2026-08-01, the first TRINITY_SLOW=1 run of the session).
        # Treat it exactly like ABSENT: there is no live corpus here to check.
        summary_path = ledger_summary_path()
        if summary_path.exists():
            try:
                import json as _json

                if _json.loads(summary_path.read_text(encoding="utf-8")).get(
                    "synthetic_fixture"
                ):
                    pytest.skip(
                        "SKIPPED, NOT PASSED — the ledger at "
                        f"{summary_path} carries `synthetic_fixture: true`, i.e. it "
                        "was written by scripts/seed_synthetic_home.py for a browser "
                        "fixture. CLAUDE.md's numbers describe the USER'S corpus and "
                        "cannot be checked against a fixture. Run this against a real "
                        "home to exercise it."
                    )
            except (OSError, ValueError):
                pass  # unreadable → fall through to the normal ABSENT/REFUSED paths

        if state == ABSENT:
            pytest.skip(
                "SKIPPED, NOT PASSED — no live disagreement ledger at "
                f"{ledger_summary_path()}, so CLAUDE.md's evidence numbers were "
                "NOT checked against anything. The extractors and the whole "
                "refusal path are still covered unconditionally by the fixture "
                "tests above; only this agreement check needs the artifact. "
                "Build it with `trinity-local trust --build`."
            )

        assert state != REFUSED, (
            "the live ledger EXISTS but cannot back the numbers CLAUDE.md is "
            f"asserting: {reason}. Publishing them anyway is exactly the "
            "green-over-degenerate failure this guard was added to stop."
        )

        drift: list[str] = []
        for name, rendered in _doc_placeholders(_CLAUDE_MD):
            if name not in values:
                continue
            if rendered != values[name]:
                drift.append(f"{name}: doc says {rendered!r}, ledger says {values[name]!r}")
        assert not drift, (
            "CLAUDE.md has drifted from the live disagreement ledger:\n  "
            + "\n  ".join(drift)
            + "\nRe-render with `.venv/bin/python scripts/render_docs.py`."
        )

    def test_live_run_actually_compared_something(self):
        """Companion to the above: when the artifact IS present, at least one
        guarded placeholder must have been compared. A CLAUDE.md that lost all
        its placeholders would otherwise make the agreement test vacuously
        green."""
        from trinity_local.evidence_claims import VERIFIED, evidence_status

        state, values, _ = evidence_status()
        if state != VERIFIED:
            pytest.skip(f"live ledger not verified (state={state}) — nothing to compare")
        compared = [n for n, _ in _doc_placeholders(_CLAUDE_MD) if n in values]
        assert compared, (
            "the live ledger verified but CLAUDE.md contains ZERO guarded "
            "evidence placeholders — the agreement test would have passed "
            "having compared nothing"
        )
