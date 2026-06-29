"""Enable ``python -m trinity_local [--mcp] ...`` — the same entrypoint as the
``trinity-local`` console script (``[project.scripts]`` → ``main:main``).

Lets the Claude Code plugin's MCP launcher fall back to ``python3 -m trinity_local
--mcp`` when the ``trinity-local`` console script isn't on PATH but the package is
importable (e.g. installed into a venv that isn't on the plugin host's PATH).
"""
from __future__ import annotations

from .main import main

if __name__ == "__main__":
    main()
