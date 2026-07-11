"""Guards for scripts/package_webstore.py — the Web Store packaging gate.

The packager is fail-closed: it must refuse the private working tree (slug
leak), refuse key drift (the manifest key derives the extension id the
pre-wired native host expects, #271), and build a clean zip from a scrubbed
tree. These tests pin each refusal AND the happy path, because a packaging
gate that silently stopped firing ships a broken store build with days of
review latency before anyone notices.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "package_webstore", REPO / "scripts" / "package_webstore.py"
)
pw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pw)


def _scrubbed_copy(tmp_path: Path) -> Path:
    """The repo tree with the private slug rewritten — what the public export
    produces, minus everything else the export does."""
    dst = tmp_path / "browser-extension"
    shutil.copytree(REPO / "browser-extension", dst)
    for p in dst.rglob("*"):
        if p.is_file() and p.suffix in pw.TEXT_SUFFIXES:
            text = p.read_text(encoding="utf-8", errors="replace")
            if pw.PRIVATE_SLUG in text:
                p.write_text(
                    text.replace("keepwhatworks/trinity", "keepwhatworks/trinity")
                        .replace("vishigondi", "keepwhatworks"),
                    encoding="utf-8",
                )
    return dst


class TestPackagerCanon:
    def test_duplicated_canonical_id_matches_registry(self):
        """The packager duplicates CANONICAL_EXTENSION_ID (so it can run on
        the public tree without importing the package) — keep it in lockstep
        with registry.py, same rule as the bash resolver."""
        from trinity_local.registry import CANONICAL_EXTENSION_ID
        assert pw.CANONICAL_EXTENSION_ID == CANONICAL_EXTENSION_ID

    def test_repo_manifest_key_derives_canonical_id(self):
        manifest = json.loads(
            (REPO / "browser-extension" / "manifest.json").read_text()
        )
        assert pw._derive_extension_id(manifest["key"]) == pw.CANONICAL_EXTENSION_ID


class TestCheckTree:
    def test_private_tree_fails_only_on_the_slug(self):
        """The repo's own tree must fail the slug gate (it links the private
        repo from UI copy — by design, the export rewrites it) and NOTHING
        else: manifest references complete, key valid, no remote code. A
        second error class appearing here = a real packaging regression."""
        _, errors = pw.check_tree(REPO / "browser-extension")
        assert errors, "private tree unexpectedly clean — slug gate dead?"
        assert all("private repo slug" in e for e in errors), errors

    def test_scrubbed_tree_is_submittable(self, tmp_path):
        _, errors = pw.check_tree(_scrubbed_copy(tmp_path))
        assert errors == []

    def test_key_drift_is_refused(self, tmp_path):
        """Mutation: swap the manifest key for a different valid key → the
        derived id no longer matches the canonical id → refuse. This is the
        #271 wire: a drifted key ships an extension the native host rejects."""
        src = _scrubbed_copy(tmp_path)
        mpath = src / "manifest.json"
        manifest = json.loads(mpath.read_text())
        import base64
        manifest["key"] = base64.b64encode(b"not the real DER key").decode()
        mpath.write_text(json.dumps(manifest))
        _, errors = pw.check_tree(src)
        assert any("key/registry drift" in e for e in errors), errors

    def test_missing_key_is_refused(self, tmp_path):
        src = _scrubbed_copy(tmp_path)
        mpath = src / "manifest.json"
        manifest = json.loads(mpath.read_text())
        del manifest["key"]
        mpath.write_text(json.dumps(manifest))
        _, errors = pw.check_tree(src)
        assert any("no 'key'" in e for e in errors), errors

    def test_missing_referenced_file_is_refused(self, tmp_path):
        src = _scrubbed_copy(tmp_path)
        (src / "background.js").unlink()
        _, errors = pw.check_tree(src)
        assert any("missing file: background.js" in e for e in errors), errors


class TestBuildZip:
    def test_zip_keeps_key_and_excludes_dev_files(self, tmp_path):
        src = _scrubbed_copy(tmp_path)
        manifest, errors = pw.check_tree(src)
        assert errors == []
        zip_path = pw.build_zip(src, tmp_path / "dist", manifest)
        assert zip_path.name == f"trinity-extension-v{manifest['version']}.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            packed = json.loads(zf.read("manifest.json"))
        # the key is load-bearing (#271) — it must survive packaging
        assert packed.get("key") == manifest["key"]
        for dev in pw.EXCLUDE_NAMES:
            assert dev not in names, f"dev file {dev} leaked into the store zip"
        assert "background.js" in names and "sidepanel.html" in names
