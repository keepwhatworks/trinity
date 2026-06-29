from __future__ import annotations

import html
import re


def _render_inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            rendered.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        escaped = html.escape(part)
        escaped = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
            escaped,
        )
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
        rendered.append(escaped)
    return "".join(rendered)


_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def _split_table_cells(line: str) -> list[str]:
    """Split a markdown table row on cell-delimiter pipes only.

    Pipes inside an inline-code span (`` `curl | bash` ``) and
    backslash-escaped pipes (``\\|``) are literal content, NOT column
    delimiters. A naive ``line.split("|")`` shatters a cell like
    `` `curl | bash` `` into two and shifts every column after it — which is
    exactly how a council member's `curl | bash`-vs-packages comparison
    table rendered as garbled 3-column soup on the review page. Mirrors GFM
    table semantics. For a row with no code spans / escapes this returns the
    same list as ``line.split("|")``.
    """
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n and line[i + 1] == "|":
            buf.append("|")  # escaped pipe → literal, not a delimiter
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
            buf.append(ch)
        elif ch == "|" and not in_code:
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf))
    return cells


def _is_table_row(line: str) -> bool:
    """A line is a markdown table row if it has 2+ delimiter pipes, or
    starts with `|`. Single-pipe lines (e.g. `apple | banana` in prose)
    don't qualify — those stay as paragraphs. Pipes inside inline code
    (`` `a | b` ``) and escaped pipes don't count toward the delimiter
    total, so a sentence mentioning `` `curl | bash` `` isn't mistaken for
    a one-column table.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("|"):
        return True
    return len(_split_table_cells(stripped)) - 1 >= 2


def _is_table_separator(line: str) -> bool:
    """Detect the `|---|---|` separator that follows a table header."""
    return bool(_TABLE_SEPARATOR_RE.match(line))


def render_markdown(text: str | None, *, heading_offset: int = 2) -> str:
    """Render a small markdown subset to HTML.

    ``heading_offset`` demotes the PROGRAMMATIC level of every content heading
    (via ``aria-level``) without changing the rendered tag — so the visual size
    is byte-identical but a screen reader never hears a content ``# Heading`` as
    a page-level ``<h1>``. This content markdown is always injected INSIDE a page
    that already owns its own structural headings (the council review page's
    ``<h1>`` question + ``<h2>`` section openers; the live council page's ``<h1>``
    task + ``<h2>`` rows). Without the offset, a chairman synthesis or member
    response that opens with ``# Verdict`` emits a literal ``<h1>`` mid-page —
    a SECOND/THIRD competing ``<h1>`` and a top-level outline break a screen-
    reader user hits navigating by heading (WCAG 1.3.1 / 2.4.6). The default
    offset of 2 nests content headings at level 3+ (under the typical ``<h2>``
    section that contains them); the visible ``<hN>`` tag is unchanged so the
    font-size/weight the CSS keys on is preserved exactly.
    """
    raw = (text or "").strip()
    if not raw:
        return '<p class="text-muted">(none)</p>'

    lines = raw.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None
    in_code = False
    code_lines: list[str] = []
    in_table = False
    table_rows: list[list[str]] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if not paragraph:
            return
        out.append(f"<p>{_render_inline(' '.join(line.strip() for line in paragraph))}</p>")
        paragraph = []

    def flush_list() -> None:
        nonlocal list_items, list_kind
        if not list_items or not list_kind:
            return
        items_html = "".join(f"<li>{item}</li>" for item in list_items)
        out.append(f"<{list_kind}>{items_html}</{list_kind}>")
        list_items = []
        list_kind = None

    def flush_table() -> None:
        nonlocal in_table, table_rows
        if not in_table or not table_rows:
            return
        rows_html = ""
        for idx, row in enumerate(table_rows):
            # Strip leading/trailing empty cells caused by leading/trailing pipes
            # ("| a | b |".split("|") -> ["", " a ", " b ", ""])
            cells = [cell.strip() for cell in row]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if not cells:
                continue
            if idx == 0:
                rows_html += "<thead><tr>" + "".join(f"<th>{_render_inline(cell)}</th>" for cell in cells) + "</tr></thead>"
            else:
                rows_html += "<tr>" + "".join(f"<td>{_render_inline(cell)}</td>" for cell in cells) + "</tr>"
        out.append(f"<table>{rows_html}</table>")
        in_table = False
        table_rows = []

    for line in lines:
        stripped = line.rstrip()
        marker = stripped.strip()

        if marker.startswith("```"):
            flush_paragraph()
            flush_list()
            flush_table()
            if in_code:
                out.append(f"<pre class=\"md-code-block\"><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code_lines.append(stripped)
            continue

        if not marker:
            flush_paragraph()
            flush_list()
            flush_table()
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", marker)
        if heading:
            flush_paragraph()
            flush_list()
            flush_table()
            level = len(heading.group(1))
            # Keep the VISIBLE tag (and thus the exact font-size the CSS / browser
            # default gives it) but carry a demoted aria-level so AT never reads a
            # content heading as a page-level <h1> competing with the page's real
            # heading. min(6, …) clamps to the valid heading range.
            aria_level = min(6, level + heading_offset)
            level_attr = f' aria-level="{aria_level}"' if aria_level != level else ""
            out.append(
                f"<h{level}{level_attr}>{_render_inline(heading.group(2).strip())}</h{level}>"
            )
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", marker)
        ordered = re.match(r"^\d+\.\s+(.*)$", marker)
        if bullet or ordered:
            flush_paragraph()
            flush_table()
            current_kind = "ul" if bullet else "ol"
            item_text = bullet.group(1) if bullet else ordered.group(1)
            if list_kind not in (None, current_kind):
                flush_list()
            list_kind = current_kind
            list_items.append(_render_inline(item_text.strip()))
            continue

        # Markdown table row detection. Tables can start with leading-pipe
        # rows (`| a | b |`) OR no-leading-pipe rows (`a | b | c`). The
        # separator line (`|---|---|`) marks the header but is not rendered.
        if _is_table_row(marker):
            if _is_table_separator(marker):
                # Separator row — only meaningful inside a table (right after
                # the header). Skip rendering. If no header preceded, fall
                # through and treat as paragraph text.
                if in_table:
                    continue
            else:
                if not in_table:
                    flush_paragraph()
                    flush_list()
                    in_table = True
                table_rows.append(_split_table_cells(marker))
                continue

        flush_table()
        paragraph.append(marker)

    if in_code:
        out.append(f"<pre class=\"md-code-block\"><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    flush_table()
    return "\n".join(out)
