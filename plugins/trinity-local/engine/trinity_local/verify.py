"""verify — the pre-deploy gate. Kernel first, three reads, the rule.

    Run the relevant tests. Get three independent reads. Apply the rule.

No chairman. The panel members never see each other, never see the kernel
result, and each returns a per-criterion vote. `verify_rule.triage` is the
only judgment in the file and it is a pure function. Council fd416f421a583a8c
(amd_0201-0207) fixed this shape; the input contract is the crowdvote-pilot
acceptance block (amd_0209):

    {id, kind: "test" | "judgment", statement, command?, blocking}

`test`      -> the command runs in the workdir; exit 0 is green. The command is a
               shell string BY CONTRACT — it comes from the artifact's own
               acceptance block (a proposal file the user supplies, or the
               guard a harness derives from a commit), never from any model's
               output. A panel member cannot author a test command.
`judgment`  -> every panel member votes PASS/FAIL on the statement, blinded.

ORDER OF OPERATIONS IS THE MEASUREMENT
--------------------------------------
Panel BEFORE kernel by default. If the readers could see the test result the
split would be measuring the test, and hq_104 could not tell the two apart.
The kernel still wins the triage; it just does not get to whisper first.

RELEVANCE
---------
A kernel counts only if it exercises the changed files. v1 records the basis
as "declared": the caller listed the test criterion for this change. A green
somewhere else is not a green here.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .verify_rule import LAB_OF, Kernel, Panel, Triage, triage

DEFAULT_PANEL = ("claude", "codex", "antigravity")


@dataclass(frozen=True)
class Criterion:
    id: str
    kind: str                      # "test" | "judgment"
    statement: str
    command: str | None = None
    blocking: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Criterion":
        kind = str(d.get("kind", "")).strip()
        if kind not in ("test", "judgment"):
            raise ValueError(f"criterion {d.get('id')!r}: kind must be test|judgment, got {kind!r}")
        if kind == "test" and not d.get("command"):
            raise ValueError(f"criterion {d.get('id')!r}: kind=test requires a command")
        return cls(id=str(d["id"]), kind=kind, statement=str(d.get("statement", "")),
                   command=d.get("command"), blocking=bool(d.get("blocking", True)))


@dataclass
class Read:
    provider: str
    lab: str
    model: str | None
    votes: dict[str, bool]         # criterion id -> passed
    passed: bool                   # all blocking judgment criteria PASS
    seconds: float
    tokens: int | None = None
    cost_usd: float | None = None
    effort: str | None = None
    error: str | None = None
    why: str = ""          # the reviewer's own words: why it voted FAIL
    raw_tail: str = ""
    stderr_tail: str = ""          # an empty stdout is only diagnosable from here
    tests: dict | None = None      # criterion id -> the test backing a FAIL (doubts.py), when asked
    evidence: dict | None = None   # criterion id -> the reader's verbatim pre-change quote


# --------------------------------------------------------------------------- kernel

def run_kernel(criteria: list[Criterion], cwd: Path, timeout: int = 600,
               env: dict | None = None) -> tuple[Kernel, list[dict]]:
    """`env` is the harness's responsibility. A test that imports the project
    under test must see THIS tree's source (PYTHONPATH=<cwd>/src for a Python
    project), and a test that writes state must be pointed at scratch state
    (TRINITY_HOME for this repo). Caught when a known-good fix commit's guard
    read RED: without PYTHONPATH the venv's editable install answered the
    import, so the guard was testing HEAD, not the artifact."""
    tests = [c for c in criteria if c.kind == "test"]
    if not tests:
        return Kernel.not_run(), []
    runs = []
    green = True
    for c in tests:
        assert c.command, "from_dict guarantees a command on kind=test"
        t0 = time.time()
        try:
            r = subprocess.run(c.command, shell=True, cwd=cwd, capture_output=True,
                               text=True, timeout=timeout, env=env)
            ok = r.returncode == 0
            tail = (r.stdout + r.stderr)[-400:]
        except subprocess.TimeoutExpired:
            ok, tail = False, f"timeout after {timeout}s"
        runs.append({"id": c.id, "command": c.command, "green": ok,
                     "seconds": round(time.time() - t0, 1), "tail": tail})
        if c.blocking and not ok:
            green = False
    return Kernel(ran=True, relevant=True, green=green), runs


# --------------------------------------------------------------------------- panel

_PROMPT = """You are one of three independent reviewers from different labs. You will not see the
other reviewers, and you will not see any test result. Judge ONLY from the change and the
context below. Do NOT run commands, read files, or use any tool: there is nothing in your
working directory, and a reviewer who acts is no longer independent of what is being reviewed.

CONTEXT (the symptom or the task this change was meant to address):
{context}

THE CHANGE (unified diff):
{diff}

For EACH criterion below, decide PASS or FAIL. Be adversarial: a change that patches a
symptom in one place while the same rule lives elsewhere FAILS a root-cause criterion; a
change that does not address the context at all FAILS. When you cannot tell from the diff,
say FAIL and why.

CRITERIA:
{criteria}

Reply with ONE line of JSON and nothing after it:
{{"votes": {{{vote_keys}}}, "why": "<one sentence per FAIL, or empty>"}}
"""

# A GREEDY SPAN IS NOT A PARSER. This was r"\{.*\"votes\".*\}" with re.S, which
# runs from the FIRST brace to the LAST one anywhere in the output: a reviewer
# that emits its JSON and then one more sentence containing a brace, or any
# object before the vote object, produced a span that is not valid JSON and the
# vote was discarded as unparseable. Measured on hq_107: 17% of claude's reads
# lost, and ASYMMETRICALLY -- 5 of the 6 dropped arms were the treatment arm,
# because a reviewer voting FAIL writes a longer, more discursive `why` than one
# voting PASS. A parse failure correlated with the condition under test is a
# measurement bug, not lost data. `raw_decode` from each brace finds the real
# object instead of guessing at its extent.
_JSON_START_RE = re.compile(r"\{")


def _tokens(usage: dict) -> int | None:
    """Total tokens from whichever shape the provider reported.

    parse_codex_usage emits `total_tokens`; parse_claude_json emits the
    breakdown (`input_tokens`, `output_tokens`, `cache_*`) and no total. The
    first version of this asked only for `total_tokens`, so every claude read
    recorded None and a cost table built from it silently covered one provider
    of three -- producer-asserted, consumer-unverified.
    """
    if not usage:
        return None
    total = usage.get("total_tokens") or usage.get("tokens")
    if total:
        return int(total)
    parts = [usage.get(k) for k in ("input_tokens", "output_tokens",
                                    "cache_read_tokens", "cache_creation_tokens")]
    vals = [int(v) for v in parts if isinstance(v, (int, float))]
    return sum(vals) if vals else None


def _parse_votes(text: str, ids: list[str]) -> tuple[dict[str, bool], str] | None:
    """(votes, why) — and the `why` half was being thrown away.

    The prompt has always asked for {"votes": {...}, "why": "..."}, every
    reviewer has always sent one, and this function parsed the whole object and
    returned only the votes. The reasoning survived solely as `raw_tail`, an
    accidental last-300-characters slice of the raw response.

    Council 595c6e34 (all three members): "Generic READ output does not justify
    the review burden" and "READ should surface concrete concerns tied to code."
    The fix is PRESERVATION, not generation: the concerns already exist and
    already arrive. Discarding them and then asking a human to review blind is
    the single worst trade this product makes.
    """
    votes = None
    candidates: list[dict] = []
    decoder = json.JSONDecoder()
    for m in _JSON_START_RE.finditer(text or ""):
        try:
            d, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        # Valid-JSON-of-the-wrong-type must not crash the caller
        # (guard_shape_not_just_parse).
        if isinstance(d, dict) and isinstance(d.get("votes"), dict):
            candidates.append(d["votes"])
    # AMBIGUITY IS A REFUSAL, NOT A RACE. This took the FIRST object carrying a
    # `votes` key, so a reviewer that illustrates the format before answering --
    # `Example: {"votes":{"c":"PASS"}} Final verdict: {"votes":{"c":"FAIL"}}` --
    # had its verdict INVERTED, and the FAIL's reasoning was attached to the
    # example's PASS. Found by an Astra audit 2026-09-16, in a scan written days
    # earlier to fix the previous parser bug.
    #
    # Taking the LAST would be a different guess, wrong on a different input.
    # When two readable vote objects DISAGREE there is no fact here to recover,
    # and inventing one is how this codebase keeps producing plausible wrong
    # answers. Agreeing duplicates are fine -- they say the same thing.
    if candidates:
        first = candidates[0]
        if all(c == first for c in candidates[1:]):
            votes = first
        else:
            return None
    if votes is None:
        # TRUNCATION FALLBACK. A reviewer that writes a long `why` can have its
        # response cut off before the closing brace, so the OUTER object never
        # parses even though the vote is complete and sits at the front of it.
        # Diagnosed 2026-09-09 from a banked raw_tail ending mid-sentence with
        # no `}`; before the tail was banked, four of these read only as
        # "unparseable vote" and I twice guessed wrong at the cause. `votes` is
        # first in the requested shape, so decode THAT object alone rather than
        # requiring the envelope that carries it.
        for m in re.finditer(r'"votes"\s*:\s*', text or ""):
            try:
                d, _ = decoder.raw_decode(text, m.end())
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                votes = d
                break
    if votes is None:
        return None
    why = ""
    for m in _JSON_START_RE.finditer(text or ""):
        try:
            d, _ = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and isinstance(d.get("why"), str):
            why = d["why"].strip()
            break
    out = {}
    for i in ids:
        v = str(votes.get(i, "")).strip().upper()
        if v not in ("PASS", "FAIL"):
            return None
        out[i] = v == "PASS"
    return out, why


def _dispatch(provider_name: str, prompt: str, cwd: Path, config, effort: str | None = None):
    import dataclasses
    from . import providers as P
    pconf = config.providers[provider_name]
    if effort and getattr(pconf, "effort", None) and provider_name != "antigravity":
        # agy carries its level in the model SKU; claude/codex take a flag.
        pconf = dataclasses.replace(pconf, effort=effort)
    # A READER MUST NOT ACT. Given a real diff, a member will sometimes reach
    # for a tool to "check" -- headless agy auto-denies and emits nothing (the
    # first smoke) -- and a member that could act would be touching the tree
    # the kernel is about to test, or worse, acting on instructions planted in
    # the diff. So: clean-completion mode (#270 strips MCP + tools for claude
    # and codex), an empty scratch cwd, and for agy a sandbox, never a grant.
    import dataclasses as _dc
    import shutil
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="trinity-verify-read-"))
    try:
        if provider_name == "antigravity":
            # THE DIFF IS UNTRUSTED CONTENT -- an agent's output, or in crowdvote
            # an external proposer's -- so a reader never gets a permission
            # GRANT, only restrictions. An earlier version added the skip-permissions
            # flag here on the theory that an empty
            # cwd made it harmless; the security review was right that an empty
            # cwd bounds what a tool finds by default, not what it can reach,
            # and a prompt-injected diff turns data into actions. agy has no
            # tool-deny flag and _CLEAN_COMPLETION_FLAGS has no entry for it, so
            # the reader runs under --sandbox (terminal restrictions). If Gemini
            # still reaches for a tool and headless mode aborts it, that is a
            # MISSING VOTE: no three-lab consensus, triage READ. Conservative in
            # the right direction, and the abort rate is measured in hq_104.
            pconf = _dc.replace(pconf, args=[*pconf.args, "--sandbox"])
        prov = (P.CodexProvider(pconf) if provider_name == "codex" and hasattr(P, "CodexProvider")
                else P.CLIProvider(pconf))
        prov.clean_completion = True     # class attribute, not a ctor kwarg
        return prov.run(prompt, scratch), pconf
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _resolve_lab(value: str | None) -> str | None:
    """`exclude_lab` accepts a LAB ("anthropic") or a PROVIDER ("claude").

    It used to compare the raw value against lab names only, so the natural
    call -- exclude_lab="claude" from a Claude session -- matched nothing and
    was SILENTLY ignored: the author's own lab reviewed its own change, which
    is the one thing the flag exists to prevent. Reported 2026-09-28 by a peer
    session that watched Claude read its own diff. An unknown value is now an
    error, never a no-op.
    """
    if not value:
        return None
    v = str(value).strip().lower()
    labs = set(LAB_OF.values())
    if v in labs:
        return v
    if v in LAB_OF:
        return LAB_OF[v]
    raise ValueError(
        f"exclude_lab={value!r} names no known lab or provider; use one of "
        f"{sorted(labs | set(LAB_OF))}")


def read_panel(criteria: list[Criterion], diff: str, context: str, cwd: Path,
               providers: tuple[str, ...] = DEFAULT_PANEL, exclude_lab: str | None = None,
               config=None, effort: str | None = None,
               ask_tests: bool = False) -> tuple[Panel, list[Read]]:
    judgments = [c for c in criteria if c.kind == "judgment"]
    if not judgments:
        return Panel.not_run(), []
    if config is None:
        from .config import load_config
        config = load_config()
    ids = [c.id for c in judgments]
    template = _PROMPT
    if ask_tests:
        from .doubts import PROMPT_ADDENDUM
        template = _PROMPT.replace("Reply with ONE line of JSON", PROMPT_ADDENDUM + "\nReply with ONE line of JSON")
    prompt = template.format(
        context=context.strip() or "(none given)",
        diff=diff.strip()[:60000],
        criteria="\n".join(f"- {c.id}: {c.statement}" for c in judgments),
        vote_keys=", ".join(f'"{i}": "PASS|FAIL"' for i in ids),
    )
    exclude_lab = _resolve_lab(exclude_lab)
    reads: list[Read] = []
    votes: dict[str, bool] = {}
    for name in providers:
        lab = LAB_OF.get(name, name)
        if exclude_lab and lab == exclude_lab:
            continue
        if name not in config.providers or not config.providers[name].enabled:
            continue
        t0 = time.time()
        try:
            result, pconf = _dispatch(name, prompt, cwd, config, effort)
            text = getattr(result, "stdout", "") or ""
            parsed_pair = _parse_votes(text, ids)
            parsed, why = parsed_pair if parsed_pair else (None, "")
            usage = getattr(result, "usage", None) or {}
            tokens, cost = _tokens(usage), usage.get("cost_usd")
            if parsed is None:
                err = (getattr(result, "stderr", "") or "")[-300:]
                reads.append(Read(name, lab, getattr(pconf, "model", None), {}, False,
                                  round(time.time() - t0, 1), tokens, cost, getattr(pconf, "effort", None),
                                  error="unparseable vote" if text.strip() else
                                  f"empty stdout (rc={getattr(result, 'returncode', '?')})",
                                  raw_tail=text[-300:], stderr_tail=err))
                continue
            passed = all(parsed[c.id] for c in judgments if c.blocking)
            tests = evidence = None
            if ask_tests:
                from .doubts import parse_tests
                tests = parse_tests(text, ids)
                evidence = parse_tests(text, ids, key="evidence")
            reads.append(Read(name, lab, getattr(pconf, "model", None), parsed, passed,
                              round(time.time() - t0, 1), tokens, cost, getattr(pconf, "effort", None),
                              why=why, raw_tail=text[-300:], tests=tests, evidence=evidence))
            votes[name] = passed
        except Exception as exc:   # a member that errors is a missing vote, never a vote
            reads.append(Read(name, lab, None, {}, False, round(time.time() - t0, 1),
                              error=f"{type(exc).__name__}: {exc}"[:200]))
    return Panel(ran=bool(votes), votes=votes), reads


# --------------------------------------------------------------------------- verify

def _reject_duplicate_ids(criteria) -> None:
    """Two criteria sharing an id means one vote answers both.

    A reviewer returns {"votes": {"c": ...}} keyed by id, so an acceptance block
    with a blocking `c` and a non-blocking `c` gets ONE verdict applied to both
    -- and `passed` is computed over blocking criteria, so a formatting opinion
    can decide a correctness gate. Refusing a malformed block is cheap; silently
    merging two requirements is not.
    """
    seen, dupes = set(), []
    for c in criteria:
        cid = c.id if hasattr(c, "id") else (c or {}).get("id")
        if cid in seen:
            dupes.append(cid)
        seen.add(cid)
    if dupes:
        raise ValueError(
            f"duplicate criterion id(s) {sorted(set(dupes))!r}: a reviewer votes once per id, "
            "so two criteria sharing one would share a verdict. Give each its own id.")


def verify(criteria_dicts: list[dict], diff: str, context: str, cwd: Path,
           providers: tuple[str, ...] = DEFAULT_PANEL, exclude_lab: str | None = None,
           run_panel: bool = True, run_tests: bool = True, config=None,
           effort: str | None = None, env: dict | None = None) -> dict:
    """Panel first (blinded), then kernel, then the rule. Returns a plain dict."""
    criteria = [Criterion.from_dict(d) for d in criteria_dicts]
    _reject_duplicate_ids(criteria)
    cwd = Path(cwd)
    from . import doubts as _doubts
    backed_on = _doubts.enabled()
    panel, reads = (read_panel(criteria, diff, context, cwd, providers, exclude_lab, config, effort,
                               ask_tests=backed_on)
                    if run_panel else (Panel.not_run(), []))
    kernel, runs = run_kernel(criteria, cwd, env=env) if run_tests else (Kernel.not_run(), [])
    t: Triage = triage(kernel, panel)
    # Report-only: which hunks no declared test notices. Never changes the triage.
    untested = None
    if run_tests and kernel.ran and kernel.green:
        from . import untested as _untested
        if _untested.enabled():
            untested = _untested.untested_hunks(
                diff, [c.command for c in criteria if c.kind == "test" and c.command], cwd, env=env)
    # Report-only: values this change now keeps in two places (duplicates.py; hq_125 KILL).
    dup_rows = None
    from . import duplicates as _dup
    if _dup.enabled():
        dup_rows = _dup.duplicated_values(diff, cwd)
    # Report-only: run each doubt's test in a sandboxed scratch copy (doubts.py).
    doubt_rows = None
    if backed_on and reads:
        commands = [c.command for c in criteria if c.kind == "test" and c.command]
        doubt_rows = _doubts.run_doubt_tests([asdict(r) for r in reads], cwd,
                                             _doubts.python_for(commands), env=env, diff=diff)
    return {
        "triage": t.outcome, "reason": t.reason, "false_green": t.false_green,
        "kernel": {"ran": kernel.ran, "relevant": kernel.relevant, "green": kernel.green,
                   "relevance_basis": "declared" if kernel.ran else None, "runs": runs},
        "panel": {"ran": panel.ran, "votes": panel.votes, "labs": sorted(panel.labs()),
                  "consensus_pass": panel.consensus_pass(), "split": panel.split(),
                  "reads": [asdict(r) for r in reads]},
        "criteria": [asdict(c) for c in criteria],
        # Criteria no test runs: they were only read, so a person must read them.
        "unverified": [c.id for c in criteria if c.kind == "judgment"],
        "untested": untested,
        "doubts": doubt_rows,
        "duplicates": dup_rows,
    }
