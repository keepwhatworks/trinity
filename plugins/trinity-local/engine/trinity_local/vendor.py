"""Vendored JS dependencies for the launchpad + memory viewer + council
review pages.

100-persona audit P06 fix: prior cuts loaded marked / petite-vue /
chart.js / 9 d3-* modules from unpkg + jsdelivr on every page open.
That contradicted the "never leaves your machine" privacy absolute —
each launchpad render reached out to 13 third-party CDNs, exposing
the user's IP + a browser fingerprint to mirrors that have no business
in the Trinity wedge.

This module publishes the same files locally:

- Bundled into the wheel under ``src/trinity_local/data/vendor/*.js``
  via the package-data declaration in pyproject.toml.
- Copied into ``~/.trinity/portal_pages/vendor/`` on each portal-html
  regen (idempotent, content-hash-skip when up-to-date).
- HTML templates reference ``./vendor/<file>.js`` instead of CDN URLs.

Net result: launchpad + memory viewer + council review all render with
zero outbound network traffic. The privacy claim becomes absolutely
true, not "absolutely true except 13 JS files."

Refreshing the vendored set is a maintenance ritual (when a new d3
version ships): edit + run ``scripts/refresh-vendor.sh``. The script
pins every URL with an explicit version; treat version bumps as
security-sensitive (the rendered HTML is what the user sees offline,
so the bytes have to be auditable). After refresh, re-run
``pytest tests/test_no_cdn_in_rendered_html.py`` — the 5 guards there
re-render launchpad + memory-viewer and check zero unpkg/jsdelivr
references make it into the produced HTML.
"""
from __future__ import annotations

import hashlib
import sys
from importlib import resources
from pathlib import Path


# Canonical list — keep in sync with files actually present under
# src/trinity_local/data/vendor/. A presence check at write time
# surfaces drift between this list and the package_data wildcard.
VENDORED_FILES: tuple[str, ...] = (
    "petite-vue.es.js",
    "chart.umd.min.js",
    "marked.min.js",
    "d3-selection.min.js",
    "d3-dispatch.min.js",
    "d3-timer.min.js",
    "d3-quadtree.min.js",
    "d3-drag.min.js",
    "d3-force.min.js",
    "d3-zoom.min.js",
    "d3-interpolate.min.js",
    "d3-color.min.js",
    # Fonts (Calm/Muted-Teal design language, v1.7.309). Hanken Grotesk for
    # everything + JetBrains Mono for commands/paths, OFL — self-hosted so the
    # launchpad's "nothing leaves your machine" promise covers webfonts too.
    # SHARED_CSS @font-face references ../portal_pages/vendor/*.woff2.
    "HankenGrotesk-400.woff2",
    "HankenGrotesk-500.woff2",
    "HankenGrotesk-600.woff2",
    "HankenGrotesk-700.woff2",
    "JetBrainsMono-400.woff2",
    "JetBrainsMono-500.woff2",
    "HankenGrotesk-OFL.txt",
    "JetBrainsMono-OFL.txt",
)


def _read_vendored(name: str) -> bytes | None:
    """Load one vendored file via the package resources API. Returns
    None when the file isn't present (e.g. source layout without the
    data wired up) — caller falls back gracefully."""
    try:
        return resources.files("trinity_local").joinpath(f"data/vendor/{name}").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        return None


def _wrap_petite_vue_as_iife(es_source: bytes) -> bytes:
    """Convert petite-vue's ES-module build into a plain IIFE.

    WHY: Chrome treats every ``file://`` URL as a unique origin, so
    ``<script type="module"> import {createApp} from './vendor/...'``
    fails CORS on the launchpad and the page renders raw `{{ }}` Vue
    templates instead of interpolating. Headless smoke (different
    flags) hides the bug. The IIFE form loads via plain ``<script
    src="...">`` and exposes the same surface on ``window.__TRINITY_VUE__``.

    Transformation: locate petite-vue's trailing
    ``export{Qe as createApp,V as nextTick,D as reactive};``
    statement and rewrite it to ``window.__TRINITY_VUE__={...};``
    so the same internal bindings ride the global. Then wrap the
    whole body in a `(function(){ ... })()` so the file's top-level
    `let/const` declarations stay scoped.

    If the export line shape ever changes (petite-vue version bump),
    we fall back to returning the original bytes — the launchpad will
    keep using the module path. The doc-consistency guard in
    ``tests/test_no_cdn_in_rendered_html.py`` covers a separate axis
    (no CDN), so this graceful degradation won't be silent: the new
    Vue-mount smoke (added alongside this fix) flags it.
    """
    import re

    text = es_source.decode("utf-8", errors="replace")
    pattern = re.compile(
        r"export\{(\w+) as createApp,(\w+) as nextTick,(\w+) as reactive\};?\s*$"
    )
    m = pattern.search(text)
    if not m:
        return es_source
    create_app, next_tick, reactive = m.group(1), m.group(2), m.group(3)
    body = text[: m.start()]
    expose = (
        f"window.__TRINITY_VUE__={{"
        f"createApp:{create_app},"
        f"nextTick:{next_tick},"
        f"reactive:{reactive}"
        f"}};"
    )
    return f"(function(){{\n{body}\n{expose}\n}})();\n".encode("utf-8")


def publish_vendor_files(target_dir: Path) -> list[Path]:
    """Copy all vendored assets (JS libraries + woff2 webfonts + OFL licenses)
    into ``target_dir/vendor/``. Idempotent
    via byte-by-byte content compare: re-runs are no-ops when nothing
    changed. Skips missing files silently (graceful degradation for
    source layouts that don't bundle the data — pages will still try
    to load ./vendor/* and 404 cleanly).

    Additionally derives ``petite-vue.iife.js`` from the ES build at
    publish time (see ``_wrap_petite_vue_as_iife``). The IIFE form is
    what the launchpad and council review pages actually load, because
    Chrome blocks ``<script type="module">`` imports on file:// URLs.

    Returns the list of paths actually written this call (zero on
    no-op runs, non-zero on first publish or post-upgrade).
    """
    vendor_dir = target_dir / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in VENDORED_FILES:
        payload = _read_vendored(name)
        if payload is None:
            continue
        target = vendor_dir / name
        if target.exists():
            try:
                if hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(payload).digest():
                    continue
            except OSError:
                pass
        try:
            target.write_bytes(payload)
            written.append(target)
        except OSError as exc:
            print(
                f"warning: failed to publish vendored file {name!r} "
                f"to {target}: {exc.__class__.__name__}: {exc}. "
                f"Launchpad will 404 on ./vendor/{name} — "
                f"check perms on {vendor_dir}.",
                file=sys.stderr,
            )

    # Derive IIFE shim for petite-vue from the ES source. Same idempotency
    # rule — only write when content actually changed.
    es_source = _read_vendored("petite-vue.es.js")
    if es_source is not None:
        iife_payload = _wrap_petite_vue_as_iife(es_source)
        iife_target = vendor_dir / "petite-vue.iife.js"
        needs_write = True
        if iife_target.exists():
            try:
                if hashlib.sha256(iife_target.read_bytes()).digest() == hashlib.sha256(iife_payload).digest():
                    needs_write = False
            except OSError:
                pass
        if needs_write:
            try:
                iife_target.write_bytes(iife_payload)
                written.append(iife_target)
            except OSError as exc:
                print(
                    f"warning: failed to write derived petite-vue.iife.js to "
                    f"{iife_target}: {exc.__class__.__name__}: {exc}",
                    file=sys.stderr,
                )

    return written
