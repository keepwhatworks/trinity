"""The lens opt-in gate — fusion is the free core, the lens is an add-on.

The load-bearing contract: a FRESH install (no opt-in marker, no built lens) does
ZERO lens work — no first build, no refresh, no stale embed pass, and critically
NO embedder-download offer (the "instant, no heavy download" promise). Opting in
(explicit marker, or an existing built lens for back-compat) flips it on.
"""
from __future__ import annotations


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_LENS_ENABLED", raising=False)


# ── the flag ──────────────────────────────────────────────────────────────────


def test_disabled_on_fresh_home(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    from trinity_local.lens_addon import lens_enabled
    assert lens_enabled() is False


def test_enabled_after_opt_in(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    from trinity_local.lens_addon import lens_enabled, enable_lens
    assert enable_lens() is True            # newly written
    assert lens_enabled() is True
    assert enable_lens() is False           # idempotent: already enabled


def test_back_compat_existing_lens_is_enabled(monkeypatch, tmp_path):
    """An existing install already has a built lens.md → treated as enabled so it
    keeps auto-refreshing without re-opting-in."""
    _fresh(monkeypatch, tmp_path)
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "lens.md").write_text(
        "## Paired tensions\n### 1. speed ↔ rigor\nSupported by 7 decisions.\n",
        encoding="utf-8")
    from trinity_local.lens_addon import lens_enabled
    assert lens_enabled() is True


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    from trinity_local.lens_addon import lens_enabled
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    assert lens_enabled() is True
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "0")   # forces off even if a lens exists
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "lens.md").write_text("x" * 200, encoding="utf-8")
    assert lens_enabled() is False


# ── the gate: a fresh (disabled) install does NO lens work ────────────────────


def test_no_lens_kicks_on_fresh_install(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    from trinity_local.cold_start import maybe_kick_first_lens_build, maybe_kick_lens_refresh
    from trinity_local.stale_pass import maybe_kick_stale_pass
    from trinity_local.embedder_wizard import maybe_offer_embedder_download

    assert maybe_kick_first_lens_build() is None
    assert maybe_kick_lens_refresh() is None
    assert maybe_kick_stale_pass(trigger="test") is False
    # THE promise: no hundreds-of-MB embedder offer on a fusion-only install.
    assert maybe_offer_embedder_download() is None


def test_gate_lets_work_through_once_enabled(monkeypatch, tmp_path):
    """Past the lens gate, the kicks reach their OWN gates. Proven by patching the
    line right after the gate to raise: disabled → early return (no raise);
    enabled → it gets past the gate and hits the patched line."""
    _fresh(monkeypatch, tmp_path)
    import trinity_local.cold_start as cs

    sentinel = RuntimeError("got past the lens gate")

    def boom():
        raise sentinel

    monkeypatch.setattr(cs, "_autoscan_disabled", boom)

    # disabled: the lens gate returns None before reaching _autoscan_disabled.
    assert cs.maybe_kick_first_lens_build() is None

    # enabled: it gets past the lens gate and hits the (patched) next check.
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    import pytest
    with pytest.raises(RuntimeError):
        cs.maybe_kick_first_lens_build()


def test_explicit_dream_opts_in(monkeypatch, tmp_path):
    """Running an explicit build (dream/lens) opts the user into the add-on, so
    the background kicks start working afterward."""
    _fresh(monkeypatch, tmp_path)
    from trinity_local.lens_addon import lens_enabled, enable_lens
    assert lens_enabled() is False
    enable_lens()  # what handle_dream / handle_me_build call at entry
    assert lens_enabled() is True


# ── the `lens-setup` wizard (the collapsed create-your-lens action) ──────────


def test_lens_setup_orchestrates_and_opts_in(monkeypatch, tmp_path, capsys):
    from types import SimpleNamespace
    import trinity_local.commands.me as me
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)  # embedder present
    monkeypatch.setattr("trinity_local.cold_start.detect_available_sources", lambda: ["claude"])
    monkeypatch.setattr("trinity_local.incremental_ingest.ingest_recent", lambda **k: SimpleNamespace(added=5))
    monkeypatch.setattr(me, "build_me_via_lens_pipeline", lambda: ("/x/lens.md", {}))

    from trinity_local.lens_addon import lens_enabled
    assert lens_enabled() is False
    assert me.handle_lens_setup(SimpleNamespace()) == 0
    assert lens_enabled() is True          # the wizard opted in
    assert "ready" in capsys.readouterr().out.lower()


def test_lens_setup_aborts_when_embedder_fails(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import trinity_local.commands.me as me
    _fresh(monkeypatch, tmp_path)
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: False)
    monkeypatch.setattr("trinity_local.commands.download_embedder.handle_download_embedder", lambda a: 1)
    monkeypatch.setattr(me, "build_me_via_lens_pipeline",
                        lambda: (_ for _ in ()).throw(AssertionError("build reached despite embedder fail")))
    assert me.handle_lens_setup(SimpleNamespace()) == 1   # never reaches the build
