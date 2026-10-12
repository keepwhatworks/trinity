#!/usr/bin/env python3
"""Put the site's analytics snippet into every public page, or take it out.

Founder decision 2026-10-11: keepwhatworks.com counts visits with Plausible (cookieless). The
snippet is whatever the Plausible dashboard issues for keepwhatworks.com, kept in
docs/analytics-snippet.html. Pages carry it between two markers right before </head>, so the
script can re-run, update the snippet everywhere, or remove it cleanly. The privacy page
describes the analytics only while the snippet exists (tests/test_site_analytics.py).

    python scripts/site_analytics.py          # insert/refresh from docs/analytics-snippet.html
    python scripts/site_analytics.py --remove # strip it from every page
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SNIPPET = DOCS / "analytics-snippet.html"
BEGIN, END = "<!-- analytics -->", "<!-- /analytics -->"
BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.S)


def pages() -> list[pathlib.Path]:
    return sorted(p for p in DOCS.rglob("*.html")
                  if "historical" not in p.parts and p != SNIPPET)


def apply(snippet: str | None) -> int:
    changed = 0
    for p in pages():
        text = p.read_text(encoding="utf-8")
        new = BLOCK.sub("", text)
        if snippet is not None:
            new = new.replace("</head>", f"{BEGIN}\n{snippet.strip()}\n{END}\n</head>", 1)
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
    return changed


if __name__ == "__main__":
    if "--remove" in sys.argv:
        print(f"removed from {apply(None)} page(s)")
    elif not SNIPPET.exists():
        sys.exit(f"{SNIPPET.relative_to(ROOT)} is missing: paste the snippet Plausible issues for keepwhatworks.com")
    else:
        print(f"updated {apply(SNIPPET.read_text(encoding='utf-8'))} page(s)")
