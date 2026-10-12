"""The site's analytics snippet and the privacy page's description of it move together.

With docs/analytics-snippet.html present, every public page carries it exactly once and the
privacy page describes Plausible and offers the opt-out (res_188: the notice and the objection
are the site's half of the consent exemption). Without it, no page carries a snippet and the
privacy page still says the site runs no analytics scripts.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("site_analytics", ROOT / "scripts" / "site_analytics.py")
SA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SA)
PRIVACY = (ROOT / "docs" / "privacy.html").read_text(encoding="utf-8")


# Any analytics loader, managed or not. The invariant is "whatever counts visitors is disclosed
# and can be refused", not "the managed block is present".
ANALYTICS = re.compile(r"plausible\.io|googletagmanager\.com|google-analytics\.com|\bgtag\(|"
                       r"cloudflareinsights\.com|goatcounter\.com|static\.cloudflareinsights", re.I)


def test_snippet_and_privacy_copy_agree():
    enabled = SA.SNIPPET.exists()
    for p in SA.pages():
        text = p.read_text(encoding="utf-8")
        n = text.count(SA.BEGIN)
        assert n == (1 if enabled else 0), f"{p.relative_to(ROOT)} carries the analytics block {n} times"
        outside = SA.BLOCK.sub("", text)
        if p.name == "privacy.html":           # the privacy page may NAME the service in prose
            outside = re.sub(r"<p>.*?</p>", "", outside, flags=re.S)
        assert not ANALYTICS.search(outside), \
            f"{p.relative_to(ROOT)} loads analytics outside the managed block (undisclosed by construction)"
    if enabled:
        assert "Plausible" in PRIVACY and 'id="plausible-optout"' in PRIVACY, \
            "the snippet ships but the privacy page does not describe it or offer the opt-out"
        assert "plausible_ignore" in PRIVACY and "runs no analytics scripts" not in PRIVACY
    else:
        assert "runs no analytics scripts" in PRIVACY and 'id="plausible-optout"' not in PRIVACY


def test_insert_is_idempotent_and_removable(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    (docs / "articles").mkdir(parents=True)
    for rel in ("index.html", "articles/a.html"):
        (docs / rel).write_text("<html><head><title>t</title></head><body></body></html>")
    monkeypatch.setattr(SA, "DOCS", docs)
    monkeypatch.setattr(SA, "SNIPPET", docs / "analytics-snippet.html")
    snippet = '<script defer data-domain="example.org" src="https://plausible.io/js/script.js"></script>'
    assert SA.apply(snippet) == 2 and SA.apply(snippet) == 0
    for p in SA.pages():
        assert p.read_text().count("plausible.io/js/script.js") == 1
    assert SA.apply(None) == 2
    assert all("plausible" not in p.read_text() for p in SA.pages())


def test_an_unmanaged_tag_is_caught(tmp_path):
    page = '<html><head><script async src="https://www.googletagmanager.com/gtag/js?id=G-X"></script></head></html>'
    assert ANALYTICS.search(SA.BLOCK.sub("", page))
