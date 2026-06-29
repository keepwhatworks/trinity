"""lens-health self-test — the user-facing degeneracy report (#3).

Green-gate discipline (docs/green-gate-checklist.md): the load-bearing assertions
are that the "TRUSTWORTHY" green is REFUSED on degenerate data — TF-IDF fallback,
collapsed topology, broken tensions, no corpus — and only EARNED on a clean lens.
A localized blemish (one polluted basin among many) is a non-blocking CAUTION, not
a wolf-cry. The environment probes (embedding backend/coverage, semantic-noise) are
monkeypatched; the basin-collapse math, the degeneracy-sweep partition, and the
real sweep's lens detection run for real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from trinity_local import lens_health as lh


# ── harness ──────────────────────────────────────────────────────────────────


def _env(monkeypatch, tmp_path, *, coverage, backend, noise=None, findings=None):
    """Point TRINITY_HOME at tmp + stub the environment probes. `findings` stubs the
    degeneracy sweep (None → leave the real sweep, which returns [] on a bare home)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("trinity_local.embeddings.prompt_node_embedding_coverage", lambda: coverage)
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: backend)
    if noise is not None:
        monkeypatch.setattr("trinity_local.me.semantic_filter.semantic_noise_report", lambda *a, **k: noise)
    if findings is not None:
        monkeypatch.setattr("trinity_local.degeneracy.run_degeneracy_sweep", lambda: findings)


def _topics(tmp_path, sizes):
    """Write a topics.json with the given per-basin sizes (turn counts)."""
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    basins = [{"id": f"b{i:02d}", "size": s, "representatives": [], "centroid": []}
              for i, s in enumerate(sizes)]
    (tmp_path / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8")


def _lens(tmp_path, body="### 1. speed ↔ rigor\nSupported by 7 decisions.\n"):
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "lens.md").write_text(body, encoding="utf-8")


_CLEAN_NOISE = {"ready": True, "fraction": 0.05, "flagged": 5, "total": 100}
_HEALTHY_COV = {"text_bearing": 5000, "embedded": 5000}


def _by_key(report):
    return {c.key: c for c in report.checks}


# ── the green is REFUSED on degenerate data ──────────────────────────────────


def test_no_corpus_is_not_trustworthy(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, coverage={"text_bearing": 0, "embedded": 0}, backend=False, findings=[])
    r = lh.run_lens_health()
    assert r.trustworthy is False
    assert _by_key(r)["corpus"].status == lh.ABSENT
    assert "no lens" in r.verdict.lower()


def test_tfidf_fallback_blocks_trust(monkeypatch, tmp_path):
    """THE load-bearing case: a lens on the SHA-1 TF-IDF fallback is lexical, not
    semantic — the green must be refused, and the semantic-noise dimension abstains."""
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=False, noise=_CLEAN_NOISE, findings=[])
    r = lh.run_lens_health()
    by = _by_key(r)
    assert by["embeddings"].status == lh.DEGRADED
    assert by["noise"].status == lh.ABSTAIN, "noise can't be measured in lexical space"
    assert r.trustworthy is False


def test_collapsed_topology_blocks_trust(monkeypatch, tmp_path):
    _topics(tmp_path, [600, 200, 200])  # largest 60% ≥ collapse floor
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE, findings=[])
    r = lh.run_lens_health()
    b = _by_key(r)["basins"]
    assert b.status == lh.DEGRADED and b.metric["largest_basin_share"] == 0.6
    assert r.trustworthy is False


def test_low_coverage_blocks_trust(monkeypatch, tmp_path):
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage={"text_bearing": 5000, "embedded": 2000},  # 40% < 70%
         backend=True, noise=_CLEAN_NOISE, findings=[])
    r = lh.run_lens_health()
    assert _by_key(r)["coverage"].status == lh.DEGRADED
    assert r.trustworthy is False


def test_broken_tensions_block_trust(monkeypatch, tmp_path):
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)  # file exists so it's not ABSENT; the sweep finding drives DEGRADED
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE,
         findings=["F/lens: 2 tension(s) supported by 0 decisions (no evidence)"])
    r = lh.run_lens_health()
    assert _by_key(r)["lens"].status == lh.DEGRADED
    assert r.trustworthy is False


def test_widespread_template_pollution_blocks_trust(monkeypatch, tmp_path):
    """1 polluted basin of 4 = 25% ≥ the 15% degrade fraction → blocks trust."""
    _topics(tmp_path, [100, 100, 100, 100])
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE,
         findings=["C/basins: 1 template-concentrated basin ['b01']"])
    r = lh.run_lens_health()
    assert _by_key(r)["basins"].status == lh.DEGRADED
    assert r.trustworthy is False


def test_high_noise_blocks_trust(monkeypatch, tmp_path):
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True,
         noise={"ready": True, "fraction": 0.55, "flagged": 55, "total": 100}, findings=[])
    r = lh.run_lens_health()
    assert _by_key(r)["noise"].status == lh.DEGRADED
    assert r.trustworthy is False


# ── the green CAN be earned, and a caution does NOT cry wolf ──────────────────


def test_clean_lens_is_trustworthy(monkeypatch, tmp_path):
    _topics(tmp_path, [100] * 10)  # well spread, largest 10%
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE, findings=[])
    r = lh.run_lens_health()
    assert r.trustworthy is True and not r.weak
    # OK or ABSTAIN both pass: a dimension that legitimately can't measure on this fixture
    # (e.g. preference-collapse needs enough held-out corrections; this fixture has none)
    # abstains — abstain neither blocks trust nor raises a caution. DEGRADED/WEAK/ABSENT
    # would still fail this, so the clean-lens invariant is intact.
    assert all(c.status in (lh.OK, lh.ABSTAIN) for c in r.checks)
    assert "TRUSTWORTHY" in lh.format_human(r) and "with cautions" not in lh.format_human(r)


def test_localized_pollution_is_a_caution_not_a_block(monkeypatch, tmp_path):
    """1 polluted basin of 10 = 10% < 15% → WEAK caution, still trustworthy, exit-0."""
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE,
         findings=["C/basins: 1 template-concentrated basin ['b01']"])
    r = lh.run_lens_health()
    by = _by_key(r)
    assert by["basins"].status == lh.WEAK
    assert r.trustworthy is True and r.weak  # trustworthy, but caution surfaced
    assert "with cautions" in lh.format_human(r)


def test_thin_corpus_is_weak_not_blocking(monkeypatch, tmp_path):
    _topics(tmp_path, [10] * 5)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage={"text_bearing": 50, "embedded": 50},  # < weak floor
         backend=True, noise=_CLEAN_NOISE, findings=[])
    r = lh.run_lens_health()
    assert _by_key(r)["corpus"].status == lh.WEAK
    assert r.trustworthy is True  # thin ≠ degenerate


def test_known_producer_leak_is_a_caution(monkeypatch, tmp_path):
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE,
         findings=["A/eval: prompt==gold degenerate item leaked into the eval set"])
    r = lh.run_lens_health()
    assert _by_key(r)["known_issues"].status == lh.WEAK
    assert r.trustworthy is True


# ── the noise dimension actually RUNS on real (and drifted) topology ──────────


def _seed_corpus_and_drifted_topics(tmp_path):
    """A real corpus (embedded nodes) + a topics.json whose basins carry real
    centroids AND a schema-drift extra key a NEWER builder wrote (the viewer's
    `prompt_id_count`, a future field). `load_basins` must tolerate the drift so
    the noise probe can compute a fraction; the old `Basin(**b)` raised
    `TypeError: unexpected keyword argument` and the whole noise dimension went
    dark, leaking the raw exception repr into the user's trust report."""
    from trinity_local.memory import upsert_prompt_node
    from trinity_local.memory.schemas import PromptNode

    for i in range(8):
        upsert_prompt_node(PromptNode(
            id=f"p{i}", transcript_id=f"t{i}", provider="claude", source_path="/x",
            turn_index=0, text=f"refactor module {i} cleanly", embedding=[0.0] * 7 + [1.0],
            created_at="2026-06-01T00:00:00Z",
        ))
    basins = []
    for j in range(4):
        cen = [0.0] * 8
        cen[j] = 1.0
        basins.append({
            "id": f"b{j:02d}", "size": 50, "top_terms": ["refactor", "module"],
            "centroid": cen, "prompt_id_count": 5, "future_field_v9": "x",  # ← drift
        })
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "topics.json").write_text(
        json.dumps({"basins": basins}), encoding="utf-8")


def test_noise_dimension_runs_on_drifted_topology_no_typeerror_leak(monkeypatch, tmp_path):
    """REGRESSION (the noise self-test going silently dark): with the real embedder
    live and a topics.json carrying real centroids, the 'Signal vs noise' dimension
    must actually COMPUTE a fraction (status OK/DEGRADED) — never ABSTAIN with a raw
    `Basin.__init__() … TypeError` leaked into a user-facing trust report. The
    semantic-noise probe and the REAL `load_basins` run; only the embedder probe +
    the noise PROTOTYPE vectors are stubbed (so the guard doesn't need MLX installed
    to bite the load_basins regression)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    _seed_corpus_and_drifted_topics(tmp_path)
    _lens(tmp_path)
    # real embedder claimed live; fixed noise prototypes so the probe computes
    # purely off load_basins + the seeded node embeddings (no MLX dependency).
    monkeypatch.setattr("trinity_local.embeddings.prompt_node_embedding_coverage",
                        lambda: _HEALTHY_COV)
    monkeypatch.setattr("trinity_local.embeddings.mlx_actually_loaded", lambda: True)
    monkeypatch.setattr("trinity_local.me.semantic_filter.noise_prototype_vectors",
                        lambda: [[1.0] + [0.0] * 7])
    monkeypatch.setattr("trinity_local.degeneracy.run_degeneracy_sweep", lambda: [])

    r = lh.run_lens_health()
    noise = _by_key(r)["noise"]
    # The dimension must have RUN — not abstained with a probe crash.
    assert noise.status in (lh.OK, lh.DEGRADED), (
        f"the noise self-test went dark (status={noise.status!r}: {noise.summary!r}) on a "
        "schema-drifted topics.json — load_basins' brittle Basin(**b) raised TypeError "
        "and the degeneracy DETECTOR silently stopped detecting"
    )
    assert "fraction" in noise.metric, "noise dimension ran but reported no fraction"
    # And no dimension may leak a raw Python exception repr to the user.
    leak = [c for c in r.checks
            if "TypeError" in c.summary or "__init__()" in c.summary or "keyword argument" in c.summary]
    assert not leak, (
        f"a raw Python exception repr leaked into the user-facing lens-health report: "
        f"{[(c.key, c.summary) for c in leak]}"
    )


# ── the verdict FLIPS at the EXACT threshold (off-by-one / >= vs > guard) ─────
# The existing degenerate/clean tests drive values FAR from the floors (0.60
# collapse, 0.10 clean, 50-prompt corpus) so a one-step boundary error in a
# trust-blocking verdict ships silently: flipping `largest >= BASIN_COLLAPSE_FLOOR`
# to `>`, `largest >= BASIN_CONCENTRATION_WARN` to `>`, or `text_bearing <
# CORPUS_WEAK_FLOOR` to `<=` all leave those tests green. These pin the FLIP at the
# exact constant so a DEGRADED/WEAK/OK verdict can't be off by one step.


def test_basin_collapse_verdict_flips_at_the_exact_floor(monkeypatch, tmp_path):
    """largest-basin share EXACTLY at BASIN_COLLAPSE_FLOOR (0.40) → DEGRADED (the
    comment promises '≥40% → collapsed', an inclusive floor); one integer step
    below (0.39) must NOT collapse — it's a WEAK concentration caution, still
    trustworthy. Mutation: `largest >= BASIN_COLLAPSE_FLOOR` → `> ` makes the 0.40
    case WEAK (trustworthy) — a collapsed lens silently blessed at the boundary."""
    assert lh.BASIN_COLLAPSE_FLOOR == 0.40  # pin the constant the fixtures target

    _lens(tmp_path)
    # largest = 40/100 = 0.40 exactly → collapse floor → DEGRADED, blocks trust.
    _topics(tmp_path, [40, 30, 30])
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True,
         noise=_CLEAN_NOISE, findings=[])
    r_at = lh.run_lens_health()
    b_at = _by_key(r_at)["basins"]
    assert b_at.metric["largest_basin_share"] == 0.40
    assert b_at.status == lh.DEGRADED, (
        "a lens whose largest basin holds EXACTLY 40% of turn-mass (== the collapse "
        f"floor) was not flagged DEGRADED (got {b_at.status!r}) — the inclusive '≥40%' "
        "boundary regressed to exclusive '>', blessing a collapsed topology at the edge"
    )
    assert r_at.trustworthy is False

    # one step below the floor (0.39) → NOT collapsed; a WEAK concentration caution.
    _topics(tmp_path, [39, 31, 30])
    r_below = lh.run_lens_health()
    b_below = _by_key(r_below)["basins"]
    assert b_below.metric["largest_basin_share"] == 0.39
    assert b_below.status == lh.WEAK, (
        "a largest-basin share of 0.39 (one step below the 0.40 collapse floor) must be a "
        f"WEAK caution, not DEGRADED (got {b_below.status!r}) — the floor crept down a step"
    )
    assert r_below.trustworthy is True


def test_basin_concentration_warn_flips_at_the_exact_threshold(monkeypatch, tmp_path):
    """largest share EXACTLY at BASIN_CONCENTRATION_WARN (0.25) → WEAK ('≥25% → warn',
    inclusive); just below (0.24) → OK (clean). Mutation: `largest >=
    BASIN_CONCENTRATION_WARN` → `> ` makes the 0.25 case OK (no caution) — a
    concentrated topology painted clean at the boundary."""
    assert lh.BASIN_CONCENTRATION_WARN == 0.25

    _lens(tmp_path)
    # largest = 25/100 = 0.25 exactly → concentration warn → WEAK caution.
    _topics(tmp_path, [25, 25, 25, 25])
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True,
         noise=_CLEAN_NOISE, findings=[])
    r_at = lh.run_lens_health()
    b_at = _by_key(r_at)["basins"]
    assert b_at.metric["largest_basin_share"] == 0.25
    assert b_at.status == lh.WEAK, (
        "a largest-basin share of EXACTLY 0.25 (== the concentration-warn threshold) "
        f"surfaced no caution (got {b_at.status!r}) — the inclusive '≥25%' warn boundary "
        "regressed to exclusive '>', painting a concentrated topology clean at the edge"
    )

    # one step below (0.24) → genuinely well-spread → OK, no caution.
    _topics(tmp_path, [24, 19, 19, 19, 19])
    r_below = lh.run_lens_health()
    b_below = _by_key(r_below)["basins"]
    assert b_below.metric["largest_basin_share"] == 0.24
    assert b_below.status == lh.OK, (
        "a largest-basin share of 0.24 (one step below the 0.25 warn threshold) must be "
        f"OK, not a WEAK caution (got {b_below.status!r}) — the warn threshold crept down"
    )


def test_corpus_depth_verdict_flips_at_the_exact_floor(monkeypatch, tmp_path):
    """text_bearing EXACTLY at CORPUS_WEAK_FLOOR (200) → OK ('< floor → thin', so the
    floor itself is enough); one prompt below (199) → WEAK. Mutation: `text_bearing <
    CORPUS_WEAK_FLOOR` → `<=` makes 200 prompts read as 'thin' — an off-by-one on the
    depth verdict that nags a corpus that just cleared the bar."""
    assert lh.CORPUS_WEAK_FLOOR == 200

    _lens(tmp_path)
    _topics(tmp_path, [100] * 10)
    # exactly at the floor → enough signal → OK.
    _env(monkeypatch, tmp_path, coverage={"text_bearing": 200, "embedded": 200},
         backend=True, noise=_CLEAN_NOISE, findings=[])
    r_at = lh.run_lens_health()
    assert _by_key(r_at)["corpus"].status == lh.OK, (
        "a corpus of EXACTLY 200 text-bearing prompts (== the weak floor) was flagged "
        "thin — the '< floor' comparison regressed to '<=', nagging a corpus that just "
        "cleared the bar"
    )

    # one prompt below the floor → genuinely thin → WEAK.
    _env(monkeypatch, tmp_path, coverage={"text_bearing": 199, "embedded": 199},
         backend=True, noise=_CLEAN_NOISE, findings=[])
    r_below = lh.run_lens_health()
    c_below = _by_key(r_below)["corpus"]
    assert c_below.status == lh.WEAK, (
        "a corpus of 199 prompts (one below the 200 weak floor) must read WEAK (thin), "
        f"not OK (got {c_below.status!r}) — the depth floor crept down a step"
    )
    assert r_below.trustworthy is True  # thin ≠ degenerate


# ── sweep partition routes findings to the owning dimension ───────────────────


def test_partition_routes_by_class():
    basin_f, lens_f, other_f = lh._partition_sweep([
        "C/basins: x", "F/lens: y", "A/eval: z", "E2/cortex: w", "B/vocab: v",
    ])
    assert basin_f == ["C/basins: x"]
    assert lens_f == ["F/lens: y"]
    assert set(other_f) == {"A/eval: z", "E2/cortex: w", "B/vocab: v"}


def test_real_sweep_detects_a_broken_lens(monkeypatch, tmp_path):
    """Integration: the REAL degeneracy sweep (not a stub) flags a 0-evidence lens,
    proving the wire from run_degeneracy_sweep → lens_health is live."""
    from trinity_local import degeneracy
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    _lens(tmp_path, body="paired tensions\n### 1. a ↔ b\nSupported by 0 decisions\n")
    assert any("F/lens" in f for f in degeneracy.run_degeneracy_sweep())


# ── command handler: exit-code contract + JSON shape ──────────────────────────


def test_handler_exit_code_and_json(monkeypatch, capsys):
    from trinity_local.commands import lens_health as cmd

    degraded = lh.LensHealthReport(
        checks=[lh.LensCheck("embeddings", "Semantic embeddings", lh.DEGRADED, "tf-idf")],
        trustworthy=False, verdict="nope")
    clean = lh.LensHealthReport(
        checks=[lh.LensCheck("embeddings", "Semantic embeddings", lh.OK, "live")],
        trustworthy=True, verdict="good")

    monkeypatch.setattr(cmd, "run_lens_health", lambda: degraded)
    assert cmd.handle_lens_health(SimpleNamespace(as_json=False)) == 1  # blocking → exit 1
    capsys.readouterr()  # drain the human-format output so only the JSON below is captured

    monkeypatch.setattr(cmd, "run_lens_health", lambda: clean)
    assert cmd.handle_lens_health(SimpleNamespace(as_json=True)) == 0   # clean → exit 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["trustworthy"] is True and payload["blocking"] == []
    assert payload["checks"][0]["key"] == "embeddings"


def test_handler_caution_exits_zero(monkeypatch):
    from trinity_local.commands import lens_health as cmd
    caution = lh.LensHealthReport(
        checks=[lh.LensCheck("basins", "Topic basins", lh.WEAK, "1 of 10")],
        trustworthy=True, verdict="caution")
    monkeypatch.setattr(cmd, "run_lens_health", lambda: caution)
    assert cmd.handle_lens_health(SimpleNamespace(as_json=False)) == 0  # caution doesn't block


# ── Lens freshness (2026-06-29): a structurally-sound lens can still be STALE ──
# (auto-refresh stopped landing — chairman timeout/quota). lens-health is the
# "can you trust the answer" verb; it greened an 18-day-frozen lens because it had
# no freshness dimension. Same gap class as the status surfacing, in the trust verb.

def test_freshness_stale_is_weak_caution(monkeypatch):
    monkeypatch.setattr("trinity_local.cold_start.lens_freshness_status",
                        lambda: ("stale", "677 new prompts, 425h since last build"))
    c = lh._freshness()
    assert c.key == "freshness"
    assert c.status == lh.WEAK, c.status
    assert "677 new prompts" in c.summary
    assert "lens --force" in c.fix  # the escape hatch must be offered


def test_freshness_current_is_ok(monkeypatch):
    monkeypatch.setattr("trinity_local.cold_start.lens_freshness_status",
                        lambda: ("current", "corpus unchanged since last build"))
    assert lh._freshness().status == lh.OK


def test_freshness_absent_abstains(monkeypatch):
    monkeypatch.setattr("trinity_local.cold_start.lens_freshness_status",
                        lambda: ("absent", "no lens built yet"))
    assert lh._freshness().status == lh.ABSTAIN


def test_stale_lens_is_a_caution_not_a_block(monkeypatch, tmp_path):
    """A sound-but-stale lens surfaces as a WEAK caution in the full report — it
    does NOT flip trust to False (the lens is valid, just behind). Mutation-proof
    of the wiring: drop _freshness() from the checks list → no 'freshness' key →
    this reds."""
    _topics(tmp_path, [100] * 10)
    _lens(tmp_path)
    _env(monkeypatch, tmp_path, coverage=_HEALTHY_COV, backend=True, noise=_CLEAN_NOISE, findings=[])
    monkeypatch.setattr("trinity_local.cold_start.lens_freshness_status",
                        lambda: ("stale", "677 new prompts, 425h since last build"))
    r = lh.run_lens_health()
    by = _by_key(r)
    assert "freshness" in by, list(by)
    assert by["freshness"].status == lh.WEAK
    assert r.trustworthy is True and r.weak  # trustworthy, but the staleness caution surfaced
    assert "with cautions" in lh.format_human(r)
