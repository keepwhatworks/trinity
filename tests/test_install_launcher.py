"""Tests for the Phase 6 cross-platform desktop launcher installer.

`install-launcher` writes a per-platform desktop entry that opens the
local file:// launchpad:
  - macOS  → ~/Applications/Trinity Local.webloc
  - Linux  → ~/.local/share/applications/trinity-local.desktop
  - Win    → Start Menu/Programs/Trinity Local.url

Tests pin the on-disk shape so a future contributor can't quietly
change the entry format and have the desktop launcher stop appearing
in the user's app menu / start menu.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path / "trinity"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(
        lambda cls: tmp_path / "home"
    ))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_install_linux_desktop_entry_writes_valid_xdg(isolated_home, tmp_path):
    """The .desktop file must be a valid XDG entry: [Desktop Entry]
    header, Type, Name, Exec lines. Most launchers refuse to display
    entries missing any of these."""
    from trinity_local.commands.install import _install_linux_desktop_entry

    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")

    target = _install_linux_desktop_entry(
        launchpad, destination=tmp_path / "applications"
    )
    assert target.name == "trinity-local.desktop"
    assert target.exists()

    content = target.read_text()
    assert content.startswith("[Desktop Entry]")
    assert "Type=Application" in content
    assert "Name=Trinity Local" in content
    assert "Exec=xdg-open " in content
    # The Exec path must point at the actual launchpad as a file:// URI.
    assert launchpad.resolve().as_uri() in content


def test_install_linux_desktop_entry_is_executable(isolated_home, tmp_path):
    """XDG desktop entries usually need the execute bit set for the
    desktop environment to honor them (GNOME especially)."""
    from trinity_local.commands.install import _install_linux_desktop_entry

    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")

    target = _install_linux_desktop_entry(
        launchpad, destination=tmp_path / "applications"
    )
    mode = target.stat().st_mode & 0o777
    assert mode & 0o100, f".desktop must be user-executable (got {oct(mode)})"


def test_install_linux_desktop_entry_idempotent(isolated_home, tmp_path):
    """Re-running install-launcher must not error or corrupt the entry."""
    from trinity_local.commands.install import _install_linux_desktop_entry

    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")

    target1 = _install_linux_desktop_entry(
        launchpad, destination=tmp_path / "applications"
    )
    content1 = target1.read_text()
    target2 = _install_linux_desktop_entry(
        launchpad, destination=tmp_path / "applications"
    )
    assert target1 == target2
    assert target2.read_text() == content1


def test_install_windows_url_shortcut_writes_internet_shortcut(
    isolated_home, tmp_path
):
    """The .url Internet Shortcut format is `[InternetShortcut]` + `URL=...`
    — minimal, schema-stable, and Windows respects it inside the Start
    Menu folder. Pin the format so a future refactor doesn't accidentally
    write a .lnk that needs COM bindings."""
    from trinity_local.commands.install import _install_windows_url_shortcut

    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")

    target = _install_windows_url_shortcut(
        launchpad, destination=tmp_path / "StartMenu"
    )
    assert target.name == "Trinity Local.url"
    assert target.exists()

    content = target.read_text()
    assert content.startswith("[InternetShortcut]")
    assert f"URL={launchpad.resolve().as_uri()}" in content


def test_handle_install_launcher_unsupported_platform_fails_loudly(
    isolated_home, tmp_path, monkeypatch, capsys
):
    """On an exotic platform (sys.platform not in {darwin, linux*, win*}),
    the CLI must print a precise hint pointing at `trinity-local serve`
    and exit non-zero. Silent failure here looks like the install
    worked, then the user wonders why the launcher isn't in their menu."""
    from trinity_local.commands import install as install_mod

    monkeypatch.setattr(install_mod.sys, "platform", "haiku")
    monkeypatch.setattr(
        "trinity_local.refresh.refresh_launchpad",
        lambda *a, **kw: tmp_path / "launchpad.html",
    )
    (tmp_path / "launchpad.html").write_text("<html/>")

    args = SimpleNamespace(destination=None)
    rc = install_mod.handle_install_launcher(args)
    assert rc == 1

    out, err = capsys.readouterr()
    assert "haiku" in err
    assert "serve" in err


def test_handle_install_launcher_linux_emits_json(
    isolated_home, tmp_path, monkeypatch, capsys
):
    """End-to-end on Linux: handle_install_launcher writes the .desktop
    file AND prints the per-platform JSON shape that the README + tests
    consume."""
    import json
    from trinity_local.commands import install as install_mod

    monkeypatch.setattr(install_mod.sys, "platform", "linux")
    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")
    monkeypatch.setattr(
        "trinity_local.refresh.refresh_launchpad",
        lambda *a, **kw: launchpad,
    )

    apps_dir = tmp_path / "fake-applications"
    args = SimpleNamespace(destination=[str(apps_dir)])
    rc = install_mod.handle_install_launcher(args)
    assert rc == 0

    out, _ = capsys.readouterr()
    payload = json.loads(out)
    assert payload["platform"] == "linux"
    assert payload["launchpad_path"] == str(launchpad)
    assert len(payload["launcher_paths"]) == 1
    written = Path(payload["launcher_paths"][0])
    assert written.exists()
    assert written.name == "trinity-local.desktop"


def test_install_macos_webloc_writes_internet_location(isolated_home, tmp_path):
    """The .webloc file is a tiny plist; double-clicking it in Finder
    opens the URL in the user's default browser. Pin the format so a
    future refactor doesn't silently break Finder's launch path."""
    from trinity_local.commands.install import _install_macos_webloc

    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")

    target = _install_macos_webloc(launchpad, destination=tmp_path / "Applications")
    assert target.name == "Trinity Local.webloc"
    assert target.exists()

    content = target.read_text(encoding="utf-8")
    assert "<plist" in content
    assert "<key>URL</key>" in content
    assert launchpad.resolve().as_uri() in content


def test_handle_install_launcher_macos_writes_webloc(
    isolated_home, tmp_path, monkeypatch, capsys
):
    """End-to-end on macOS: handle_install_launcher writes a .webloc
    AND prints the per-platform JSON shape."""
    import json
    from trinity_local.commands import install as install_mod

    monkeypatch.setattr(install_mod.sys, "platform", "darwin")
    launchpad = tmp_path / "launchpad.html"
    launchpad.write_text("<html/>")
    monkeypatch.setattr(
        "trinity_local.refresh.refresh_launchpad",
        lambda *a, **kw: launchpad,
    )

    apps_dir = tmp_path / "fake-Applications"
    args = SimpleNamespace(destination=[str(apps_dir)])
    rc = install_mod.handle_install_launcher(args)
    assert rc == 0

    out, _ = capsys.readouterr()
    payload = json.loads(out)
    assert payload["platform"] == "darwin"
    assert len(payload["launcher_paths"]) == 1
    written = Path(payload["launcher_paths"][0])
    assert written.exists()
    assert written.name == "Trinity Local.webloc"


def test_handle_install_launcher_registered_in_parser():
    """The subcommand must be reachable from the public CLI. Without
    this registration, the handler exists but `trinity-local
    install-launcher` errors with `unknown subcommand`."""
    import argparse
    from trinity_local.commands.install import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register(subparsers)

    args = parser.parse_args(["install-launcher"])
    assert getattr(args, "handler", None) is not None
    assert args.handler.__name__ == "handle_install_launcher"
