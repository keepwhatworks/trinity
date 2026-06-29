"""The embedder-download wizard: the in-session offer that lets a plugin-only
install (lexical fallback) pull the full embedding engine.

Covers the cheap gate, decision persistence, the no-op-when-gated path, the
accept→install flow (subprocess + elicit mocked), and a drift guard tying
ENGINE_DEPS to pyproject's `[mlx]` extras so the vendored copy can't rot.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from trinity_local import embedder_wizard as ew

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    # The whole suite/CI sets this; the wizard honours it as a kill-switch, so
    # clear it for the gate tests that need to reach the offer path.
    monkeypatch.delenv("TRINITY_AUTOSCAN_DISABLED", raising=False)
    # The embedder download IS the lens add-on; maybe_offer_embedder_download is
    # gated on the opt-in. These tests exercise that offer path, so enable it.
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    # Default: pretend the embedder is NOT live (the plugin-only case) so the
    # gate can open. Individual tests override.
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: False)
    yield


# ─── ENGINE_DEPS drift guard ───────────────────────────────────────────────

def _pyproject_mlx_extras() -> list[str]:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r"^mlx\s*=\s*\[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    assert m, "couldn't find the [mlx] extras array in pyproject.toml"
    # Pull every quoted dependency string, dropping `# ...` comment lines.
    body = "\n".join(
        ln for ln in m.group(1).splitlines() if not ln.lstrip().startswith("#")
    )
    return re.findall(r'"([^"]+)"', body)


def test_engine_deps_match_pyproject_mlx_extras():
    """The vendored plugin engine ships without pyproject.toml, so the wizard
    hardcodes the deps — they MUST equal the real `[mlx]` extras (markers and
    all) or a plugin user installs the wrong/stale engine."""
    assert list(ew.ENGINE_DEPS) == _pyproject_mlx_extras(), (
        "embedder_wizard.ENGINE_DEPS drifted from pyproject [mlx]. Sync them."
    )


# ─── gate ──────────────────────────────────────────────────────────────────

def test_offers_on_fresh_lexical_install():
    ok, reason = ew.should_offer_embedder()
    assert ok and reason == "offer"


def test_skips_when_embedder_already_live(monkeypatch):
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    ok, reason = ew.should_offer_embedder()
    assert not ok and reason == "embedder already live"


def test_skips_when_autoscan_disabled(monkeypatch):
    monkeypatch.setenv("TRINITY_AUTOSCAN_DISABLED", "1")
    ok, _ = ew.should_offer_embedder()
    assert not ok


def test_opt_out_is_permanent():
    ew.record_decision("opt_out")
    ok, reason = ew.should_offer_embedder()
    assert not ok and reason == "user opted out"


def test_decline_suppresses_within_cooldown_then_reoffers(monkeypatch):
    ew.record_decision("decline")
    ok, reason = ew.should_offer_embedder()
    assert not ok and reason == "within decline cooldown"

    # Age the decline past the cooldown → re-offer.
    state = ew._load_state()
    state["last_declined_at"] = "2000-01-01T00:00:00+00:00"
    ew._save_state(state)
    ok, reason = ew.should_offer_embedder()
    assert ok and reason == "offer"


def test_in_progress_and_done_suppress():
    for status in ("in_progress", "done"):
        ew._save_state({"install_status": status})
        ok, reason = ew.should_offer_embedder()
        assert not ok and reason == f"install {status}"


def test_malformed_decline_timestamp_reoffers():
    ew._save_state({"last_declined_at": "not-a-date"})
    ok, reason = ew.should_offer_embedder()
    assert ok and reason == "offer"


# ─── decision persistence ──────────────────────────────────────────────────

def test_record_accept_marks_in_progress():
    ew.record_decision("accept")
    state = ew._load_state()
    assert state["install_status"] == "in_progress" and "accepted_at" in state


# ─── orchestration ─────────────────────────────────────────────────────────

def test_maybe_offer_returns_none_when_gated(monkeypatch):
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    spawned = []
    monkeypatch.setattr(ew.threading, "Thread", lambda *a, **k: spawned.append(k) or _NoThread())
    assert ew.maybe_offer_embedder_download() is None
    assert not spawned, "must not spawn a worker when the gate is closed"


class _NoThread:
    def start(self):  # pragma: no cover - only the no-spawn assertion runs it
        pass


def test_maybe_offer_spawns_worker_when_open(monkeypatch):
    started = {"n": 0}

    class _FakeThread:
        def __init__(self, *a, **k):
            self._target = k.get("target")

        def start(self):
            started["n"] += 1

    monkeypatch.setattr(ew.threading, "Thread", _FakeThread)
    rec = ew.maybe_offer_embedder_download()
    assert rec == {"status": "offered", "reason": "offer"}
    assert started["n"] == 1


def test_accept_flow_runs_install_and_marks_done(monkeypatch):
    """elicit→accept drives a deps install then a model download; a successful
    download marks install done."""
    monkeypatch.setattr(ew, "_elicit_choice", lambda: "accept")

    calls = []

    class _R:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        if "pip" in cmd:
            return _R(0)
        if "download-embedder" in cmd:
            return _R(0, out=json.dumps({"ok": True, "message": "Model ready"}))
        return _R(1)

    monkeypatch.setattr(ew.subprocess, "run", _fake_run)
    monkeypatch.setattr("trinity_local.mcp_features.mcp_log", lambda *a, **k: True)

    ew._wizard_thread_body()

    assert any("pip" in c for c in calls) and any("download-embedder" in c for c in calls)
    assert ew._load_state()["install_status"] == "done"


def test_accept_flow_records_failure_on_pip_error(monkeypatch):
    monkeypatch.setattr(ew, "_elicit_choice", lambda: "accept")

    class _R:
        returncode, stdout, stderr = 1, "", "boom"

    monkeypatch.setattr(ew.subprocess, "run", lambda cmd, **kw: _R())
    monkeypatch.setattr("trinity_local.mcp_features.mcp_log", lambda *a, **k: True)

    ew._wizard_thread_body()
    state = ew._load_state()
    assert state["install_status"] == "failed" and "pip exit 1" in state.get("error", "")


def test_decline_via_thread_body_persists_without_install(monkeypatch):
    monkeypatch.setattr(ew, "_elicit_choice", lambda: "decline")
    ran = []
    monkeypatch.setattr(ew, "_run_install_and_download", lambda: ran.append(1))
    ew._wizard_thread_body()
    assert not ran
    assert "last_declined_at" in ew._load_state()


def test_unsupported_elicitation_writes_no_state(monkeypatch):
    monkeypatch.setattr(ew, "_elicit_choice", lambda: None)
    ew._wizard_thread_body()
    assert ew._load_state() == {}, "no client support → no decision recorded, re-offer next session"
