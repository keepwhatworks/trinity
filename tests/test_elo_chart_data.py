"""_elo_chart_data — the "which model wins" launchpad chart.

Live 2026-05-31: the founder's chart carried three local-model experiments
(gemma4local / qwen27 / qwen35) at the 1500 Elo default with 1 game + 0 wins
each — pure clutter next to providers with 50-250 games. A single-game
entity's Elo hasn't moved off the seed, so it's noise on a win-rate chart.
These pin the min-games filter without hurting cold-start.

The deeper slug-fragmentation — chatgpt vs codex, gemini vs antigravity,
claude_ai vs claude are the same labs under legacy (web-capture) vs current
(CLI) slugs — was a founder decision, resolved 2026-06-01 (#275). First framed
as a merge ("this still shows dups", v1.7.157), then the founder narrowed it:
"no one is interested in old model scores — which LATEST models should I use,
for what." So build_elo_snapshot now EXCLUDES the web-capture era entirely
(legacy slugs + unrecorded "?" models = the GPT-4/Claude-2/Gemini-1 generation),
counting only current-era CLI councils; _elo_chart_data labels the survivors with
the recognizable brand (claude→Claude, codex→GPT, antigravity→Gemini).
"""
from __future__ import annotations

from trinity_local.launchpad_data import _elo_chart_data


def test_drops_single_game_noise():
    snapshot = {"providers": {
        "claude": {"elo": 1661, "total_games": 62, "wins": 46},
        "codex": {"elo": 1506, "total_games": 57, "wins": 14},
        "gemma4local": {"elo": 1495, "total_games": 1, "wins": 0},
        "qwen27": {"elo": 1495, "total_games": 1, "wins": 0},
        "qwen35": {"elo": 1495, "total_games": 1, "wins": 0},
    }}
    chart = _elo_chart_data(snapshot)
    assert "Claude" in chart["labels"]
    assert "GPT" in chart["labels"]  # codex → brand "GPT"
    for noise in ("Gemma4Local", "Qwen27", "Qwen35"):
        assert noise not in chart["labels"], f"{noise} (1 game) should be filtered"
    # labels and values stay aligned after filtering
    assert len(chart["labels"]) == len(chart["datasets"][0]["data"]) == 2


def test_keeps_two_game_entities_for_cold_start():
    """A new user's providers reach 2 games quickly — the threshold must not
    hide them, or the chart looks empty on a fresh install."""
    snapshot = {"providers": {
        "claude": {"elo": 1512, "total_games": 2, "wins": 1},
        "codex": {"elo": 1488, "total_games": 3, "wins": 1},
    }}
    chart = _elo_chart_data(snapshot)
    assert sorted(chart["labels"]) == ["Claude", "GPT"]


def test_labels_use_brand_not_raw_slug():
    """#275 (founder report 2026-06-01): the chart showed fragmented raw slugs
    — Antigravity, Chatgpt, Claude, Claude_Ai, Codex, Gemini — as 6 separate
    bars for 3 labs. After build_elo_snapshot canonicalizes, the chart must label
    the merged providers with the recognizable brand, never the bare harness
    slug ('Antigravity' for Gemini, 'Codex' for GPT)."""
    snapshot = {"providers": {
        "claude": {"elo": 1592, "total_games": 383, "wins": 254},
        "codex": {"elo": 1672, "total_games": 384, "wins": 267},
        "antigravity": {"elo": 1259, "total_games": 467, "wins": 38},
    }}
    chart = _elo_chart_data(snapshot)
    assert sorted(chart["labels"]) == sorted(["Claude", "GPT", "Gemini"])
    # The confusing harness names must NOT appear as labels.
    assert "Antigravity" not in chart["labels"]
    assert "Codex" not in chart["labels"]


def test_build_elo_snapshot_excludes_old_web_era_councils(monkeypatch):
    """Founder decision 2026-06-01: "no one is interested in old model scores —
    which LATEST models should I use, for what." build_elo_snapshot must EXCLUDE
    the web-capture era (legacy slugs chatgpt/claude_ai/gemini + unrecorded "?"
    models = the GPT-4/Claude-2/Gemini-1 generation), counting ONLY current-era
    CLI councils where every member ran a known model. A strong 2023 ChatGPT must
    NOT inflate today's GPT rating."""
    import trinity_local.telemetry as tel

    councils = [
        # OLD web-era: legacy slugs, no model version → must be EXCLUDED.
        {"council_run_id": "old1", "winner_provider": "chatgpt", "member_results": [
            {"provider": "chatgpt"}, {"provider": "gemini"}, {"provider": "claude_ai"}]},
        # OLD CLI-era but model unrecorded ("?") → also EXCLUDED (web import).
        {"council_run_id": "old2", "winner_provider": "codex", "member_results": [
            {"provider": "codex", "model": "?"}, {"provider": "claude", "model": "?"}]},
        # CURRENT-era: every member ran a known model → counted.
        {"council_run_id": "cur1", "winner_provider": "claude", "member_results": [
            {"provider": "claude", "model": "claude-opus-4-8"},
            {"provider": "codex", "model": "gpt-5.5"},
            {"provider": "antigravity", "model": "gemini-3.1-pro-preview"}]},
    ]
    monkeypatch.setattr(tel, "_iter_council_payloads", lambda: iter(councils))

    snap = tel.build_elo_snapshot()
    # Only the one current-era council counts; the two old ones are dropped.
    assert snap["council_count"] == 1
    provs = snap["providers"]
    assert set(provs) == {"claude", "codex", "antigravity"}
    # claude won the single current council; the excluded chatgpt/codex wins must
    # NOT inflate anyone — each lab has exactly 1 game (cur1), not 2-3.
    assert provs["claude"]["wins"] == 1
    assert provs["codex"]["total_games"] == 1 and provs["codex"]["wins"] == 0
    assert provs["antigravity"]["total_games"] == 1


def test_empty_snapshot_renders_empty_chart_not_crash():
    chart = _elo_chart_data({"providers": {}})
    assert chart["labels"] == []
    assert chart["datasets"][0]["data"] == []


def test_missing_total_games_treated_as_zero():
    """An older snapshot lacking total_games shouldn't crash or sneak a
    zero-game entity onto the chart."""
    snapshot = {"providers": {
        "claude": {"elo": 1600, "total_games": 5, "wins": 3},
        "legacy": {"elo": 1500},  # no total_games key
    }}
    chart = _elo_chart_data(snapshot)
    assert chart["labels"] == ["Claude"]
