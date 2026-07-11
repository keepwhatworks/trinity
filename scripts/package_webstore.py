#!/usr/bin/env python3
"""Package the browser extension for Chrome Web Store submission — fail-closed.

Builds trinity-extension-v{version}.zip from a browser-extension/ tree after
verifying it is actually submittable. Every check exists because the Web Store
(or a user clicking a link inside the shipped extension) would otherwise fail
AFTER upload, where the feedback loop is days, not seconds:

  * private-slug gate — the PRIVATE repo slug must not appear in any packaged
    text file (the extension links GitHub install docs from its UI; a private
    slug 404s for every user and leaks the private repo name). The public
    export tree (internal/sync_public.sh) is the tree that passes this —
    package from there, not from the private working tree.
  * `key` KEPT + verified — manifest.json's embedded public key pins the
    extension ID (#271): sideloads AND a Web Store upload carrying the key get
    the same `paoo…` id, which is what the pre-wired native-messaging host's
    allowed_origins expects. Stripping the key here would hand the store a
    fresh id and silently kill capture for every store install — so the
    packager VERIFIES the key derives registry.CANONICAL_EXTENSION_ID and
    ships it unchanged. (The store consumes the key at first upload to assign
    the item id, then serves copies without it — the id survives.)
  * manifest-reference completeness — every file the manifest names (icons,
    scripts, sandbox pages, side panel, web-accessible resources) must exist
    in the tree; a rename that misses the manifest becomes a broken store
    build otherwise.
  * no remote code — MV3 store policy: no <script src="http…"> and no
    importScripts(http…). (Instructional curl strings in UI copy are fine —
    they are text, not code the extension loads.)
  * dev-file exclusion — README.md / package.json / smoke-stagehand.mjs /
    dotfiles stay out of the zip.

Usage:
    python3 scripts/package_webstore.py                  # auto-picks the public
                                                         # export tree if present
    python3 scripts/package_webstore.py --source PATH --out DIR

Submission itself (store listing, screenshots, privacy disclosures, review
answers) is the founder's step — see internal/webstore-submission-notes.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

PRIVATE_SLUG = "vishigondi"  # any hit = packaging the wrong tree
# Chrome derives an extension id from the manifest's public key: sha256 of the
# DER key, first 32 hex nibbles mapped 0-f → a-p. Must match registry.py's
# CANONICAL_EXTENSION_ID (same derivation TestCanonicalIdSingleSourceOfTruth
# pins) — duplicated here so the packager works on the PUBLIC export tree
# without importing the package.
CANONICAL_EXTENSION_ID = "paoocajnigihknfodgienihbopikinbm"


def _derive_extension_id(key_b64: str) -> str:
    import base64
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()
    return "".join(chr(ord("a") + int(c, 16)) for c in digest[:32])
EXCLUDE_NAMES = {"README.md", "package.json", "smoke-stagehand.mjs"}
TEXT_SUFFIXES = {".js", ".html", ".css", ".json", ".md", ".mjs", ".txt"}


def _manifest_referenced_files(manifest: dict) -> set[str]:
    refs: set[str] = set()
    refs.update((manifest.get("icons") or {}).values())
    refs.update(((manifest.get("action") or {}).get("default_icon") or {}).values())
    bg = (manifest.get("background") or {}).get("service_worker")
    if bg:
        refs.add(bg)
    for cs in manifest.get("content_scripts") or []:
        refs.update(cs.get("js") or [])
        refs.update(cs.get("css") or [])
    sp = (manifest.get("side_panel") or {}).get("default_path")
    if sp:
        refs.add(sp)
    refs.update((manifest.get("sandbox") or {}).get("pages") or [])
    for war in manifest.get("web_accessible_resources") or []:
        refs.update(war.get("resources") or [])
    return refs


def check_tree(source: Path) -> tuple[dict, list[str]]:
    """Return (manifest, errors). Empty errors = submittable."""
    errors: list[str] = []
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        return {}, [f"no manifest.json under {source}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return {}, [f"manifest.json unparseable: {exc}"]

    if manifest.get("manifest_version") != 3:
        errors.append("manifest_version must be 3")
    if not manifest.get("version"):
        errors.append("manifest has no version")
    key_b64 = manifest.get("key")
    if not key_b64:
        errors.append("manifest has no 'key' — store installs would get a fresh id "
                      "and the pre-wired native host (#271) would reject them")
    else:
        derived = _derive_extension_id(key_b64)
        if derived != CANONICAL_EXTENSION_ID:
            errors.append(f"manifest key derives id {derived}, expected "
                          f"{CANONICAL_EXTENSION_ID} — key/registry drift")

    for ref in sorted(_manifest_referenced_files(manifest)):
        if not (source / ref).is_file():
            errors.append(f"manifest references missing file: {ref}")

    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(source).as_posix()
        if path.name in EXCLUDE_NAMES:
            continue  # excluded from the zip, so exempt from content gates
        text = path.read_text(encoding="utf-8", errors="replace")
        if PRIVATE_SLUG in text:
            errors.append(
                f"private repo slug in {rel} — package from the PUBLIC export "
                f"tree (internal/sync_public.sh rewrites every URL), not the "
                f"private working tree"
            )
        low = text.lower()
        if path.suffix == ".html" and ('<script src="http' in low or "<script src='http" in low):
            errors.append(f"remote <script src> in {rel} — MV3 store policy forbids remote code")
        if path.suffix in {".js", ".mjs"} and "importscripts(" in low and "http" in low.split("importscripts(", 1)[1][:120]:
            errors.append(f"remote importScripts in {rel} — MV3 store policy forbids remote code")
    return manifest, errors


def build_zip(source: Path, out_dir: Path, manifest: dict) -> Path:
    version = manifest["version"]
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"trinity-extension-v{version}.zip"
    # The manifest ships VERBATIM — the embedded key is load-bearing (#271, id
    # preservation for the pre-wired native host); check_tree already verified
    # it derives the canonical id.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(source).as_posix()
            name = path.name
            if name in EXCLUDE_NAMES or name.startswith(".") or name.endswith(".map"):
                continue
            zf.write(path, rel)
    return zip_path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    default_public = repo_root.parent / "trinity-local-public" / "browser-extension"
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--source", type=Path,
                    default=default_public if default_public.is_dir()
                    else repo_root / "browser-extension",
                    help="extension tree to package (default: the public export "
                         "tree when present — it is the tree the slug gate passes)")
    ap.add_argument("--out", type=Path, default=repo_root / "dist",
                    help="output directory (default: dist/)")
    args = ap.parse_args()

    source = args.source.resolve()
    print(f"source: {source}")
    manifest, errors = check_tree(source)
    if errors:
        print("✗ NOT submittable:")
        for e in errors:
            print(f"  - {e}")
        return 2

    zip_path = build_zip(source, args.out.resolve(), manifest)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]
    size_kb = zip_path.stat().st_size / 1024
    print(f"✓ {zip_path.name}  ({size_kb:.0f} KB, sha256 {digest}…)")
    print(f"  version {manifest['version']}; manifest key kept (derives the canonical "
          f"extension id — store installs stay wired to the native host).")
    print("  Next (founder): upload at https://chrome.google.com/webstore/devconsole — "
          "see internal/webstore-submission-notes.md for the listing + permission notes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
