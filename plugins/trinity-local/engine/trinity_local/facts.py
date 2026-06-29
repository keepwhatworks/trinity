"""Multi-surface canonical facts (#131).

The canonical-placeholder system in ``scripts/render_docs.py`` handles
COUNTS (test count, MCP tool count, py file count, etc.) — values
computed by introspecting the codebase. This module is the sibling
surface for CANONICAL STRINGS that recur across ≥2 user-facing
surfaces: brand-domain URLs, install commands, versioned manifests,
schema banners, etc.

Per council_76e5aef79bb9f241 #3. The motivation is the same as #129's
provider/tool SSOT registry: strings that recur across docs drift
multiple times during development (e.g., an early brand-domain change
hit 5 surfaces by hand; each new launch-day artifact adds another).
Defining facts in one Python
module and consuming them via the existing canonical-placeholder
syntax means new surfaces automatically stay current after a re-render.

Usage in docs:
    <!-- canonical:landing_domain -->keepwhatworks.com<!-- /canonical -->

Usage in code (e.g. share-card renderer):
    from trinity_local.facts import LANDING_DOMAIN
    footer = f"⠕ Trinity · {LANDING_DOMAIN}"

The renderer in ``scripts/render_docs.py`` imports ``FACTS`` and merges
it into its CANONICAL dict, so a new fact added here is automatically
available as a `<!-- canonical:NAME -->` placeholder everywhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────────────────
# Fact values — direct constants
# ───────────────────────────────────────────────────────────────────────

# The brand-shared landing domain for Trinity share cards + footer
# tagline + docs/CNAME. Defined once here as the SSOT so the next
# domain change is a 1-line edit (an earlier by-hand domain change
# touched 5 surfaces — the motivation for centralizing it).
# Also re-exported from share_card_base.LANDING_URL for back-compat
# with existing share-card consumers.
LANDING_DOMAIN: str = "keepwhatworks.com"

# The GitHub repo URL — appears in 20+ surfaces (install docs,
# launch-day artifacts, README, launch-package). Trinity's `install.sh`
# at scripts/install.sh references this implicitly; if the repo ever
# moves (rename, org transfer, fork), every surface needs updating.
# Single source here, embedded into install_command below.
GITHUB_REPO_URL: str = "https://github.com/keepwhatworks/trinity"

# The canonical curl-bash install command, appears in README hero +
# every INSTALL-*.md + launch-day artifacts. Derived from
# GITHUB_REPO_URL so a repo move only requires one edit upstream.
INSTALL_COMMAND: str = (
    f"curl -fsSL {GITHUB_REPO_URL.replace('https://github.com', 'https://raw.githubusercontent.com')}"
    "/main/scripts/install.sh | bash"
)


# ───────────────────────────────────────────────────────────────────────
# Fact derivers — computed at render time
# ───────────────────────────────────────────────────────────────────────

def chrome_extension_version() -> str:
    """Read browser-extension/manifest.json's `version` field.

    The manifest IS the source of truth for the extension version.
    Doc surfaces that name the version (e.g. install instructions)
    should canonical-template this value instead of inlining a literal.
    """
    manifest_path = _REPO_ROOT / "browser-extension" / "manifest.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "unknown"
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(data.get("version", "unknown"))


# ───────────────────────────────────────────────────────────────────────
# Registry — consumed by scripts/render_docs.py
# ───────────────────────────────────────────────────────────────────────

# Each entry is (name → callable returning the rendered string). The
# callable indirection lets us mix direct constants (wrapped trivially)
# with computed values (chrome_extension_version) under one shape.
# render_docs.py merges this into its CANONICAL dict at startup.
FACTS: dict[str, Callable[[], str]] = {
    "landing_domain": lambda: LANDING_DOMAIN,
    "chrome_extension_version": chrome_extension_version,
    "github_repo_url": lambda: GITHUB_REPO_URL,
    "install_command": lambda: INSTALL_COMMAND,
}
