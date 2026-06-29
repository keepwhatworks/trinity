"""Guards for clean_ollama_output (v1.7.94).

Raw `ollama run` stdout from a reasoning model carries (a) the full
chain-of-thought, (b) a "...done thinking." sentinel, then (c) the answer, plus
ANSI spinner/cursor escape bytes emitted even to a pipe. The cleaner must leave
ONLY the final answer so a local council member is comparable to a flagship and
the chairman's context isn't polluted with terminal garble.
"""
from trinity_local.providers import clean_ollama_output


def test_drops_thinking_keeps_answer():
    raw = "Thinking...\nThe user wants a pick. Weigh options.\n...done thinking.\n\nRecommend: **(b)**. Done."
    out = clean_ollama_output(raw)
    assert out == "Recommend: **(b)**. Done."


def test_strips_ansi_and_control_bytes():
    raw = "\x1b[?25l\x1b[1G⠋ \x1b[K\x1b[?25h...done thinking.\n\nHELLO"
    out = clean_ollama_output(raw)
    assert out == "HELLO"
    assert "\x1b" not in out


def test_removes_think_tags():
    raw = "<think>internal reasoning here</think>The answer is 42."
    out = clean_ollama_output(raw)
    assert out == "The answer is 42."
    assert "reasoning" not in out


def test_no_sentinel_returns_cleaned_full_text():
    # A non-reasoning model (no sentinel) — keep everything, just de-ANSI.
    raw = "\x1b[?25lPlain answer with no thinking.\x1b[?25h"
    assert clean_ollama_output(raw) == "Plain answer with no thinking."


def test_last_sentinel_wins():
    # "done thinking." could appear inside the trace; keep text after the LAST.
    raw = "step one done thinking. about it\n...done thinking.\n\nFinal answer."
    assert clean_ollama_output(raw) == "Final answer."


def test_empty_passthrough():
    assert clean_ollama_output("") == ""
    assert clean_ollama_output(None) is None
