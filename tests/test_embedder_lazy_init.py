"""The embedding backend must not cost a Metal context just to be constructed.

WHY THIS EXISTS. `embeddings/__init__` constructs the MLX backend at MODULE
level, and `me/basins.py` imports that package for the pure helper
`is_finite_embedding`, which schema migrations pull in on EVERY `trinity-local`
startup. So an eager import inside `MlxNativeEmbedder.__init__` is paid by every
process in the product — CLI verbs that only print help, and MCP servers that
never embed anything.

Measured 2026-07-25 before the fix, isolated EMPTY home, zero tool calls:
366 MB idle (498 MB with autoscan) against a documented ~62 MB, mlx/Metal
mapped. Eight concurrent MCP servers held 4,047 MB.

The guard below asserts the INVARIANT (no heavy module in sys.modules after
construction), not the implementation, so it survives a rewrite of how the probe
is done but reds the moment construction gets expensive again.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

HEAVY = ("mlx.core", "mlx_embeddings", "torch", "sentence_transformers")


def _probe(body: str) -> set[str]:
    """Run `body` in a FRESH interpreter and return which heavy modules loaded.

    A subprocess is required: pytest's own session has almost certainly imported
    the embedder already, so an in-process check would pass vacuously no matter
    what the constructor does.
    """
    src = (
        "import sys\n"
        "sys.path.insert(0, 'src')\n"
        f"{body}\n"
        "import json\n"
        f"print(json.dumps([m for m in {HEAVY!r} if m in sys.modules]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", src], capture_output=True, text=True, timeout=180
    )
    if out.returncode != 0:
        pytest.fail(f"probe failed: {out.stderr[-2000:]}")
    return set(__import__("json").loads(out.stdout.strip().splitlines()[-1]))


class TestConstructionIsCheap:
    def test_importing_the_embeddings_package_loads_no_heavy_module(self):
        """The package selector runs at import; it must stay a probe.

        This is the line that actually shipped the bug — `from ..embeddings
        import is_finite_embedding` in basins.py, reached from schema migrations
        at startup.
        """
        loaded = _probe("import trinity_local.embeddings  # noqa")
        assert not loaded, (
            f"importing trinity_local.embeddings pulled in {sorted(loaded)}. "
            "Construction must probe (importlib.util.find_spec), never import — "
            "every trinity-local process pays this."
        )

    def test_importing_basins_loads_no_heavy_module(self):
        """basins.py is imported for a PATH HELPER during schema migrations."""
        loaded = _probe("import trinity_local.me.basins  # noqa")
        assert not loaded, f"importing me.basins pulled in {sorted(loaded)}"

    def test_full_cli_startup_loads_no_heavy_module(self):
        """The end-to-end invariant: `trinity-local --help` must stay light."""
        loaded = _probe(
            "import sys; sys.argv = ['trinity-local', '--help']\n"
            "from trinity_local.main import main\n"
            "try:\n"
            "    main()\n"
            "except SystemExit:\n"
            "    pass"
        )
        assert not loaded, (
            f"CLI startup pulled in {sorted(loaded)} — this is the regression "
            "that cost 366MB per process."
        )


class TestSelectorContractPreserved:
    def test_missing_package_still_raises_so_the_selector_falls_through(self):
        """The eager import was load-bearing for BACKEND SELECTION: on a machine
        without MLX it raised, and embeddings/__init__ fell through to the torch
        backend. A lazy constructor that always succeeds would silently select a
        backend that cannot embed. The probe must keep raising."""
        src = (
            "import sys; sys.path.insert(0, 'src')\n"
            "import importlib.util\n"
            "_real = importlib.util.find_spec\n"
            "importlib.util.find_spec = lambda n, *a, **k: (None if n in ('mlx', 'mlx_embeddings') else _real(n, *a, **k))\n"
            "from trinity_local.embeddings.backend_mlx_native import MlxNativeEmbedder\n"
            "try:\n"
            "    MlxNativeEmbedder()\n"
            "    print('NO_RAISE')\n"
            "except ImportError:\n"
            "    print('RAISED')\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", src], capture_output=True, text=True, timeout=180
        )
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.strip().splitlines()[-1] == "RAISED", (
            "MlxNativeEmbedder must raise ImportError when MLX is absent, or the "
            "selector in embeddings/__init__ will pick a backend that cannot embed "
            "instead of falling through to the torch backend."
        )

    def test_constructed_backend_still_reports_as_available(self):
        """Laziness must not change what the product believes about itself:
        is_available()/get_backend() are read by health checks, the launchpad,
        and ingest's real-embedder gate."""
        loaded = _probe(
            "import trinity_local.embeddings as e\n"
            "assert e.is_available() is True, 'backend went unavailable'\n"
            "assert e.get_backend() == 'mlx', f'backend={e.get_backend()}'"
        )
        assert not loaded, (
            f"is_available()/get_backend() pulled in {sorted(loaded)} — these are "
            "cheap-check paths called on abstain branches; they must not force a load."
        )


class TestLazyLoadStillWorks:
    def test_first_embed_loads_the_model_and_returns_real_vectors(self):
        """Deferring the import must not break embedding. Proves the fix moved
        the cost rather than removing the capability."""
        src = (
            "import sys; sys.path.insert(0, 'src')\n"
            "from trinity_local.embeddings import embed_batch\n"
            "v = embed_batch(['cost realism', 'realism about cost'])\n"
            "import math\n"
            "a, b = v[0], v[1]\n"
            "na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))\n"
            "cos = sum(x*y for x, y in zip(a, b)) / (na*nb)\n"
            "print('DIM', len(a))\n"
            "print('COS', round(cos, 3))\n"
            "print('HEAVY_AFTER_EMBED', 'mlx_embeddings' in sys.modules)\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", src], capture_output=True, text=True, timeout=600
        )
        if out.returncode != 0:
            pytest.skip(f"embedder unavailable in this env: {out.stderr[-500:]}")
        lines = dict(
            ln.split(" ", 1) for ln in out.stdout.strip().splitlines() if " " in ln
        )
        assert int(lines["DIM"]) == 768, f"wrong dim: {lines}"
        assert float(lines["COS"]) > 0.5, (
            f"paraphrases scored {lines['COS']} — that is the TF-IDF stub, meaning "
            "the lazy path silently failed to load MLX and degraded instead"
        )
        assert lines["HEAVY_AFTER_EMBED"] == "True", (
            "mlx_embeddings absent after a real embed — the deferred import never "
            "ran, so this test is not proving what it claims"
        )
