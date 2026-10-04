"""mcp 2.0 (2026-07-28) removed the low-level Server decorators Trinity's MCP
server uses; every install path that resolved `mcp>=1.0` after that date got a
server that crashed on start. Every path must carry pyproject's capped spec,
and status must refuse an installed 2.x even though it imports."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]


def _spec() -> str:
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    return next(d.split("#")[0].strip() for d in deps if re.match(r"mcp\b", d))


def test_pyproject_caps_mcp_below_2():
    assert re.search(r"<\s*2\b", _spec()), f"mcp must be capped below 2; got {_spec()!r}"


def test_every_install_path_uses_the_same_spec():
    spec = _spec()
    for rel in ("scripts/install.sh", "plugins/trinity-local/bin/trinity-mcp", "src/trinity_local/commands/update.py"):
        text = (REPO / rel).read_text()
        found = set(re.findall(r"mcp>=[0-9.]+(?:,<[0-9.]+)?", text))
        assert found == {spec}, f"{rel} installs {sorted(found)}; pyproject says {spec!r}"


def test_status_refuses_mcp_2_even_though_it_imports(monkeypatch):
    from trinity_local import health_checks as hc
    monkeypatch.setattr(hc, "_mcp_version", lambda: "2.3.0")
    r = hc._check_mcp_available()
    assert not r.ok and "2.3.0" in r.detail and r.fix == "trinity-local update --deps"
    monkeypatch.setattr(hc, "_mcp_version", lambda: "1.27.1")
    assert hc._check_mcp_available().ok


def test_update_deps_fix_installs_the_capped_spec(monkeypatch, tmp_path):
    """Advice closure for the fix above: `update --deps` asks pip for the capped
    spec, which downgrades an installed 2.x."""
    import subprocess

    from trinity_local.commands import update as upd
    argv = []
    monkeypatch.setattr(subprocess, "run", lambda a, **k: argv.append(a) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    upd.handle_update(SimpleNamespace(deps=True, check=False, json=False, skill_dir=None))
    assert argv and _spec() in argv[0]
