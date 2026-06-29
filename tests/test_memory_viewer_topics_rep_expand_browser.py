"""Regression: the topics.json Reader's representative-thread INTERACTIONS work.

Found 2026-06-17 driving the memory-viewer topics Reader detail panel (open it by
clicking a basin node in the topology graph). Two thread-level affordances are the
"see the conversation behind a basin" payoff of the topics Reader:

  1. EXPAND/COLLAPSE — a multi-turn representative is an `.expandable` thread card;
     clicking it toggles `.open`, reveals the per-turn list (CSS hides
     `.topics-rep-turns` until `.open`), and flips the chevron ▸→▾.
  2. Per-rep REPLAY chip — copies `trinity-local council --task "<headline>"`; its
     click handler calls `event.stopPropagation()` so it copies WITHOUT also
     toggling the surrounding li's expand state.

Both were ONLY guarded at the STRING level before (TestRepReplayChip asserts
`.topics-rep-replay` / `"event.stopPropagation()"` appear in the rendered HTML, and
NOTHING ever drove the expand toggle). A string-presence check stays GREEN if the
wiring breaks — the listener gets detached, the toggle handler is removed, or
stopPropagation fires on the wrong event — while the real affordance silently dies
("green while the value is gone"). This drives the REAL panel and pins the BEHAVIOUR.

Slow-marked (spawns portal-html + chromium); runs in the slow shard, skips when
Playwright/chromium are absent.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# portal-html subprocess + chromium → real-browser/subprocess test. Marked slow so
# the default `pytest -q` stays fast (runs via TRINITY_SLOW=1 / `pytest -m slow`).
pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]


def _topics_thread_shape() -> dict:
    """Two basins, both thread-shaped; basin b00's first rep is a 3-turn thread
    (the `.expandable` card we drive) and its second is single-turn (no expand)."""
    return {
        "basins": [
            {
                "id": "b00",
                "label": "floor-plan engine",
                "size": 120,
                "thread_count": 8,
                "top_terms": ["floorplan", "prefab", "layout"],
                "centroid": [1.0, 0.0, 0.0],
                "representatives": [
                    {
                        "transcript_id": "tx-aaa",
                        "headline": "Generate a floor plan for a 3-bed prefab",
                        "turn_count": 3,
                        "turns": [
                            {"turn_index": 0, "snippet": "Generate a floor plan for a 3-bed prefab"},
                            {"turn_index": 1, "snippet": "make the kitchen open-plan"},
                            {"turn_index": 2, "snippet": "now export to DXF"},
                        ],
                    },
                    {
                        "transcript_id": "tx-bbb",
                        "headline": "Optimize the garage placement",
                        "turn_count": 1,
                        "turns": [{"turn_index": 0, "snippet": "Optimize the garage placement"}],
                    },
                ],
            },
            {
                "id": "b01",
                "label": "embeddings",
                "size": 80,
                "thread_count": 5,
                "top_terms": ["embedding", "vector", "cosine"],
                "centroid": [0.0, 1.0, 0.0],
                "representatives": [
                    {
                        "transcript_id": "tx-ccc",
                        "headline": "Compare MLX vs torch embedding speed",
                        "turn_count": 2,
                        "turns": [
                            {"turn_index": 0, "snippet": "Compare MLX vs torch embedding speed"},
                            {"turn_index": 1, "snippet": "and on a tight budget"},
                        ],
                    },
                ],
            },
        ]
    }


def _render_portal(home: Path) -> Path:
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(_topics_thread_shape()), encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    assert (pages / "memory.html").exists(), "portal-html didn't write memory.html"
    return pages


# Open the topics Reader detail panel by clicking the first basin node. The reps
# render inside showDetail(); a thread-shape rep is an `.expandable` li.
_OPEN_DETAIL = """() => {
    const c = document.querySelector('circle');
    if (c) c.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
}"""


def test_topics_rep_thread_expand_and_replay_chip_dont_toggle_parent():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on(
            "console",
            lambda m: errs.append(m.text)
            if m.type == "error" and "woff2" not in m.text and "404" not in m.text
            else None,
        )
        page.goto(target, wait_until="load")
        page.wait_for_timeout(1000)
        page.evaluate(_OPEN_DETAIL)
        page.wait_for_timeout(400)

        # Precondition: the thread-shape detail rendered with an expandable rep.
        setup = page.evaluate(
            """() => {
                const reps = [...document.querySelectorAll('.topics-rep-thread')];
                return {
                  repCount: reps.length,
                  expandable: reps.filter(li => li.classList.contains('expandable')).length,
                  replayChips: document.querySelectorAll('.topics-rep-replay').length,
                };
            }"""
        )
        assert setup["repCount"] >= 2, (
            f"the topics Reader detail panel didn't render the seeded representative "
            f"threads (click a basin node → reps): {setup}"
        )
        assert setup["expandable"] >= 1, (
            f"no `.expandable` multi-turn rep rendered — the expand affordance can't "
            f"be driven: {setup}"
        )
        assert setup["replayChips"] >= 1, f"no per-rep Replay chip rendered: {setup}"

        # --- (1) EXPAND/COLLAPSE toggle ---
        expand = page.evaluate(
            """() => {
                const li = [...document.querySelectorAll('.topics-rep-thread')]
                    .find(l => l.classList.contains('expandable'));
                const turns = li.querySelector('.topics-rep-turns');
                const chev = li.querySelector('.topics-rep-chev');
                const snap = () => ({
                  open: li.classList.contains('open'),
                  chev: chev ? chev.textContent : null,
                  turnsVisible: turns ? getComputedStyle(turns).display !== 'none' : null,
                });
                const before = {...snap(),
                  turnNodes: turns ? turns.querySelectorAll('.topics-rep-turn').length : 0};
                li.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
                const afterOpen = snap();
                li.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
                const afterCollapse = snap();
                return {before, afterOpen, afterCollapse};
            }"""
        )
        # Collapsed by default — the turns are present in the DOM but hidden.
        assert expand["before"]["turnsVisible"] is False, (
            f"a multi-turn rep's per-turn list must be HIDDEN until the user expands "
            f"it: {expand['before']}"
        )
        assert expand["before"]["turnNodes"] >= 3, (
            f"the seeded 3 turns aren't in the collapsed thread's DOM: {expand['before']}"
        )
        # Click → expands: turns become visible, chevron flips ▸→▾.
        assert expand["afterOpen"]["open"] is True and expand["afterOpen"]["turnsVisible"] is True, (
            f"clicking a `.expandable` representative thread did NOT expand it — the "
            f"per-turn reveal (the 'see the conversation behind this basin' payoff) is "
            f"DEAD: {expand['afterOpen']}"
        )
        assert expand["afterOpen"]["chev"] == "▾", (
            f"the chevron didn't flip to ▾ on expand: {expand['afterOpen']}"
        )
        # Second click → collapses back.
        assert (
            expand["afterCollapse"]["open"] is False
            and expand["afterCollapse"]["turnsVisible"] is False
            and expand["afterCollapse"]["chev"] == "▸"
        ), f"a second click did NOT collapse the thread back: {expand['afterCollapse']}"

        # --- (2) Replay chip copies WITHOUT toggling the parent (stopPropagation) ---
        replay = page.evaluate(
            """() => {
                const li = [...document.querySelectorAll('.topics-rep-thread')]
                    .find(l => l.classList.contains('expandable'));
                // ensure collapsed first so a toggle would be observable
                if (li.classList.contains('open'))
                    li.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
                const openBefore = li.classList.contains('open');
                const chip = li.querySelector('.topics-rep-replay');
                const labelBefore = chip.textContent;
                chip.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
                return {
                  openBefore,
                  openAfter: li.classList.contains('open'),
                  labelBefore,
                  labelAfter: chip.textContent,
                };
            }"""
        )
        assert replay["labelBefore"] == "Replay" and "Copied" in replay["labelAfter"], (
            f"the Replay chip didn't flash its copy confirmation on click: {replay}"
        )
        assert replay["openBefore"] is False and replay["openAfter"] is False, (
            f"clicking the per-rep Replay chip ALSO toggled the surrounding thread's "
            f"expand state — the chip's click handler is missing stopPropagation, so a "
            f"user who only meant to copy the replay command accidentally expands/"
            f"collapses the thread: {replay}"
        )

        assert not errs, f"the topics-rep expand/replay interaction threw: {errs}"
        browser.close()


def _topics_long_unbreakable_turn_shape() -> dict:
    """One basin whose first rep is a multi-turn thread carrying a long
    SEPARATOR-FREE token in BOTH its headline and its turn snippets — the
    reachable corpus shape (the founder pastes URLs / ~/.cache/... paths / long
    identifiers into prompts, which become topics.json rep headlines + turns).
    A 160-char run of a single char defeats the browser's default URL-only
    break points entirely."""
    hard = "x" * 160
    url = (
        "https://collector.example.invalidxyz/g/collect?measurement_id="
        "G-ABCDEF1234567890&api_secret=" + ("z" * 90)
    )
    path = (
        "/Users/founder/.cache/huggingface/hub/models--nomic-ai--modernbert-"
        "embed-base/snapshots/0123456789abcdef0123456789abcdef01234567/"
        "model.safetensors.index.json.backup.tmp"
    )
    return {
        "basins": [
            {
                "id": "b00",
                "label": "long token basin",
                "size": 42,
                "thread_count": 3,
                "top_terms": ["design", "arch"],
                "centroid": [1.0, 0.0, 0.0],
                "representatives": [
                    {
                        "transcript_id": "tx-long",
                        "headline": hard,
                        "turn_count": 3,
                        "turns": [
                            {"turn_index": 0, "snippet": url},
                            {"turn_index": 1, "snippet": path},
                            {"turn_index": 2, "snippet": hard},
                        ],
                    },
                ],
            },
        ]
    }


@pytest.mark.parametrize("viewport_width", [320, 768, 1024])
def test_expanded_turn_with_long_unbreakable_token_does_not_overflow_viewport(
    viewport_width: int,
):
    """Class guard (Iter 360 flex/grid min-content blow-out, the topics-Reader
    sibling): an EXPANDED representative turn whose snippet is a long
    separator-free token must NOT push memory.html past the viewport.

    Founder symptom this pins: open a basin in the topics Reader, click a
    representative thread to expand its turns, and a turn that quotes a long URL
    / ~/.cache/... path / identifier (verbatim user-prompt text) streams off the
    right edge and the whole basin-detail document horizontal-scrolls. The turn
    body is a `.topics-rep-turn-text` cell in a `.topics-rep-turn` grid whose
    track WAS a bare `1fr` (implicit min-content min) with NO break rule on the
    text — so the longest unbreakable run pinned the track wide and blew
    memory.html to scrollWidth ~1053 across the entire 561–1024 single-column
    band. (Masked ≤560px only by the incidental overflow-x:auto coupled on by
    the detail's overflow-y:auto, and absorbed ≥1080px by the wide content — so
    the global narrow-overflow sweep at 320/375 never saw it.) Fixed at SOURCE
    with `grid-template-columns: 32px minmax(0,1fr)` + overflow-wrap:anywhere on
    `.topics-rep-turn-text` (and the same on `.topics-rep-headline`).

    Mutation-proof: revert either the minmax(0,1fr) or the overflow-wrap →
    RED at viewport_width=768 (the overflow re-appears, right edge ~1025 ≫ 768).
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "topics.json").write_text(
        json.dumps(_topics_long_unbreakable_turn_shape()), encoding="utf-8"
    )
    env = dict(os.environ)
    env["TRINITY_HOME"] = str(home)
    env["TRINITY_AUTOSCAN_DISABLED"] = "1"
    env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "trinity_local.main", "portal-html"],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, f"portal-html failed: {result.stderr[-400:]}"
    pages = home / "portal_pages"
    target = f"file://{pages / 'memory.html'}?file=topics.json&basin=b00"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        page = browser.new_page(viewport={"width": viewport_width, "height": 900})
        page.goto(target, wait_until="load")
        page.wait_for_timeout(1000)
        # Deep-link auto-opens basin b00's detail; expand every thread to reveal turns.
        page.eval_on_selector_all(
            ".topics-rep-thread",
            "els => els.forEach(e => e.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window})))",
        )
        page.wait_for_timeout(400)

        probe = page.evaluate(
            """(vw) => {
                const turns = [...document.querySelectorAll('.topics-rep-turn-text')];
                let worstRight = 0, worstSel = null, worstLeft = 0;
                // The document-level scroll is the headline blow-out signal; the
                // per-element right edge is the position:visible paint-past signal
                // the doc-scroll measure misses when an ancestor clips.
                ['.topics-rep-turn-text', '.topics-rep-headline', '.topics-rep-turn',
                 '.topics-reps-list', '.topics-graph-detail', '.content'].forEach(s => {
                    document.querySelectorAll(s).forEach(el => {
                        const r = el.getBoundingClientRect();
                        if (r.right > worstRight) { worstRight = r.right; worstSel = s; worstLeft = r.left; }
                    });
                });
                return {
                    expandedTurns: turns.length,
                    docScrollWidth: document.documentElement.scrollWidth,
                    docClientWidth: document.documentElement.clientWidth,
                    worstRight: Math.round(worstRight),
                    worstLeft: Math.round(worstLeft),
                    worstSel,
                    vw,
                };
            }""",
            viewport_width,
        )
        # Precondition: the long-token turns actually rendered + expanded (else the
        # overflow assertions pass vacuously on an empty/unopened detail panel).
        assert probe["expandedTurns"] >= 3, (
            f"the seeded long-token turns didn't render/expand in the topics Reader "
            f"detail panel (deep-link ?basin=b00 → expand): {probe}"
        )
        # Document must not horizontal-scroll (headline / flex blow-out).
        assert probe["docScrollWidth"] <= probe["docClientWidth"] + 1, (
            f"a long unbreakable token in an expanded representative turn blew "
            f"memory.html past the viewport at {viewport_width}px: scrollWidth "
            f"{probe['docScrollWidth']} > clientWidth {probe['docClientWidth']} "
            f"(worst element {probe['worstSel']} right={probe['worstRight']}). The "
            f"`.topics-rep-turn` grid track must be minmax(0,1fr) AND "
            f"`.topics-rep-turn-text` must break long tokens (overflow-wrap:anywhere)."
        )
        # No element may paint past the viewport's right edge or before its left
        # (the per-element check the doc-scroll measure misses when overflow:auto
        # on an ancestor turns the blow-out into an internal scroll).
        assert probe["worstRight"] <= viewport_width + 2 and probe["worstLeft"] >= -2, (
            f"an element paints past the {viewport_width}px viewport with a long "
            f"unbreakable turn token: {probe['worstSel']} right={probe['worstRight']} "
            f"left={probe['worstLeft']} (founder symptom: the topics basin-detail "
            f"turn text streams off the right edge). Break `.topics-rep-turn-text`."
        )
        browser.close()


def test_single_turn_rep_thread_advertises_no_interactivity():
    """INVERSE-affordance guard (Iter 147 lineage, on the memory-viewer surface):
    a SINGLE-turn representative thread must NOT advertise interactivity it lacks.

    Founder symptom this pins: "a single-turn rep row in the topics Reader shows a
    pointer cursor (or sprouts a chevron / role=button / tabindex) — it LOOKS like an
    expandable thread, but clicking it does nothing (there is no per-turn list to
    reveal)." `.topics-rep-thread.expandable` carries `cursor:pointer` + a hover-tint +
    a :focus-visible ring + role=button/tabindex/aria-expanded, and source ADDS the
    `expandable` class ONLY when `turnCount > 1`. If that gate slips — the `cursor`/
    `:hover`/`:focus-visible`/`role` is moved onto the bare `.topics-rep-thread` (or
    `expandable` is added unconditionally) — a single-turn rep becomes a NO-OP
    affordance-lie, EXACTLY the dead retired-control-CSS shape Iter 147 fixed on the
    static review page. The positive expand test only ever drives the multi-turn card;
    NOTHING pinned the single-turn row stays inert, so a CSS-scope slip ships green.

    Drives the REAL viewer, opens the detail panel, and asserts the seeded single-turn
    rep ('Optimize the garage placement', turn_count=1): NOT `.expandable`, computed
    `cursor` != 'pointer', no chevron, no role/tabindex/aria-expanded, NOT focusable,
    and clicking it neither adds `.open` nor reveals any turns list. A
    rendered-DOM/computed-style/interaction assertion, not a source-string check.
    """
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    home = Path(tempfile.mkdtemp()) / "trinity"
    home.mkdir(parents=True)
    pages = _render_portal(home)
    target = f"file://{pages / 'memory.html'}?file=topics.json"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - env-dependent
            pytest.skip(f"no launchable chromium: {exc}")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.goto(target, wait_until="load")
        page.wait_for_timeout(1000)
        page.evaluate(_OPEN_DETAIL)
        page.wait_for_timeout(400)

        probe = page.evaluate(
            """() => {
                const reps = [...document.querySelectorAll('.topics-rep-thread')];
                // The single-turn rep is the one WITHOUT a chevron / expandable class.
                // (The seed's expandable multi-turn rep carries .topics-rep-chev.)
                const single = reps.find(li => !li.classList.contains('expandable'));
                const multi = reps.find(li => li.classList.contains('expandable'));
                if (!single) return {found: false, repCount: reps.length};
                const cs = getComputedStyle(single);
                single.focus();
                const focusedAfter = (document.activeElement === single);
                // Click it: a single-turn row must NOT toggle .open or reveal turns.
                single.dispatchEvent(new MouseEvent('click', {bubbles: true, view: window}));
                const turnsEl = single.querySelector('.topics-rep-turns');
                return {
                    found: true,
                    repCount: reps.length,
                    haveMulti: !!multi,
                    expandable: single.classList.contains('expandable'),
                    cursor: cs.cursor,
                    hasChev: !!single.querySelector('.topics-rep-chev'),
                    role: single.getAttribute('role'),
                    tabindex: single.getAttribute('tabindex'),
                    ariaExpanded: single.getAttribute('aria-expanded'),
                    focusable: focusedAfter,
                    openAfterClick: single.classList.contains('open'),
                    turnsVisibleAfterClick: turnsEl
                        ? getComputedStyle(turnsEl).display !== 'none'
                        : false,
                };
            }"""
        )
        assert probe["found"], (
            "the topics Reader detail panel didn't render a single-turn representative "
            f"row to test inertness on (reps seen: {probe.get('repCount')}). The seed "
            "ships a multi-turn AND a single-turn rep; the single-turn one is missing."
        )
        # Precondition: the multi-turn expandable card IS present, so we know the
        # detail rendered the full rep set (not a degenerate render that would pass
        # the inertness checks vacuously).
        assert probe["haveMulti"] and probe["repCount"] >= 2, (
            f"the detail panel didn't render both rep shapes (probe={probe}) — the "
            "inverse-affordance check needs the single-turn row alongside the multi-turn one."
        )
        # The defect shape: a single-turn row advertising interactivity it lacks.
        assert not probe["expandable"], (
            "a SINGLE-turn rep row carries the `.expandable` class — it will show the "
            "expand cursor/role/chevron but has no per-turn list to reveal, so clicking "
            "it does nothing (the Iter 147 NO-OP affordance-lie, here on the topics Reader)."
        )
        assert str(probe["cursor"]) != "pointer", (
            f"a SINGLE-turn rep row computes cursor={probe['cursor']!r} — a pointer cursor "
            "on a row with no expand handler is the affordance-lie: it LOOKS clickable but "
            "clicking it does nothing. The `cursor:pointer` must stay scoped to "
            "`.topics-rep-thread.expandable` (turnCount > 1), not the bare row."
        )
        assert not probe["hasChev"], (
            "a SINGLE-turn rep row sprouted a `.topics-rep-chev` disclosure chevron — it "
            "promises an expand that doesn't exist."
        )
        assert (
            probe["role"] is None
            and probe["tabindex"] is None
            and probe["ariaExpanded"] is None
        ), (
            f"a SINGLE-turn rep row carries button-role/disclosure attributes "
            f"(role={probe['role']!r}, tabindex={probe['tabindex']!r}, "
            f"aria-expanded={probe['ariaExpanded']!r}) — it announces an activatable "
            "control to AT that does nothing. These belong only on the multi-turn "
            "`.expandable` card."
        )
        assert not probe["focusable"], (
            "a SINGLE-turn rep row took keyboard focus (it's in the Tab order) — a "
            "keyboard user lands on a row that activates nothing."
        )
        # The interaction proof: clicking it changed nothing.
        assert not probe["openAfterClick"] and not probe["turnsVisibleAfterClick"], (
            "clicking a SINGLE-turn rep row toggled `.open` / revealed a turns list — the "
            f"row faked an expand (open={probe['openAfterClick']}, "
            f"turnsVisible={probe['turnsVisibleAfterClick']})."
        )
        assert not errs, f"the single-turn rep inertness check threw JS errors: {errs}"
        browser.close()
