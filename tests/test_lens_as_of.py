"""The lens as of a past date (amd_0313): the clock pin, the dated lens history, and the
registered mutation test — changing anything after the cutoff leaves the historical lens
byte-identical, while changing something before it does not.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CUTOFF = "2026-06-01T00:00:00+00:00"


def _hq132():
    spec = importlib.util.spec_from_file_location("hq132", REPO / "internal" / "experiments" / "hq132_lens_as_of.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestClock:
    def test_unset_is_the_wall_clock(self, monkeypatch):
        from datetime import datetime, timezone

        from trinity_local.utils import now_dt
        monkeypatch.delenv("TRINITY_CLOCK", raising=False)
        assert abs((now_dt() - datetime.now(timezone.utc)).total_seconds()) < 5

    def test_pin_moves_every_reader(self, monkeypatch):
        from trinity_local.memory.index import _recency_score
        from trinity_local.utils import now_dt, now_iso
        monkeypatch.setenv("TRINITY_CLOCK", CUTOFF)
        assert now_iso() == CUTOFF
        assert now_dt().isoformat() == CUTOFF
        # Sampling's recency is read on the pinned clock: a prompt 30 days before the pin
        # scores one half-life, whatever today is.
        assert _recency_score("2026-05-02T00:00:00+00:00") == pytest.approx(0.5)

    def test_malformed_pin_raises(self, monkeypatch):
        from trinity_local.utils import now_iso
        monkeypatch.setenv("TRINITY_CLOCK", "June first")
        with pytest.raises(ValueError):
            now_iso()


class TestLensHistory:
    def _lens(self, text: str, registry: str = "[]"):
        from trinity_local.me.lens_registry import registry_path
        from trinity_local.state_paths import lens_path
        lens_path().parent.mkdir(parents=True, exist_ok=True)
        lens_path().write_text(text, encoding="utf-8")
        registry_path().parent.mkdir(parents=True, exist_ok=True)
        registry_path().write_text(registry, encoding="utf-8")

    def test_no_lens_refuses(self, patch_trinity_home):
        from trinity_local.me.lens_history import snapshot_lens
        assert snapshot_lens() == {"ok": False, "reason": "no lens yet"}

    def test_copies_once_per_change_and_reads_back_by_date(self, patch_trinity_home, monkeypatch):
        from trinity_local.me.lens_history import history_dir, lens_as_of, snapshot_lens
        monkeypatch.setenv("TRINITY_CLOCK", "2026-06-01T00:00:00+00:00")
        self._lens("# Lens v1\n")
        first = snapshot_lens()
        assert first["ok"] and not first.get("unchanged")
        assert snapshot_lens()["unchanged"], "an unchanged lens must not leave a second copy"
        monkeypatch.setenv("TRINITY_CLOCK", "2026-07-01T00:00:00+00:00")
        self._lens("# Lens v2\n")
        second = snapshot_lens()
        assert second["digest"] != first["digest"]
        assert len([d for d in history_dir().iterdir() if not d.name.startswith(".")]) == 2

        june = lens_as_of("2026-06-15T00:00:00+00:00")
        assert june["digest"] == first["digest"]
        assert (Path(june["path"]) / "lens.md").read_text() == "# Lens v1\n"
        assert lens_as_of("2026-07-01T00:00:00+00:00")["digest"] == second["digest"]
        assert lens_as_of("2026-05-31T23:59:59+00:00") is None

    def test_a_reverted_lens_is_a_new_dated_copy(self, patch_trinity_home, monkeypatch):
        from trinity_local.me.lens_history import lens_as_of, snapshot_lens
        for at, text in (("2026-06-01", "A"), ("2026-07-01", "B"), ("2026-08-01", "A")):
            monkeypatch.setenv("TRINITY_CLOCK", at + "T00:00:00+00:00")
            self._lens(text)
            snapshot_lens()
        assert (Path(lens_as_of("2026-08-02T00:00:00+00:00")["path"]) / "lens.md").read_text() == "A"
        assert (Path(lens_as_of("2026-07-02T00:00:00+00:00")["path"]) / "lens.md").read_text() == "B"

    def test_one_pinned_clock_keeps_write_order(self, patch_trinity_home, monkeypatch):
        # Under a pinned clock every copy has the same `at`; the last one written must win,
        # including a lens that reverts to an earlier version.
        from trinity_local.me.lens_history import history_dir, lens_as_of, snapshot_lens
        monkeypatch.setenv("TRINITY_CLOCK", CUTOFF)
        for text in ("A", "B", "A"):
            self._lens(text)
            assert not snapshot_lens().get("unchanged")
        assert len(list(history_dir().iterdir())) == 3
        assert (Path(lens_as_of(CUTOFF)["path"]) / "lens.md").read_text() == "A"
        self._lens("A")
        assert snapshot_lens()["unchanged"]

    def test_unreadable_copy_is_skipped_not_fatal(self, patch_trinity_home, monkeypatch):
        from trinity_local.me.lens_history import history_dir, lens_as_of, snapshot_lens
        monkeypatch.setenv("TRINITY_CLOCK", CUTOFF)
        self._lens("x")
        snapshot_lens()
        bad = history_dir() / "2026-05-01T000000_000000000000"
        bad.mkdir()
        (bad / "manifest.json").write_text("[1, 2]")
        assert lens_as_of("2026-06-02T00:00:00+00:00")["at"] == CUTOFF

    def test_every_build_path_leaves_a_copy(self, patch_trinity_home, monkeypatch):
        from trinity_local.me.build_meters import record_build_meters
        monkeypatch.setenv("TRINITY_CLOCK", CUTOFF)
        self._lens("# Lens\n")
        out = record_build_meters()
        assert out["lens_history"]["ok"], out["lens_history"]


# A recording provider for the build subprocess: deterministic answers that depend on what
# the chairman is shown, and a log of the digest of every prompt it was shown.
_FAKE = r'''
import hashlib, json, os, re
from unittest import mock
from trinity_local.providers import ProviderResult
class _Fake:
    def run(self, prompt, cwd=None):
        h = hashlib.sha256(prompt.encode()).hexdigest()
        with open(os.environ["HQ132_PROMPT_LOG"], "a") as fh:
            fh.write(h + "\n")
        if "THE FOUR SIGNAL TYPES" in prompt:
            ids = [m.strip() for m in re.findall(r"\[([^\]·]+) · basin=", prompt)]
            out = "\n".join(json.dumps({"id": "r", "type": "REFRAME", "prompt_id": pid,
                "model_quote": "a verbose multi-paragraph framing of the answer",
                "user_substitute": "no, reframe it around " + h[:8], "why_signal": "pivot"}) for pid in ids)
        elif "privileged" in prompt and "sacrificed" in prompt:
            out = json.dumps({"id": "d1", "privileged": "ship " + h[:8], "sacrificed": "polish",
                "valence": "correction", "basin": "b00", "verbatim": "just ship it " + h[:8], "prompt_id": "p0"})
        else:
            out = "[]"
        return ProviderResult(provider="claude", stdout=out, stderr="", returncode=0)
mock.patch("trinity_local.providers.make_provider", return_value=_Fake()).start()
'''


def _node(tx: str, i: int, ts: str, text: str, **extra) -> dict:
    return {"id": f"{tx}_{i}", "transcript_id": tx, "provider": "claude", "source_path": "/x", "turn_index": i,
            "text": text, "embedding": [0.0] * 8, "created_at": ts, "timestamp": ts,
            "preceding_assistant_text": f"the assistant gave a long, multi-part answer to turn {i} of {tx}",
            "following_assistant_text": "", **extra}


def _source(root: Path, *, after_text="later work", early_text="early work", later_fields=None) -> Path:
    """A home with one session before the cutoff, one spanning it and one after it."""
    later_fields = later_fields or {}
    # A transcript in the source home whose header says a machine started the session: the
    # authorship filter reads it when a node has no stored origin.
    transcript = root / "transcripts" / "before.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"type": "user", "entrypoint": "sdk-cli"}) + "\n")
    rows = []
    for i in range(6):
        rows.append(_node("before", i, f"2026-05-{10 + i:02d}T12:00:00+00:00",
                          f"{early_text} number {i}, and keep the scope narrow this time", **later_fields))
    rows.append(_node("before", 6, "2026-05-20T12:00:00+00:00", "a scripted request",
                      source_path=str(transcript)))
    rows.append(_node("spanning", 0, "2026-05-31T23:00:00+00:00", f"{after_text}: started late in May"))
    rows.append(_node("spanning", 1, "2026-06-01T01:00:00+00:00", f"{after_text}: finished in June"))
    for i in range(3):
        rows.append(_node("after", i, f"2026-06-{10 + i:02d}T12:00:00+00:00", f"{after_text} {i}"))
    p = root / "prompts"
    p.mkdir(parents=True)
    (p / "prompt_nodes.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (p / "turn_windows.jsonl").write_text("".join(json.dumps(
        {"id": f"w_{tx}", "transcript_id": tx, "center_prompt_id": f"{tx}_0", "text": f"{tx} {early_text if tx == 'before' else after_text}",
         "embedding": [0.0] * 8, "turn_start": 0, "turn_end": 1}) + "\n" for tx in ("before", "spanning", "after")))
    (p / "transcript_nodes.jsonl").write_text("".join(json.dumps(
        {"id": tx, "transcript_id": tx, "title": None, "prompt_ids": [], "centroid_embedding": [0.0] * 8,
         "themes": ["t"], "density": None}) + "\n" for tx in ("before", "after")))
    (p / "dispatched_prompts.jsonl").write_text(
        json.dumps({"h": "a" * 40, "ts": "2026-05-20T00:00:00+00:00"}) + "\n"
        + json.dumps({"h": hashlib.sha1(after_text.encode()).hexdigest(), "ts": "2026-06-20T00:00:00+00:00"}) + "\n")
    return root


def _tree(root: Path) -> dict[str, str]:
    return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(root.rglob("*")) if f.is_file()}


class TestCutoffMutation:
    """amd_0313's registered test, through the real build in a fresh process."""

    def _run(self, tmp_path: Path, name: str, monkeypatch, **source_kw) -> dict:
        hq = _hq132()
        src = _source(tmp_path / f"src_{name}", **source_kw)
        home = tmp_path / f"asof_{name}"
        hq.materialize(src, home, CUTOFF)
        materialized = _tree(home)
        log = tmp_path / f"prompts_{name}.log"
        monkeypatch.setenv("HQ132_PROMPT_LOG", str(log))
        monkeypatch.setenv("TRINITY_DISABLE_MLX", "1")
        out = hq.build(home, CUTOFF, [src], preamble=_FAKE)
        from_build = {k: (home / k).read_bytes() for k in ("memories/lens.md", "me/lens_registry.json")
                      if (home / k).is_file()}
        return {"materialized": materialized, "prompts": sorted(log.read_text().split()),
                "lens": from_build, "history": out["meters"]["lens_history"]}

    def test_after_cutoff_changes_leave_the_lens_identical(self, tmp_path, monkeypatch):
        base = self._run(tmp_path, "base", monkeypatch)
        assert base["prompts"], "the chairman was never called: the test would pass vacuously"
        assert "memories/lens.md" in base["lens"] and base["history"]["ok"]
        assert base["history"]["at"] == CUTOFF

        later = self._run(tmp_path, "later", monkeypatch, after_text="a rewritten later session",
                          later_fields={"council_run_ids": ["c1"], "themes": ["x"], "importance": 0.9,
                                        "last_replayed_at": "2026-09-01T00:00:00+00:00",
                                        "chairman_winner": "codex", "cluster_id": "k"})
        assert later["materialized"] == base["materialized"]
        assert later["prompts"] == base["prompts"]
        assert later["lens"] == base["lens"]
        assert later["history"]["digest"] == base["history"]["digest"]

    def test_before_cutoff_changes_reach_the_lens(self, tmp_path, monkeypatch):
        base = self._run(tmp_path, "base", monkeypatch)
        early = self._run(tmp_path, "early", monkeypatch, early_text="different early work")
        assert early["materialized"] != base["materialized"]
        assert early["prompts"] != base["prompts"]


class TestMaterialize:
    def test_partitions_sessions_and_resets_later_fields(self, tmp_path):
        hq = _hq132()
        src = _source(tmp_path / "src", later_fields={"council_run_ids": ["c1"], "themes": ["x"]})
        c = hq.materialize(src, tmp_path / "asof", CUTOFF)
        assert c["sessions"] == {"before": 1, "after": 1, "spanning": 1, "undated": 0}
        rows = [json.loads(line) for line in (tmp_path / "asof/prompts/prompt_nodes.jsonl").read_text().splitlines()]
        assert {r["transcript_id"] for r in rows} == {"before"}
        assert all(r["council_run_ids"] == [] and r["themes"] == [] for r in rows)
        # No pointer to a live file survives; the origin it would have been read for does.
        assert all(r["source_path"] == "" for r in rows)
        assert [r["origin"] for r in rows if r["id"] == "before_6"] == ["headless"]
        disp = (tmp_path / "asof/prompts/dispatched_prompts.jsonl").read_text().splitlines()
        assert len(disp) == 1 and json.loads(disp[0])["ts"] < CUTOFF

    def test_credential_rows_never_reach_the_isolated_home(self, tmp_path):
        hq = _hq132()
        src = _source(tmp_path / "src", early_text="password: hunter2hunter2")
        c = hq.materialize(src, tmp_path / "asof", CUTOFF)
        assert c["secret_rows_dropped"] == 6  # the scripted request has no credential
        assert "hunter2" not in "".join(f.read_text() for f in (tmp_path / "asof").rglob("*.jsonl"))
