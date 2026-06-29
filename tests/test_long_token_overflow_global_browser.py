"""GLOBAL long-unbreakable-token horizontal-overflow guard (the class-killer).

WHY THIS EXISTS — per-surface whack-a-mole kept missing instances. `overflow-wrap`
is a CROSS-CUTTING concern at (surface × state × free-text-field): any per-surface
selector census that doesn't drive EVERY state misses fields, and parallel-surface
drift means one surface's twin gets the break rule while another's private copy
doesn't. The long-token class was falsely declared "complete" three times (iter 288
missed `.task-collapsible` summary → 296; iter 297's disclosure-widget census missed
the running/failed `.provider-status-detail` → 301). The durable fix is a SINGLE
global guard that drives every USER-FACING render surface, seeded so EVERY free-text
field a user/LLM can fill carries a long unbreakable token, and asserts no horizontal
overflow at the narrow production widths — so a NEW free-text container (a future
card, a renamed binding) is swept automatically, not by hand.

WHAT IT CAUGHT on its first run (2026-06-22, this iter):
  • `.cold-open` / `.council-value` on the launchpad HOME — the hero-proof card's
    cold-open line interpolates the lens tension poles (pole_a/pole_b) VERBATIM, the
    SAME free LLM/lens text as the taste-list, but it sits in a SEPARATE card with
    its own inline style and the taste-card census never reached it. A long pole
    token blew HOME to 952px at 320px. Fixed in launchpad_template.py.
  • `.pick-basin-name` in the memory viewer picks.json view — the human-readable
    basin name (topology label / top-terms, free corpus text) had no break rule
    while the sibling `.pick-basin` id already broke; a long basin label blew the
    picks view to 869px at 320px. Fixed in memory_viewer.py.

THE SURFACES (the reachability-gated render_*_html / render_*_page entrypoints,
excluding the dead render_unified_council_page #311):
  • launchpad HOME + /stats   — render_launchpad_html / render_stats_html
  • live council page         — render_live_council_page, in RUNNING / FAILED /
                                COMPLETED states (each exposes different fields)
  • post-hoc review page      — render_review_html
  • memory viewer             — render_memory_viewer_html, every .md/.json tab

THE INTENTIONAL INNER-SCROLL EXCLUSIONS (legitimate bounded cases — a wide table or
a stretched grid track that scrolls WITHIN its own box; documented at source):
  • TABLE.routing-table / wide markdown TABLE — `display:block; overflow-x:auto`
    (the cheat-sheet / routing reader: fixed multi-column data that scrolls inside
    its card, never the document).
  • `.answers-grid` / `.answers-grid-three` columns — `minmax(0,1fr)` tracks that
    shrink to fit; the inner answer wraps, the TRACK is intentional.
These are excluded by NAME (an offending element whose own or ancestor's class is in
the allowlist AND whose computed overflow-x is auto/scroll is bounded inner-scroll).
The guard never blanket-skips: the DOCUMENT-level `scrollWidth <= clientWidth`
assertion still has to hold, so a real overflow that escapes one of these containers
still reds.

This guard DRIVES THE REAL PAGE in Chromium at 320 + 375 (the widths where an
unbreakable token bites hardest) and reads DOM geometry — not a CSS string check.
Parametrized over (surface, width) so future surfaces are swept automatically.

MUTATION-PROVEN to BITE: stripping the `.cold-open` break rule reds exactly the
launchpad-home cases; stripping `.pick-basin-name` reds exactly the memory-viewer
case; the other surfaces stay green. (Recorded in the iter report.)

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

REPO = Path(__file__).resolve().parents[1]

# The narrow production widths where an unbreakable token bites hardest. 320 is the
# smallest supported phone; 375 is the iPhone-mini baseline.
_WIDTHS = [320, 375]

# A battery of long UNBREAKABLE tokens — each has NO space/separator break
# opportunity for ~100+ chars, so it can only fit if a break rule wraps it. These
# are exactly the shapes LLM/corpus free text emits: a run-together identifier, a
# deep github URL, a deep /Users path, a separator-free regex.
_RUN = "a" + "ZxQwErTyUiOpAsDfGhJkLzXcVbNm0123456789" * 2 + "ZxQwErTyUiOpAsDfGhJkLzXcVbN_END110"
_URL = (
    "https://github.com/keepwhatworks/trinity/blob/main/src/trinity_local/very/"
    "deeply/nested/handler_implementation_module_with_a_long_name.py"
)
_PATH = (
    "/Users/someverylongusername/projects/deeply/nested/module/submodule/"
    "handler_implementation_with_a_very_long_filename.py"
)
_REGEX = (
    "Replacethewholeregexwithaurllibparsecallwhichhandlesalledgecasescorrectly"
    "andcannotcatastrophicallybacktrackonadversarialinputstrings"
)
# Verify the discriminating shape at import time (a typo'd short token would make
# the whole suite vacuous — a long SPACE-FREE run is the only shape that needs
# overflow-wrap to break).
assert " " not in _RUN and len(_RUN) >= 100
assert " " not in _URL and " " not in _PATH and " " not in _REGEX

# A long (>240 char) task body so the live page renders the .task-collapsible
# disclosure (the >240 branch) whose <summary> slices the first 200 chars — a
# un_RUN-prefixed slice has no break opportunity. The trailing words keep it
# multi-line in the expanded <p>.
_LONG_TASK = _RUN + " " + ("comparison " * 40) + _URL + " " + _PATH

# Intentional inner-scroll containers (class substrings). An offending element that
# IS one of these, or sits inside one, AND whose computed overflow-x is auto/scroll,
# is a bounded inner-scroll — legitimate, not a page-overflow bug. Documented at
# source (the rule comments name each).
_INNER_SCROLL_CLASSES = [
    "routing-table",   # cheat-sheet / routing reader — overflow-x:auto data table
    "md-code-block",   # cheat-sheet CLI snippet — overflow-x:auto
    "answers-grid",    # live council answer columns — minmax(0,1fr) tracks
]


def _browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    sp = sync_playwright().start()
    try:
        browser = sp.chromium.launch()
    except Exception as exc:  # chromium not installed
        sp.stop()
        pytest.skip(f"no launchable chromium for the global overflow guard: {exc}")
    return sp, browser


def _serve(directory) -> tuple[http.server.HTTPServer, int]:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(directory)
    )
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


# The DOM probe: document-level scrollWidth vs clientWidth (the canonical broken
# mobile symptom), PLUS the worst real offender after EXCLUDING the documented
# inner-scroll containers, PLUS whether the long token actually painted (so a
# no-render can't pass vacuously) and whether raw mustache leaked (an unmounted
# petite-vue would make the "overflow" meaningless).
_PROBE_JS = r"""(args) => {
  const [longtok, innerScrollClasses] = args;
  const de = document.documentElement;
  const inInnerScroll = (el) => {
    let node = el;
    while (node && node !== document.body) {
      const cls = (node.className && node.className.toString)
        ? node.className.toString() : '';
      for (const k of innerScrollClasses) {
        if (cls.indexOf(k) !== -1) {
          const ox = getComputedStyle(node).overflowX;
          if (ox === 'auto' || ox === 'scroll') return true;
        }
      }
      node = node.parentElement;
    }
    return false;
  };
  let worst = null, worstRight = 0;
  document.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    if (inInnerScroll(el)) return;          // bounded inner-scroll — legitimate
    if (Math.round(r.right) > worstRight) {
      worstRight = Math.round(r.right);
      const cls = (el.className && el.className.toString)
        ? el.className.toString() : '';
      worst = (el.tagName + '.' + cls).slice(0, 70);
    }
  });
  return {
    clientW: de.clientWidth,
    scrollW: de.scrollWidth,
    worst, worstRight,
    hasLong: document.body.innerText.includes(longtok),
    braceLeak: document.body.innerText.includes('{{'),
  };
}"""


# ── per-surface seed/render helpers ─────────────────────────────────────────────
# Each returns (url_or_file_path, served_httpd_or_None, served_home_or_None).


def _seed_synthetic(home: Path) -> None:
    """Populate a baseline synthetic home (councils, basins, lens, taste) via the
    canonical seeder, then overlay long tokens into the free-text fields below."""
    sys.path.insert(0, str(REPO))
    import scripts.seed_synthetic_home as seeder  # type: ignore

    seeder.seed(home)


def _overlay_launchpad_long_tokens(home: Path) -> None:
    """Long tokens into every launchpad free-text field: the taste lens poles +
    failure modes (taste card), and the lens.md tensions (the cold-open hero-proof
    line, which interpolates pole_a/pole_b verbatim)."""
    me = home / "me"
    me.mkdir(parents=True, exist_ok=True)
    (me / "lenses.json").write_text(
        json.dumps({"lenses": [
            {"pole_a": _RUN, "pole_b": _URL, "failure_a": _PATH, "failure_b": _REGEX,
             "tension_decisions": [], "dual_evidence": {},
             "basins_spanned": ["b00"], "verdict": "accepted", "horizon": "tactical"},
        ]}),
        encoding="utf-8",
    )
    (home / "memories" / "lens.md").write_text(
        f"# Lens\n\n## Tensions\n\n- **{_RUN} vs {_URL}**: leans {_PATH}\n",
        encoding="utf-8",
    )


def _build_launchpad(home: Path, *, view: str) -> tuple[str, http.server.HTTPServer]:
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    _seed_synthetic(home)
    _overlay_launchpad_long_tokens(home)
    from trinity_local import vendor
    from trinity_local.launchpad_page import write_portal_html

    write_portal_html()
    vendor.publish_vendor_files(home / "portal_pages")
    httpd, port = _serve(home)
    page = "stats.html" if view == "stats" else "launchpad.html"
    return f"http://127.0.0.1:{port}/portal_pages/{page}", httpd


def _build_review(home: Path) -> tuple[str, None]:
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    import trinity_local.review as review_mod
    from trinity_local.review import ReviewResult

    review_mod.review_pages_dir = lambda: home  # type: ignore[assignment]
    rev = ReviewResult(
        review_id="rev-global-overflow",
        task_id="task-overflow-0001",
        original_provider="codex",
        reviewer_provider="claude",
        verdict=f"Incorrect at {_PATH} — see {_URL}.",
        issues=[_REGEX, f"The token {_RUN} must wrap."],
        suggestions=[f"Replace {_URL} and the run {_RUN}."],
        reviewed_at="2026-06-22T00:00:00",
    )
    path = review_mod.render_review_html(rev)
    return f"file://{path}", None


_MEMBERS = ["claude", "codex", "antigravity"]


def _live_status(token: str, state: str) -> dict:
    base = {
        "status_token": token,
        "task_text": _LONG_TASK,
        "memberOrder": _MEMBERS,
    }
    if state == "running":
        base.update({
            "status": "running",
            "members": {
                "claude": {"status": "running", "model": "claude-opus-4-8",
                           "reasoning_summary": _RUN + _URL},
                "codex": {"status": "failed", "model": "gpt-5.5",
                          "reasoning_summary": "ECONNREFUSED:" + _PATH},
                "antigravity": {"status": "pending", "model": "gemini-3.1-pro"},
            },
            "synthesis": {"status": "pending"},
        })
    elif state == "failed":
        base.update({
            "status": "failed",
            "error": "Council failed: " + _PATH + " " + _REGEX,
            "members": {
                "claude": {"status": "failed", "model": "claude-opus-4-8",
                           "reasoning_summary": _URL},
                "codex": {"status": "failed", "model": "gpt-5.5",
                          "reasoning_summary": _RUN},
                "antigravity": {"status": "failed", "model": "gemini-3.1-pro",
                                "reasoning_summary": _REGEX},
            },
            "synthesis": {"status": "failed"},
        })
    else:  # completed
        base.update({
            "status": "completed",
            "members": {
                p: {"status": "done", "model": m,
                    "response_text": f"Answer with {_RUN} and {_URL} at {_PATH} ({_REGEX})."}
                for p, m in [("claude", "claude-opus-4-8"),
                             ("codex", "gpt-5.5"),
                             ("antigravity", "gemini-3.1-pro")]
            },
            "synthesis": {
                "status": "done",
                "response_text": f"Synthesis: {_RUN} {_URL} {_PATH} {_REGEX}",
                "routing_label": {
                    "winner": "claude", "runner_up": "codex", "confidence": "high",
                    "agreed_claims": [f"They agree on {_RUN}", f"path {_PATH}"],
                    "disagreed_claims": [{
                        "claim": f"Use {_URL} not {_REGEX}",
                        "providers_for": ["claude"], "providers_against": ["codex"],
                        "why_matters": f"because {_PATH} and {_RUN}",
                    }],
                },
            },
        })
    return base


def _build_live_council(home: Path, *, state: str) -> tuple[str, http.server.HTTPServer]:
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    from trinity_local import vendor
    from trinity_local.council_review import write_live_council_page
    from trinity_local.launchpad_page import write_portal_html
    from trinity_local.state_paths import portal_pages_dir, review_pages_dir

    write_portal_html()
    write_live_council_page()
    vendor.publish_vendor_files(review_pages_dir())
    vendor.publish_vendor_files(portal_pages_dir())
    status_dir = portal_pages_dir() / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    token = f"tok_{state}"
    status = _live_status(token, state)
    sidecar = (
        "window.__TRINITY_COUNCIL_STATUS__ = window.__TRINITY_COUNCIL_STATUS__ || {};\n"
        f"window.__TRINITY_COUNCIL_STATUS__[{json.dumps(token)}] = {json.dumps(status)};\n"
    )
    (status_dir / f"council_status_{token}.js").write_text(sidecar, encoding="utf-8")
    httpd, port = _serve(home)
    url = (
        f"http://127.0.0.1:{port}/review_pages/live_council.html"
        f"?status_token={token}&members=claude,codex,antigravity"
    )
    return url, httpd


def _build_memory_viewer(home: Path, *, mfile: str) -> tuple[str, http.server.HTTPServer]:
    os.environ["TRINITY_HOME"] = str(home)
    os.environ["TRINITY_AUTOSCAN_DISABLED"] = "1"
    _seed_synthetic(home)
    mem = home / "memories"
    # Overlay long tokens into every memory file's free text.
    (mem / "lens.md").write_text(
        f"# Lens\n\n## Tensions\n\n- **{_RUN} vs abstract**: leans {_URL}\n"
        f"- path: {_PATH}\n- regex: `{_REGEX}`\n",
        encoding="utf-8",
    )
    (mem / "vocabulary.md").write_text(
        f"# Vocabulary\n\n## Anchors\n- {_RUN}\n- {_URL}\n- {_PATH}\n",
        encoding="utf-8",
    )
    (home / "core.md").write_text(
        f"# Core\n\nYou prefer {_RUN} and lead with {_URL} before {_PATH}. "
        f"Regex {_REGEX}.\n",
        encoding="utf-8",
    )
    (mem / "generators.md").write_text(
        f"# Generators\n\n## Invariant 1\n\n{_RUN}\n\nEvidence: {_URL} at {_PATH} "
        f"matching {_REGEX}.\n",
        encoding="utf-8",
    )
    # topics.json: long token as a basin label + top_term (the pick-basin-name +
    # topology-label free text).
    (mem / "topics.json").write_text(
        json.dumps({"basins": [{
            "id": "b00", "centroid": [1, 0, 0, 0], "size": 20, "label": _RUN,
            "top_terms": [_URL, _PATH],
            "representatives": [{"id": "r0", "snippet": _RUN + " " + _URL}],
        }]}),
        encoding="utf-8",
    )
    from trinity_local import vendor
    from trinity_local.memory_viewer import write_memory_viewer

    write_memory_viewer()
    vendor.publish_vendor_files(home / "portal_pages")
    httpd, port = _serve(home)
    return f"http://127.0.0.1:{port}/portal_pages/memory.html?file={mfile}", httpd


# (surface_id, builder, settle_ms). settle_ms is the post-goto wait: the live
# council page polls + mounts (needs ~2.8s); the static pages paint immediately but
# the launchpad mounts petite-vue (~0.9s).
_SURFACES = [
    ("launchpad-home", lambda h: _build_launchpad(h, view="home"), 900),
    ("launchpad-stats", lambda h: _build_launchpad(h, view="stats"), 900),
    ("review", lambda h: _build_review(h), 350),
    ("live-running", lambda h: _build_live_council(h, state="running"), 2800),
    ("live-failed", lambda h: _build_live_council(h, state="failed"), 2800),
    ("live-completed", lambda h: _build_live_council(h, state="completed"), 2800),
    ("mv-core", lambda h: _build_memory_viewer(h, mfile="core.md"), 1000),
    ("mv-lens", lambda h: _build_memory_viewer(h, mfile="lens.md"), 1000),
    ("mv-topics", lambda h: _build_memory_viewer(h, mfile="topics.json"), 1300),
    ("mv-vocab", lambda h: _build_memory_viewer(h, mfile="vocabulary.md"), 1000),
    ("mv-generators", lambda h: _build_memory_viewer(h, mfile="generators.md"), 1000),
    ("mv-picks", lambda h: _build_memory_viewer(h, mfile="picks.json"), 1000),
    ("mv-routing", lambda h: _build_memory_viewer(h, mfile="routing.json"), 1000),
]

# Surfaces where the long token is EXPECTED to paint into visible body text (so a
# vacuous no-render is caught). The launchpad-stats view HIDES the home-only taste
# /cold-open cards by design, and the topics.json/routing.json readers render the
# token inside the SVG graph / a scroll table (not body innerText) — so don't
# require hasLong there.
_REQUIRE_LONG_PAINTED = {
    "launchpad-home", "review", "live-running", "live-failed", "live-completed",
    "mv-core", "mv-lens", "mv-vocab", "mv-generators", "mv-picks",
}


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize("surface_id,builder,settle_ms", _SURFACES,
                         ids=[s[0] for s in _SURFACES])
def test_no_long_token_horizontal_overflow(surface_id, builder, settle_ms, width):
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp(prefix=f"trinity-ovf-{surface_id}-"))
    httpd = None
    sp, browser = _browser()
    try:
        url, httpd = builder(home)
        page = browser.new_context(
            viewport={"width": width, "height": 1400}
        ).new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)[:160]))
        page.goto(url)
        page.wait_for_timeout(settle_ms)
        geo = page.evaluate(_PROBE_JS, [_RUN, _INNER_SCROLL_CLASSES])
        page.close()
    finally:
        browser.close()
        sp.stop()
        if httpd is not None:
            httpd.shutdown()

    # PRECONDITION A: petite-vue (where present) mounted — raw mustache means the
    # binding never ran, so any "overflow" is unbound template text, not the real
    # rendered geometry.
    assert not geo["braceLeak"], (
        f"[{surface_id} @{width}] raw petite-vue '{{{{ }}}}' leaked — the page never "
        "mounted, so the overflow check is vacuous"
    )

    # PRECONDITION B: the long token actually painted (a no-render can't pass).
    if surface_id in _REQUIRE_LONG_PAINTED:
        assert geo["hasLong"], (
            f"[{surface_id} @{width}] the long unbreakable token did NOT paint into "
            "visible text — the seed/render path broke; fix the fixture before "
            "trusting the overflow assertion (a no-render would pass vacuously)"
        )

    # THE BITE: the whole document must fit the viewport (no horizontal scrollbar),
    # AND no real (non-inner-scroll) element's right edge may exceed the viewport.
    # A long unbreakable token in any free-text field is the founder symptom — it
    # blew the launchpad home to 952px and the picks view to 869px at 320px before
    # the .cold-open / .pick-basin-name break rules landed.
    assert geo["scrollW"] <= geo["clientW"] + 1, (
        f"[{surface_id} @{width}] documentElement scrollWidth {geo['scrollW']} > "
        f"clientWidth {geo['clientW']} — a long unbreakable token (path / URL / "
        f"identifier / regex) blew the page out horizontally. Widest non-inner-scroll "
        f"element: {geo['worst']!r} (right {geo['worstRight']}). Add the shared break "
        "rule (overflow-wrap: break-word/anywhere; word-break) to the free-text "
        "container that holds the token — the long-unbreakable-token class."
    )
    assert geo["worstRight"] <= geo["clientW"] + 1, (
        f"[{surface_id} @{width}] a non-inner-scroll element spills past the viewport "
        f"(right {geo['worstRight']} > clientWidth {geo['clientW']}, element "
        f"{geo['worst']!r}) — a long token did not wrap inside its free-text box."
    )
