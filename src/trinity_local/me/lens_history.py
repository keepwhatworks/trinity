"""Dated, content-addressed copies of the lens, written once and never rewritten (amd_0313).

The tension registry accumulates evidence in place, so a past lens cannot be read back from it
(res_163: every tension first seen before a cutoff now cites evidence from after it). Every
build path therefore leaves a copy of the two files that make up the lens, the rendered
``lens.md`` and the tension registry, under ``me/lens_history/<at>_<digest>/`` with a
``manifest.json`` of their hashes. A build whose lens did not change leaves nothing new.

A historical build runs the same pipeline under a pinned clock (``utils.now_dt``) in an
isolated home and leaves its copy the same way, so the production history and the
retrospective study read one format. ``lens_as_of`` is the one reader.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..utils import now_iso
from .basins import me_dir
from .lens_registry import _parse_iso, registry_path

MANIFEST = "manifest.json"


def history_dir() -> Path:
    return me_dir() / "lens_history"


def _lens_files() -> dict[str, Path]:
    from ..state_paths import lens_path
    return {"lens.md": lens_path(), "lens_registry.json": registry_path()}


def _digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def _manifests() -> list[dict]:
    """Every copy's manifest in write order; a directory without a valid manifest is skipped."""
    out = []
    root = history_dir()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        try:
            m = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (isinstance(m, dict) and isinstance(m.get("digest"), str) and isinstance(m.get("seq"), int)
                and _parse_iso(str(m.get("at") or ""))):
            out.append({**m, "path": str(d)})
    return sorted(out, key=lambda m: m["seq"])


def snapshot_lens() -> dict:
    """Copy the current lens into the history unless it equals the latest copy."""
    blobs = {name: p.read_bytes() for name, p in _lens_files().items() if p.is_file()}
    if "lens.md" not in blobs:
        return {"ok": False, "reason": "no lens yet"}
    hashes = {name: hashlib.sha256(b).hexdigest() for name, b in sorted(blobs.items())}
    digest = _digest(hashes)
    prior = _manifests()
    if prior and prior[-1]["digest"] == digest:
        return {"ok": True, "unchanged": True, "path": prior[-1]["path"], "digest": digest}
    at = now_iso()
    try:
        from ..me_builder import _corpus_fingerprint
        fingerprint = _corpus_fingerprint()
    except Exception:  # noqa: BLE001 — provenance, not a reason to lose the copy
        fingerprint = None
    root = history_dir()
    root.mkdir(parents=True, exist_ok=True)
    seq = prior[-1]["seq"] + 1 if prior else 0
    final = root / f"{seq:06d}_{at[:19].replace(':', '')}_{digest[:12]}"
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        for name, b in blobs.items():
            (tmp / name).write_bytes(b)
        (tmp / MANIFEST).write_text(json.dumps(
            {"seq": seq, "at": at, "digest": digest, "sha256": hashes, "corpus_fingerprint": fingerprint},
            indent=1, sort_keys=True), encoding="utf-8")
        os.rename(tmp, final)
    except BaseException:
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()
        raise
    return {"ok": True, "path": str(final), "digest": digest, "at": at}


def lens_as_of(at: str) -> dict | None:
    """The manifest (with its ``path``) of the last copy written at or before ``at``, or None."""
    cut = _parse_iso(at)
    if cut is None:
        raise ValueError(f"not an ISO timestamp: {at!r}")
    eligible = [m for m in _manifests() if _parse_iso(m["at"]) <= cut]  # already in write order
    return eligible[-1] if eligible else None
