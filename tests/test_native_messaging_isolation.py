"""#265 — install-extension must never write to the user's REAL Chrome
native-messaging dir from a test, and the conftest guard must heal it if a
future test does.

The incident: a test run overwrote the developer's real
`local.trinity.capture.json` with a pytest tmp path + a fake extension id,
silently killing browser capture for days. These guards keep `pytest` safe to
run on a machine with Trinity installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _chrome_manifest(home: Path) -> Path:
    """The Chrome NM manifest under an arbitrary home, per OS."""
    import sys

    if sys.platform == "darwin":
        base = home / "Library" / "Application Support" / "Google" / "Chrome"
    elif sys.platform.startswith("linux"):
        base = home / ".config" / "google-chrome"
    else:  # win32 uses the registry — skip there
        pytest.skip("Windows uses the registry, not a filesystem manifest")
    return base / "NativeMessagingHosts" / "local.trinity.capture.json"


class TestManifestCandidatesHelper:
    def test_includes_real_chrome_path(self):
        from tests.conftest import _real_native_messaging_manifests

        paths = _real_native_messaging_manifests()
        # On mac/linux at least the Chrome manifest path must be present.
        assert any("Chrome" in str(p) or "google-chrome" in str(p) for p in paths)


class TestInstallExtensionRespectsHome:
    def test_install_writes_only_under_patched_home(self, tmp_path, monkeypatch):
        """With Path.home() patched to a tmp dir, install-extension must write
        the manifest there — and must NOT touch the real manifest. This is the
        exact isolation the clobbering run lacked."""
        from trinity_local.commands.install import handle_install_extension

        # Record the real manifest's bytes (if any) BEFORE the patched run.
        real = _chrome_manifest(Path.home())
        before = real.read_bytes() if real.exists() else None

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        host = tmp_path / "trinity-local-capture-host"
        host.write_text("#!/bin/sh\n")
        handle_install_extension(
            SimpleNamespace(
                extension_id=None,  # default → canonical id
                host_path=str(host),
                browsers=["chrome"],
                firefox=False,
            )
        )

        # Wrote under the patched home.
        patched = _chrome_manifest(tmp_path)
        assert patched.exists(), "manifest must be written under the patched home"
        d = json.loads(patched.read_text())
        assert d["path"] == str(host)

        # Did NOT touch the real manifest.
        after = real.read_bytes() if real.exists() else None
        assert after == before, "install-extension must not write the REAL manifest"
