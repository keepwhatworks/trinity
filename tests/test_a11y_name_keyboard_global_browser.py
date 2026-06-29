"""GLOBAL accessible-name + keyboard-operability guard (the third class-killer).

WHY THIS EXISTS — the a11y track (iters 232-239) fixed accessible-name + keyboard
operability PER-SURFACE: the memory-viewer topology <circle> nodes got tabindex +
role + keydown (232), the in-provider sync pill got the same widget promotion (233),
the refine <textarea>s got an aria-label (97), the live-council chain divider got
aria-expanded (segment-toggle), etc. But NAME + KEYBOARD are CROSS-CUTTING concerns
at (surface × state × control): every one of those was found by DRIVING ONE surface,
and a parallel surface's twin control — or a control that only appears in a state the
per-surface pass never drove — can ship an icon-only button with no name, or a
mouse-only `[role=button]` that no keyboard can reach. This is the same lesson the
GLOBAL overflow guard (test_long_token_overflow_global_browser) and the GLOBAL
contrast guard (test_contrast_aa_global_browser) taught: a single guard that drives
EVERY render surface and reads the REAL AX tree catches the (surface × state) cells
and the parallel-surface drift a per-surface census keeps missing. Reusing the
overflow guard's `_SURFACES` harness keeps all THREE global guards on ONE surface
list — a NEW render surface added there inherits name + keyboard + contrast + overflow
at once.

WHAT IT ASSERTS, for every interactive control on every live render surface:

  1. ACCESSIBLE NAME — every `<button>`, `<a href>`, `[role=button]`, `[role=link]`,
     `<summary>`, `<input>`/`<textarea>`/`<select>`, and `[tabindex="0"]` control has
     a NON-EMPTY accessible name. Read from the REAL AX tree
     (`Accessibility.getFullAXTree`), NOT a naive textContent check — a button named
     ONLY by aria-label (an `<svg>`-icon button) PASSES; a button with only a
     decorative icon and no name FAILS. The icon-only-no-name control is the founder
     symptom (the 232-239 class).

  2. KEYBOARD OPERABILITY — every such control is keyboard-reachable: a NATIVE
     interactive element (button / a[href] / input / textarea / select / summary) is
     inherently operable; a CUSTOM control (`[role=button]`/`[role=link]` on a
     non-native tag, or a bare `[tabindex="0"]` clickable) must carry `tabindex >= 0`
     AND a real keydown/keyup/keypress listener (detected via
     `DOMDebugger.getEventListeners` on the resolved node — the Iter-232/234 pattern:
     a div/span/circle role=button needs tabindex=0 + keydown firing the same action
     as the click). A mouse-only `[role=button]` that is NOT focusable, or is
     focusable but has no key handler, is the WCAG 2.1.1 defect.

HOW THE NAME IS COMPUTED — the AX tree's `name.value` IS the spec accessible-name
(aria-label → aria-labelledby → text content → title → <label for> → for inputs the
associated label/value/placeholder-as-last-resort), computed by Chromium per the
ARIA accname spec. We join each interactive DOM element to its AX node via the
backendNodeId (we tag each control with a unique data attribute and read it back from
the CDP DOM tree, so the join is exact, not order-dependent).

THE SURFACES (reachability-gated render_*_html / render_*_page, excluding the dead
render_unified_council_page #311) — inherited verbatim from the overflow guard's
`_SURFACES`:
  • launchpad HOME + /stats   — render_launchpad_html / render_stats_html
  • live council page         — render_live_council_page (RUNNING / FAILED / COMPLETED)
  • post-hoc review page      — render_review_html
  • memory viewer             — render_memory_viewer_html, every .md/.json tab

DOCUMENTED EXEMPTIONS (legitimate non-controls / spec carve-outs, each by a tight
rule — the guard never blanket-skips a real unnamed/mouse-only control):
  • Elements the AX tree marks `ignored` (display:none, aria-hidden, off-layout) — not
    in the accessibility tree at all, so not a perceivable control.
  • A `[tabindex="0"]` that is a NATIVE interactive element or a scroll/region/listbox
    container (role in a small structural set) — tabindex on a focusable region for
    scroll/skip purposes is not a "control" needing a key handler. We only require a
    key handler for tabindex elements that ALSO declare role=button/link/menuitem/etc
    (an actual command control) or carry no role at all but sit on a non-native tag.
  • The hidden file `<input type=file>` behind a styled label (memory viewer import) —
    if present, it's keyboard-reachable through its label; the AX tree names it.

This guard DRIVES THE REAL PAGE in Chromium, reads the REAL AX tree + the REAL event
listeners — not a source-string or textContent check. Parametrized over
(surface, width) so future surfaces are swept automatically.

MUTATION-PROVEN to BITE (recorded in the iter report): stripping the aria-label from
ONE surface's icon-only control reds EXACTLY that surface's case (empty accessible
name); stripping a custom control's tabindex/keydown reds EXACTLY that surface's case
(mouse-only). The other surfaces stay green.

Slow + browser marked; skips when Playwright/chromium are absent.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.browser]

# Reuse the GLOBAL overflow guard's surface harness (seed + per-surface builders +
# _browser/_serve). Importing it keeps all THREE global guards on ONE surface list, so
# a new render surface added there is swept by name + keyboard + contrast + overflow at
# once. The overflow guard overlays long tokens into the free-text fields; that's
# harmless here (it changes only the visible TEXT, never the controls / their names /
# their handlers), and it means all the global guards run against identical pages.
import tests.test_long_token_overflow_global_browser as ovf  # noqa: E402

# One representative desktop width + one phone width per surface. Name/keyboard wiring
# is width-independent for these controls, but a media query could in principle render
# a DIFFERENT control set at the phone width (a mobile-only menu button), so 393 sweeps
# that too while keeping the suite fast.
_WIDTHS = [1280, 393]

# The interactive-control selector: the elements a user can click/activate that MUST
# carry a name + be keyboard-operable. `[tabindex="0"]` catches the custom widgets
# (the topology circle, the chain divider) that aren't a native tag or [role].
_CONTROL_SELECTOR = (
    "button, a[href], [role=button], [role=link], summary, "
    "input:not([type=hidden]), textarea, select, [tabindex='0']"
)

# Native tags that are inherently keyboard-operable (focusable + activatable by the UA
# with no JS) — a control on one of these needs NO custom key handler.
_NATIVE_INTERACTIVE_TAGS = {"button", "a", "input", "textarea", "select", "summary"}

# Roles that, when carried by a [tabindex=0] element, make it a focusable REGION /
# structural container rather than a command CONTROL — a key handler is not required
# (the user tabs to it to scroll / read, not to activate it). Anything NOT in this set
# (role=button/link/menuitem/tab/switch/checkbox/…) on a [tabindex=0] non-native tag is
# a command control and MUST have a key handler.
_STRUCTURAL_TABINDEX_ROLES = {
    "region", "group", "list", "listbox", "grid", "table", "tabpanel",
    "document", "application", "dialog", "log", "status", "alert", "none",
    "presentation", "img", "figure", "article", "main", "navigation",
}

# Key-event listener types that satisfy keyboard operability for a custom control.
_KEY_LISTENER_TYPES = {"keydown", "keyup", "keypress"}

# Surfaces that are read-only by design and render ZERO interactive controls — the
# precondition "renders >=2 controls" does NOT apply to them (it would false-fail).
# The name/keyboard assertions still run (they trivially hold on an empty control set),
# so if such a surface ever GAINS a control it is swept like any other.
#   • render_review_html — the post-hoc review page is a static archived VERDICT
#     SNAPSHOT (verdict + issues + suggestions, all read-only prose; the footer is bare
#     closing tags). It has no buttons / links / inputs by design — confirmed at source
#     (src/trinity_local/review.py), so requiring controls there is wrong.
_NO_CONTROLS_EXPECTED = {"review"}


# Tag every interactive control with a unique data-a11y-idx and return per-control DOM
# facts (tag, role, href, tabindex, native-ness, visibility). The AX name + the event
# listeners are read separately via CDP and joined on data-a11y-idx (an exact join, not
# order-dependent). Returns only controls that are ACTUALLY in layout (a 0-box / hidden
# control is not perceivable; the AX `ignored` flag is the authoritative second filter).
_TAG_CONTROLS_JS = r"""(selector) => {
  const els = [...document.querySelectorAll(selector)];
  const out = [];
  els.forEach((el, i) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    const visible = r.width > 0 && r.height > 0 &&
      s.visibility !== 'hidden' && s.display !== 'none';
    el.setAttribute('data-a11y-idx', String(i));
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').trim().toLowerCase();
    const ti = el.getAttribute('tabindex');
    out.push({
      idx: i,
      tag,
      role,
      hasHref: tag === 'a' && el.hasAttribute('href'),
      tabindex: ti === null ? null : parseInt(ti, 10),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      visible,
      txt: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40),
      cls: ((el.className && el.className.toString) ? el.className.toString() : '').slice(0, 50),
    });
  });
  return out;
}"""


def _ax_names_by_idx(cdp):
    """backendNodeId → (role, name, ignored) for every AX node, joined to our
    data-a11y-idx by walking the CDP DOM tree (which carries both backendNodeId and the
    attribute we set). Returns {a11y_idx: (ax_role, ax_name, ignored)}."""
    cdp.send("Accessibility.enable")
    cdp.send("DOM.enable")
    tree = cdp.send("Accessibility.getFullAXTree")
    by_back = {}
    for n in tree["nodes"]:
        b_id = n.get("backendDOMNodeId")
        if b_id is None:
            continue
        role = (n.get("role") or {}).get("value")
        name = (n.get("name") or {}).get("value") or ""
        ignored = bool(n.get("ignored"))
        by_back[b_id] = (role, name, ignored)

    doc = cdp.send("DOM.getDocument", {"depth": -1})
    out = {}
    stack = [doc["root"]]
    while stack:
        node = stack.pop()
        for c in node.get("children", []) or []:
            stack.append(c)
        attrs = node.get("attributes", []) or []
        ad = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs), 2)}
        idx = ad.get("data-a11y-idx")
        if idx is None:
            continue
        b_id = node.get("backendNodeId")
        ax = by_back.get(b_id, (None, "", True))
        out[int(idx)] = (ax[0], ax[1], ax[2], node.get("nodeId"))
    return out


def _has_key_listener(cdp, node_id):
    """True if the DOM node (by CDP nodeId) carries a real keydown/keyup/keypress
    listener — proves keyboard ACTIVATION wiring for a custom control."""
    try:
        ro = cdp.send("DOM.resolveNode", {"nodeId": node_id})
        oid = ro["object"]["objectId"]
        ls = cdp.send("DOMDebugger.getEventListeners", {"objectId": oid})
    except Exception:
        return False
    return any(li.get("type") in _KEY_LISTENER_TYPES for li in ls.get("listeners", []))


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize("surface_id,builder,settle_ms", ovf._SURFACES,
                         ids=[s[0] for s in ovf._SURFACES])
def test_controls_named_and_keyboard_operable(surface_id, builder, settle_ms, width):
    pytest.importorskip("playwright.sync_api")

    home = Path(tempfile.mkdtemp(prefix=f"trinity-a11y-{surface_id}-"))
    httpd = None
    sp, browser = ovf._browser()
    try:
        url, httpd = builder(home)
        page = browser.new_context(
            viewport={"width": width, "height": 1400}
        ).new_page()
        page.goto(url)
        page.wait_for_timeout(settle_ms)
        braces = page.evaluate("() => document.body.innerText.includes('{{')")
        controls = page.evaluate(_TAG_CONTROLS_JS, _CONTROL_SELECTOR)
        cdp = page.context.new_cdp_session(page)
        ax = _ax_names_by_idx(cdp)
        # For each VISIBLE custom (non-native) control that needs a key handler, read
        # its listeners now (while the CDP session + node ids are live).
        key_listener = {}
        for c in controls:
            if not c["visible"]:
                continue
            native = c["tag"] in _NATIVE_INTERACTIVE_TAGS and (
                c["tag"] != "a" or c["hasHref"]
            )
            if native:
                continue
            ax_for = ax.get(c["idx"])
            if not ax_for:
                continue
            node_id = ax_for[3]
            if node_id is not None:
                key_listener[c["idx"]] = _has_key_listener(cdp, node_id)
        page.close()
    finally:
        browser.close()
        sp.stop()
        if httpd is not None:
            httpd.shutdown()

    # PRECONDITION A: petite-vue (where present) mounted — raw mustache means the
    # bindings never ran, so the controls/names/handlers are unbound template text and
    # the whole check is vacuous.
    assert not braces, (
        f"[{surface_id} @{width}] raw petite-vue '{{{{ }}}}' leaked — the page never "
        "mounted, so the a11y name/keyboard check is vacuous"
    )

    # PRECONDITION B: the page actually rendered interactive controls (a blank render
    # would pass with 0 controls). Every surface that HAS controls paints multiple when
    # populated; floor at 2 so a seed/render regression can't green this vacuously. The
    # read-only review snapshot (see _NO_CONTROLS_EXPECTED) is exempt — it has none by
    # design, so the name/keyboard assertions below simply have nothing to check.
    visible_controls = [c for c in controls if c["visible"]]
    if surface_id not in _NO_CONTROLS_EXPECTED:
        assert len(visible_controls) >= 2, (
            f"[{surface_id} @{width}] only {len(visible_controls)} visible interactive "
            "control(s) found — the seed/render path looks broken; a near-blank page "
            "would pass the a11y assertion vacuously. Fix the fixture before trusting "
            "the result."
        )

    unnamed = []          # controls with an EMPTY accessible name
    mouse_only = []       # custom controls not keyboard-operable

    for c in visible_controls:
        if c["disabled"]:
            continue  # WCAG exempts disabled controls
        ax_for = ax.get(c["idx"])
        if ax_for is None:
            # Not joined to the AX tree at all — treat as ignored (not perceivable).
            continue
        ax_role, ax_name, ignored, _nid = ax_for
        if ignored:
            # AX-ignored (display:none/aria-hidden/off-layout) — not a perceivable
            # control. The visible-box filter already removed most; this is the
            # authoritative second pass (documented exemption).
            continue

        # (1) ACCESSIBLE NAME — must be non-empty per the AX tree (aria-label /
        # labelledby / text / title / <label for>). An icon-only control with no name
        # is the founder symptom.
        if not (ax_name or "").strip():
            unnamed.append((c["tag"], c["role"], c["cls"], c["txt"]))

        # (2) KEYBOARD OPERABILITY.
        native = c["tag"] in _NATIVE_INTERACTIVE_TAGS and (
            c["tag"] != "a" or c["hasHref"]
        )
        if native:
            continue  # UA makes native controls keyboard-operable
        # Custom control: must be focusable (tabindex >= 0) …
        focusable = c["tabindex"] is not None and c["tabindex"] >= 0
        if not focusable:
            mouse_only.append((c["tag"], c["role"], c["cls"], c["txt"],
                               "not focusable (no tabindex>=0)"))
            continue
        # … and, unless it's a focusable structural REGION (scroll/skip target), it
        # must carry a real key-activation listener.
        is_structural = c["role"] in _STRUCTURAL_TABINDEX_ROLES
        if is_structural:
            continue
        if not key_listener.get(c["idx"], False):
            mouse_only.append((c["tag"], c["role"], c["cls"], c["txt"],
                               "focusable but no keydown/keyup/keypress handler"))

    # THE BITE — every interactive control must have a non-empty accessible name AND be
    # keyboard-operable. An icon-only button with no aria-label (the 232-239 founder
    # symptom) reds the first; a mouse-only [role=button]/clickable with no
    # tabindex+keydown (the Iter-232/234 topology-node symptom) reds the second.
    assert not unnamed, (
        f"[{surface_id} @{width}] interactive control(s) with an EMPTY accessible name "
        f"(read from the real AX tree — aria-label / labelledby / text / title / "
        f"<label for>). An icon-only control with no name is invisible to assistive "
        f"tech (WCAG 4.1.2). Give it an aria-label (or visible text / <label>). "
        f"Offenders (tag, role, class, text):\n  "
        + "\n  ".join(repr(u) for u in unnamed)
    )
    assert not mouse_only, (
        f"[{surface_id} @{width}] custom interactive control(s) NOT keyboard-operable "
        f"(WCAG 2.1.1 Keyboard) — a mouse-only [role=button]/clickable. Promote it to a "
        f"real widget: tabindex=0 + a keydown(Enter/Space) firing the SAME action as the "
        f"click (the Iter-232/234 topology-node / sync-pill pattern). Offenders "
        f"(tag, role, class, text, why):\n  "
        + "\n  ".join(repr(m) for m in mouse_only)
    )
