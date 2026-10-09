"""Which prompts are the founder? The class map every lens read goes through.

Founder, 2026-10-07: "I want the compression to be solely of me, the directions i push, so you
could launch multiple versions of me to explore the world and share the residuals with me."

The prompt store holds everything that arrived as role=user, and much of that is not the
person: Claude Code and Codex runs started by a machine (`claude -p`, the Agent SDK, `codex
exec`), agent-to-agent messages, a /loop re-sending the same directive, later copies of a text
(including Trinity's own dispatches of the user's question to other models), and model output
pasted back in. On the founder's corpus about 39% of ingested prompts were one of these
(res_160). A lens fit on that is partly a lens of the machines around the person.

`build_authorship_map()` classifies every prompt node at lens-build time and writes
`me/authorship_map.json` (versioned; it is corpus-relative, since "earliest copy" changes as the
corpus grows, so it is rebuilt on every build rather than stamped once at ingest).
`iter_lens_nodes()` is the ONE gate lens-side readers use: dropped classes are skipped, a paste
contributes only the user's own lines, everything else passes. Retrieval, search and the trust
ledger read the store directly and are not filtered.

Classes dropped, first rule wins (council_c2c21ef89b71c9c0; gate hq_130, res_162):
  headless    the session was started by a machine: `origin` stored at ingest, or, for nodes
              ingested before that field existed, the transcript's own metadata if it still exists
  machine     production's role=user filter rejects it, or it carries an agent/harness marker
  repeat      the same text (>= 40 chars) already appeared earlier in the same transcript
  copy        a later copy of a text whose earliest instance is another node ('dispatched' when
              the text is in Trinity's dispatch ledger); the earliest instance must still pass
              every other rule on its own
  template    a later instance of a prompt skeleton (its first 8 words with numbers and quoted
              spans as slots) seen in >= 5 sessions. Almost always a script filling in a form
              ("Fix the visual issue for plan "<name>" (<score>). ISSUE: ..."): 2026-10-08, 564 such
              rows from an automated loop were kept as the founder because their sessions predate
              the entrypoint field. Measured on the founder's store: 862 drops in 31 skeletons,
              about 5% of them the founder re-sending an opener in another app; as for copies,
              the earliest instance stays (council_186be390a7ba1788, amd_0326)
  paste       >= 40% of its 8-word shingles appear in an assistant turn EARLIER than the prompt
              (assistants quote the user back afterwards, so only earlier text counts); the
              user's own lines inside it are kept as its text
Kept: everything else, including nodes whose session origin is unknown. The research filter's
embedding-based paste_like flag is not computed here.

Gate result (hq_130, res_162): a palate fit on only the founder's acts is non-inferior to one
fit on everything (-0.003 bits/decision, CI -0.008..+0.002, margin -0.03), so this ships as
cleanup, with no "compresses you better" claim. Escape hatch: TRINITY_LENS_ONLY_ME=0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Iterator

from ..state_paths import trinity_home
from ..utils import now_iso

MAP_VERSION = 2   # 2: the template class. The palate epoch follows this number.
DROP = ("headless", "machine", "repeat", "copy", "dispatched", "template", "paste")
REPEAT_MIN_CHARS = 40
TEMPLATE_WORDS, TEMPLATE_MIN_SESSIONS = 8, 5
_SLOT_QUOTED = re.compile(r'"[^"\n]{1,120}"|\'[^\'\n]{1,120}\'|`[^`\n]{1,120}`')
_SLOT_NUMBER = re.compile(r"\d+(?:[.,/:-]\d+)*")
_SKELETON_WORD = re.compile(r"\w+|#")
PASTE_MIN_CHARS, PASTE_SHARE, SHINGLE = 200, 0.40, 8
OWN_MIN_CHARS = 3     # a one-word framing ("Thoughts?") is the user's direction too (verify, codex)
_W = re.compile(r"\w+")
MARKERS = re.compile(r"<teammate-message|Another Claude session sent a message|<cross-session-message"
                     r"|<task-notification>|\[SYSTEM NOTIFICATION|<system-reminder>|^# Make the failing tests pass"
                     r"|Base directory for this skill|<recommended_plugins>|<command-(?:name|message)>"
                     r"|This session is being continued from a previous conversation", re.M)


def lens_filter_enabled() -> bool:
    return os.environ.get("TRINITY_LENS_ONLY_ME", "1") != "0"


def authorship_map_path():
    return trinity_home() / "me" / "authorship_map.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _epoch(ts) -> float | None:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _session_origin_from_file(path: str | None, provider: str | None, memo: dict) -> str | None:
    """For nodes ingested before `origin` existed: read it off the transcript, if still on disk."""
    if not path:
        return None
    if path in memo:
        return memo[path]
    origin = None
    try:
        with open(path, errors="replace") as fh:
            if provider == "claude":
                for line in fh:
                    if '"entrypoint"' in line:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            continue
                        e = row.get("entrypoint")
                        origin = "headless" if str(e).startswith("sdk") else "interactive"
                        break
            elif provider == "codex":
                for i, line in enumerate(fh):
                    if i > 5:
                        break
                    d = json.loads(line)
                    if not isinstance(d, dict):
                        continue
                    if d.get("type") == "session_meta":
                        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                        src = p.get("source")
                        origin = ("headless" if p.get("originator") == "codex_exec"
                                  or (isinstance(src, dict) and "subagent" in src) else "interactive")
                        break
    except (OSError, ValueError):
        origin = None
    memo[path] = origin
    return origin


def _shingles(text: str) -> list[int]:
    w = _W.findall(text.lower())
    return [hash(" ".join(w[i:i + SHINGLE])) for i in range(len(w) - SHINGLE + 1)]


def _assistant_index(nodes):
    """(sorted shingle hashes, earliest time each appeared in any assistant turn) as numpy
    arrays: about 100 MB on a 45k-prompt store, where a dict would take ~700 MB."""
    import numpy as np
    hs, ts = [], []
    for n in nodes:
        t = _epoch(n.timestamp)
        if t is None:
            continue
        for text, dt in ((n.preceding_assistant_text, -1.0), (n.following_assistant_text, 1.0)):
            sh = _shingles(text or "")
            if sh:
                hs.append(np.fromiter(sh, dtype=np.int64, count=len(sh)))
                ts.append(np.full(len(sh), t + dt))
    if not hs:
        return np.zeros(0, dtype=np.int64), np.zeros(0)
    H, T = np.concatenate(hs), np.concatenate(ts)
    order = np.lexsort((T, H))
    H, T = H[order], T[order]
    first = np.r_[True, H[1:] != H[:-1]]
    return H[first], T[first]


def _paste_share(text: str, t: float | None, index) -> float:
    import numpy as np
    H, T = index
    sh = _shingles(text)
    if not sh or t is None or len(H) == 0:
        return 0.0
    q = np.fromiter(sh, dtype=np.int64, count=len(sh))
    pos = np.minimum(np.searchsorted(H, q), len(H) - 1)
    return float(np.mean((H[pos] == q) & (T[pos] < t - 0.5)))


def _own_text(text: str, t: float | None, index) -> str:
    keep = []
    for line in re.split(r"(?<=[.?!:])\s+|\n+", text):
        line = line.strip()
        if len(line) >= OWN_MIN_CHARS and (len(_W.findall(line)) < SHINGLE or _paste_share(line, t, index) < 0.5):
            keep.append(line)
    return " ".join(keep)


def _dispatch_hashes() -> set[str]:
    from ..dispatch_ledger import ledger_path
    out = set()
    try:
        for line in ledger_path().read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("h"), str):
                out.add(row["h"])
    except OSError:
        pass
    return out


def _skeleton(text: str) -> str | None:
    """The first TEMPLATE_WORDS words with quoted spans and numbers as slots, or None if shorter."""
    if len(text or "") < REPEAT_MIN_CHARS:
        return None
    words = _SKELETON_WORD.findall(_SLOT_NUMBER.sub(" # ", _SLOT_QUOTED.sub(" q ", text.lower())))
    return " ".join(words[:TEMPLATE_WORDS]) if len(words) >= TEMPLATE_WORDS else None


def classify(nodes) -> tuple[dict[str, str], dict[str, str]]:
    """(node id -> dropped class, node id -> own text for pastes). Pure over its input."""
    from ..dispatch_ledger import _norm_hash
    from ..ingest import is_user_facing_text
    memo: dict = {}
    disp = _dispatch_hashes()
    index = _assistant_index(nodes)
    earliest: dict[str, tuple] = {}
    for n in nodes:
        k = _norm(n.text)
        if len(k) >= REPEAT_MIN_CHARS:
            stamp = (_epoch(n.timestamp) or float("inf"), n.id)
            if k not in earliest or stamp < earliest[k]:
                earliest[k] = stamp
    skel_tx: dict[str, set] = defaultdict(set)
    skel_first: dict[str, tuple] = {}
    for n in nodes:
        sk = _skeleton(n.text or "")
        if sk:
            skel_tx[sk].add(n.transcript_id)
            stamp = (_epoch(n.timestamp) or float("inf"), n.id)
            if sk not in skel_first or stamp < skel_first[sk]:
                skel_first[sk] = stamp
    seen_in_tx: dict[str, set] = defaultdict(set)
    dropped, own = {}, {}
    for n in sorted(nodes, key=lambda x: (x.transcript_id or "", x.turn_index or 0)):
        text = n.text or ""
        key = _norm(text)
        repeat = len(key) >= REPEAT_MIN_CHARS and key in seen_in_tx[n.transcript_id]
        seen_in_tx[n.transcript_id].add(key)
        origin = getattr(n, "origin", None) or _session_origin_from_file(n.source_path, n.provider, memo)
        if origin == "headless":
            dropped[n.id] = "headless"
        elif not is_user_facing_text(text) or MARKERS.search(text):
            dropped[n.id] = "machine"
        elif repeat:
            dropped[n.id] = "repeat"
        elif len(key) >= REPEAT_MIN_CHARS and earliest.get(key, (None, n.id))[1] != n.id:
            dropped[n.id] = "dispatched" if _norm_hash(text) in disp else "copy"
        elif (sk := _skeleton(text)) and len(skel_tx[sk]) >= TEMPLATE_MIN_SESSIONS and skel_first[sk][1] != n.id:
            dropped[n.id] = "template"
        elif len(text) >= PASTE_MIN_CHARS:
            t = _epoch(n.timestamp)
            if _paste_share(text, t, index) >= PASTE_SHARE:
                dropped[n.id] = "paste"
                o = _own_text(text, t, index)
                if len(o) >= OWN_MIN_CHARS:
                    own[n.id] = o
    return dropped, own


def ensure_authorship_map(fingerprint: str | None = None) -> dict | None:
    """Rebuild the map when the corpus changed since it was written (or it is missing).

    Failure behaviour, stated exactly (verify, codex): if the FIRST build fails there is no map
    and the lens reads the store unfiltered; if a REBUILD fails the last good map stays in force.
    A stale map never drops a prompt it has not classified (new prompts pass through), so it errs
    toward keeping, and the palate epoch does not flip on a transient failure."""
    if not lens_filter_enabled():
        return None
    m = _load_map()
    if m and fingerprint and m.get("fingerprint") == fingerprint:
        return None
    return build_authorship_map(fingerprint)


def build_authorship_map(fingerprint: str | None = None) -> dict:
    """Classify the whole store and write the map atomically. Returns a numbers-only summary.
    Best-effort for its caller: the lens build must not fail because this did."""
    from ..memory.store import iter_prompt_nodes_no_embedding
    t0 = time.time()
    nodes = list(iter_prompt_nodes_no_embedding(limit=None))
    dropped, own = classify(nodes)
    counts: dict[str, int] = defaultdict(int)
    for c in dropped.values():
        counts[c] += 1
    summary = {"version": MAP_VERSION, "built_at": now_iso(), "fingerprint": fingerprint, "nodes": len(nodes),
               "dropped": len(dropped), "share_kept": round(1 - len(dropped) / max(1, len(nodes)), 4),
               "counts": dict(counts), "own_text_recovered": len(own), "secs": round(time.time() - t0, 1)}
    from ..utils import atomic_write_text
    atomic_write_text(authorship_map_path(), json.dumps({**summary, "classes": dropped, "own_text": own}))
    print(f"[authorship] {summary['nodes']} prompts, {summary['dropped']} not the user "
          f"({summary['share_kept']:.0%} kept)", file=sys.stderr)
    return summary


_CACHE: tuple | None = None


def _load_map() -> dict | None:
    global _CACHE
    path = authorship_map_path()
    try:
        st = path.stat()
    except OSError:
        return None
    if _CACHE and _CACHE[0] == (st.st_mtime, st.st_size):
        return _CACHE[1]
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # a valid-JSON file of the wrong shape must read as "no map", never crash a lens build
    if not (isinstance(m, dict) and m.get("version") == MAP_VERSION
            and isinstance(m.get("classes"), dict) and isinstance(m.get("own_text"), dict)):
        return None
    _CACHE = ((st.st_mtime, st.st_size), m)
    return m


def authorship_status() -> dict:
    m = _load_map()
    if not lens_filter_enabled():
        return {"state": "disabled", "reason": "TRINITY_LENS_ONLY_ME=0"}
    if not m:
        return {"state": "absent", "reason": "no authorship map yet; the next lens build writes it"}
    return {"state": "ok", **{k: m[k] for k in ("built_at", "nodes", "dropped", "share_kept", "counts")}}


def lens_view(node_id: str, text: str) -> str | None:
    """The text the lens may use for one prompt: the text itself, the user's own lines of a
    paste, or None when the prompt is not the user. The single rule behind lens_only() and the
    readers that parse the store file directly."""
    m = _load_map() if lens_filter_enabled() else None
    if not m or node_id not in m["classes"]:
        return text
    return m["own_text"].get(node_id)


def is_lens_node_id(node_id: str) -> bool:
    """False when the map says this prompt is not the user (and the filter is on).
    Derived from lens_view(), so there is one rule: a dropped prompt counts only when the user's
    own lines were recovered from it."""
    return lens_view(node_id, "") is not None


def lens_only(nodes) -> Iterator:
    """THE GATE: wrap any prompt-node iterator a lens-side reader uses,
    `lens_only(iter_prompt_nodes(limit=None))`. Dropped prompts are skipped and a paste yields
    only the user's own lines. With no map (first build) or the filter off, it passes everything
    through unchanged. Wrapping (rather than replacing the reader) keeps each caller's own
    iterator, so tests that patch it keep working; tests/test_authorship.py fails if a lens-side
    reader stops wrapping."""
    from dataclasses import replace
    m = _load_map() if lens_filter_enabled() else None
    if not m:
        yield from nodes
        return
    for n in nodes:
        text = lens_view(getattr(n, "id", None), getattr(n, "text", ""))
        if text is None:
            continue
        yield n if text == getattr(n, "text", "") else replace(n, text=text)


def iter_lens_nodes(limit: object = None, *, embeddings: bool = True) -> Iterator:
    """The prompt store through the gate."""
    from ..memory.store import iter_prompt_nodes, iter_prompt_nodes_no_embedding
    yield from lens_only(iter_prompt_nodes(limit=limit) if embeddings else iter_prompt_nodes_no_embedding(limit=limit))
