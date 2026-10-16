"""Virtualized notebook view: one Line-API ScrollView, no widget per cell."""

from __future__ import annotations

import asyncio
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Callable, Iterable
from operator import attrgetter
from time import monotonic

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ..model import Notebook, Region
from ..render import (
    LEFT,
    OUTPUT_CAP,
    RenderedCell,
    estimate_height,
    measure_height,
    render_cell,
)
from ..search import Hit, case_sensitive
from ..state import Mode, Search, ViewState, line_range, visual_range
from .layout import Layout

CACHE_SIZE = 96  # rendered cells kept (LRU)
MARGIN_CELLS = 1  # extra cells rendered above/below the viewport
REFINE_SLICE = 0.004  # seconds of background measuring per chunk
REFINE_PAUSE = 0.002

FOCUS = Style(color="cyan", bold=True)
VISUAL_BG = Style(bgcolor="#262b3d")
LINE_BG = Style(bgcolor="#34405e")
LINE_VISUAL_BG = Style(bgcolor="#3f5a8f")
HIT = Style(bgcolor="yellow", color="black")
CURRENT_HIT = Style(bgcolor="dark_orange", color="black", bold=True)
CURSOR_MARK = Style(color="magenta", bold=True)
RANGE_MARK = Style(color="yellow", bold=True)

Spans = dict[int, list[tuple[int, int, bool]]]  # row -> (x0, x1, is_current)
_LINE_MODES = (Mode.LINE, Mode.LINE_VISUAL)
_by_cell = attrgetter("cell")


class _Entry:
    """A rendered cell plus per-row Strips and search spans, built lazily."""

    __slots__ = ("hit_rows", "hit_search", "rc", "strips")

    def __init__(self, rc: RenderedCell) -> None:
        self.rc = rc
        self.strips: list[Strip | None] = [None] * rc.height
        self.hit_search: Search | None = None
        self.hit_rows: Spans = {}

    def strip(self, row: int) -> Strip:
        strip = self.strips[row]
        if strip is None:
            strip = self.strips[row] = Strip(self.rc.lines[row], self.rc.width)
        return strip


# -- strip helpers -------------------------------------------------------------


def _tint(strip: Strip, style: Style) -> Strip:
    segments = Segment.apply_style(strip, post_style=style)
    return Strip(segments, strip.cell_length)


def _restyle(strip: Strip, spans: Iterable[tuple[int, int, Style]]) -> Strip:
    """Apply a style on top of each (x0, x1) span; spans must not overlap."""
    parts: list[Strip] = []
    pos = 0
    for x0, x1, style in sorted(spans, key=lambda s: s[0]):
        parts.append(strip.crop(pos, x0))
        parts.append(_tint(strip.crop(x0, x1), style))
        pos = max(pos, x1)
    parts.append(strip.crop(pos))
    return Strip.join(parts)


def _marker(strip: Strip, glyph: str, style: Style) -> Strip:
    return Strip.join([Strip([Segment(glyph + " ", style)], 2), strip.crop(2)])


def _search_spans(rc: RenderedCell, search: Search, first: int, last: int) -> Spans:
    """Row spans for hits[first:last] (all in one cell)."""
    rows: Spans = {}
    if not rc.exact:
        return _approx_spans(rc, search, first, last)
    for k in range(first, last):
        hit = search.hits[k]
        block = rc.block(hit.region)
        if block is None or hit.line >= len(block.lines):
            continue
        for row, x0, x1 in block.lines[hit.line].spans(hit.start, hit.end):
            rows.setdefault(row, []).append((LEFT + x0, LEFT + x1, k == search.current))
    return rows


def _approx_spans(rc: RenderedCell, search: Search, first: int, last: int) -> Spans:
    """Markdown rendered as rich text: match the query in the visible rows."""
    sensitive = case_sensitive(search.query)
    needle = search.query if sensitive else search.query.lower()
    current = None if search.current is None else search.current - first
    rows: Spans = {}
    count = 0
    for row, text in enumerate(rc.plain):
        hay = text if sensitive else text.lower()
        pos = hay.find(needle)
        while pos != -1 and count < last - first:
            x0 = cell_len(text[:pos])
            x1 = x0 + cell_len(text[pos : pos + len(needle)])
            rows.setdefault(row, []).append((x0, x1, count == current))
            count += 1
            pos = hay.find(needle, pos + len(needle))
    return rows


class NotebookView(ScrollView, can_focus=True, inherit_bindings=False):
    """Paints only visible lines; keypress cost does not depend on notebook size."""

    ALLOW_SELECT = False
    DEFAULT_CSS = """
    NotebookView {
        overflow-x: hidden;
        overflow-y: scroll;
        scrollbar-size-vertical: 1;
    }
    """

    def __init__(self, nb: Notebook, state: ViewState) -> None:
        super().__init__()
        self.nb = nb
        self.state = state
        self.table = Layout()
        self._cache: OrderedDict[tuple, _Entry] = OrderedDict()
        self._expanded: set[int] = set()  # cells whose output cap is lifted
        self._width = 0
        self._busy = False
        self._started = False

    # -- flags and cache --------------------------------------------------

    def _flags(self, state: ViewState, i: int) -> tuple[bool, bool, bool]:
        """(markdown raw, output folded, cap lifted) effective for a cell."""
        raw = state.markdown_raw or (state.mode in _LINE_MODES and i == state.cursor)
        return raw, state.outputs_hidden or i in state.folded, i in self._expanded

    def _estimate(self, i: int) -> int:
        folded = self._flags(self.state, i)[1]
        return estimate_height(self.nb.cells[i], self._width, folded=folded)

    def _render(self, i: int) -> RenderedCell:
        raw, folded, expand = self._flags(self.state, i)
        return render_cell(
            self.nb.cells[i],
            self.nb.language,
            self._width,
            markdown_raw=raw,
            folded=folded,
            expand_output=expand,
        )

    def _measure(self, i: int) -> int:
        raw, folded, expand = self._flags(self.state, i)
        return measure_height(
            self.nb.cells[i],
            self._width,
            markdown_raw=raw,
            folded=folded,
            expand_output=expand,
        )

    def _entry(self, i: int) -> _Entry:
        key = (i, self._width, *self._flags(self.state, i))
        entry = self._cache.get(key)
        if entry is None:
            entry = self._cache[key] = _Entry(self._render(i))
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return entry

    # -- layout and anchoring ---------------------------------------------

    def _anchor(self) -> tuple[int, int]:
        return self.table.locate(int(self.scroll_y))

    def _mutate(self, change: Callable[[], None]) -> None:
        """Change the layout while keeping the first visible row in place."""
        cell, offset = self._anchor()
        change()
        self.virtual_size = Size(self._width, self.table.total)
        if len(self.table):
            offset = min(offset, self.table.height(cell) - 1)
            y = self.table.top(cell) + offset
            if y != int(self.scroll_y):
                self.scroll_to(y=y, animate=False, force=True)

    def _apply(self, heights: dict[int, int]) -> None:
        if heights:
            self._mutate(lambda: self.table.set_heights(heights))

    def _reset_layout(self) -> None:
        cells = range(len(self.nb.cells))
        self._mutate(lambda: self.table.reset(self._estimate(i) for i in cells))
        self._start_refine()

    def _prepare(self) -> None:
        """Render cells around the viewport and record their real heights."""
        if self._busy or not self._width or not self.nb.cells:
            return
        self._busy = True
        try:
            for _ in range(3):  # anchoring may shift what is visible
                updates = self._measure_visible()
                if not updates:
                    break
                self._apply(updates)
        finally:
            self._busy = False

    def _measure_visible(self) -> dict[int, int]:
        y = int(self.scroll_y)
        height = max(self.scrollable_content_region.height, 1)
        first = max(self.table.locate(y)[0] - MARGIN_CELLS, 0)
        last = min(
            self.table.locate(y + height - 1)[0] + MARGIN_CELLS, len(self.nb.cells) - 1
        )
        updates = {}
        for i in range(first, last + 1):
            h = self._entry(i).rc.height
            if h != self.table.height(i) or not self.table.is_exact(i):
                updates[i] = h
        return updates

    # -- background refinement --------------------------------------------

    def _start_refine(self) -> None:
        if self.is_attached:
            self.run_worker(self._refine(), group="refine", exclusive=True)

    async def _refine(self) -> None:
        center = self.state.cursor
        todo = sorted(self.table.pending(), key=lambda i: -abs(i - center))
        while todo:
            deadline = monotonic() + REFINE_SLICE
            heights: dict[int, int] = {}
            while todo and monotonic() < deadline:
                i = todo.pop()
                if not self.table.is_exact(i):
                    heights[i] = self._measure(i)
            self._apply(heights)
            await asyncio.sleep(REFINE_PAUSE)

    # -- public API --------------------------------------------------------

    def load(self, nb: Notebook, state: ViewState) -> None:
        """Swap in a freshly loaded notebook (reload)."""
        self.nb, self.state = nb, state
        self._cache.clear()
        self._expanded.clear()
        if self._width:
            self._reset_layout()
            self._prepare()
        self.refresh()

    def set_state(
        self, state: ViewState, *, hit: Hit | None = None, reveal: bool = True
    ) -> None:
        old, self.state = self.state, state
        if self._width:
            self._lift_caps(hit)
            self._refresh_flags(old, state)
            self._prepare()
            if reveal:
                self._reveal_cursor(hit)
        self.refresh()

    def expand_output(self, i: int) -> None:
        """Lift the 500-line cap for a cell."""
        if i in self._expanded:
            return
        self._expanded.add(i)
        self._mutate(lambda: self.table.set_estimates({i: self._estimate(i)}))
        self._prepare()
        self.refresh()

    def scroll_lines(self, delta: int) -> None:
        self.scroll_to(y=int(self.scroll_y) + delta, animate=False, force=True)

    def page(self, pages: float) -> int:
        """Scroll by `pages` viewports; return the cell now at the cursor's row."""
        height = max(self.scrollable_content_region.height, 1)
        row = int(self.table.top(self.state.cursor) - self.scroll_y)
        row = max(0, min(row, height - 1))
        target = int(self.scroll_y) + round(pages * height)
        self.scroll_to(y=target, animate=False, force=True)
        self._prepare()
        return self.table.locate(int(self.scroll_y) + row)[0]

    def cell_at_top(self) -> int:
        return self.table.locate(int(self.scroll_y))[0]

    # -- state changes -----------------------------------------------------

    def _lift_caps(self, hit: Hit | None) -> None:
        s = self.state
        wanted = None
        if hit is not None and hit.region is Region.OUTPUT:
            wanted = hit.line
        elif s.mode in _LINE_MODES and s.region is Region.OUTPUT:
            wanted = s.line
        if wanted is not None and wanted >= OUTPUT_CAP:
            cell = hit.cell if hit is not None else s.cursor
            self._expanded.add(cell)
            self.table.set_estimates({cell: self._estimate(cell)})

    def _refresh_flags(self, old: ViewState, new: ViewState) -> None:
        cells = self.nb.cells
        changed: set[int] = {old.cursor, new.cursor}
        changed |= old.folded ^ new.folded
        if old.markdown_raw != new.markdown_raw:
            changed |= {c.index for c in cells if c.kind == "markdown"}
        if old.outputs_hidden != new.outputs_hidden:
            changed |= {c.index for c in cells if c.outputs}
        stale = {
            i: self._estimate(i)
            for i in changed
            if i < len(cells) and self._flags(old, i) != self._flags(new, i)
        }
        if stale:
            self._mutate(lambda: self.table.set_estimates(stale))

    def _reveal(self, top: int, bottom: int) -> None:
        """Scroll minimally so virtual rows top..bottom are visible."""
        height = max(self.scrollable_content_region.height, 1)
        y = int(self.scroll_y)
        if top < y or bottom - top + 1 >= height:
            target = top
        elif bottom >= y + height:
            target = bottom - height + 1
        else:
            return
        self.scroll_to(y=target, animate=False, force=True)
        self._prepare()

    def _reveal_cursor(self, hit: Hit | None) -> None:
        s = self.state
        if not self.nb.cells:
            return
        base = self.table.top(s.cursor)
        rc = self._entry(s.cursor).rc
        a, b = self._focus_rows(rc, hit)
        self._reveal(base + a, base + b)

    def _focus_rows(self, rc: RenderedCell, hit: Hit | None) -> tuple[int, int]:
        """Rows of the cell (first, last) that must be visible."""
        s = self.state
        whole = (0, rc.height - 2)
        if hit is not None and hit.cell == s.cursor and rc.exact:
            block = rc.block(hit.region)
            if block is not None and hit.line < len(block.lines):
                row = block.lines[hit.line].locate(hit.start)[0]
                return row, row
        if s.mode in _LINE_MODES:
            block = rc.block(s.region)
            if block is not None and s.line < len(block.lines):
                g = block.lines[s.line]
                return g.row, g.row + g.rows - 1
        return whole

    # -- events ------------------------------------------------------------

    def on_resize(self, event: events.Resize) -> None:
        width = self.scrollable_content_region.width
        if not width or width == self._width:
            return
        self._width = width
        self._reset_layout()
        if not self._started:
            self._started = True
            self.scroll_to(
                y=self.table.top(self.state.cursor), animate=False, force=True
            )
        self._prepare()
        self.refresh()

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        self._prepare()

    # -- painting ----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self._width
        row_y = y + int(self.scroll_offset.y)
        if not self.nb.cells or not width or row_y >= self.table.total:
            return Strip.blank(width)
        i, row = self.table.locate(row_y)
        entry = self._entry(i)
        if row >= entry.rc.height:
            return Strip.blank(width)
        return self._overlay(i, entry, row)

    def _overlay(self, i: int, entry: _Entry, row: int) -> Strip:
        s = self.state
        strip = entry.strip(row)
        lo, hi = visual_range(s)
        in_range = s.mode is Mode.VISUAL and lo <= i <= hi
        is_cursor = i == s.cursor
        if in_range:
            strip = _tint(strip, VISUAL_BG)
        if is_cursor or in_range:
            strip = self._focus_border(entry.rc, row, strip)
        if is_cursor and s.mode in _LINE_MODES:
            strip = self._line_overlay(entry.rc, row, strip)
        if s.search is not None:
            strip = self._hit_overlay(i, entry, row, strip)
        if row == 0 and (is_cursor or in_range):
            glyph, style = ("▶", CURSOR_MARK) if is_cursor else ("◆", RANGE_MARK)
            strip = _marker(strip, glyph, style)
        return strip

    def _focus_border(self, rc: RenderedCell, row: int, strip: Strip) -> Strip:
        block = rc.block(self.state.region)
        if block is None or row not in block.panel:
            return strip
        width = strip.cell_length
        if row in (block.panel.start, block.panel.stop - 1):
            return _restyle(strip, [(0, width, FOCUS)])
        return _restyle(strip, [(0, 1, FOCUS), (width - 1, width, FOCUS)])

    def _line_overlay(self, rc: RenderedCell, row: int, strip: Strip) -> Strip:
        s = self.state
        where = rc.locate_row(row)
        if where is None or where[0] is not s.region:
            return strip
        lo, hi = line_range(s)
        if not lo <= where[1] <= hi:
            return strip
        style = LINE_BG if s.mode is Mode.LINE else LINE_VISUAL_BG
        return _restyle(strip, [(LEFT, strip.cell_length - LEFT, style)])

    def _hit_overlay(self, i: int, entry: _Entry, row: int, strip: Strip) -> Strip:
        search = self.state.search
        assert search is not None
        if entry.hit_search is not search:
            first = bisect_left(search.hits, i, key=_by_cell)
            last = bisect_right(search.hits, i, key=_by_cell)
            entry.hit_rows = _search_spans(entry.rc, search, first, last)
            entry.hit_search = search
        spans = entry.hit_rows.get(row)
        if not spans:
            return strip
        return _restyle(
            strip, [(a, b, CURRENT_HIT if cur else HIT) for a, b, cur in spans]
        )
