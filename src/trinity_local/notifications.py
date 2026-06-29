"""Cross-platform `open <path>` helper.

Used by commands/portal.py, commands/council.py, commands/review.py, and
commands/me_card.py to launch the OS default handler (browser, Preview,
etc.) for a generated artifact. macOS uses `open`, Linux uses `xdg-open`,
Windows uses `cmd /c start`, with a `webbrowser.open(file://...)` fallback.

Filename retained for back-compat with existing imports; the system-
notification feature it formerly housed (`notify`, `notifications_enabled`,
notifications-enable/disable CLI) was retired pre-launch — the launchpad's
own progress indicators replace it.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import webbrowser
from pathlib import Path


def open_path(path: str | Path) -> bool:
    target = Path(path).expanduser().resolve()
    try:
        if platform.system().lower() == "darwin" and shutil.which("open"):
            subprocess.run(["open", str(target)], capture_output=True, text=True, check=False)
            return True
        if platform.system().lower() == "windows" and shutil.which("cmd"):
            subprocess.run(
                ["cmd", "/c", "start", "", str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            return True
        if platform.system().lower() == "linux" and shutil.which("xdg-open"):
            subprocess.run(["xdg-open", str(target)], capture_output=True, text=True, check=False)
            return True
        return webbrowser.open(target.as_uri())
    except Exception:
        return False
