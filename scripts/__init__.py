"""Trinity scripts/ — heavy operations as shebang-runnable + importable.

Phase 2 of the three-tier architecture (council_ff3da1fa84906791).
Each script in this package has a dual interface:

  1. **Shebang-executable**: `python3 scripts/embed.py < input.json`
     — runs standalone with on-demand venv-scoped dependencies at
     `~/.trinity/.venvs/<script_name>/`. The skill tier uses this
     path via Claude Code's bash tool.

  2. **Importable**: `from scripts.embed import embed_batch` — the
     pip tier (`trinity_local/`) imports the same functions. Deps
     are assumed to be pre-installed at pip-install time, so the
     venv-bootstrap block is a no-op when imported.

Shared utilities (audit log, venv bootstrap) live in `_runtime.py`.
"""
