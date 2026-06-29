"""The staged-tip ladder — dependency-ordered onboarding after the first win.

Load-bearing: a rung never surfaces before its prereq, exactly one shows per
interaction, and a shown rung never nags again. The ladder ORDER is the
dependency graph, so the tests walk the funnel state-by-state.
"""
from __future__ import annotations

import trinity_local.tips as tips
from trinity_local.tips import TipState, next_tip


def _state(monkeypatch, **kw):
    st = TipState(**kw)
    monkeypatch.setattr(tips, "gather_state", lambda: st)
    monkeypatch.setattr(tips, "_seen", lambda: set())   # nothing dismissed unless a test says so
    # once-tips self-mark on return; no-op the writer so these pure-ladder tests
    # never touch the filesystem (the real ~/.trinity). Real-home tests set TRINITY_HOME.
    monkeypatch.setattr(tips, "mark_tip_seen", lambda k: None)


# ── prereq gating: the right rung for each funnel state ───────────────────────


def test_no_tip_before_first_win(monkeypatch):
    _state(monkeypatch, councils_run=0)
    assert next_tip() is None


def test_first_win_offers_create_lens(monkeypatch):
    _state(monkeypatch, councils_run=1, lens_enabled=False, lens_built=False)
    t = next_tip()
    assert t["key"] == "create-lens" and t["kind"] == "elicit"


def test_after_enabling_offers_web_history(monkeypatch):
    # lens enabled (they clicked create), building; web surfaces not captured yet.
    _state(monkeypatch, councils_run=1, lens_enabled=True, lens_built=False, web_surfaces_present=False)
    assert next_tip()["key"] == "add-web-history"


def test_web_history_skipped_when_already_captured(monkeypatch):
    # enabled + web already present + not built yet → no eligible rung.
    _state(monkeypatch, councils_run=1, lens_enabled=True, web_surfaces_present=True, lens_built=False)
    assert next_tip() is None


def test_built_lens_offers_view_then_health(monkeypatch):
    _state(monkeypatch, councils_run=3, lens_enabled=True, lens_built=True, web_surfaces_present=True)
    # view-lens comes first…
    assert next_tip()["key"] == "view-lens"
    # …and once it's been shown, health is next.
    monkeypatch.setattr(tips, "_seen", lambda: {"view-lens"})
    assert next_tip()["key"] == "lens-health"


def test_one_per_interaction(monkeypatch):
    """next_tip returns a single rung even when several prereqs are satisfied."""
    _state(monkeypatch, councils_run=3, lens_enabled=True, lens_built=True, web_surfaces_present=False)
    t = next_tip()
    assert isinstance(t, dict) and t["key"]  # exactly one, not a list


# ── dismissal: a shown rung never nags again ─────────────────────────────────


def test_once_tips_advance_but_create_lens_persists(monkeypatch, tmp_path):
    """Regression (persona sweep 2026-06-16): the production injection uses
    next_tip() with mark=False. create-lens (the conversion gate, once=False) must
    PERSIST until the user opts in; the once-tips must each show ONCE and ADVANCE —
    else a built-lens / no-web user is stuck on add-web-history forever and never
    sees view-lens / lens-health (the starvation bug the sweep caught)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))   # real persistence, isolated home
    (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "council_outcomes" / "council_x.json").write_text("{}", encoding="utf-8")

    # Fresh user (no lens): create-lens persists across calls.
    monkeypatch.delenv("TRINITY_LENS_ENABLED", raising=False)
    assert [(next_tip() or {}).get("key") for _ in range(3)] == ["create-lens"] * 3

    # Built lens, web history not imported: the once-tips advance, then stop.
    monkeypatch.setenv("TRINITY_LENS_ENABLED", "1")
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "lens.md").write_text("## tensions\n" + "x" * 80, encoding="utf-8")
    seq = [(next_tip() or {}).get("key") for _ in range(5)]
    assert seq == ["add-web-history", "view-lens", "lens-health", None, None], seq


def test_seen_rung_is_not_reshown(monkeypatch):
    st = TipState(councils_run=1, lens_enabled=False, lens_built=False)
    monkeypatch.setattr(tips, "gather_state", lambda: st)
    monkeypatch.setattr(tips, "_seen", lambda: {"create-lens"})
    assert next_tip() is None  # the only eligible rung is dismissed → nothing


# ── live state + persistence (real tmp home) ─────────────────────────────────


def test_gather_state_and_seen_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_LENS_ENABLED", raising=False)

    # cold home: nothing built, no councils.
    st = tips.gather_state()
    assert st.councils_run == 0 and st.lens_built is False and st.lens_enabled is False

    # a council outcome + a built lens flip the live signals.
    (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "council_outcomes" / "council_abc.json").write_text("{}", encoding="utf-8")
    (tmp_path / "memories").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memories" / "lens.md").write_text("x" * 80, encoding="utf-8")
    st2 = tips.gather_state()
    assert st2.councils_run == 1 and st2.lens_built is True

    # mark_tip_seen persists and dedups.
    tips.mark_tip_seen("view-lens")
    tips.mark_tip_seen("view-lens")
    assert tips._seen() == {"view-lens"}


def test_next_tip_mark_records_seen(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_LENS_ENABLED", raising=False)
    (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "council_outcomes" / "council_x.json").write_text("{}", encoding="utf-8")
    # first surface returns create-lens AND records it…
    assert next_tip(mark=True)["key"] == "create-lens"
    # …so the next call no longer offers it.
    assert next_tip() is None


# ── _text() rides the tip into tool results ──────────────────────────────────


def test_text_injects_the_tip(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_LENS_ENABLED", raising=False)
    (tmp_path / "council_outcomes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "council_outcomes" / "council_a.json").write_text("{}", encoding="utf-8")
    from trinity_local.mcp_server import _text
    import json as _json
    payload = _json.loads(_text({"hello": "world"})["text"])
    assert payload["tip"]["key"] == "create-lens"


# ── CTA honesty: every rung points at a REAL CLI subcommand ──────────────────


def test_every_tip_cta_is_a_real_cli_subcommand():
    """Founder symptom (Iter onboarding-ladder audit): the `view-lens` rung told
    the user to run `trinity-local portal` — a command that DOES NOT EXIST (the
    module is `portal`, the verbs are `portal-html`/`open-review`/`review-link`/
    `serve`), so the one-click "view your lens" CTA dead-ended on
    `error: argument {...}: invalid choice: 'portal'`. A tip is the onboarding
    funnel's nudge; a tip that names a non-existent command is a DEAD-END.

    Resolve each ladder CTA's leading `trinity-local <sub>` token against the REAL
    parser's actual subcommand choices — render-independent, behavioral. RED on
    the un-fixed `cta="trinity-local portal"`.
    """
    import argparse

    from trinity_local.main import build_parser
    from trinity_local.tips import LADDER

    parser = build_parser()
    valid_subcommands: set[str] = set()
    for action in parser._subparsers._group_actions:  # type: ignore[union-attr]
        if isinstance(action, argparse._SubParsersAction):
            valid_subcommands.update(action.choices.keys())
    # (A) the parser actually exposes subcommands — guard isn't vacuous.
    assert "lens-show" in valid_subcommands and "portal" not in valid_subcommands

    assert LADDER, "the ladder must have rungs (else nothing to gate)"
    for tip in LADDER:
        parts = tip.cta.split()
        assert parts and parts[0] == "trinity-local", (
            f"tip {tip.key!r} CTA must lead with `trinity-local`: {tip.cta!r}"
        )
        sub = parts[1]
        # (B) the discriminating assertion: the subcommand the user is told to run
        # must be a choice the parser accepts — else the CTA dead-ends.
        assert sub in valid_subcommands, (
            f"tip {tip.key!r} points at `trinity-local {sub}`, which is NOT a real "
            f"subcommand — running it errors `invalid choice: {sub!r}` (the "
            f"`view-lens`→`portal` dead-end)."
        )


# ── E1: "open the council page?" elicit on completion ────────────────────────


def test_open_council_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.setenv("TRINITY_OPEN_COUNCIL_PROMPT", "0")
    from trinity_local.mcp_server import _maybe_offer_open_council
    assert _maybe_offer_open_council("council_x", "/tmp/p.html") is None


def test_open_council_accept_opens_once(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_OPEN_COUNCIL_PROMPT", raising=False)
    opened: list[str] = []
    monkeypatch.setattr("trinity_local.mcp_features.elicit", lambda m, s: {"open": True})
    monkeypatch.setattr("trinity_local.notifications.open_path", lambda p: opened.append(p) or True)
    from trinity_local.mcp_server import _maybe_offer_open_council
    rec = _maybe_offer_open_council("council_y", "/tmp/page.html")
    assert rec == {"opened": True, "review_path": "/tmp/page.html"} and opened == ["/tmp/page.html"]
    # asked at most once per council — the marker blocks the re-poll.
    assert _maybe_offer_open_council("council_y", "/tmp/page.html") is None


def test_open_council_degrades_to_text_when_unsupported(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_OPEN_COUNCIL_PROMPT", raising=False)
    monkeypatch.setattr("trinity_local.mcp_features.elicit", lambda m, s: None)  # no client support
    from trinity_local.mcp_server import _maybe_offer_open_council
    rec = _maybe_offer_open_council("council_z", "/tmp/p.html")
    assert rec["kind"] == "text" and rec["cta"] == "/tmp/p.html"


def test_open_council_decline_does_not_open(monkeypatch, tmp_path):
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_OPEN_COUNCIL_PROMPT", raising=False)
    monkeypatch.setattr("trinity_local.mcp_features.elicit", lambda m, s: {"open": False})
    monkeypatch.setattr("trinity_local.notifications.open_path",
                        lambda p: (_ for _ in ()).throw(AssertionError("must not open on decline")))
    from trinity_local.mcp_server import _maybe_offer_open_council
    assert _maybe_offer_open_council("council_w", "/tmp/p.html") == {"opened": False}


def test_open_council_rejects_path_traversal_id(monkeypatch, tmp_path):
    """The council_run_id is joined into a filesystem path — a crafted id must be
    rejected before any path build / open (security review, 2026-06-16)."""
    monkeypatch.setenv("TRINITY_HOME", str(tmp_path))
    monkeypatch.delenv("TRINITY_OPEN_COUNCIL_PROMPT", raising=False)
    monkeypatch.setattr("trinity_local.notifications.open_path",
                        lambda p: (_ for _ in ()).throw(AssertionError("opened a traversal path")))
    monkeypatch.setattr("trinity_local.mcp_features.elicit",
                        lambda m, s: (_ for _ in ()).throw(AssertionError("elicited on a crafted id")))
    from trinity_local.mcp_server import _maybe_offer_open_council
    # Rejected even when a review_path is supplied (the id guard is first).
    for bad in ("../../etc/passwd", "council/../x", "a/b", "..", "x.y", "a b", ""):
        assert _maybe_offer_open_council(bad, "/tmp/legit.html") is None
