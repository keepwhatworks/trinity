"""No machine-agent text source may enter the ingest source list.

WHY THIS EXISTS — the 188-verdict incident, and founder lock #1.

Trinity's lens learns from the user's RAW TRANSCRIPTS only. Founder lock #1
(claude.md, founder-locked 2026-07-02): there is NO council->lens, eval->lens,
or chairman->lens edge, ever. The lens is the objective the chairman is judged
against; feed the optimizer's own output back into it and taste converges to a
flattering mirror.

Trinity has ALREADY been burned by exactly this class, at the ingest boundary
this file guards. `trust --build` dispatched its resolver through `claude -p`.
That subprocess wrote a transcript into `~/.claude/projects`, the `claude`
ingest source swept it up, and its turns were indexed as `role=user` — so the
next build read the MACHINE's own words back as "what the user did next".
188 ledger verdicts were invalidated and had to be re-resolved (2026-07-26).
The per-model ordering survived but the spread compressed by a third: the
headline number fell 77% -> 68%, and every per-model figure taken before the
fix became unquotable.

The contamination was invisible the entire time it ran, because every check
was GREEN — the corpus grew, the resolver produced verdicts, the tally had n.
Nothing in the system distinguishes "the user decided this" from "a model that
Trinity itself launched typed this". That is the whole failure: at ingest, text
is text.

THE FORWARD RISK. The user is building a multi-agent workspace on Buzz (a Nostr
workspace) where a company of agents will emit machine text at volume. One
subprocess's output read as user prompts cost 188 verdicts. A company of agents
is that same bug multiplied by headcount, running continuously. A council
ratified that this needs a STRUCTURAL wall AT INGEST rather than a filter
downstream — a downstream filter needs someone to remember to run it, and the
incident above is the proof that nobody notices in time.

WHAT THIS GUARD IS: A RATCHET, NOT A REPAIR. There is no Buzz-origin source in
the tree today — verified at authoring time, zero `buzz`/`nostr` occurrences
anywhere under `src/`. Nothing here is fixing a present defect. This test pins
the ingest source surface to an explicit allowlist of HUMAN-AUTHORED sources so
that any source added later — whatever it ends up being NAMED — reds this test
until someone makes the call deliberately and records who authored the text.

It covers both halves of the source surface, because they fail independently:
  * `incremental_ingest.DEFAULT_SOURCES` — what the MCP hot path sweeps
    automatically on every tool call.
  * the `watch_runtime` deny-gates (`_source_root` / `_parse_source_path`) —
    what `ingest_recent(sources=[...])` can reach when a caller names a source
    EXPLICITLY, bypassing DEFAULT_SOURCES entirely.
A Buzz source wired into the second but not the first is still ingestable, and
would be invisible to a guard that only read the default tuple.

DO NOT DELETE THIS TEST TO MAKE A NEW SOURCE PASS. Adding a line to
`_HUMAN_AUTHORED_SOURCES` below is the deliberate act this guard exists to
force. It costs one line and one sentence naming who typed the text. That
sentence is the entire point.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from trinity_local import watch_runtime
from trinity_local.incremental_ingest import DEFAULT_SOURCES

_REPO = Path(__file__).resolve().parents[1]
_WATCH_RUNTIME = _REPO / "src" / "trinity_local" / "watch_runtime.py"
_BUNDLE = _REPO / "plugins" / "trinity-local" / "engine" / "trinity_local"

# Every ingest source Trinity is allowed to read, each with WHO AUTHORED THE TEXT.
# A source belongs here ONLY if the turns it yields as role=user were typed by the
# human. Adding a line is the deliberate decision this guard exists to force — do
# it in a commit whose message says why the new source carries human authorship.
_HUMAN_AUTHORED_SOURCES = {
    "claude": "Claude Code CLI transcripts — user turns are typed by the human.",
    "codex": "Codex CLI rollout transcripts — user turns are typed by the human.",
    "gemini": "Gemini CLI session transcripts — user turns are typed by the human.",
    "antigravity": "agy CLI transcripts — user turns are typed by the human.",
    "cowork": "Claude local-agent-mode sessions — user turns are typed by the human.",
    "browser_claude": "claude.ai captures via the Chrome extension — the human's messages.",
    "browser_chatgpt": "chatgpt.com captures via the Chrome extension — the human's messages.",
    "browser_gemini": "gemini.google.com captures via the Chrome extension — the human's messages.",
}

# Name shapes a Buzz/Nostr-origin source would plausibly carry. This is the NAMED
# half of the wall: it exists so the failure message says "188 verdicts" instead
# of "unexpected source". The allowlist above is the half that still holds when
# the source is named something nobody predicted.
_MACHINE_ORIGIN_TOKENS = re.compile(
    r"buzz|nostr|npub|nsec|nip\d|relay|agent_feed|agent_workspace|agent_swarm",
    re.IGNORECASE,
)

_REMEDY = (
    "\n\nIf this source's role=user turns are typed by a HUMAN, add it to "
    "_HUMAN_AUTHORED_SOURCES in this file with a sentence naming the author. "
    "If they are produced by an AGENT, it must not be an ingest source at all — "
    "machine text read back as user prompts is the 2026-07-26 incident that "
    "invalidated 188 ledger verdicts, and it is founder lock #1 (no optimizer "
    "output flows into the lens). Do not delete this guard to make it pass."
)


def _source_literals(func_name: str) -> set[str]:
    """String literals compared against the `source` parameter inside `func_name`.

    Reads the deny-gates structurally rather than importing a list, because
    there IS no list — `watch_runtime` dispatches on a chain of `if source ==`
    branches and raises ValueError on the fallthrough. Parsing the branches is
    what lets this guard see a source that was wired into the adapter registry
    without ever being added to DEFAULT_SOURCES.
    """
    tree = ast.parse(_WATCH_RUNTIME.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == func_name):
            continue
        for cmp_node in ast.walk(node):
            if not isinstance(cmp_node, ast.Compare):
                continue
            if not (isinstance(cmp_node.left, ast.Name) and cmp_node.left.id == "source"):
                continue
            for comparator in cmp_node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    found.add(comparator.value)
                elif isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                    for elt in comparator.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            found.add(elt.value)
    return found


# --------------------------------------------------------------------------
# 1. The automatic path: what every MCP tool call sweeps.
# --------------------------------------------------------------------------


def test_default_sources_is_exactly_the_human_authored_allowlist():
    """DEFAULT_SOURCES may contain nothing but reviewed human-authored sources."""
    unreviewed = sorted(set(DEFAULT_SOURCES) - set(_HUMAN_AUTHORED_SOURCES))
    assert not unreviewed, (
        "Ingest source(s) added to incremental_ingest.DEFAULT_SOURCES without a "
        f"human-authorship review: {unreviewed}. Every source in DEFAULT_SOURCES "
        "is swept into the prompt index on every MCP tool call and becomes lens "
        "input." + _REMEDY
    )


def test_no_buzz_origin_source_in_default_sources():
    """The named half: a Buzz/Nostr-shaped source name reds with the incident."""
    offenders = sorted(s for s in DEFAULT_SOURCES if _MACHINE_ORIGIN_TOKENS.search(s))
    assert not offenders, (
        f"Buzz/Nostr-origin ingest source(s) in DEFAULT_SOURCES: {offenders}. "
        "A multi-agent workspace emits machine text at volume; ingesting it "
        "indexes agent output as role=user prompts and feeds it to the lens. "
        "This is the 2026-07-26 contamination (188 ledger verdicts invalidated, "
        "headline 77% -> 68%) scaled by agent headcount." + _REMEDY
    )


# --------------------------------------------------------------------------
# 2. The explicit path: what `ingest_recent(sources=[...])` can reach.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gate", ["_source_root", "_parse_source_path"])
def test_adapter_registry_admits_only_human_authored_sources(gate):
    """A source reachable by name is ingestable even if it is not a default."""
    wired = _source_literals(gate)
    # Non-vacuity: if the AST walk ever stops finding branches it would pass by
    # matching nothing at all — the exact green-over-degenerate-data shape this
    # repo keeps re-learning. Require it to still see the known registry.
    assert len(wired) >= len(_HUMAN_AUTHORED_SOURCES), (
        f"watch_runtime.{gate} scan found only {sorted(wired)} — the dispatch "
        "shape changed and this guard is no longer reading the registry. Fix the "
        "scan; do not delete the guard."
    )
    unreviewed = sorted(wired - set(_HUMAN_AUTHORED_SOURCES))
    assert not unreviewed, (
        f"watch_runtime.{gate} resolves ingest source(s) that were never reviewed "
        f"for human authorship: {unreviewed}. A source wired here is ingestable "
        "via ingest_recent(sources=[...]) even when it is absent from "
        "DEFAULT_SOURCES." + _REMEDY
    )


def test_adapter_registry_rejects_buzz_origin_sources_at_runtime():
    """Default-deny holds behaviourally, not just in the literal scan."""
    for name in ("buzz", "buzz_workspace", "nostr", "agent_feed"):
        with pytest.raises(ValueError):
            watch_runtime._source_root(name)


# --------------------------------------------------------------------------
# 3. The vendored copy the plugin marketplace actually ships.
# --------------------------------------------------------------------------


def test_bundled_engine_has_no_buzz_origin_source():
    """The plugin ships its own engine copy; the wall must stand in it too."""
    ingest = _BUNDLE / "incremental_ingest.py"
    assert ingest.exists(), (
        "bundled engine copy of incremental_ingest.py is missing — this guard "
        "would pass vacuously. Run `bash scripts/bundle_engine.sh`."
    )
    for module in (ingest, _BUNDLE / "watch_runtime.py"):
        text = module.read_text(encoding="utf-8")
        hits = sorted(set(m.group(0) for m in _MACHINE_ORIGIN_TOKENS.finditer(text)))
        assert not hits, (
            f"machine-agent-origin token(s) {hits} in the vendored engine at "
            f"{module.relative_to(_REPO)}. The plugin marketplace ships this copy."
            + _REMEDY
        )


# --------------------------------------------------------------------------
# 4. Non-vacuity: this guard must be capable of failing.
# --------------------------------------------------------------------------


def test_guard_cannot_pass_vacuously():
    """A guard that passes on empty input is decoration.

    Two ways this suite could go green while the wall is gone: DEFAULT_SOURCES
    empties out (nothing left to find a Buzz source in), or the token regex rots
    to matching nothing. Pin both.
    """
    assert set(DEFAULT_SOURCES) == set(_HUMAN_AUTHORED_SOURCES), (
        "DEFAULT_SOURCES no longer matches the reviewed allowlist — a source was "
        f"dropped ({sorted(set(_HUMAN_AUTHORED_SOURCES) - set(DEFAULT_SOURCES))}) "
        "or added without review. An emptied DEFAULT_SOURCES would make the "
        "Buzz-source assertions above pass by having nothing to check."
    )
    for probe in ("buzz", "buzz_agents", "nostr_relay", "agent_feed", "NOSTR"):
        assert _MACHINE_ORIGIN_TOKENS.search(probe), (
            f"the machine-origin token pattern no longer matches {probe!r} — it "
            "has rotted to matching nothing and the named half of the wall is "
            "silently inert."
        )
    assert not _MACHINE_ORIGIN_TOKENS.search("browser_claude"), (
        "the machine-origin token pattern matches a legitimate human source — it "
        "is over-broad and will red on honest additions."
    )
