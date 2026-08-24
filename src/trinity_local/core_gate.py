"""core_gate.py — every core/lens item must EARN its place. (LLM-free)

WHY (founder, 2026-08-10, and measured the same day)
====================================================
`architecture-of-endurance` specifies the shape: "a hard, deliberately painful
core wrapped in a fast, freely moving skin ... Freeze the core. Free the skin."
The audit (amd_0153) found we had it exactly inverted. `distill.write_core` was an
unconditional `path.write_text`; the full precondition set was "a source memory
exists", "core.md is OLDER than some source" (a timestamp, not a quality check),
and "provider stdout is non-empty". No history, no diff, no comparison against
the incumbent, no rollback. `lens-build` auto-triggers it, so the core turned over
on essentially every build — the fastest-moving artifact in the system, playing
the constitution's role at the statutes' update rate.

The comparison inside this repo was the finding: scoring a compression tactic
needs a 7-key REGISTRATION and a pre-registered falsifier; a CONFIRM verdict needs
burn-once, coder triangulation and a stratified null. Rewriting the founder's
identity needed one non-empty string.

WHAT EARNS A PLACE, MEASURED
============================
hq_042..046 built the instrument this gate needs. An artifact's worth is how
cheaply it prices the founder's own held-out utterances — `bits(TEST | artifact)`
under a preset dictionary. On that instrument core.md already has the FLATTEST
decay curve of five arms (0.33pp/bucket against recent verbatim's 1.62), so
durability is its measured strength, and rewriting it every build from shifting
material is the mechanism that prevents durable structure from accumulating.

So: a candidate core is a PROPOSAL, not a commit. It replaces the incumbent only
by pricing held-out text at least as well. And `item_values()` scores each lens
item by leave-one-out ablation — remove it, re-score; an item that costs nothing
when deleted has not earned its place.

SAFETY — this is a decision-directive green, so it follows the green-gate protocol
==================================================================================
  * FAIL CLOSED, never open. If the instrument cannot run — no real embedder is
    needed here, but too few held-out nodes, an unreadable corpus, a degenerate
    target — the gate REFUSES and KEEPS THE INCUMBENT. For a core, refusing to
    write IS the safe direction; that is what "freeze" means. The candidate is
    still versioned, so nothing is lost.
  * NOTHING IS DESTROYED. Every candidate, admitted or rejected, is written to
    `~/.trinity/core_history/` with its score and verdict. The previous behaviour
    had exactly one file and no recovery path.
  * PRE-REGISTERED FLOOR: a candidate must not price held-out text WORSE than the
    incumbent by more than `TOLERANCE_BITS` (default 0 — ties admit, so a rewrite
    that merely rephrases still lands, but a degradation does not).
  * The floor is on BITS, an absolute measured quantity, not on a ratio that a
    shrinking corpus could flatter.

RELATION TO THE FOUNDER-LOCKS
=============================
This does NOT touch `TRINITY_REGRESSION_GATE` (lens-tension reconcile, founder-locked
default-OFF) and does not arm it. Different surface, different mechanism: that gate
drops candidate TENSIONS on a preference-collapse test; this one gates the CORE
WRITE on prediction. And the scoring target is held-out TRANSCRIPT text only —
never council outcomes — so the lens-learns-from-transcripts-only lock holds.
"""
from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from typing import Callable
from datetime import datetime, timezone
import pathlib
from pathlib import Path

from .state_paths import core_path, trinity_home

DICT_BUDGET = 28 * 1024
MIN_HELDOUT = 40          # below this the instrument is refused, not stretched
TOLERANCE_BITS = 0        # a candidate may tie, never degrade


def history_dir() -> Path:
    d = trinity_home() / "core_history"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zlib_bytes(texts, dictionary: bytes | None) -> int:
    """Compressed BYTES of each text coded ALONE under a preset dictionary.

    Named for what it returns. `internal/experiments/prediction_bits.bits_given`
    is the same computation under a misleading name — it says bits and returns
    bytes, and that cost a factual claim on 2026-08-10 (every absolute bits/byte
    figure in hq_042..048 was 8x low; ratios were unaffected). Ratios are all this
    gate needs, so the unit does not change a verdict — but the name now says
    which unit it is.
    """
    zd = dictionary[-DICT_BUDGET:] if dictionary else None
    total = 0
    for t in texts:
        co = (zlib.compressobj(9, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, zd)
              if zd else zlib.compressobj(9, zlib.DEFLATED, -15))
        total += len(co.compress(t.encode("utf-8"))) + len(co.flush())
    return total


NEURAL_MODEL = "mlx-community/Qwen3-0.6B-4bit"
NEURAL_HELDOUT = 60      # forward passes are the cost; 60 is ~2 min on an M1 Ultra
NEURAL_CTX_TOKENS = 3000
_neural_cache: dict = {}


def _neural_available() -> bool:
    """True only when the local LM stack AND a cached model are both present.

    Never downloads: HF_HUB_OFFLINE is pinned at startup (commitment #5), so a
    missing model must degrade to zlib rather than reach the network.
    """
    import importlib.util
    if not (importlib.util.find_spec("mlx") and importlib.util.find_spec("mlx_lm")):
        return False
    hub = pathlib.Path.home() / ".cache/huggingface/hub"
    return any(hub.glob("models--mlx-community--Qwen3-0.6B*")) if hub.exists() else False


def _neural_bits(texts, artifact: bytes | None) -> float:
    """Bits to code each text given `artifact` as CONTEXT, under a local LM.

    Measured 58.2% sharper than the zlib ruler at exactly this job (hq_049:
    1.554 bits/byte vs 3.992 on identical bytes), which is why the gate prefers
    it. The model is a RULER, never the memory — what gets stored is still
    `core.md`, readable and diffable. That distinction is the whole reason this
    is allowed to exist: an opaque judge of a transparent artifact keeps the
    audit trail the product is built on.

    Runs LOCALLY and OFFLINE at build time. It satisfies commitments #2 (nothing
    uploads), #3 (no hosted tier), #4 (no API billing) and #5 (HF offline)
    unchanged; it is the same category of object as the embedder Trinity already
    runs on every ingest. Founder bent #1's letter for exactly this on
    2026-08-10.
    """
    import math

    import mlx.core as mx
    import mlx.nn as nn

    if "m" not in _neural_cache:
        from mlx_lm import load
        _neural_cache["m"], _neural_cache["t"] = load(NEURAL_MODEL)
    model, tok = _neural_cache["m"], _neural_cache["t"]

    ctx = []
    if artifact:
        ctx = tok.encode(artifact.decode("utf-8", "ignore"))[-NEURAL_CTX_TOKENS:]
    total = 0.0
    for t in texts:
        ids = tok.encode(t)
        if len(ids) < 2:
            continue
        seq = list(ctx) + list(ids)
        x = mx.array([seq])
        lg = model(x[:, :-1]).astype(mx.float32)
        lp = nn.losses.cross_entropy(lg.reshape(-1, lg.shape[-1]),
                                     x[:, 1:].reshape(-1), reduction="none")
        # TOKEN-MATCHED START (res_082). `lp[i]` is the loss for predicting
        # seq[i+1]. The old slice was `max(len(ctx)-1, 0)`, which scores
        # len(ids) tokens when a context exists and len(ids)-1 when it does
        # not — so the no-artifact baseline scored ONE FEWER TOKEN PER TEXT
        # than every candidate it was compared against.
        #
        # That is not a rounding error. Measured on 120 held-out prompts it was
        # the dominant term and it inverted the sign of the answer: the real
        # core priced 3,010 bits WORSE than no-core under the old slice and 58
        # bits BETTER once the arms score the same tokens.
        #
        # Starting at len(ctx) drops the first text token from every arm, so
        # each scores exactly len(ids)-1. The cost is one unscored token per
        # text, paid identically everywhere; the benefit is that the comparison
        # is between artifacts rather than between token counts.
        total += float(mx.sum(lp[len(ctx) if ctx else 0:]).item()) / math.log(2)
    return total


def score_bits(texts, artifact: bytes | None) -> tuple[float, str]:
    """The ruler. Neural when available, zlib otherwise; returns (bits, which).

    Both arms are only ever used to compare a CANDIDATE against the INCUMBENT on
    the same texts with the same scorer, so a mid-life switch of ruler cannot
    flip a past verdict — it only sharpens future ones.

    WHAT THIS RULER CANNOT DO (res_026, measured 2026-08-15). It prices
    VOCABULARY, not content. hq_083 scored a word-shuffled twin of an artifact
    against the real thing, paired over 200 held-out founder utterances: the
    SHUFFLED version won, -6.83 vs -10.55 bits per item, 148-52, p<1e-4.
    Coherent propositional structure actively hurts a 0.6B model's prediction.

    So a verdict from this gate discriminates word choice, not meaning — a
    candidate asserting the OPPOSITE of the incumbent in the same vocabulary
    would price identically. Three things keep that tolerable and none of them
    make it semantic: it only ever compares same-genre prose on identical
    texts, it fails closed, and it archives every candidate. Do not read an
    admission as evidence that a candidate says something better.
    """
    if _neural_available():
        try:
            return _neural_bits(texts[:NEURAL_HELDOUT], artifact), "neural"
        except Exception:
            pass          # a broken local stack must degrade, never block a build
    return float(_zlib_bytes(texts, artifact) * 8), "zlib"


def heldout_texts(limit: int = 400) -> list[str]:
    """The founder's most recent OWN utterances, exact-deduped.

    Human-authored only: 8.7% of corpus nodes hold 75.6% of the bytes and that
    mass is machine-dispatched scaffolding. Scoring against it would measure how
    well the core predicts the SCAFFOLD, which is the defect that silently
    inverted hq_042..044 before it was caught.
    """
    p = trinity_home() / "prompts" / "prompt_nodes.jsonl"
    if not p.exists():
        return []
    rows, seen = [], set()
    try:
        with p.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not isinstance(r, dict):
                    continue          # a bare list/str line is not a prompt node
                t = (r.get("text") or "").strip()
                ts = r.get("created_at") or r.get("timestamp")
                if not t or not ts or len(t) < 20 or t in seen:
                    continue
                if len(t) > 2000 or t.count("{") + t.count("[") > len(t) * 0.02:
                    continue          # scaffold-looking; see docstring
                seen.add(t)
                rows.append((str(ts), t))
    except OSError:
        return []
    rows.sort(key=lambda r: r[0])
    return [t for _, t in rows[-limit:]]


@dataclass
class CoreVerdict:
    admitted: bool
    reason: str
    candidate_bits: int | None = None
    incumbent_bits: int | None = None
    heldout_n: int = 0
    archived: str | None = None

    def to_dict(self) -> dict:
        d = {"admitted": self.admitted, "reason": self.reason,
             "heldout_n": self.heldout_n}
        for k in ("candidate_bits", "incumbent_bits", "archived"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


def _archive(text: str, verdict: str, extra: dict) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = history_dir() / f"core-{stamp}-{verdict}.md"
    meta = {"at": stamp, "verdict": verdict, **extra}
    p.write_text("<!-- " + json.dumps(meta) + " -->\n" + text.strip() + "\n",
                 encoding="utf-8")
    return str(p)



# ── stance adjudication (res_026's fix) ───────────────────────────────────────


STANCE_MODEL = "qwen3.8:27b-q4_K_M"
STANCE_URL = "http://localhost:11434/v1/chat/completions"
STANCE_N = 9
STANCE_MAJORITY = 0.60

_STANCE_PROMPT = """Below are TWO candidate context documents, then a SENTENCE the author
of those documents actually wrote later.

Which document better captures how this author thinks, such that the sentence
follows naturally from it?

Answer with ONLY the letter A or B. No prose.

DOCUMENT A:
%s

DOCUMENT B:
%s

THE AUTHOR'S LATER SENTENCE:
%s
"""


def stance_prefers_candidate(candidate: str, incumbent: str,
                             texts: list[str]) -> tuple[bool, str] | None:
    """Does a LOCAL reader think the candidate captures this author better?

    WHY THIS EXISTS. `score_bits` is content-blind — measured, not suspected:
    hq_083 fed it a word-shuffled twin of an artifact and the SHUFFLE won,
    148-52, p<1e-4. So the bits ruler discriminates vocabulary, and a candidate
    asserting the OPPOSITE of the incumbent in the same words prices identically
    (res_026). This adds the axis bits cannot see.

    WHY A CHOICE AND NOT A BIGGER RULER. Ollama exposes no echo mode, so
    prompt-token logprobs are unreadable and a larger local model cannot score
    bits at all. The instrument had to change shape, not size.

    WHAT LICENSES IT. hq_104: on real core.md versus a COHERENT INVERSION of the
    same length and register — written by the judge itself, so the handicap ran
    against the real document — a local 27B preferred the real one 54/60 = 90%,
    CI [0.82, 0.98], position bias 53% against a 70% bar.

    It can only make the gate STRICTER: propose_core requires bits AND stance.
    Returns None when no local judge is reachable, and the caller then behaves
    exactly as before — a missing judge must not silently admit.
    """
    import json as _json
    import random as _random
    import re as _re
    import urllib.request as _url

    sample = list(texts)
    _random.Random(len(sample)).shuffle(sample)
    sample = sample[:STANCE_N]
    if not sample:
        return None

    wins = 0
    scored = 0
    for i, t in enumerate(sample):
        cand_is_a = (i % 2 == 0)          # alternate so position cannot decide it
        a, b = (candidate, incumbent) if cand_is_a else (incumbent, candidate)
        body = _json.dumps({"model": STANCE_MODEL,
                            "messages": [{"role": "user",
                                          "content": _STANCE_PROMPT % (a, b, t)}],
                            "max_tokens": 3000, "temperature": 0}).encode()
        try:
            req = _url.Request(STANCE_URL, data=body,
                               headers={"Content-Type": "application/json"})
            with _url.urlopen(req, timeout=120) as r:
                d = _json.loads(r.read())
        except Exception:
            return None                    # no judge reachable — caller falls back
        m = (d.get("choices") or [{}])[0].get("message") or {}
        txt = (m.get("content") or "").strip()
        if not txt:
            # reasoning models spend the budget before answering; Ollama exposes
            # the trace as `reasoning`, and reading the wrong key once turned a
            # working judge into a 75% parse rate (hq_104 run 1).
            txt = (m.get("reasoning") or m.get("reasoning_content") or "")[-300:]
        hit = _re.findall(r"\b([AB])\b", txt)
        if not hit:
            continue
        scored += 1
        if (hit[-1] == "A") == cand_is_a:
            wins += 1
    if scored < 3:
        return None                        # too few usable answers to mean anything
    rate = wins / scored
    return (rate >= STANCE_MAJORITY,
            f"{STANCE_MODEL} prefers the candidate on {wins}/{scored} held-out prompts")


# --- the length confound, measured 2026-08-24 (res_079) -----------------------
# `score_bits` conditions held-out text on the candidate and returns perplexity.
# It never charges L(candidate) and never subtracts a no-candidate baseline, so
# it is monotone in LENGTH and nearly blind to content. Measured on 400 held-out
# prompts with the neural ruler:
#
#   no core at all ....... 11,732 bits   <- strictly the best "core" available
#   a single space ....... 12,215
#   a quota error ........ 12,597
#   an OAuth error ....... 13,061
#   the REAL 1,187-char core 13,213
#
# Every candidate scores WORSE than no core, and shorter junk beats longer truth
# every time. At MATCHED length the artifact cancels and nothing is left: the
# real core loses to its own word-shuffle by 381 bits and beats character-level
# gibberish by 1.0 bit in 13,000 (0.008%).
#
# The token-count asymmetry that made the BASELINE look unbeatable was fixed
# 2026-08-24 (res_082) and was worth ~3,068 bits, enough to invert the
# core-versus-baseline sign. Necessary and NOT sufficient: token-matched, the
# real core still loses to its own word-shuffle by 334 bits and a single space
# still prices better than both. The gate therefore stays closed.
#
# So this ruler cannot rank cores. It ranked length, and every historical
# admission was won on brevity — which is how an OAuth error became the
# founder's identity on 2026-08-18 and a session-limit notice replaced it on
# 2026-08-24. The 2026-08-18 fix repaired a genuine fail-open, but its stated
# rationale ("with a real corpus present the ruler rejects that string outright,
# so the ruler was never the problem") is FALSIFIED: with 400 held-out texts the
# neural ruler admitted the OAuth string at 13,973 against the real core's
# 14,222.
#
# Until a ruler exists that survives a length-matched control, the gate FAILS
# CLOSED: it never admits over a live incumbent on ruler evidence alone.
LENGTH_CONFOUNDED_RULER = True

_ERROR_SHAPES = (
    "session limit", "usage limit", "rate limit", "quota",
    "failed to authenticate", "oauth", "session expired",
    "please try again", "api error", "overloaded",
)


def looks_like_provider_error(text: str) -> bool:
    """Defence in depth: a provider error must never reach the ruler.

    Both corrupted cores were provider errors that the distill stage returned as
    if they were answers, with ok=true. The gate below fails closed anyway, but a
    core is the founder's identity and this class of input deserves a named
    refusal rather than a generic one.
    """
    t = " ".join((text or "").lower().split())
    if not t:
        return False
    # Short AND matching an error shape. Length alone is not suspicious (a terse
    # core is legitimate) and a shape alone is not either (a real core could
    # discuss rate limits) -- it is the conjunction that identifies the failure.
    return len(t) < 400 and any(shape in t for shape in _ERROR_SHAPES)


def propose_core(candidate: str, *, heldout: list[str] | None = None,
                 stance_fn: Callable[[str, str, list[str]], tuple[bool, str] | None]
                 | None = None) -> CoreVerdict:
    """Gate a candidate core. Admitted only if it does not price held-out text worse.

    ALWAYS archives, whatever the verdict — the pre-gate behaviour kept exactly one
    file with no recovery path, so a bad distill was unrecoverable.
    """
    cand = (candidate or "").strip()
    if not cand:
        return CoreVerdict(False, "empty candidate", archived=None)

    if looks_like_provider_error(cand):
        return CoreVerdict(
            False,
            "candidate looks like a PROVIDER ERROR, not a distillation — refused "
            "before scoring. Two cores were lost this way (OAuth 2026-08-18, "
            "session limit 2026-08-24); the distill stage returned the error as "
            "an answer and reported ok=true.",
            archived=_archive(cand, "refused-provider-error", {"len": len(cand)}))

    incumbent = ""
    cp = core_path()
    if cp.exists():
        try:
            incumbent = cp.read_text(encoding="utf-8").strip()
        except OSError:
            incumbent = ""

    if not incumbent:
        return CoreVerdict(True, "no incumbent core — first write admitted",
                           archived=_archive(cand, "admitted", {"reason": "first-write"}))

    texts = heldout if heldout is not None else heldout_texts()
    if not texts:
        # An incumbent EXISTS and the ruler cannot score. The old comment here
        # read "no corpus at all means the incumbent was never earned either" —
        # false, and it cost a core. The incumbent WAS earned, when the corpus
        # was readable; the corpus being unreadable RIGHT NOW is a statement
        # about this moment, not about the incumbent.
        #
        # Measured 2026-08-18 (res_062): a `lens --deep` run whose OAuth expired
        # mid-flight produced the 72-byte string "Failed to authenticate: OAuth
        # session expired and could not be refreshed". The same auth failure
        # emptied the held-out corpus, so this branch admitted the error string
        # as the founder's identity — and the chairman reads core.md FIRST.
        # With a real corpus present the ruler rejects that string outright
        # (6104 bits vs the incumbent's 5087), so the ruler was never the
        # problem; this fail-OPEN was, in a function whose own docstring
        # promises it FAILS CLOSED.
        #
        # A genuine first build still writes: that is the `no incumbent` branch
        # above, which returns before this one.
        return CoreVerdict(
            False,
            "no corpus to score against, but an incumbent EXISTS — keeping it. "
            "An unreadable corpus is a fact about this run, not about the core.",
                           heldout_n=0,
                           archived=_archive(cand, "admitted", {"reason": "cold-install"}))
    if len(texts) < MIN_HELDOUT:
        # FAIL CLOSED: cannot score => keep the incumbent. Freezing is the safe
        # direction for a core, and the candidate is preserved on disk.
        return CoreVerdict(
            False, f"held-out sample too thin to score ({len(texts)} < {MIN_HELDOUT}) "
                   "— incumbent kept, candidate archived",
            heldout_n=len(texts),
            archived=_archive(cand, "refused-unscorable", {"heldout_n": len(texts)}))

    cb, ruler = score_bits(texts, cand.encode("utf-8"))
    ib, _ = score_bits(texts, incumbent.encode("utf-8"))
    ok = cb <= ib + TOLERANCE_BITS

    if LENGTH_CONFOUNDED_RULER and ok:
        # The ruler said "admit". Measured, it says that whenever the candidate
        # is SHORTER, so the recommendation carries no information about quality
        # and a live incumbent must not be replaced on it. Numbers are still
        # computed and archived, because the day a ruler passes a length-matched
        # control this becomes evidence again.
        ok = False
        return CoreVerdict(
            False,
            f"[{ruler} ruler] candidate prices {len(texts)} held-out prompts at "
            f"{cb:.0f} bits vs incumbent {ib:.0f}, which the ruler reads as an "
            "improvement — but this ruler is LENGTH-CONFOUNDED (res_079: it "
            "prefers a single space to the real core, and prefers the real "
            "core's own word-shuffle to the real core). Incumbent kept, "
            "candidate archived for human review.",
            candidate_bits=int(cb), incumbent_bits=int(ib), heldout_n=len(texts),
            archived=_archive(cand, "refused-length-confounded-ruler",
                              {"candidate_bits": cb, "incumbent_bits": ib,
                               "heldout_n": len(texts), "ruler": ruler}))

    # SECOND AXIS (res_026). Bits cannot see stance, so a candidate asserting the
    # opposite of the incumbent in the same vocabulary passes the ruler. Ask a
    # local reader as well and require BOTH. This can only make the gate
    # stricter; when no judge is reachable it returns None and nothing changes.
    stance_note = ""
    # INJECTED so a locked module grows no flags and the suite stays hermetic:
    # no stance_fn means byte-for-byte the pre-2026-08-17 behaviour. Callers that
    # want the second axis pass `stance_prefers_candidate`.
    stance = stance_fn(cand, incumbent, texts) if (ok and stance_fn) else None
    if stance is not None:
        stance_ok, stance_note = stance
        ok = ok and stance_ok
        stance_note = f" | stance: {stance_note}"

    verdict = "admitted" if ok else "rejected"
    arch = _archive(cand, verdict, {"candidate_bits": cb, "incumbent_bits": ib,
                                    "heldout_n": len(texts), "ruler": ruler,
                                    "stance": stance_note.strip(" |") or None})
    reason = (f"[{ruler} ruler] candidate prices {len(texts)} held-out prompts at "
              f"{cb:.0f} bits vs incumbent {ib:.0f}" + stance_note
              + ("" if ok else " — degradation refused, core frozen"))
    return CoreVerdict(ok, reason, candidate_bits=int(cb), incumbent_bits=int(ib),
                       heldout_n=len(texts), archived=arch)


def item_values(items: list[str], *, heldout: list[str] | None = None) -> list[dict]:
    """Leave-one-out value of each item: what does REMOVING it cost?

    An item that costs nothing when deleted has not earned its place. Returns one
    row per item with `bits_without` minus `bits_with_all` — positive means the
    item pays for itself, zero or negative means it is dead weight.
    """
    texts = heldout if heldout is not None else heldout_texts()
    if len(texts) < MIN_HELDOUT or not items:
        return []
    joined = "\n".join(i.strip() for i in items if i.strip())
    full, _ = score_bits(texts, joined.encode("utf-8"))
    out = []
    for i, item in enumerate(items):
        rest = "\n".join(x.strip() for j, x in enumerate(items) if j != i and x.strip())
        without, _ = (score_bits(texts, rest.encode("utf-8")) if rest
                      else score_bits(texts, None))
        out.append({"index": i, "head": item.strip()[:60],
                    "bits_saved": int(without - full),
                    "earns_place": without - full > 0})
    return out
