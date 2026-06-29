---
class: live
---

# Install Trinity (Python library access — power-user path)

> The primary install path is the curl-bash installer in
> [`INSTALL-skill.md`](INSTALL-skill.md). It clones the repo, drops
> wrappers, and registers MCP. You don't need this doc unless you
> want to embed Trinity in your own Python code via
> `from trinity_local import …`.

## When to use this path

- You want to import Trinity's modules into your own Python project:
  `from trinity_local.council_runtime import …`
- You're running Trinity in a fresh CI environment where the
  curl-bash installer's wrappers in `~/.local/bin/` don't make sense
  (e.g., a Docker layer where you want pip-managed deps).
- You're a contributor working on Trinity itself — `pip install -e .`
  from a clone gives you the editable install.

**Trinity does NOT publish to PyPI.** No `pip install trinity-local`
shorthand. The path is always git-clone-then-pip-from-the-clone.

## Install

```bash
# Clone (or reuse the existing skill clone if you already ran the
# installer):
git clone <!-- canonical:github_repo_url -->https://github.com/keepwhatworks/trinity<!-- /canonical --> trinity-local
cd trinity-local

# Editable install — your changes to the cloned source take effect
# without re-installing:
pip install -e .

# Or one-shot install without -e if you don't plan to edit:
pip install .
```

If you already installed via the curl-bash, the same source is at
`~/.trinity/code/` (with `~/.claude/skills/trinity/` as a back-compat
symlink that resolves to the same place):

```bash
cd ~/.trinity/code/
pip install -e .
```

## Common workflows

```bash
# Verify everything's wired:
trinity-local status

# Ingest existing CLI transcripts into ~/.trinity/prompts/
# (auto-discovers ~/.claude/projects/, ~/.codex/sessions/, ~/.gemini/antigravity-cli/brain/)
trinity-local ingest-recent

# Dream the core memories (~5-15 min)
trinity-local dream

# Run a council on a single question
trinity-local council --task "what is the right caching strategy"

# Personalized eval suite
trinity-local eval-build
trinity-local eval-run --target antigravity
trinity-local eval-show
```

See `trinity-local --help` for the full command list.

## Why no PyPI publish?

Three reasons (per the launch architecture decision; see
[`three-tier-architecture.md`](three-tier-architecture.md)):

1. **Trust positioning**. Trinity's pitch is "your transcripts never
   leave your machine." A PyPI wheel adds a supply-chain trust step
   (`pip install` is opaque after the fact). A git clone is
   auditable end-to-end — `ls ~/.claude/skills/trinity/` shows
   every file the install ever touches.

2. **Distribution simplicity**. One channel (GitHub) — not two
   (GitHub + PyPI). Updates: `trinity-local update` does
   `git pull` + MCP refresh + status. No "is the pip cache
   stale" confusion.

3. **No launch-day gate**. PyPI publish is a slow human-in-the-loop
   process (twine upload, project metadata, version bump cadence).
   Skipping it means Monday's launch depends on exactly ONE external
   gate flipping: `github.com/keepwhatworks/trinity` going public.

## Audit log

The pip tier writes to `~/.trinity/audit.log` via
`scripts/_runtime.audit_log()` like the other tiers. Inspect with
`tail -20 ~/.trinity/audit.log`.

The trust-gating library (`trinity_local.trust`) was retired
2026-05-22 (post-launch sweep iter #117) — `~/.trinity/trust.toml` is
no longer consulted by any tier. Gating config + the dedicated CLI
land in v1.1 as a fresh build. See [`historical/trust-mode.md`](historical/trust-mode.md)
for the original design — preserved as the historical record of the
substrate Trinity moved away from.

## Heavy ops as standalone scripts

Trinity ships the heavy operations as shebang-runnable Python at
`scripts/` (`embed.py`, `cluster.py`, `pca.py`, `descriptor.py`,
`signature.py`, `anchor.py`). The pip tier imports from these
modules in v1.0; v1.1 inverts so the scripts are the canonical
location and the pip tier is the thin wrapper.

```bash
git clone <!-- canonical:github_repo_url -->https://github.com/keepwhatworks/trinity<!-- /canonical --> trinity-local
cd trinity-local
echo '{"texts": ["hello", "world"]}' | python3 scripts/embed.py
```

The first run creates a script-scoped venv at
`~/.trinity/.venvs/embed/` and installs deps; subsequent runs reuse
it. See `scripts/<name>.py --help` for each script's I/O contract.
The pip wheel isn't needed for this path — `scripts/` are
self-contained.

## See also

- [`INSTALL-skill.md`](INSTALL-skill.md) — primary install path
  (curl-bash; the user-facing default)
- [`INSTALL-extension.md`](INSTALL-extension.md) — Chrome extension
  for cross-surface UI
- [`three-tier-architecture.md`](three-tier-architecture.md) — full
  architecture spec
