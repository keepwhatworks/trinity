"""Guard: provider identifiers must be compared CANONICALLY, never raw.

THE BUG THIS PREVENTS. The same lab is recorded under different slugs depending on
which surface produced the council: web-capture councils store `chatgpt` /
`claude_ai` / `gemini`, while CLI councils and `CouncilRoutingLabel.from_dict`
store `codex` / `claude` / `antigravity`. So a raw `winner == original_winner` is
False for two records of the SAME lab, and that False means nothing.

It has bitten three times, all on 2026-07-24/25, all the same shape — read a raw
JSON field, compare it to a normalized one:
  1. `combine_addressability_census.py` could not find the winning member's answer
     on 19 of 41 councils, so token-novelty read a spurious 1.00 on ~46% of the
     sample — inflating the very rate the census existed to gate on.
  2. `rechair_backfill.py` reported a 54% chairman "model effect"; 38 points of it
     was pure aliasing. That wrong number was reported to the founder and had to be
     retracted.
  3. `rechair_arm_c_legacy_prompt.py` inherited the same comparison.

`same_provider()` exists so the comparison is right by construction. This guard
exists so nobody has to remember it.
"""
from __future__ import annotations

import pathlib
import re

from trinity_local.council_schema import same_provider

REPO = pathlib.Path(__file__).resolve().parents[1]


class TestSameProviderSemantics:
    def test_folds_capture_slugs_onto_dispatch_slugs(self):
        """The three aliases that caused every instance of this bug."""
        assert same_provider("chatgpt", "codex")
        assert same_provider("claude_ai", "claude")
        assert same_provider("gemini", "antigravity")
        # and the lab-name spellings that also appear on disk
        assert same_provider("openai", "codex")
        assert same_provider("anthropic", "claude")
        assert same_provider("google", "antigravity")

    def test_case_and_whitespace_insensitive(self):
        assert same_provider("ChatGPT", "codex")
        assert same_provider("  claude_ai  ", "CLAUDE")

    def test_genuinely_different_labs_still_differ(self):
        """The guard must not make everything equal — that would hide real changes."""
        assert not same_provider("claude", "codex")
        assert not same_provider("chatgpt", "antigravity")
        assert not same_provider("claude_ai", "gemini")

    def test_missing_winner_is_not_a_match(self):
        """'no winner' must never compare equal to a winner, or a council with no
        pick would read as agreeing with everything."""
        assert not same_provider("", "claude")
        assert not same_provider(None, "claude")
        assert not same_provider("", "")
        assert not same_provider(None, None)


class TestNoRawProviderComparisons:
    """Scan the experiment harnesses for the raw-comparison pattern.

    Scoped to internal/experiments/ because that is where all three instances
    happened: those scripts read council JSON directly with `json.loads`, which
    bypasses the normalizing loader that protects production paths. Production
    comparisons go through `from_dict`, so both sides are already canonical there.
    """

    # A line that MENTIONS a provider slug and CONTAINS an equality comparison,
    # with no normalizer anywhere on it. Deliberately NOT requiring the operator to
    # sit adjacent to the slug word — the first version of this guard did, and it
    # was vacuous: the real offender read
    #     moved = (label.winner or "").lower() != str(c["orig_winner"] or "").lower()
    # where `winner` is followed by ` or "").lower() !=`, so an adjacency pattern
    # sailed straight past the exact bug this file exists to catch.
    SLUG = re.compile(r"\b(?:winner|orig_winner|winner_provider|provider)\b")
    CMP = re.compile(r"(?:==|!=)")
    NORMALIZED = re.compile(r"same_provider|normalize_provider_slug|\bn_\(|\bnorm\(")

    # Comparisons that appear INSIDE a quoted span are prose, not code — a
    # docstring reading `just parroting "followed==winner"` is not a slug bug.
    QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

    @classmethod
    def _is_offender(cls, line: str) -> bool:
        if cls.NORMALIZED.search(line):
            return False
        # strip quoted spans first so prose and literals can't trigger the lint
        line = cls.QUOTED.sub("''", line)
        if not (cls.SLUG.search(line) and cls.CMP.search(line)):
            return False
        # Comparing against a string literal is safe — a literal is canonical by
        # inspection (`if side == "for"`, `if provider == "claude"` in config code).
        if re.search(r"(?:==|!=)\s*['\"]", line):
            return False
        # `is None` / `is not None` are not slug comparisons.
        return True

    def test_experiment_harnesses_compare_providers_canonically(self):
        offenders: list[str] = []
        for path in sorted((REPO / "internal" / "experiments").glob("*.py")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or not stripped:
                    continue
                # comparing to a literal string constant is fine — a literal is
                # already canonical by inspection (e.g. `if side == "for"`).
                if self._is_offender(stripped):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {stripped[:100]}")
        assert not offenders, (
            "Raw provider comparison(s) found. The same lab is recorded as "
            "chatgpt/claude_ai/gemini on web-capture councils and codex/claude/"
            "antigravity elsewhere, so `==` on unnormalized slugs is meaningless — "
            "it produced a retracted 54% finding on 2026-07-25. Use "
            "`same_provider(a, b)` from council_schema.\n  " + "\n  ".join(offenders)
        )
