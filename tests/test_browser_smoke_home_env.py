"""Guard: scripts/browser_smoke.py must resolve its TRINITY_HOME from the
``$TRINITY_HOME`` env var — so the 36-surface gate can run against an isolated
synthetic home, PII-free, in any environment.

Why this matters (PII + divergence). `browser_smoke.main()` regenerates the
launchpad by running ``portal-html`` in a subprocess with ``env=dict(os.environ)``
— so the regen ALWAYS respects ``$TRINITY_HOME`` (via ``state_paths.trinity_home``).
But the module read its served/inspected home from a hardcoded
``Path.home() / ".trinity"``. With ``$TRINITY_HOME`` set those two DIVERGED: regen
wrote the synthetic home's pages while the smoke served + read the founder's real
``~/.trinity`` — so the gate could only ever run against real (PII-laden) data, and
its screenshots + surface logs leaked real prompts (the same hazard #284 hardened
the me-card regen scripts against). The fix resolves TRINITY_HOME from the env var,
exactly like ``trinity_home()``, so regen / serve / read agree on one home and the
whole gate can run on a synthetic home.

These pin both directions. Mutation: revert browser_smoke to a hardcoded
``Path.home() / ".trinity"`` and the env-honoring test reds (it would still equal
the real home regardless of the override).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SMOKE = Path(__file__).resolve().parents[1] / "scripts" / "browser_smoke.py"


def _load_smoke_module():
    """Load scripts/browser_smoke.py fresh so its module-level
    ``TRINITY_HOME = _resolve_trinity_home()`` re-evaluates against the current env.
    (Importing it executes only constants + def statements — the one
    ``trinity_local`` import lives inside a function, so module load is side-effect
    free besides reading the env.)"""
    spec = importlib.util.spec_from_file_location("browser_smoke_under_test", SMOKE)
    assert spec and spec.loader, "could not load scripts/browser_smoke.py as a module"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_browser_smoke_honors_trinity_home_env(tmp_path, monkeypatch):
    """With $TRINITY_HOME set, the smoke targets THAT home (resolved) — not the real
    ~/.trinity. This is what lets the gate run on a synthetic home without leaking
    the founder's prompts into screenshots/logs."""
    synthetic = tmp_path / "syn_home"
    synthetic.mkdir()
    monkeypatch.setenv("TRINITY_HOME", str(synthetic))

    mod = _load_smoke_module()

    # `.resolve()` (matching trinity_home()) so a /tmp symlink → /private/tmp on macOS
    # compares equal.
    assert mod.TRINITY_HOME == synthetic.resolve(), (
        f"browser_smoke ignored $TRINITY_HOME — resolved {mod.TRINITY_HOME} instead "
        f"of {synthetic.resolve()}. The gate would serve/read the real ~/.trinity "
        "(PII) while regen wrote the synthetic home — the divergence is back."
    )
    assert mod.TRINITY_HOME != (Path.home() / ".trinity"), (
        "with $TRINITY_HOME set, the smoke must NOT fall back to the real home"
    )


def test_browser_smoke_defaults_to_real_home_without_env(monkeypatch):
    """Unset $TRINITY_HOME → the normal supported path stays ~/.trinity (the founder's
    `python scripts/browser_smoke.py` keeps working unchanged)."""
    monkeypatch.delenv("TRINITY_HOME", raising=False)

    mod = _load_smoke_module()

    assert mod.TRINITY_HOME == Path.home() / ".trinity", (
        f"without $TRINITY_HOME the smoke must default to ~/.trinity, got "
        f"{mod.TRINITY_HOME}"
    )
