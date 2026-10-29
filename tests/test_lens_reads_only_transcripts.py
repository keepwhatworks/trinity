"""Founder lock 1: the lens learns from transcripts only. Council outcomes, evals, ratings, picks and the scoreboard
never feed it. Tests stopped a council result from being WRITTEN into the user's prompts, but nothing stopped the
lens build from READING those stores (internal/rule-enforcement.md, 2026-10-26). The lens-building modules may
name none of them, by state_paths helper or by path, outside a docstring. One documented exception: me_builder
appends its own build counts to analytics/ and never reads them back."""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "trinity_local"
LENS_MODULES = sorted((SRC / "me").rglob("*.py")) + [SRC / "me_builder.py", SRC / "distill.py", SRC / "core_gate.py"]
NOT_TRANSCRIPTS = {"council_outcomes_dir", "review_pages_dir", "council_status_dir", "council_status_json_path",
                   "council_status_js_path", "outcomes_log_path", "analytics_dir", "concerns_path", "scoreboard_dir",
                   "picks_path", "dispatch_outcomes_path"}
PATH_WORDS = ("council_outcomes", "scoreboard", "picks.json", "verify_concerns", "review_pages", "evals/",
              "build_runs", "dispatch_outcomes")
EXCEPTIONS = {("me_builder.py", "analytics_dir"): "appends stage-0 build counts (stage0_runs.jsonl); never read"}


def _non_transcript_refs(source: str) -> set[str]:
    tree = ast.parse(source)
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    found = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in NOT_TRANSCRIPTS:
            found.add(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in NOT_TRANSCRIPTS:
            found.add(n.attr)
        elif isinstance(n, ast.ImportFrom):
            found |= {a.name for a in n.names if a.name in NOT_TRANSCRIPTS}
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            found |= {w for w in PATH_WORDS if w in n.value}
    return found


def test_the_detector_sees_a_non_transcript_read():
    assert _non_transcript_refs("from ..state_paths import picks_path\n") == {"picks_path"}
    assert _non_transcript_refs("rows = load(picks_path())\n") == {"picks_path"}             # a bare name
    assert _non_transcript_refs("d = state_paths.scoreboard_dir()\n") == {"scoreboard_dir"}  # an attribute
    assert _non_transcript_refs("p = home / 'council_outcomes' / f\n") == {"council_outcomes"}
    assert _non_transcript_refs('"""Never reads picks.json."""\nx = 1\n') == set()     # a docstring is prose
    assert _non_transcript_refs("p = prompts_dir() / 'index.jsonl'\n") == set()


def test_the_lens_names_no_store_but_transcripts():
    assert len(LENS_MODULES) > 20, len(LENS_MODULES)
    bad = {}
    for p in LENS_MODULES:
        rel = p.relative_to(SRC).as_posix()
        refs = {r for r in _non_transcript_refs(p.read_text(encoding="utf-8")) if (rel, r) not in EXCEPTIONS}
        if refs:
            bad[rel] = sorted(refs)
    assert not bad, f"the lens learns from transcripts only (founder lock 1); these name another store: {bad}"


def test_the_exception_only_writes():
    """me_builder may use analytics_dir exactly twice: the import and one append. The exception is per module, so a
    second, reading use would otherwise ride on it (codex verify read, 2026-10-26)."""
    src = (SRC / "me_builder.py").read_text(encoding="utf-8")
    uses = [l.strip() for l in src.splitlines() if "analytics_dir" in l and not l.strip().startswith("#")]
    assert len(uses) == 2, uses
    assert uses[0] == "from .state_paths import analytics_dir", uses
    assert uses[1].startswith('with (analytics_dir() / "stage0_runs.jsonl").open("a"'), uses
