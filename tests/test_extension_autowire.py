"""Extension auto-wire: pre-register the native host for the canonical
extension id so the published extension connects with zero further setup.

The mechanism: install.sh pre-wires the host for ONE fixed id
(registry.CANONICAL_EXTENSION_ID). When the user installs the published
extension (same id), the host is already there → "registers itself if
found". A sideloaded build (different id) falls back to --extension-id.
CHROME_WEB_STORE_URL flips the launchpad CTA from sideload to one-click.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The PRE-KEY placeholder: the id Chrome path-derived for the founder's
# locally-loaded unpacked build before browser-extension/manifest.json
# carried a "key" (0.2.22, #271). The canonical id is now KEY-PINNED —
# identical for every sideload and preserved by a Web Store upload whose
# zip carries the key — so this value survives only as (a) the legacy
# allowed_origin for pre-key sideloads (registry.LEGACY_EXTENSION_IDS)
# and (b) the publish-coupling tripwire below: if anyone reverts the
# registry constant to this path-derived placeholder, publishing breaks
# every install again. Hard-coded here (not imported) so the guard keeps
# firing even if someone edits the registry constant.
_KNOWN_SIDELOAD_PLACEHOLDER_ID = "caaojjhagginmgobdaheincllmblcjoi"


class TestCanonicalIdSingleSourceOfTruth:
    def test_constant_is_valid_chrome_id(self):
        from trinity_local.registry import CANONICAL_EXTENSION_ID
        assert re.fullmatch(r"[a-p]{32}", CANONICAL_EXTENSION_ID), (
            "canonical extension id must be Chrome's 32-char a-p format"
        )

    def test_manifest_key_derives_canonical_id(self):
        """The REAL single-source-of-truth guard post-#271: the id Chrome
        assigns is derived from the manifest's embedded public key
        (sha256 of the DER key, first 16 bytes, nibbles mapped 0-f→a-p).
        registry.CANONICAL_EXTENSION_ID must equal that derivation —
        change either the key or the constant without the other and this
        reds. This is what makes the id machine-independent: every
        Load-unpacked sideload AND a Web Store upload carrying the key
        get this exact id, so the pre-wired host manifest always matches."""
        import base64
        import hashlib
        import json as _json
        from trinity_local.registry import CANONICAL_EXTENSION_ID
        manifest = _json.loads(
            (REPO / "browser-extension" / "manifest.json").read_text()
        )
        key_b64 = manifest.get("key")
        assert key_b64, (
            "browser-extension/manifest.json must carry a 'key' — without it "
            "Chrome path-derives a per-machine id and #271 regresses."
        )
        digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()
        derived = "".join(chr(ord("a") + int(c, 16)) for c in digest[:32])
        assert derived == CANONICAL_EXTENSION_ID, (
            f"manifest key derives id {derived} but registry says "
            f"{CANONICAL_EXTENSION_ID} — the two MUST move together."
        )

    def test_legacy_ids_still_accepted(self):
        """Pre-key sideloads (the founder's original install) keep working:
        the legacy path-derived id stays in the accepted-origins set."""
        from trinity_local.registry import extension_origin_ids
        assert _KNOWN_SIDELOAD_PLACEHOLDER_ID in extension_origin_ids()
        # canonical always leads the list (it's the primary sendMessage target)
        from trinity_local.registry import CANONICAL_EXTENSION_ID
        assert extension_origin_ids()[0] == CANONICAL_EXTENSION_ID

    def test_bash_resolver_default_matches_constant(self):
        """launcher_path_resolver.sh hard-codes the same id as its default
        (bash can't import Python). Drift = the resolver probes a different
        extension dir than the host is wired for. Keep them in lockstep."""
        from trinity_local.registry import CANONICAL_EXTENSION_ID
        resolver = (REPO / "scripts" / "launcher_path_resolver.sh").read_text()
        m = re.search(r'EXTENSION_ID="\$\{1:-([a-p]{32})\}"', resolver)
        assert m, "resolver must define EXTENSION_ID default in the {1:-<id>} form"
        assert m.group(1) == CANONICAL_EXTENSION_ID, (
            f"resolver default {m.group(1)} != registry CANONICAL_EXTENSION_ID "
            f"{CANONICAL_EXTENSION_ID} — update both when the id changes."
        )


class TestInstallShPreWiresHost:
    def test_install_sh_calls_install_extension(self):
        sh = (REPO / "scripts" / "install.sh").read_text()
        assert "install-extension" in sh, (
            "install.sh must pre-wire the capture host (best-effort) so the "
            "published extension connects with no second command."
        )
        # Best-effort: must not hard-fail the install if pre-wiring fails.
        assert re.search(r"install-extension[^\n]*\|\||install-extension.*then", sh) or \
            "could not pre-wire" in sh, (
            "the pre-wire step must be best-effort (browser capture is optional)."
        )


def _chrome_manifest(home: Path) -> Path:
    """Platform-aware native-messaging manifest path (matches install.py)."""
    import sys
    if sys.platform == "darwin":
        return (home / "Library/Application Support/Google/Chrome/"
                "NativeMessagingHosts/local.trinity.capture.json")
    return (home / ".config/google-chrome/NativeMessagingHosts/"
            "local.trinity.capture.json")


class TestInstallExtensionDefaultsToCanonical:
    def test_no_id_defaults_to_canonical_and_writes_manifest(self, tmp_path, monkeypatch):
        import json
        from types import SimpleNamespace

        from trinity_local.commands.install import handle_install_extension
        from trinity_local.registry import CANONICAL_EXTENSION_ID

        # install-extension writes under Path.home(); sandbox it.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        host = tmp_path / "trinity-local-capture-host"
        host.write_text("#!/bin/sh\n")
        rc = handle_install_extension(SimpleNamespace(
            extension_id=None, host_path=str(host), browsers=["chrome"], firefox=False,
        ))
        assert rc in (0, None)
        manifest = _chrome_manifest(tmp_path)
        assert manifest.exists(), "default install-extension must write the host manifest"
        d = json.loads(manifest.read_text())
        # Post-#271 the manifest accepts EVERY Trinity id: the key-pinned
        # canonical id FIRST, then the legacy pre-key sideload ids.
        from trinity_local.registry import extension_origin_ids
        assert d["allowed_origins"] == [
            f"chrome-extension://{eid}/" for eid in extension_origin_ids()
        ]
        assert d["allowed_origins"][0] == f"chrome-extension://{CANONICAL_EXTENSION_ID}/"

    def test_explicit_sideload_id_is_added_not_substituted(self, tmp_path, monkeypatch):
        """--extension-id (a fork with its own key / an odd sideload) must be
        ACCEPTED without dropping the canonical id — the host is inert for
        origins that never connect, and dropping canonical would re-break
        the store/keyed install the moment the fork user updates (#271)."""
        import json
        from types import SimpleNamespace

        from trinity_local.commands.install import handle_install_extension
        from trinity_local.registry import extension_origin_ids

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        host = tmp_path / "trinity-local-capture-host"
        host.write_text("#!/bin/sh\n")
        sideload = "abcdefghijklmnopabcdefghijklmnop"
        handle_install_extension(SimpleNamespace(
            extension_id=sideload, host_path=str(host), browsers=["chrome"], firefox=False,
        ))
        d = json.loads(_chrome_manifest(tmp_path).read_text())
        origins = d["allowed_origins"]
        assert origins[0] == f"chrome-extension://{sideload}/", (
            "the explicit id is the primary origin"
        )
        for eid in extension_origin_ids():
            assert f"chrome-extension://{eid}/" in origins, (
                f"canonical/legacy id {eid} must stay accepted alongside "
                "the explicit --extension-id"
            )

    def test_bogus_extension_id_rejected(self, tmp_path, monkeypatch):
        """The a-p 32-char validation still gates explicit ids."""
        from types import SimpleNamespace

        from trinity_local.commands.install import handle_install_extension

        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        host = tmp_path / "trinity-local-capture-host"
        host.write_text("#!/bin/sh\n")
        rc = handle_install_extension(SimpleNamespace(
            extension_id="not-a-real-id", host_path=str(host), browsers=["chrome"], firefox=False,
        ))
        assert rc == 1
        assert not _chrome_manifest(tmp_path).exists()


@pytest.mark.usefixtures("patch_trinity_home")
class TestWebStoreSwitch:
    def test_browser_extension_exposes_web_store_url(self):
        from trinity_local.launchpad_data import _browser_extension
        from trinity_local.registry import CHROME_WEB_STORE_URL
        ext = _browser_extension()
        assert "webStoreUrl" in ext
        assert ext["webStoreUrl"] == CHROME_WEB_STORE_URL

    def test_launchpad_card_gates_cta_on_web_store_url(self):
        tpl = (REPO / "src/trinity_local/launchpad_template.py").read_text()
        # The card branches on webStoreUrl: "Add to Chrome" when set,
        # sideload docs when empty.
        assert "pageData.browserExtension.webStoreUrl" in tpl
        assert "Add to Chrome" in tpl

    def test_publish_must_replace_the_sideload_placeholder_id(self):
        """#271 publish-coupling guard. The moment CHROME_WEB_STORE_URL is
        set (the extension is published + the launchpad advertises one-click
        "Add to Chrome"), CANONICAL_EXTENSION_ID is what install.sh pre-wires
        the native host for AND what the file:// launchpad's default
        sendMessage target resolves to. If it is still the founder's local
        sideload id at that point, EVERY Web Store install gets a host +
        launchpad wired to an id no installed extension has → dispatch and
        capture are silently dead for everyone (exactly #271).

        So: publish (non-empty store URL) and the placeholder id are mutually
        exclusive. The Web Store assigns its own permanent id — the founder
        cannot choose the placeholder value — so this can only fail if the
        store URL was flipped on without updating the id. Catch it in CI
        instead of in every new user's broken first run.
        """
        from trinity_local.registry import (
            CANONICAL_EXTENSION_ID,
            CHROME_WEB_STORE_URL,
        )
        if CHROME_WEB_STORE_URL.strip():
            assert CANONICAL_EXTENSION_ID != _KNOWN_SIDELOAD_PLACEHOLDER_ID, (
                "CHROME_WEB_STORE_URL is set (publish happened) but "
                "CANONICAL_EXTENSION_ID is still the local sideload placeholder "
                f"{_KNOWN_SIDELOAD_PLACEHOLDER_ID!r}. Replace it with the id the "
                "Chrome Web Store dashboard assigned to the published item (and "
                "update scripts/launcher_path_resolver.sh's default to match) "
                "before shipping — otherwise every Web Store install is wired to "
                "an id no extension has and capture/dispatch silently die (#271)."
            )
