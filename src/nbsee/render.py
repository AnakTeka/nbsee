"""Cell -> lines of Rich segments, plus the geometry overlays need. Rich only."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style
from rich.syntax import Syntax
from rich.text import Span, Text

from .ansi import sanitize, strip_background, to_plain
from .model import Cell, Output, Region

OUTPUT_CAP = 500  # source lines of output shown before "… N more lines"
TAB_SIZE = 4
MIN_WIDTH = 12
DEFAULT_THEME = "monokai"

HEADER_STYLE = Style(color="grey50")
BORDER_STYLE = Style(color="grey50")
ERROR_BORDER_STYLE = Style(color="red")
DIM_STYLE = Style(color="grey50", italic=True)

# Columns used by the panel frame: "│ " on the left, " │" on the right.
FRAME = 4
LEFT = 2

_CONSOLE = Console(color_system="truecolor", force_terminal=True, width=80)


# -- geometry ----------------------------------------------------------------


@dataclass(frozen=True)
class LineGeom:
    """Where one source line landed: its wrapped rows and column mapping."""

    row: int  # absolute row (within the cell) of the first wrapped row
    text: str  # display text (tabs expanded)
    starts: tuple[int, ...]  # display index at which each wrapped row begins
    tabs: tuple[int, ...] | None  # raw index -> display index when tabs exist

    @property
    def rows(self) -> int:
        return len(self.starts)

    def _display(self, col: int) -> int:
        col = max(0, col)
        if self.tabs is None:
            return min(col, len(self.text))
        return self.tabs[min(col, len(self.tabs) - 1)]

    def locate(self, col: int) -> tuple[int, int]:
        """(absolute row, x within the content area) for a raw column."""
        d = self._display(col)
        i = bisect_right(self.starts, d) - 1
        return self.row + i, cell_len(self.text[self.starts[i] : d])

    def spans(self, start: int, end: int) -> list[tuple[int, int, int]]:
        """(row, x0, x1) pieces covering raw columns [start, end)."""
        d0, d1 = self._display(start), self._display(end)
        first = bisect_right(self.starts, d0) - 1
        out: list[tuple[int, int, int]] = []
        for i in range(first, len(self.starts)):
            row_start = self.starts[i]
            row_end = self.starts[i + 1] if i + 1 < len(self.starts) else d1
            if row_start >= d1 and i > first:
                break
            a, b = max(d0, row_start), min(d1, row_end)
            x0 = cell_len(self.text[row_start:a])
            out.append((self.row + i, x0, x0 + cell_len(self.text[a:b])))
        return out


@dataclass(frozen=True)
class Block:
    """A rendered region (input or output) of a cell."""

    region: Region
    panel: range  # absolute rows including the top and bottom border
    lines: tuple[LineGeom, ...]  # shown source lines, in order
    total: int  # source lines in the region (may exceed len(lines))

    @property
    def content(self) -> range:
        return range(self.panel.start + 1, self.panel.stop - 1)

    def line_at(self, row: int) -> int | None:
        """Source line shown on an absolute row, if any."""
        if not self.lines or row not in self.content:
            return None
        i = bisect_right([g.row for g in self.lines], row) - 1
        g = self.lines[i]
        return i if row < g.row + g.rows else None


@dataclass(frozen=True)
class RenderedCell:
    lines: tuple[tuple[Segment, ...], ...]  # each exactly `width` cells wide
    width: int
    input: Block
    output: Block | None
    hidden_row: int | None  # row of the one-line "[output hidden]"
    exact: bool  # False when source lines cannot be mapped to rows (Markdown)

    @property
    def height(self) -> int:
        return len(self.lines)

    @property
    def plain(self) -> list[str]:
        return ["".join(s.text for s in line) for line in self.lines]

    def block(self, region: Region) -> Block | None:
        return self.input if region is Region.INPUT else self.output

    def locate_row(self, row: int) -> tuple[Region, int] | None:
        """(region, source line) shown on a row, for exact cells."""
        for blk in (self.input, self.output):
            if blk is not None and (i := blk.line_at(row)) is not None:
                return blk.region, i
        return None


# -- text preparation --------------------------------------------------------

_CONTROL = {c: chr(0x2400 + c) for c in range(32) if c != 9}
_CONTROL[0x7F] = "␡"
_CONTROL.update({c: "·" for c in range(0x80, 0xA0)})


def _expand_tabs(raw: str) -> tuple[str, tuple[int, ...] | None]:
    """Replace controls and tabs; map raw indices to display indices if needed."""
    text = raw.translate(_CONTROL)
    if "\t" not in text:
        return text, None
    out: list[str] = []
    mapping: list[int] = []
    cells = size = 0
    for ch in text:
        mapping.append(size)
        piece = " " * (TAB_SIZE - cells % TAB_SIZE) if ch == "\t" else ch
        out.append(piece)
        size += len(piece)
        cells += cell_len(piece)
    mapping.append(size)
    return "".join(out), tuple(mapping)


def wrap_starts(text: str, width: int, *, words: bool = True) -> list[int]:
    """Indices where each wrapped row starts; word-aware or hard-folded."""
    starts = [0]
    if cell_len(text) <= width:
        return starts
    row = col = i = 0
    space = -1
    while i < len(text):
        ch = text[i]
        w = cell_len(ch)
        if col + w > width and i > row and ch != " ":
            cut = space + 1 if words and space >= row else i
            starts.append(cut)
            row, i, col, space = cut, cut, 0, -1
            continue
        col += w
        if ch == " ":
            space = i
        i += 1
    return starts


def _split_lines(text: Text) -> list[Text]:
    """Split like `str.splitlines`, keeping styles (matches `Cell.lines`)."""
    pieces = text.plain.splitlines(keepends=True)
    if not pieces:
        return []
    offsets, pos = [], 0
    for piece in pieces[:-1]:
        pos += len(piece)
        offsets.append(pos)
    lines = list(text.divide(offsets)) if offsets else [text]
    for line, piece in zip(lines, pieces, strict=True):
        extra = len(piece) - len(piece.splitlines()[0])
        if extra:
            line.right_crop(extra)
    return lines


def _plain_lines(lines: list[str]) -> list[tuple[Text, tuple[int, ...] | None]]:
    out = []
    for raw in lines:
        shown, tabs = _expand_tabs(raw)
        out.append((Text(shown), tabs))
    return out


def _foreground(style: Style) -> Style:
    return Style(
        color=style.color,
        bold=style.bold,
        italic=style.italic,
        underline=style.underline,
        dim=style.dim,
    )


def _without_background(text: Text) -> Text:
    """Drop the theme's background so the terminal's own shows through."""
    if isinstance(text.style, Style):
        text.style = _foreground(text.style)
    text.spans = [
        Span(sp.start, sp.end, _foreground(sp.style))
        if isinstance(sp.style, Style)
        else sp
        for sp in text.spans
    ]
    return text


def _highlighted_lines(
    lines: list[str], language: str, theme: str
) -> list[tuple[Text, tuple[int, ...] | None]]:
    prepared = [_expand_tabs(line) for line in lines]
    code = "\n".join(p[0] for p in prepared) + "\n"
    highlighted = Syntax(code, language, theme=theme).highlight(code)
    pieces = _split_lines(_without_background(highlighted))
    if len(pieces) != len(lines):
        return _plain_lines(lines)
    return [(piece, p[1]) for piece, p in zip(pieces, prepared, strict=True)]


def _styled(raw: str, plain: str) -> Text:
    """Colored Text for sanitized `raw`, guaranteed to carry exactly `plain`."""
    styled = Text.from_ansi(raw)
    if not plain.startswith(styled.plain):
        return Text(plain)
    if len(plain) > len(styled.plain):  # from_ansi drops one trailing newline
        styled.append(plain[len(styled.plain) :])
    return styled


def _output_text(out: Output, plain: str, cap: int | None) -> tuple[Text, bool]:
    """Styled text of one output; cut to `cap` lines when it is longer."""
    raw = sanitize(out.text or "")
    if out.kind == "error":
        raw = strip_background(raw)
    if cap is not None and raw.count("\n") >= cap:
        raw = "\n".join(raw.split("\n", cap)[:cap])
        return _styled(raw, to_plain(raw)), True
    return _styled(raw, plain), False


def _output_lines(
    cell: Cell, plains: list[tuple[Output, str]], cap: int | None
) -> tuple[list[tuple[Text, tuple[int, ...] | None]], int]:
    """Styled lines of the output text (first `cap`) and the total line count."""
    texts: list[Text] = []
    cut = False
    for out, plain in plains:
        if not plain:
            continue
        text, cut = _output_text(out, plain, cap)
        texts.append(text)
        if cut:
            break
    lines = _split_lines(Text("\n").join(texts))
    total = len(cell.lines(Region.OUTPUT)) if cut else len(lines)
    prepared = []
    for line in lines if cap is None else lines[:cap]:
        tabs = _expand_tabs(line.plain)[1]
        if tabs is not None:
            line.expand_tabs(TAB_SIZE)
        prepared.append((line, tabs))
    return prepared, total


# -- row building -----------------------------------------------------------


def _segments(text: Text) -> list[Segment]:
    return list(text.render(_CONSOLE))


def _pad(line: list[Segment], width: int) -> list[Segment]:
    return Segment.adjust_line_length(line, width)


def _wrapped_rows(
    prepared: list[tuple[Text, tuple[int, ...] | None]],
    cw: int,
    *,
    words: bool,
    first_row: int,
) -> tuple[list[list[Segment]], list[LineGeom]]:
    rows: list[list[Segment]] = []
    geoms: list[LineGeom] = []
    for text, tabs in prepared:
        starts = wrap_starts(text.plain, cw, words=words)
        parts = text.divide(starts[1:]) if len(starts) > 1 else [text]
        geoms.append(LineGeom(first_row + len(rows), text.plain, tuple(starts), tabs))
        rows.extend(_pad(_segments(part), cw) for part in parts)
    return rows, geoms


def _frame_top(width: int, title: str, style: Style) -> list[Segment]:
    label = f" {title} " if title else ""
    bar = "─" * max(0, width - 2 - cell_len(label))
    return [Segment(f"╭{label}{bar}╮", style)]


def _frame_bottom(width: int, style: Style) -> list[Segment]:
    return [Segment("╰" + "─" * (width - 2) + "╯", style)]


def _frame_row(content: list[Segment], style: Style) -> list[Segment]:
    return [Segment("│ ", style), *content, Segment(" │", style)]


def _panel(
    content: list[list[Segment]],
    width: int,
    title: str,
    style: Style,
) -> list[list[Segment]]:
    cw = width - FRAME
    blank = [Segment(" " * cw)]
    body = content or [blank]
    return [
        _frame_top(width, title, style),
        *(_frame_row(row, style) for row in body),
        _frame_bottom(width, style),
    ]


def _header(cell: Cell, width: int) -> list[Segment]:
    label = f"[{cell.index + 1}] {cell.kind}"
    if cell.kind == "code" and cell.execution_count is not None:
        label += f"  In[{cell.execution_count}]"
    return _pad([Segment("  "), Segment(label, HEADER_STYLE)], width)


def _placeholder_rows(plains: list[tuple[Output, str]], cw: int) -> list[list[Segment]]:
    rows = []
    for out, plain in plains:
        label = None if plain else out.placeholder
        if label:
            parts, _ = _wrapped_rows(
                [(Text(label, DIM_STYLE), None)], cw, words=False, first_row=0
            )
            rows.extend(parts)
    return rows


def _markdown_rows(source: str, cw: int) -> list[list[Segment]]:
    opts = _CONSOLE.options.update(width=cw, height=None)
    rows = _CONSOLE.render_lines(
        Markdown(source, hyperlinks=False), opts, pad=True, new_lines=False
    )
    return [list(r) for r in rows]


def _input_block(
    cell: Cell, language: str, cw: int, theme: str, raw: bool
) -> tuple[list[list[Segment]], list[LineGeom], bool]:
    """Content rows, line geometry (relative to the panel), exactness."""
    lines = cell.lines(Region.INPUT)
    if cell.kind == "markdown" and not raw:
        return _markdown_rows(cell.source, cw), [], False
    if cell.kind == "code":
        prepared = _highlighted_lines(lines, language, theme)
    else:
        prepared = _plain_lines(lines)
    rows, geoms = _wrapped_rows(prepared, cw, words=True, first_row=0)
    return rows, geoms, True


def _shift(geoms: list[LineGeom], base: int) -> tuple[LineGeom, ...]:
    return tuple(LineGeom(g.row + base, g.text, g.starts, g.tabs) for g in geoms)


def render_cell(
    cell: Cell,
    language: str,
    width: int,
    *,
    markdown_raw: bool = False,
    folded: bool = False,
    theme: str = DEFAULT_THEME,
    expand_output: bool = False,
) -> RenderedCell:
    """Render a cell to exactly-`width`-wide lines plus overlay geometry."""
    width = max(width, MIN_WIDTH)
    cw = width - FRAME
    lines: list[list[Segment]] = [_header(cell, width)]

    rows, geoms, exact = _input_block(cell, language, cw, theme, markdown_raw)
    panel = _panel(rows, width, "", BORDER_STYLE)
    in_block = Block(
        Region.INPUT,
        range(len(lines), len(lines) + len(panel)),
        _shift(geoms, len(lines) + 1),
        len(cell.lines(Region.INPUT)),
    )
    lines.extend(panel)

    out_block: Block | None = None
    hidden_row: int | None = None
    plains = [(o, o.plain) for o in cell.outputs]
    if any(plain or o.placeholder for o, plain in plains):
        if folded:
            hidden_row = len(lines)
            lines.append(_pad([Segment("  [output hidden]", DIM_STYLE)], width))
        else:
            panel_rows, out_block = _output_block(
                cell, plains, width, len(lines), expand_output
            )
            lines.extend(panel_rows)
    lines.append(_pad([], width))
    return RenderedCell(
        lines=tuple(tuple(line) for line in lines),
        width=width,
        input=in_block,
        output=out_block,
        hidden_row=hidden_row,
        exact=exact,
    )


def _output_block(
    cell: Cell, plains: list[tuple[Output, str]], width: int, base: int, expand: bool
) -> tuple[list[list[Segment]], Block]:
    cw = width - FRAME
    cap = None if expand else OUTPUT_CAP
    prepared, total = _output_lines(cell, plains, cap)
    rows, geoms = _wrapped_rows(prepared, cw, words=False, first_row=base + 1)
    if total > len(prepared):
        more = Text(f"… {total - len(prepared)} more lines", DIM_STYLE)
        rows.append(_pad(_segments(more), cw))
    rows.extend(_placeholder_rows(plains, cw))
    style = ERROR_BORDER_STYLE if cell.has_error else BORDER_STYLE
    panel = _panel(rows, width, "Error" if cell.has_error else "Output", style)
    block = Block(Region.OUTPUT, range(base, base + len(panel)), tuple(geoms), total)
    return panel, block


# -- measuring ---------------------------------------------------------------


def _wrapped_count(lines: list[str], cw: int, *, words: bool) -> int:
    return sum(
        len(wrap_starts(_expand_tabs(line)[0], cw, words=words)) for line in lines
    )


def measure_height(
    cell: Cell,
    width: int,
    *,
    markdown_raw: bool = False,
    folded: bool = False,
    expand_output: bool = False,
) -> int:
    """Exact `render_cell(...).height` without highlighting or styling text."""
    cw = max(width, MIN_WIDTH) - FRAME
    if cell.kind == "markdown" and not markdown_raw:
        body = len(_markdown_rows(cell.source, cw))
    else:
        body = _wrapped_count(cell.lines(Region.INPUT), cw, words=True)
    rows = 1 + 2 + max(body, 1) + 1  # header, input frame + body, spacer
    plains = [(o, o.plain) for o in cell.outputs]
    if not any(plain or o.placeholder for o, plain in plains):
        return rows
    if folded:
        return rows + 1
    lines = cell.lines(Region.OUTPUT)
    shown = lines if expand_output else lines[:OUTPUT_CAP]
    body = _wrapped_count(shown, cw, words=False) + (len(lines) > len(shown))
    for out, plain in plains:
        label = None if plain else out.placeholder
        if label:
            body += len(wrap_starts(label, cw, words=False))
    return rows + 2 + max(body, 1)


# -- estimates ---------------------------------------------------------------


def estimate_height(cell: Cell, width: int, *, folded: bool = False) -> int:
    """Cheap row estimate (ignores wrapping and never sanitizes text)."""
    del width  # wrapping is ignored on purpose
    source_lines = cell.source.count("\n") + 1 if cell.source else 1
    rows = 1 + source_lines + 2 + 1  # header, input panel, spacer
    if not cell.outputs:
        return rows
    if folded:
        return rows + 1
    lines = sum(o.text.count("\n") + 1 if o.text else 1 for o in cell.outputs)
    return rows + min(lines, OUTPUT_CAP + 1) + 2
