"""A mutation must change behaviour, or the experiment measures nothing.

hq_108 flips one token on a line a fix ADDED and asks whether a blinded panel
notices. Caught 2026-09-09 with the run in flight: the operator list includes
`or` -> `and`, and it matched the word "or" inside a DOCSTRING. A line reading
"The recorded schema version, or 0 for a pre-versioning run", inside triple
quotes, became "... an 0 ...". Prose changed; the program did not. The two arms were
the same program, so the pair could not discriminate and was scored concordant,
diluting the measurement toward the null and biasing the experiment toward KILL.

One of the first six mutants was contaminated this way. The gate's
identical-artifact refusal does not catch it, because the TEXT differs.
"""
from __future__ import annotations

import importlib.util
import random
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent / "internal" / "experiments"


def _mod():
    spec = importlib.util.spec_from_file_location("hq108", HERE / "hq108_subtle_ablation.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.skipif(not (HERE / "hq108_subtle_ablation.py").exists(),
                    reason="experiment harness not present")
class TestOnlyLogicIsMutable:
    @pytest.mark.parametrize("label,line", [
        ("docstring with 'or'", '+    """The recorded schema version, or 0 for a pre-run."""'),
        ("comment with 'and'", "+    # combine the flags and return the result"),
        ("bare string literal", '+    "a message mentioning or and and"'),
        ("single-quoted prose", "+    'the value is True or False'"),
        ("blank", "+    "),
        ("diff header", "+++ b/x.py"),
        # A CONTINUATION line of a multi-line docstring does not start with a
        # quote, so the prose pattern cannot see it. It is still prose, and it
        # still contains mutable words. The operator requirement is what
        # catches it — without that check this line is a live target and the
        # mutant is a no-op. (A surviving mutation on this rule meant the test
        # was incomplete, not that the rule was decoration.)
        ("docstring continuation", "+    version, or 0 for a pre-versioning run."),
        ("prose continuation with 'and'", "+    the flags and the result together."),
        # A docstring that happens to contain parentheses passes the code-SHAPE
        # check, so the prose pattern is the only thing that stops it. Without
        # this case, deleting the prose pattern broke nothing and it read as
        # dead code. It is not.
        ("docstring with parens", '+    """Return the count (excluding skipped) or zero."""'),
        ("comment with a call", "+    # calls f(x) and returns or raises"),
    ])
    def test_prose_is_never_a_mutation_target(self, label, line):
        assert _mod().added_lines(line + "\n") == [], f"would mutate prose: {label}"

    @pytest.mark.parametrize("label,line", [
        ("comparison", "+    if ref.stat().st_mtime > vocab_mtime:"),
        ("boolean in a return", "+        return path or default"),
        ("call with a default", '+    x = raw.get("scoring_degraded", False)'),
        ("is not", '+    if data.get("aggregate_score") is not None:'),
    ])
    def test_logic_stays_mutable(self, label, line):
        assert _mod().added_lines(line + "\n") == [line[1:]], f"lost a real target: {label}"

    def test_a_mixed_diff_keeps_only_the_logic(self):
        diff = ('+"""prose with or in it"""\n'
                "+# a comment with and\n"
                "+    if a > b:\n"
                "+        return a or b\n")
        assert _mod().added_lines(diff) == ["    if a > b:", "        return a or b"]


SAMPLE = '''HTML = """
<div class="a">
</div>
"""

def g(a, b):
    """Return a or b, for a value."""
    # combine a and b
    if a > b:
        return a or b
    return True
'''


class TestTheTokenizerDecidesWhatIsCode:
    """Line heuristics could not tell markup from code, and both directions hurt.

    Three line-based filters still passed a bare HTML line inside a triple-quoted
    template: it carries no quotes of its own, and `<` and `>` read as code
    punctuation. On 2026-09-09 two of the first three mutants were template
    markup — `</div>` became `<=/div>`, which the panel duly caught, inflating
    the CAUGHT count with a mutant no reviewer could miss.

    The docstring contamination biased the experiment toward KILL. This one
    biases it toward PASS. Same root error: guessing at syntax from one line of
    text. The AST does not guess — everything inside a string literal is a
    single Constant node, and no operator node lands on those lines.
    """

    def _tree(self):
        wd = Path(tempfile.mkdtemp())
        (wd / "x.py").write_text(SAMPLE)
        return wd

    def _fresh(self, wd):
        (wd / "x.py").write_text(SAMPLE)
        return wd

    def test_only_real_operator_tokens_are_mutable(self):
        wd = self._tree()
        found = {(ln, tok) for ln, _, _, tok in _mod().mutable_spans(wd / "x.py")}
        assert found == {(9, ">"), (10, "or"), (11, "True")}

    @pytest.mark.parametrize("line", [
        "</div>",
        '<div class="a">',
        "Return a or b, for a value.",
        "    # combine a and b",
    ])
    def test_markup_and_prose_are_never_mutated(self, line):
        wd = self._fresh(self._tree())
        assert _mod().mutate_file(wd, line, random.Random(7)) is None, \
            f"mutated a non-code line: {line!r}"

    @pytest.mark.parametrize("line,expected", [
        ("    if a > b:", "if a >= b:"),
        ("        return a or b", "return a and b"),
        ("    return True", "return False"),
    ])
    def test_real_operators_are_mutated(self, line, expected):
        wd = self._fresh(self._tree())
        got = _mod().mutate_file(wd, line, random.Random(7))
        assert got is not None and got[2] == expected

    def test_a_file_that_does_not_parse_yields_no_targets(self):
        wd = Path(tempfile.mkdtemp())
        (wd / "broken.py").write_text("def f(:\n")
        assert _mod().mutable_spans(wd / "broken.py") == []

    def test_an_fstring_placeholder_does_not_make_its_markup_mutable(self):
        """The bug that killed the AST gate. An f-string placeholder puts nodes
        on the line that holds it, so `<script ...>{json.dumps(d)}</script>`
        read as a mutable LINE and the flip landed on the `<` of `<script`,
        producing `<=script`. A tokenizer sees one STRING token there."""
        wd = Path(tempfile.mkdtemp())
        (wd / "t.py").write_text(
            'def page(d):\n'
            '    return f"""\n'
            '<script id="x">{json.dumps(d)}</script>\n'
            '"""\n')
        assert _mod().mutable_spans(wd / "t.py") == []
        line = '<script id="x">{json.dumps(d)}</script>'
        assert _mod().mutate_file(wd, line, random.Random(3)) is None
