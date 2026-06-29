"""Tests for #131 — facts.py + extended canonical renderer.

Three guard families:
1. Fact values are non-empty + correct shape
2. FACTS registry is wired into render_docs.CANONICAL
3. Code consumers still see the constants (back-compat with the
   pre-#131 share_card_base.LANDING_URL import path)
"""
from __future__ import annotations


class TestFactValues:
    def test_landing_domain_is_keepwhatworks(self):
        from trinity_local.facts import LANDING_DOMAIN

        assert LANDING_DOMAIN == "keepwhatworks.com"

    def test_chrome_extension_version_parses_from_manifest(self):
        from trinity_local.facts import chrome_extension_version

        version = chrome_extension_version()
        # MV3 version format: dotted digits like "0.2.17"
        assert version != "unknown"
        assert "." in version
        parts = version.split(".")
        assert all(part.isdigit() for part in parts), f"Non-numeric part in {version!r}"

    def test_github_repo_url_well_formed(self):
        from trinity_local.facts import GITHUB_REPO_URL

        assert GITHUB_REPO_URL.startswith("https://github.com/")
        # No trailing slash — every consumer concatenates suffix paths
        assert not GITHUB_REPO_URL.endswith("/")

    def test_install_command_derives_from_repo_url(self):
        """The install command must point at the raw-content host of
        the SAME repo as GITHUB_REPO_URL. Catches drift where someone
        edits one constant without the other."""
        from trinity_local.facts import GITHUB_REPO_URL, INSTALL_COMMAND

        # Repo path (everything after github.com) must appear in the
        # raw-content URL inside the install command
        repo_path = GITHUB_REPO_URL.replace("https://github.com", "")
        assert repo_path in INSTALL_COMMAND
        # The command must be a curl pipe to bash — the canonical
        # one-liner shape the README hero block + launch-day artifacts
        # reference.
        assert INSTALL_COMMAND.startswith("curl -fsSL ")
        assert INSTALL_COMMAND.endswith(" | bash")
        assert "scripts/install.sh" in INSTALL_COMMAND

    def test_chrome_extension_version_matches_manifest_directly(self):
        """Drift guard: fact derivation must produce same value the
        manifest holds. Catches a future facts.py bug that misreads
        the JSON or returns a wrong key."""
        import json
        from pathlib import Path
        from trinity_local.facts import chrome_extension_version

        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "browser-extension" / "manifest.json").read_text(encoding="utf-8")
        )
        assert chrome_extension_version() == manifest["version"]


class TestFactsRegistry:
    def test_facts_registry_contains_expected_keys(self):
        from trinity_local.facts import FACTS

        assert "landing_domain" in FACTS
        assert "chrome_extension_version" in FACTS
        assert "github_repo_url" in FACTS
        assert "install_command" in FACTS

    def test_each_fact_callable_returns_non_empty_string(self):
        from trinity_local.facts import FACTS

        for name, fn in FACTS.items():
            value = fn()
            assert isinstance(value, str), f"{name} returned non-str {type(value)}"
            assert value, f"{name} returned empty string"
            assert value != "unknown", f"{name} fell through to 'unknown' fallback"


class TestRendererWiring:
    def test_canonical_dict_includes_facts(self):
        """render_docs.CANONICAL must merge facts.FACTS at startup so
        new fact names automatically become valid <!-- canonical:... -->
        placeholders without touching the renderer."""
        import importlib.util
        from pathlib import Path

        renderer_path = Path(__file__).resolve().parents[1] / "scripts" / "render_docs.py"
        spec = importlib.util.spec_from_file_location("render_docs_for_test", renderer_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert "landing_domain" in module.CANONICAL
        assert "chrome_extension_version" in module.CANONICAL
        # Counts should still be present
        assert "test_count" in module.CANONICAL


class TestBackCompat:
    def test_landing_url_constant_still_resolves_to_canonical_domain(self):
        """share_card_base.LANDING_URL is the import path many existing
        share-card consumers use. Refactor must preserve it."""
        from trinity_local.share_card_base import LANDING_URL
        from trinity_local.facts import LANDING_DOMAIN

        assert LANDING_URL == LANDING_DOMAIN
        assert LANDING_URL == "keepwhatworks.com"

    def test_footer_tagline_uses_canonical_domain(self):
        from trinity_local.share_card_base import FOOTER_TAGLINE
        from trinity_local.facts import LANDING_DOMAIN

        assert LANDING_DOMAIN in FOOTER_TAGLINE
