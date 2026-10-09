"""hq_138 stage 1: the trust-ledger test-retest gate refuses on degenerate input and cannot PASS
below its registered bars (internal/experiments/hq138_registration.md); and, after its FAIL, every
surface quoting the per-model rates states the measured reliability."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "internal" / "experiments"

pytestmark = pytest.mark.skipif(
    not EXP.is_dir(), reason="internal/ is not present in the public export"
)


@pytest.fixture
def g(monkeypatch):
    monkeypatch.syspath_prepend(str(EXP))
    spec = importlib.util.spec_from_file_location("hq138_ledger_reliability", EXP / "hq138_ledger_reliability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(n_claims: int, agree: bool, per_claim: int = 3, decided: bool = True) -> tuple[dict, list]:
    shipped, rows = {}, []
    for i in range(n_claims):
        cid = f"council_{i:04d}#0"
        s = "followed" if i % 2 else "contradicted"
        shipped[cid] = s
        for k in range(per_claim):
            if not decided:
                res = "unresolved"
            elif agree:
                res = s
            else:
                res = "followed" if (i + k) % 2 else "contradicted"
            rows.append({"arm": f"Vdec{k + 1}", "claim_id": cid, "seat": "claude", "effort": "low",
                         "status": "ok", "resolution": res, "prior": s})
    return shipped, rows


def test_perfect_test_retest_passes(g):
    shipped, rows = _rows(60, agree=True)
    out = g.measure(shipped, rows)
    assert out["verdict"] == "PASS" and out["kappa"] == 1.0


def test_coin_flip_rereads_fail(g):
    shipped, rows = _rows(60, agree=False)
    out = g.measure(shipped, rows)
    assert out["verdict"] == "FAIL" and out["kappa"] < 0.6


def test_too_few_claims_is_inconclusive_even_when_perfect(g):
    shipped, rows = _rows(20, agree=True)
    assert g.measure(shipped, rows)["verdict"] == "INCONCLUSIVE"


def test_rereads_that_never_decide_fail_on_coverage(g):
    """A re-read that abstains is absence, not agreement: perfect kappa on a sliver of
    decided pairs must not pass when most re-reads come back unresolved."""
    shipped, rows = _rows(60, agree=True)
    _, undecided = _rows(60, agree=True, per_claim=6, decided=False)
    out = g.measure(shipped, rows + undecided)
    assert out["coverage"] < 0.6 and out["verdict"] == "FAIL"


def test_non_production_arms_do_not_count(g):
    shipped, rows = _rows(60, agree=True)
    for r in rows:
        r["effort"] = "high"
    out = g.measure(shipped, rows)
    assert out["decided_pairs"] == 0 and out["verdict"] == "INCONCLUSIVE"


def test_prior_mismatch_voids(g, tmp_path, monkeypatch):
    import json
    shipped, rows = _rows(60, agree=True)
    rows[0]["prior"] = "unresolved"
    led = tmp_path / "disagreement_ledger"
    led.mkdir()
    (led / "resolutions.jsonl").write_text(
        "\n".join(json.dumps({"claim_id": c, "resolution": r}) for c, r in shipped.items()) + "\n")
    arms = tmp_path / "arms.jsonl"
    arms.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(g, "SHIPPED", led / "resolutions.jsonl")
    monkeypatch.setattr(g, "ARMS", arms)
    assert g.main()["verdict"] == "VOID"


# A quoted per-model rate or chairman figure: the canonical placeholders, or the literal numbers.
_QUOTE = re.compile(r"canonical:ledger_(opus48|gemini31|gpt55|chairman_agreement)|28W-13L|\b28-13\b|61\.7%|66\.1%")


def _quoting_surfaces() -> list[Path]:
    files = [ROOT / "AGENTS.md", ROOT / "README.md"]
    files += [p for p in (ROOT / "docs").rglob("*.md") if "historical" not in p.parts]
    files += sorted((ROOT / ".claude" / "skills").rglob("SKILL.md"))
    files += sorted((ROOT / "internal" / "launch-copy").rglob("*.md"))
    return [p for p in files if p.is_file() and _QUOTE.search(p.read_text(encoding="utf-8"))]


def test_quoted_ledger_rates_carry_the_measured_reliability():
    """hq_138 FAIL (registered): every surface quoting the per-model rates or the chairman figure
    states the label's measured reliability in the same paragraph (or table row), and none calls
    them PROVEN. MUTATION: drop the sentence from any quoting surface, or the figure from the
    shipped caveat, and this reds."""
    from trinity_local.disagreement_ledger import BEHAVIOURAL_TIER_CAVEAT

    assert "kappa 0.47" in BEHAVIOURAL_TIER_CAVEAT and "not proven" in BEHAVIOURAL_TIER_CAVEAT
    surfaces = _quoting_surfaces()
    assert ROOT / "AGENTS.md" in surfaces and ROOT / ".claude/skills/trinity-discipline/SKILL.md" in surfaces
    for p in surfaces:
        for para in re.split(r"\n\s*>?\s*\n", p.read_text(encoding="utf-8")):
            units = para.splitlines() if para.lstrip().startswith("|") else [para]
            for unit in units:
                if not _QUOTE.search(unit):
                    continue
                flat = " ".join(unit.replace(">", " ").split())
                assert "kappa 0.47" in flat, f"{p.relative_to(ROOT)}: a ledger rate is quoted without its reliability"
                assert not re.search(r"(?<!NOT )\bPROVEN\b(?!-BUT)", flat.split("Previously")[0].split("formerly")[0]), \
                    f"{p.relative_to(ROOT)}: a ledger rate is still called PROVEN"
