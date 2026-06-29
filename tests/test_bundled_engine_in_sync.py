"""The engine VENDORED into the Claude Code plugin must stay byte-in-sync with
`src/trinity_local`. The plugin ships the engine so a marketplace install needs no
separate `curl|bash` (scripts/bundle_engine.sh regenerates it); if the committed
bundle drifts from src/, a release ships a STALE engine to every plugin user — the
exact silent-skew this guard exists to prevent. Regenerate + commit on any engine
change: `bash scripts/bundle_engine.sh`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "trinity_local"
BUNDLE = REPO / "plugins" / "trinity-local" / "engine" / "trinity_local"

# Mirror scripts/bundle_engine.sh's rsync excludes so the comparison matches what
# the bundle script actually ships.
_EXCLUDE_DIR_PARTS = {"__pycache__"}
_EXCLUDE_SUFFIXES = {".pyc"}
_EXCLUDE_NAMES = {".DS_Store"}


def _file_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _EXCLUDE_DIR_PARTS for part in p.parts):
            continue
        if p.suffix in _EXCLUDE_SUFFIXES or p.name in _EXCLUDE_NAMES:
            continue
        out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


def test_bundled_engine_matches_src():
    assert BUNDLE.exists(), (
        "plugins/trinity-local/engine/trinity_local is missing — the plugin no longer "
        "ships the engine. Run `bash scripts/bundle_engine.sh` to vendor it."
    )
    src = _file_hashes(SRC)
    bundle = _file_hashes(BUNDLE)
    missing = sorted(set(src) - set(bundle))
    extra = sorted(set(bundle) - set(src))
    drifted = sorted(r for r in (set(src) & set(bundle)) if src[r] != bundle[r])
    assert not (missing or extra or drifted), (
        "bundled engine drifted from src/ — run `bash scripts/bundle_engine.sh` and "
        "commit the result so the marketplace ships the current engine.\n"
        f"  missing from bundle ({len(missing)}): {missing[:8]}\n"
        f"  extra in bundle ({len(extra)}): {extra[:8]}\n"
        f"  content-drifted ({len(drifted)}): {drifted[:8]}"
    )
