"""Tests for `trinity-local install-extension` (Native Messaging
manifest installer — Phase 2 of the Chrome extension transition).

Verifies:
- Chrome + Edge manifests written by default (chromium schema:
  allowed_origins)
- --firefox writes Firefox-schema manifest (allowed_extensions)
- Refuses to write when extension ID is malformed
- Refuses to write when host binary can't be located
- Manifest JSON has the canonical name + type + path + allowed_origins
  shape Chrome's Native Messaging spec requires
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


VALID_ID = "a" * 32  # 32-char a-p ID, the Chrome format


def _make_args(**overrides):
    """Build a fake argparse Namespace for handle_install_extension."""
    defaults = dict(
        extension_id=VALID_ID,
        host_path="/usr/local/bin/trinity-local-capture-host",
        browsers=None,
        firefox=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect Path.home() to a tmp dir so manifest writes are
    sandboxed. Real $HOME is never touched by these tests."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_writes_chrome_and_edge_by_default(fake_home, capsys):
    """Default --browsers is chrome + edge. Both manifests should land."""
    from trinity_local.commands.install import handle_install_extension

    rc = handle_install_extension(_make_args())
    assert rc == 0

    if sys.platform == "darwin":
        chrome = fake_home / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / "local.trinity.capture.json"
        edge = fake_home / "Library" / "Application Support" / "Microsoft Edge" / "NativeMessagingHosts" / "local.trinity.capture.json"
    elif sys.platform.startswith("linux"):
        chrome = fake_home / ".config" / "google-chrome" / "NativeMessagingHosts" / "local.trinity.capture.json"
        edge = fake_home / ".config" / "microsoft-edge" / "NativeMessagingHosts" / "local.trinity.capture.json"
    else:
        pytest.skip(f"manifest paths not modeled for {sys.platform!r}")

    assert chrome.exists(), f"chrome manifest missing at {chrome}"
    assert edge.exists(), f"edge manifest missing at {edge}"
    payload = json.loads(chrome.read_text())
    assert payload["name"] == "local.trinity.capture"
    assert payload["type"] == "stdio"
    assert payload["path"] == "/usr/local/bin/trinity-local-capture-host"
    # Post-#271: the explicit id leads, and the canonical + legacy Trinity
    # ids stay accepted alongside it (the host is inert for ids that never
    # connect; dropping canonical would re-break keyed/store installs).
    from trinity_local.registry import extension_origin_ids
    assert payload["allowed_origins"][0] == f"chrome-extension://{VALID_ID}/"
    for eid in extension_origin_ids():
        assert f"chrome-extension://{eid}/" in payload["allowed_origins"]


def test_chrome_only_via_browsers_flag(fake_home, capsys):
    """--browsers chrome → only chrome (no edge)."""
    from trinity_local.commands.install import handle_install_extension

    rc = handle_install_extension(_make_args(browsers=["chrome"]))
    assert rc == 0

    if sys.platform == "darwin":
        edge = fake_home / "Library" / "Application Support" / "Microsoft Edge" / "NativeMessagingHosts" / "local.trinity.capture.json"
    elif sys.platform.startswith("linux"):
        edge = fake_home / ".config" / "microsoft-edge" / "NativeMessagingHosts" / "local.trinity.capture.json"
    else:
        pytest.skip(f"manifest paths not modeled for {sys.platform!r}")

    assert not edge.exists(), "edge manifest written despite --browsers chrome only"


def test_firefox_writes_separate_schema(fake_home, capsys):
    """--firefox writes a Firefox-format manifest (allowed_extensions
    instead of allowed_origins)."""
    from trinity_local.commands.install import handle_install_extension

    rc = handle_install_extension(_make_args(firefox=True))
    assert rc == 0

    if sys.platform == "darwin":
        firefox = fake_home / "Library" / "Application Support" / "Mozilla" / "NativeMessagingHosts" / "local.trinity.capture.json"
    elif sys.platform.startswith("linux"):
        firefox = fake_home / ".mozilla" / "native-messaging-hosts" / "local.trinity.capture.json"
    else:
        pytest.skip(f"firefox path not modeled for {sys.platform!r}")

    assert firefox.exists(), f"firefox manifest missing at {firefox}"
    payload = json.loads(firefox.read_text())
    # Firefox-schema distinguishing field:
    assert "allowed_extensions" in payload, (
        "firefox manifest must use allowed_extensions, not allowed_origins"
    )
    assert "allowed_origins" not in payload


def test_rejects_malformed_extension_id(fake_home, capsys):
    from trinity_local.commands.install import handle_install_extension

    bad_args = _make_args(extension_id="not-a-real-id")
    rc = handle_install_extension(bad_args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "extension ID" in out
    assert "a-p format" in out


def test_rejects_missing_host_path(fake_home, capsys, monkeypatch):
    """If --host-path isn't given AND shutil.which finds nothing,
    refuse to write a manifest pointing at a broken path."""
    from trinity_local.commands import install
    monkeypatch.setattr(install.shutil if hasattr(install, "shutil") else __import__("shutil"), "which", lambda name: None)
    import shutil as real_shutil
    monkeypatch.setattr(real_shutil, "which", lambda name: None)

    bad_args = _make_args(host_path=None)
    rc = install.handle_install_extension(bad_args)
    assert rc == 1
    out = capsys.readouterr().out
    assert "could not locate" in out
    assert "trinity-local-capture-host" in out


def test_no_extension_id_defaults_to_canonical(fake_home, capsys):
    """When --extension-id is omitted, default to the canonical (published
    Web Store) id and pre-wire the host for it — so install.sh's best-effort
    pre-wire and a bare `install-extension` both register the host the
    published extension connects to. (Changed from the old 'print
    Load-unpacked instructions' behavior, which dead-ended the auto-wire.)"""
    import json

    from trinity_local.commands.install import handle_install_extension
    from trinity_local.registry import CANONICAL_EXTENSION_ID

    rc = handle_install_extension(_make_args(extension_id=None))
    assert rc in (0, None)
    out = capsys.readouterr().out
    assert "canonical extension id" in out
    if sys.platform == "darwin":
        manifest = (fake_home / "Library/Application Support/Google/Chrome/"
                    "NativeMessagingHosts/local.trinity.capture.json")
    else:
        manifest = (fake_home / ".config/google-chrome/NativeMessagingHosts/"
                    "local.trinity.capture.json")
    assert manifest.exists()
    # Post-#271: canonical (key-pinned) id first, legacy pre-key sideload
    # ids kept alongside so an existing unreloaded install keeps capturing.
    from trinity_local.registry import extension_origin_ids
    origins = json.loads(manifest.read_text())["allowed_origins"]
    assert origins == [f"chrome-extension://{eid}/" for eid in extension_origin_ids()]
    assert origins[0] == f"chrome-extension://{CANONICAL_EXTENSION_ID}/"
