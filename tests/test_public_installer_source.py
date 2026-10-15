"""docs/install.sh picks what to install: the release's wheel when it has one (the only artifact
that can carry the telemetry key, which is never in git), else the tag's source tarball; and
TRINITY_CHANNEL reaches `trinity-local install`. Run against stub curl / uv / trinity-local."""
from __future__ import annotations

import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = ROOT / "docs" / "install.sh"
TAGS = json.dumps([{"name": "v1.7.401"}, {"name": "v1.7.400"}], indent=2)
WHEEL = "https://github.com/keepwhatworks/trinity/releases/download/v1.7.401/trinity_local-1.7.401-py3-none-any.whl"


def _run(tmp_path, release: str | None, channel: str | None = None):
    stubs, log = tmp_path / "bin", tmp_path / "log"
    stubs.mkdir()
    rel_file = tmp_path / "release.json"
    if release is not None:
        rel_file.write_text(release)
    (stubs / "curl").write_text(f"""#!/bin/sh
for a in "$@"; do url="$a"; done
case "$url" in
  */tags) printf '%s\\n' '{TAGS}' ;;
  */releases/tags/*) [ -f "{rel_file}" ] && cat "{rel_file}" || exit 22 ;;
  *) exit 22 ;;
esac
""")
    (stubs / "uv").write_text(f"""#!/bin/sh
if [ "$1 $2" = "tool dir" ]; then echo "{stubs}"; exit 0; fi
echo "uv $*" >> "{log}"
""")
    (stubs / "trinity-local").write_text(f"""#!/bin/sh
echo "trinity-local $* channel=${{TRINITY_CHANNEL:-}}" >> "{log}"
""")
    for f in stubs.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path)}
    if channel:
        env["TRINITY_CHANNEL"] = channel
    r = subprocess.run(["sh", str(INSTALL)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return log.read_text().splitlines()


def _installed(lines):
    return [l for l in lines if l.startswith("uv tool install")][0].split()[-1]


def test_the_release_wheel_is_preferred(tmp_path):
    rel = json.dumps({"assets": [{"browser_download_url": WHEEL}]}, indent=2)
    assert _installed(_run(tmp_path, rel)) == WHEEL


def test_a_release_without_a_wheel_installs_the_tarball(tmp_path):
    rel = json.dumps({"assets": []}, indent=2)
    assert _installed(_run(tmp_path, rel)).endswith("/archive/refs/tags/v1.7.401.tar.gz")


def test_a_missing_release_installs_the_tarball(tmp_path):
    assert _installed(_run(tmp_path, None)).endswith("/archive/refs/tags/v1.7.401.tar.gz")


def test_the_channel_reaches_trinity_local_install(tmp_path):
    lines = _run(tmp_path, None, channel="hn")
    assert "trinity-local install channel=hn" in lines

