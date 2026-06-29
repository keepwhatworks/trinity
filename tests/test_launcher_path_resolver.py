"""Tests for scripts/launcher_path_resolver.sh — the Chrome auto-update
foundation.

When the Web Store version of the Trinity extension ships, the Python
source rides inside the extension package. Chrome auto-updates the
extension every ~5 hours; this resolver script finds the latest
version dir at runtime so `~/.local/bin/trinity-local` picks up the
new Python without git pull.

Probe order pinned by these tests (drift here = silent regression):
  1. browser extension dirs, in preference order (Chrome → Brave → Edge → Arc)
     sorted -V across versions, latest wins
  2. ~/.trinity/code/ (sideload / dev / pre-Web-Store)
  3. ~/.claude/skills/trinity/ (pre-pivot legacy back-compat)

If none found → exit 1 with empty stdout. Caller (the install.sh
wrapper, eventually) surfaces a clear error.
"""
from __future__ import annotations

import subprocess
from pathlib import Path



SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "launcher_path_resolver.sh"
EXT_ID = "caaojjhagginmgobdaheincllmblcjoi"
MARKER_REL = "trinity/src/trinity_local/__init__.py"


def _run(home: Path, *args: str, env_extra: dict | None = None) -> tuple[int, str, str]:
    """Run the resolver with HOME pointed at a temp dir + a clean env.
    Returns (exit_code, stdout, stderr)."""
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _seed_extension(home: Path, browser_path: str, version: str) -> Path:
    """Create a fake browser extension dir with the marker file present.
    Returns the resolved trinity source dir that should be returned."""
    ext_dir = home / browser_path / EXT_ID / version
    marker = ext_dir / MARKER_REL
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return ext_dir / "trinity"


def _seed_canonical(home: Path) -> Path:
    """Drop a ~/.trinity/code/src/trinity_local/ marker."""
    target = home / ".trinity" / "code" / "src" / "trinity_local"
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").touch()
    return home / ".trinity" / "code"


def _seed_legacy_skill(home: Path) -> Path:
    """Drop a ~/.claude/skills/trinity/src/trinity_local/ marker."""
    target = home / ".claude" / "skills" / "trinity" / "src" / "trinity_local"
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").touch()
    return home / ".claude" / "skills" / "trinity"


# ─── Force-source override ─────────────────────────────────────────

class TestForceSourceOverride:
    def test_explicit_override_wins(self, tmp_path):
        """TRINITY_FORCE_SOURCE bypasses all probing — useful for dev
        installs and tests. The path still has to be a valid source
        dir or we ignore the override."""
        forced = tmp_path / "custom" / "src" / "trinity_local"
        forced.mkdir(parents=True)
        (forced / "__init__.py").touch()
        rc, out, _ = _run(
            tmp_path,
            env_extra={"TRINITY_FORCE_SOURCE": str(tmp_path / "custom")},
        )
        assert rc == 0
        assert out == str(tmp_path / "custom")

    def test_invalid_force_source_falls_through(self, tmp_path):
        """A forced path that ISN'T a valid source dir should be
        ignored, not picked. Otherwise a stale env var could break
        the resolver."""
        _seed_canonical(tmp_path)
        rc, out, _ = _run(
            tmp_path,
            env_extra={"TRINITY_FORCE_SOURCE": "/nonexistent/path"},
        )
        assert rc == 0
        assert out == str(tmp_path / ".trinity" / "code")


# ─── Browser extension probing ─────────────────────────────────────

class TestBrowserExtensionProbing:
    def test_chrome_macos_path_found(self, tmp_path):
        """The macOS Chrome path is the canonical browser location.
        With a fake extension installed, the resolver should pick it."""
        expected = _seed_extension(
            tmp_path,
            "Library/Application Support/Google/Chrome/Default/Extensions",
            "1.2.3_0",
        )
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(expected)

    def test_brave_linux_path_found(self, tmp_path):
        """Linux Brave path — same shape, different prefix. Multi-browser
        support means the resolver must check Linux paths too."""
        expected = _seed_extension(
            tmp_path,
            ".config/BraveSoftware/Brave-Browser/Default/Extensions",
            "1.0.0_0",
        )
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(expected)

    def test_latest_version_wins(self, tmp_path):
        """Chrome rewrites the version subdir on every update. The
        resolver MUST pick the highest semver, not 'first' or
        'last by mtime'. sort -V handles this."""
        ext_root = "Library/Application Support/Google/Chrome/Default/Extensions"
        _seed_extension(tmp_path, ext_root, "1.0.0_0")
        _seed_extension(tmp_path, ext_root, "1.10.0_0")  # 1.10 > 1.2 via -V
        _seed_extension(tmp_path, ext_root, "1.2.0_0")
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        # The expected winner is 1.10.0_0.
        assert out.endswith("1.10.0_0/trinity"), (
            f"sort -V should pick 1.10.0_0 as highest; got {out}"
        )

    def test_chrome_wins_over_brave_when_both_present(self, tmp_path):
        """Probe order is Chrome first. When the user has the extension
        in both Chrome and Brave, Chrome's version is the one Trinity
        loads — picking a single browser keeps the auto-update story
        deterministic."""
        chrome_path = _seed_extension(
            tmp_path,
            "Library/Application Support/Google/Chrome/Default/Extensions",
            "1.0.0_0",
        )
        _seed_extension(
            tmp_path,
            "Library/Application Support/BraveSoftware/Brave-Browser/Default/Extensions",
            "2.0.0_0",
        )
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        # Even though Brave's version is HIGHER, Chrome wins because of
        # probe order.
        assert out == str(chrome_path)

    def test_extension_dir_without_marker_skipped(self, tmp_path):
        """A version subdir that exists but doesn't contain
        trinity/src/trinity_local/__init__.py is from BEFORE the Python-
        in-package change. Skip it — otherwise we'd return a path
        the launcher can't actually execute Python from."""
        ext_root = "Library/Application Support/Google/Chrome/Default/Extensions"
        bad_version = tmp_path / ext_root / EXT_ID / "0.5.0_0"
        bad_version.mkdir(parents=True)
        # NO marker file written.
        _seed_canonical(tmp_path)
        rc, out, _ = _run(tmp_path)
        # Should fall through to canonical, not return the bad version.
        assert rc == 0
        assert out == str(tmp_path / ".trinity" / "code")

    def test_custom_extension_id_arg(self, tmp_path):
        """The script takes an extension ID arg so future versions of
        Trinity could use a different ID. Default exists for the
        common case."""
        custom_id = "abcdefghijklmnopqrstuvwxyzaaaaaa"
        expected = tmp_path / "Library/Application Support/Google/Chrome/Default/Extensions" / custom_id / "1.0.0_0"
        marker = expected / MARKER_REL
        marker.parent.mkdir(parents=True)
        marker.touch()
        rc, out, _ = _run(tmp_path, custom_id)
        assert rc == 0
        assert out == str(expected / "trinity")


# ─── Fallbacks ─────────────────────────────────────────────────────

class TestFallbacks:
    def test_canonical_trinity_code_fallback(self, tmp_path):
        """No extension installed → fall back to ~/.trinity/code/.
        This is the curl|bash sideload path Trinity uses today."""
        expected = _seed_canonical(tmp_path)
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(expected)

    def test_legacy_skill_fallback(self, tmp_path):
        """No extension, no ~/.trinity/code/ → fall back to the
        pre-2026-05-19-pivot location at ~/.claude/skills/trinity/.
        Existing installs must keep working through the pivot."""
        expected = _seed_legacy_skill(tmp_path)
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(expected)

    def test_no_source_anywhere_exits_nonzero(self, tmp_path):
        """If absolutely nothing's installed, exit code 1 + empty
        stdout. The caller surfaces the error; we don't pretend a
        nonexistent path is valid."""
        rc, out, _ = _run(tmp_path)
        assert rc == 1
        assert out == ""

    def test_canonical_wins_over_legacy(self, tmp_path):
        """When BOTH ~/.trinity/code/ and ~/.claude/skills/trinity/
        exist (mid-migration), the canonical post-pivot location wins
        — otherwise the user would silently stay on the legacy
        location forever."""
        canonical = _seed_canonical(tmp_path)
        _seed_legacy_skill(tmp_path)
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(canonical)

    def test_extension_wins_over_canonical(self, tmp_path):
        """Extension installed AND canonical present → extension wins
        because Chrome auto-update is the cleaner path. The user only
        falls back to canonical when they're on a sideload."""
        ext = _seed_extension(
            tmp_path,
            "Library/Application Support/Google/Chrome/Default/Extensions",
            "1.0.0_0",
        )
        _seed_canonical(tmp_path)
        rc, out, _ = _run(tmp_path)
        assert rc == 0
        assert out == str(ext)
