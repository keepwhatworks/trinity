"""The lens-constitution airgap — enforced by the data structure, not by policy.

`me/constitution.py` is the MINER: it emits an `EvidenceBundle` of failures clustered by
*what would fix them*, and it must be **structurally unable** to author or gate the lens
edit (Endurance §III: the optimizer can't take the derivative of a constraint that isn't
in its hands). This guard enforces both halves and is mutation-proven:

  * the module imports nothing that can WRITE the lens (`lens_registry` / `save_*` /
    `reconcile` / `me_path`) — add such an import and `test_miner_imports_nothing_that_writes`
    reds;
  * no evidence type carries a field that PRESCRIBES an edit (`pole*`/`lens*`/`edit*`/
    `registry*`/`verdict*`/`proposed*`) — add such a field and
    `test_evidence_types_carry_no_edit_field` reds.

Plus a functional check that `mine_evidence` clusters by the dominant fix axis, abstains
under the TF-IDF fallback, and surfaces the q-field confound guard.
"""
from __future__ import annotations

import ast
import re
from dataclasses import fields
from pathlib import Path

from trinity_local.me.constitution import (
    EvidenceBundle,
    EvidenceCluster,
    EvidenceSignature,
    label_q_status,
    mine_evidence,
)
from trinity_local.me.preference_acts import (
    MODEL_MISS,
    PreferenceAct,
    Q_CONFOUND,
    Q_OPERATIVE,
    Q_UNCERTAIN,
)

_MODULE = Path(__file__).resolve().parents[1] / "src" / "trinity_local" / "me" / "constitution.py"

# The lens WRITE surface — the miner may import none of it (that is the airgap).
_BANNED_IMPORT_NAMES = {
    "save_registry", "reconcile", "save_preference_acts", "save_pairs",
    "save_orderings", "save_basins", "me_path",
}
_BANNED_IMPORT_MODULES = {"lens_registry"}
# Any field naming an edit rather than evidence breaks the type-level airgap.
_BANNED_FIELD = re.compile(r"pole|lens|edit|registry|verdict|proposed", re.IGNORECASE)


def test_miner_imports_nothing_that_writes():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    breaches: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod_parts = (node.module or "").split(".")
            if _BANNED_IMPORT_MODULES & set(mod_parts):
                breaches.append(f"from {node.module} import ...")
            for alias in node.names:
                if alias.name in _BANNED_IMPORT_NAMES:
                    breaches.append(f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _BANNED_IMPORT_MODULES & set(alias.name.split(".")):
                    breaches.append(f"import {alias.name}")
    assert not breaches, (
        "AIRGAP BREACH: the miner imports the lens write-surface — the optimizer could "
        f"reach up and rewrite the rule above it: {breaches}"
    )


def test_evidence_types_carry_no_edit_field():
    for cls in (EvidenceBundle, EvidenceCluster, EvidenceSignature):
        for f in fields(cls):
            assert not _BANNED_FIELD.search(f.name), (
                f"AIRGAP BREACH: {cls.__name__}.{f.name} names an EDIT, not evidence — the "
                "bundle must carry evidence and stop; the proposer holds the pen, not the miner"
            )


# ── functional: clustering by fix axis + the confound guard ──────────────────────────

# A deterministic 4-D embedder aligned to the four TASTE_AXES (order: concrete↔abstract,
# terse↔verbose, decisive↔hedging, action↔description). +1 toward the first pole. Built
# from the same prototype keywords TASTE_AXES uses, so the axis frame ≈ the basis and a
# crafted act projects onto its intended axis — no MLX needed.
_AXIS_KEYWORDS = [
    (("code", "runnable", "command", "exact", "specific", "product"),
     ("concept", "general", "high-level", "overview", "theory")),
    (("short", "one line", "just the answer", "keep it"),
     ("detailed", "thorough", "explanation", "caveat", "full context")),
    (("pick one", "make the call", "commit", "what would you do"),
     ("depends", "tradeoffs", "can't decide", "weigh")),
    (("do it", "ship", "build it"),
     ("could do", "the options", "approaches")),
]


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for t in texts:
        tl = t.lower()
        v = [0.0, 0.0, 0.0, 0.0]
        for i, (pos, neg) in enumerate(_AXIS_KEYWORDS):
            v[i] += sum(1.0 for k in pos if k in tl)
            v[i] -= sum(1.0 for k in neg if k in tl)
        out.append(v)
    return out


def _act(id_, privileged, sacrificed, q_status):
    return PreferenceAct(id=id_, trigger=MODEL_MISS, privileged=privileged,
                         sacrificed=sacrificed, kind="REFRAME", why="w", q_status=q_status)


def test_mine_evidence_clusters_by_fix_axis_and_flags_confound():
    acts = [
        _act("t1", "keep it short, just the answer",
             "a detailed thorough explanation with full context", Q_OPERATIVE),
        _act("t2", "one line",
             "a detailed thorough explanation with caveats", Q_OPERATIVE),
        _act("c1", "the exact command to run",
             "explain the general concept", Q_OPERATIVE),
        _act("conf", "keep it short",
             "a detailed thorough explanation", Q_CONFOUND),
    ]
    bundle = mine_evidence(acts, embed_fn=_fake_embed)
    assert bundle.ready and bundle.n_acts == 4
    by_key = {c.fix_key: c for c in bundle.fix_clusters}
    # The three terse steers (incl. the confound) cluster on one fix; the concrete steer
    # forms its own — clustered by what would FIX them, not by surface text.
    terse = next(c for k, c in by_key.items() if k.startswith("terse↔verbose"))
    concrete = next(c for k, c in by_key.items() if k.startswith("concrete↔abstract"))
    assert {s.act_id for s in terse.signatures} == {"t1", "t2", "conf"}
    assert {s.act_id for s in concrete.signatures} == {"c1"}
    # The confound guard: the CONFOUND act is in the cluster's evidence but does NOT count
    # as operative support, and it raises the confound fraction so the proposer can refuse.
    assert terse.operative_support == 2
    assert terse.confound_fraction == round(1 / 3, 3)  # 1 of 3 sigs is CONFOUND
    assert concrete.operative_support == 1 and concrete.confound_fraction == 0.0
    # The mechanism names the reusable fix, not an edit to write.
    assert terse.mechanism == "favor terse over verbose"


def test_mine_evidence_abstains_without_real_embeddings():
    # embed_fn=None + no MLX → abstain, never cluster on the TF-IDF word-overlap fallback.
    bundle = mine_evidence([_act("x", "p p p", "s s s s s", Q_OPERATIVE)], embed_fn=None)
    if not bundle.ready:  # the expected path on a TF-IDF / no-embedder box
        assert "embedding" in bundle.reason.lower() and bundle.fix_clusters == ()


def test_mine_evidence_empty_is_clean():
    bundle = mine_evidence([], embed_fn=_fake_embed)
    assert not bundle.ready and bundle.fix_clusters == () and bundle.n_acts == 0


# ── label_q_status: observational q-attribution (geometric tier) ──────────────────────
def _unlabeled(id_, privileged, sacrificed):
    return PreferenceAct(id=id_, trigger=MODEL_MISS, privileged=privileged,
                         sacrificed=sacrificed, kind="REFRAME", why="w")


def test_label_q_status_sets_three_statuses():
    operative = _unlabeled(
        "op", "keep it short just the answer",
        "a detailed thorough explanation with full context")          # one axis dominant
    confound = _unlabeled(
        "cf", "command short", "concept detailed")                    # two axes equally → ambiguous
    uncertain = _unlabeled(
        "un", "xyzzy plover", "frobnicate the wizzle")                # no axis signal
    acts = [operative, confound, uncertain]
    n = label_q_status(acts, embed_fn=_fake_embed)
    assert n == 3
    assert operative.q_status == Q_OPERATIVE and operative.q_axis == "terse↔verbose"
    assert confound.q_status == Q_CONFOUND        # which axis drove it is genuinely ambiguous
    assert uncertain.q_status == Q_UNCERTAIN      # no axis clearly involved → safe default

    # And the confound guard then bites in mine_evidence: the CONFOUND act doesn't count as
    # operative support for its cluster.
    bundle = mine_evidence(acts, embed_fn=_fake_embed)
    for c in bundle.fix_clusters:
        for s in c.signatures:
            if s.act_id == "cf":
                assert c.operative_support < len(c.signatures)


def test_label_q_status_abstains_without_real_embeddings():
    # embed_fn=None + no MLX → label nothing (returns 0, acts stay UNCERTAIN-by-default).
    a = _unlabeled("x", "keep it short", "a detailed thorough explanation")
    n = label_q_status([a], embed_fn=None)
    if n == 0:  # the expected path on a TF-IDF / no-embedder box
        assert a.q_status == "" and a.causal_status() == Q_UNCERTAIN
