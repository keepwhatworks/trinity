"""Tests for the council-review markdown renderer.

render_markdown renders every council member response + the chairman synthesis
on the flagship review page, and had zero coverage. The regression that
prompted this file (found 2026-05-31 by browser-dogfooding a real council
review page): a prose line containing `` `curl | bash` `` — a pipe INSIDE an
inline-code span — was mis-detected as a markdown table and split on that
pipe, rendering the winner's response as garbled 3-column soup. The fix makes
table detection + cell splitting ignore pipes inside code spans and escaped
pipes (GFM semantics).
"""
from __future__ import annotations

from trinity_local.markdown_utils import (
    _is_table_row,
    _split_table_cells,
    render_markdown,
)


# ---- The regression: pipe inside inline code is not a table delimiter ----


def test_prose_line_with_inline_code_pipe_is_not_a_table():
    """`` `curl | bash` minimizes friction ... | ... `` was rendered as a
    3-column table. A pipe inside `` `code` `` must not count as a delimiter."""
    line = "`curl | bash` minimizes friction but `pip | brew` is cleaner"
    assert _is_table_row(line) is False


def test_inline_code_pipe_kept_in_one_cell_in_real_table():
    row = "| `curl | bash` | minimizes friction | maximizes trust |"
    cells = _split_table_cells(row)
    # leading/trailing empty from the bounding pipes, then 3 real cells
    assert cells == ["", " `curl | bash` ", " minimizes friction ", " maximizes trust ", ""]


def test_real_table_with_code_pipe_renders_correct_column_count():
    md = (
        "| Path | Tradeoff |\n"
        "|---|---|\n"
        "| `curl | bash` | one line, max trust ask |\n"
        "| packages | inspectable, more friction |\n"
    )
    html = render_markdown(md)
    assert html.count("<th>") == 2, "header must be 2 columns, not split on the in-code pipe"
    assert "<code>curl | bash</code>" in html, "the code span must survive intact in its cell"


def test_escaped_pipe_is_literal_not_a_delimiter():
    cells = _split_table_cells(r"| a \| b | c |")
    assert cells == ["", " a | b ", " c ", ""]


# ---- Equivalence: normal tables behave exactly as before the fix ----


def test_normal_table_split_identical_to_naive_split():
    for row in ("| a | b | c |", "a | b | c", "| x |", "one | two | three | four"):
        assert _split_table_cells(row) == row.split("|"), row


def test_plain_two_column_table_renders():
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
    html = render_markdown(md)
    assert html.count("<th>") == 2
    assert "<td>1</td>" in html and "<td>2</td>" in html


# ---- General parser sanity (it had no coverage at all) ----


def test_headings_and_paragraphs():
    html = render_markdown("## Title\n\nA paragraph here.")
    # The VISIBLE tag is unchanged (<h2> keeps its font-size) but a demoted
    # aria-level ships so AT nests this content heading below the page section
    # it lives in instead of competing for the page-level <h1>/<h2> outline
    # (WCAG 1.3.1 / 2.4.6 — render_markdown heading_offset, Iter 238).
    assert '<h2 aria-level="4">Title</h2>' in html
    assert "<p>A paragraph here.</p>" in html


def test_content_headings_carry_a_demoted_aria_level():
    """Every content heading keeps its visible <hN> tag but announces a level
    offset by ``heading_offset`` (default 2), clamped to 6 — so chairman
    synthesis / member-response markdown injected INSIDE a page that already
    owns its <h1> question + <h2> sections never emits a SECOND competing
    page-level <h1> or a top-level outline break a screen-reader user hits
    navigating by heading. Founder symptom (Iter 238): "a chairman synthesis
    that opens with '# Verdict' rendered a literal <h1> mid-page — a second
    competing <h1> and a top-level outline break (WCAG 1.3.1 / 2.4.6)."
    """
    # default offset=2: # -> aria-level 3, ## -> 4, ### -> 5, #### -> 6 (clamped)
    assert '<h1 aria-level="3">A</h1>' in render_markdown("# A")
    assert '<h2 aria-level="4">B</h2>' in render_markdown("## B")
    assert '<h3 aria-level="5">C</h3>' in render_markdown("### C")
    assert '<h4 aria-level="6">D</h4>' in render_markdown("#### D")
    # clamp: a deep content heading never exceeds aria-level 6
    assert '<h5 aria-level="6">E</h5>' in render_markdown("##### E")
    # an h6 is already at the floor — offset clamps to 6 == its own level, so no
    # redundant aria-level attribute is emitted (the announced level is already 6)
    assert "<h6>F</h6>" in render_markdown("###### F")
    # A content "# Heading" must NOT emit a bare <h1> (the competing-h1 founder
    # symptom). The tag stays <h1> (visual size) but the aria-level attr is
    # always present so the announced level is demoted.
    h1_out = render_markdown("# Verdict")
    assert "<h1 " in h1_out and 'aria-level="3"' in h1_out
    assert "<h1>" not in h1_out, (
        "a content '# Heading' emitted a bare <h1> — it would compete with the "
        "page's real <h1> and break the heading outline (WCAG 1.3.1 / 2.4.6)"
    )
    # heading_offset=0 reproduces the legacy (un-demoted) behavior for callers
    # that own a context where a content <h1> is genuinely the page title.
    assert render_markdown("# Title", heading_offset=0) == "<h1>Title</h1>"


def test_inline_code_and_bold():
    html = render_markdown("Use `trinity-local council` and **own your taste**.")
    assert "<code>trinity-local council</code>" in html
    assert "<strong>own your taste</strong>" in html


def test_bullets_render_as_list():
    html = render_markdown("- one\n- two\n")
    assert html.count("<li>") == 2 and "<ul>" in html


def test_fenced_code_block_preserves_pipes_verbatim():
    html = render_markdown("```\na | b | c\n```")
    assert "a | b | c" in html and "<table>" not in html


def test_empty_input_is_safe():
    assert render_markdown("") == '<p class="text-muted">(none)</p>'
    assert render_markdown(None) == '<p class="text-muted">(none)</p>'


# ---- XSS / HTML-escaping (the v-html sanitizer contract) ----
#
# render_markdown's output is injected into the live council page via Vue
# `v-html` (council_review.py:1120/1179 — responseHtml / synthesisHtml). That is
# the LEAST-trusted content on the core product page: raw council-member output.
# A council on a malicious question, or a prompt-injected provider, can emit
# `<script>` / `<img onerror>` in its answer. render_markdown must therefore
# escape ALL input and only emit a controlled tag allowlist — `v-html` does no
# escaping of its own. The renderer escapes-first (html.escape in _render_inline)
# then adds safe tags; these pin that contract so a future refactor (e.g. swapping
# in a markdown lib with raw-HTML passthrough) can't silently open an XSS hole.

from html.parser import HTMLParser

_ALLOWED_TAGS = {
    "p", "strong", "em", "code", "pre", "br", "blockquote",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "a",
}


class _HtmlAuditor(HTMLParser):
    """Collect any disallowed tag, any on* event handler, or any href/src whose
    scheme isn't http(s)/mailto/anchor — the things that would make v-html unsafe."""

    def __init__(self) -> None:
        super().__init__()
        self.violations: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            self.violations.append(f"disallowed <{tag}>")
        for name, value in attrs:
            lname = name.lower()
            if lname.startswith("on"):
                self.violations.append(f"event handler {name}= on <{tag}>")
            if lname in ("href", "src") and value:
                scheme_ok = value.strip().lower().startswith(
                    ("https://", "http://", "mailto:", "#")
                )
                if not scheme_ok:
                    self.violations.append(f"unsafe {name}={value!r} on <{tag}>")


def _audit(md: str) -> list[str]:
    auditor = _HtmlAuditor()
    auditor.feed(render_markdown(md))
    return auditor.violations


_XSS_PAYLOADS = [
    "<script>window.x=1</script>",
    "<img src=x onerror=window.x=1>",
    "<svg/onload=window.x=1>",
    "<details open ontoggle=window.x=1>x</details>",
    "<iframe srcdoc='<script>1</script>'></iframe>",
    "[click](javascript:window.x=1)",
    '<a href="javascript:window.x=1">x</a>',
    '<a href="data:text/html,<script>1</script>">x</a>',
    "prose before <script>bad()</script> prose after",
    "| <img src=x onerror=1> | second cell |\n| --- | --- |\n| a | b |",  # table cell
    "- <script>list item</script>",                                        # list item
    "# <img src=x onerror=1> heading",                                     # heading
    "> <script>quote</script>",                                            # blockquote-ish
    "```\n<script>code block</script>\n```",                               # fenced code
]


def test_render_markdown_neutralizes_xss_payloads():
    """No payload — in any block context — may emit a live dangerous tag, an
    on* handler, or a javascript:/data: href into the v-html'd output."""
    for payload in _XSS_PAYLOADS:
        violations = _audit(payload)
        assert not violations, (
            f"render_markdown leaked a live XSS vector for {payload!r}: "
            f"{violations}\n  rendered: {render_markdown(payload)!r}"
        )


def test_render_markdown_escapes_raw_angle_brackets():
    """The defense is escape-first: a literal `<script>` must come back as the
    escaped entity, never a live tag. Pin the entity so a regression that drops
    html.escape (the load-bearing call) reds this, not just the parser audit."""
    out = render_markdown("<script>alert(1)</script>")
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_render_markdown_drops_javascript_link_scheme():
    """A markdown link is only turned into an <a> for an http(s) URL; a
    javascript: target must NOT become an anchor (stays inert text)."""
    out = render_markdown("[click](javascript:alert(1))")
    assert "<a " not in out
    assert "javascript:" in out  # present, but as escaped/inert text, not an href


def test_render_markdown_keeps_legitimate_formatting():
    """The escaping must not break real markdown — bold/italic, https links, and
    inline code still render as their safe allowlisted tags."""
    out = render_markdown("normal **bold** and *em* and [ok](https://example.com) and `a|b`")
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out
    assert '<a href="https://example.com">ok</a>' in out
    assert "<code>a|b</code>" in out
    assert not _audit("normal **bold** [ok](https://example.com) `a|b`")
