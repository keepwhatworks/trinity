"""Structural guards for the optional Stagehand Chrome smoke.

The real browser smoke is gated behind ``TRINITY_CHROME_SMOKE=1`` because
CI does not have a real Chrome profile. These fast checks keep the
driver wiring honest without launching Chrome or installing npm deps.
"""
from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
EXT_DIR = REPO / "browser-extension"
SMOKE = EXT_DIR / "smoke-stagehand.mjs"
PACKAGE = EXT_DIR / "package.json"


def _smoke_src() -> str:
    return SMOKE.read_text(encoding="utf-8")


def test_stagehand_smoke_is_packaged_as_optional_extension_dev_dependency():
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))

    assert package["scripts"]["smoke:stagehand"] == "node ./smoke-stagehand.mjs"
    assert "@browserbasehq/stagehand" in package["devDependencies"]


def test_stagehand_smoke_uses_local_browser_launch_options():
    src = _smoke_src()

    assert 'env: "LOCAL"' in src
    assert "localBrowserLaunchOptions:" in src
    assert "executablePath: chromeExecutablePath" in src
    assert "userDataDir" in src
    assert "headless," in src

    before_local_options = src.split("localBrowserLaunchOptions:", 1)[0]
    assert "new Stagehand({" in before_local_options
    constructor_prefix = before_local_options.split("new Stagehand({", 1)[1]
    assert "headless" not in constructor_prefix, (
        "Stagehand deprecated top-level headless; keep it under "
        "localBrowserLaunchOptions.headless."
    )


def test_stagehand_smoke_stays_deterministic_and_local_only():
    src = _smoke_src()

    assert 'env: "BROWSERBASE"' not in src
    assert "BROWSERBASE_API_KEY" not in src
    assert ".act(" not in src
    assert ".extract(" not in src
    assert ".observe(" not in src


def test_python_packaging_does_not_depend_on_selenium():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8").lower()
    setup_py = (REPO / "setup.py").read_text(encoding="utf-8").lower()

    assert "selenium" not in pyproject
    assert "selenium" not in setup_py
