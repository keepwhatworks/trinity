"""Convert an artificialanalysis.ai models export (xlsx) into Trinity's
`data/reference_evals.json`. Run after downloading a fresh export so the
launchpad's reference evals table tracks the current model lineup.

Usage:
    python tools/sync_reference_evals.py ~/Downloads/models-data_YYYY-MM-DD.xlsx
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# Map provider name (as used in trinity config.json) -> the slug used by
# artificialanalysis.ai. Update when bumping config models.
PROVIDER_TO_SLUG = {
    "claude": "claude-opus-4-8",            # Adaptive Reasoning, Max Effort
    "gemini": "gemini-3-1-pro-preview",     # AA's slug; CLI accepts "gemini-3.1-pro-preview"
    "codex": "gpt-5-5",                      # GPT-5.5 (xhigh) — top OpenAI on AA
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    try:
        import openpyxl
    except ImportError:
        print("error: openpyxl is required. install: pip install openpyxl", file=sys.stderr)
        return 1

    xlsx_path = Path(argv[1]).expanduser().resolve()
    if not xlsx_path.exists():
        print(f"error: file not found: {xlsx_path}", file=sys.stderr)
        return 1

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Models"]

    # Header lives on row 5; data starts at row 6.
    rows: dict[str, dict] = {}
    for i, row in enumerate(ws.iter_rows(min_row=5, values_only=True)):
        if i == 0 or not row or row[0] is None:
            continue
        name, slug, release, creator, intel, coding, agentic, cost = row
        rows[slug] = {
            "name": name,
            "slug": slug,
            "release_date": str(release) if release else None,
            "creator": creator,
            "intelligence_index": intel,
            "coding_index": coding,
            "agentic_index": agentic,
            "cost_per_index_usd": cost,
        }

    fetched_at = xlsx_path.stem.split("_")[-1]
    out: dict = {
        "source": "artificialanalysis.ai",
        "fetched_at": fetched_at,
        "attribution": "Data sourced from artificialanalysis.ai. Required attribution: https://artificialanalysis.ai",
        "providers": {},
        "categories": {},
    }

    missing: list[str] = []
    for provider, slug in PROVIDER_TO_SLUG.items():
        if slug not in rows:
            missing.append(f"{provider}={slug}")
            continue
        out["providers"][provider] = rows[slug]

    categories = [
        ("intelligence", "Artificial Analysis Intelligence Index", "/100", "intelligence_index"),
        ("coding",       "Artificial Analysis Coding Index",      "/100", "coding_index"),
        ("agentic",      "Artificial Analysis Agentic Index",     "/100", "agentic_index"),
    ]
    for key, bench, unit, field in categories:
        out["categories"][key] = {
            "benchmark": bench,
            "unit": unit,
            "source": "artificialanalysis.ai",
            "fetched_at": fetched_at,
            "models": {p: out["providers"][p][field] for p in out["providers"]},
        }

    repo_root = Path(__file__).resolve().parents[1]
    dest = repo_root / "data" / "reference_evals.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))

    if missing:
        print(f"WARN: missing slugs (provider=slug): {', '.join(missing)}", file=sys.stderr)
    print(f"wrote {dest}")
    print(f"  providers: {sorted(out['providers'].keys())}")
    print(f"  categories: {sorted(out['categories'].keys())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
