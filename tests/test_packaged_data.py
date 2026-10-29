"""Data files the installed code reads ship inside the package (coherence review 2026-10-16).

Two readers reached into the repo, which no wheel carries: extension repair read
browser-extension/page-hook.js (so "Repair extension" crashed on every install that had something
to repair), and the launchpad's benchmark chart read data/reference_evals.json (so every install
showed the hardcoded fallback, with older categories). Both are package data now.
scripts/wheel_smoke.py checks the same paths in a real installed wheel.
"""
from __future__ import annotations

from pathlib import Path

import trinity_local

REPO = Path(__file__).resolve().parents[1]
# Wherever the package was imported from (this checkout, another checkout's editable install,
# a wheel): what matters is that the file sits inside the package, not inside a repository.
PKG = Path(trinity_local.__file__).resolve().parent


def test_the_packaged_page_hook_is_the_extensions_own():
    from trinity_local.commands import extension_repair
    packaged = extension_repair._PAGE_HOOK_PATH
    assert packaged.read_bytes() == (REPO / "browser-extension" / "page-hook.js").read_bytes(), (
        "src/trinity_local/data/extension/page-hook.js drifted from browser-extension/page-hook.js, "
        "its source: run bash scripts/bundle_engine.sh")
    assert packaged.is_relative_to(PKG), packaged
    bundle = (REPO / "scripts" / "bundle_engine.sh").read_text(encoding="utf-8")
    assert 'cp "$REPO/browser-extension/page-hook.js" "$SRC/data/extension/page-hook.js"' in bundle, (
        "the bundle step no longer generates the package copy, so it can drift by hand")


def test_the_benchmark_chart_reads_the_real_file_not_the_fallback():
    from trinity_local import global_benchmarks as g
    path = g._reference_evals_path()
    assert path.is_file() and path.is_relative_to(PKG), path
    assert g.get_global_benchmarks() != g._hardcoded_fallback()


def test_both_are_declared_as_package_data():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"data/reference_evals.json"' in text and '"data/extension/page-hook.js"' in text


def test_the_refresh_tool_writes_the_rows_the_chart_reads():
    """tools/sync_reference_evals.py keyed the Google row "gemini" while the shipped file
    and its readers use "antigravity", so a re-run would have dropped Gemini from the chart
    (found 2026-10-27). Its provider keys must be the shipped file's, and council slugs."""
    import importlib.util
    import json

    from trinity_local.config import CANONICAL_COUNCIL_PROVIDERS
    spec = importlib.util.spec_from_file_location("sync_reference_evals", REPO / "tools" / "sync_reference_evals.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    shipped = json.loads((Path(trinity_local.__file__).parent / "data" / "reference_evals.json").read_text())
    assert set(tool.PROVIDER_TO_SLUG) == set(shipped["providers"]) == set(CANONICAL_COUNCIL_PROVIDERS)
