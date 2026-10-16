"""Pure view state and transitions. No Textual or Rich imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .model import Cell, Notebook, Region
from .search import Hit, find


class Mode(Enum):
    NORMAL = "normal"
    VISUAL = "visual"
    LINE = "line"
    LINE_VISUAL = "line-visual"


_LINE_MODES = (Mode.LINE, Mode.LINE_VISUAL)


@dataclass(frozen=True)
class Search:
    query: str
    hits: tuple[Hit, ...]
    current: int | None  # index into hits


@dataclass(frozen=True)
class ViewState:
    cursor: int = 0  # cell index
    region: Region = Region.INPUT
    mode: Mode = Mode.NORMAL
    anchor: int = 0  # cell anchor (VISUAL) or line anchor (LINE_VISUAL)
    line: int = 0  # line cursor within the region (LINE modes)
    outputs_hidden: bool = False
    folded: frozenset[int] = frozenset()
    markdown_raw: bool = False
    search: Search | None = None


# -- helpers ---------------------------------------------------------------


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def _cell(s: ViewState, nb: Notebook) -> Cell | None:
    if not nb.cells:
        return None
    return nb.cells[_clamp(s.cursor, 0, len(nb.cells) - 1)]


def _line_count(s: ViewState, nb: Notebook) -> int:
    cell = _cell(s, nb)
    return len(cell.lines(s.region)) if cell else 0


def _order(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


# -- cell navigation -------------------------------------------------------


def _set_cursor(s: ViewState, nb: Notebook, index: int) -> ViewState:
    if not nb.cells:
        return s
    new = _clamp(index, 0, len(nb.cells) - 1)
    if new == s.cursor:
        return s
    region = s.region if s.mode is Mode.VISUAL else Region.INPUT
    if s.mode in _LINE_MODES:  # leaving the cell leaves line mode
        return replace(
            s, cursor=new, region=region, mode=Mode.NORMAL, anchor=new, line=0
        )
    return replace(s, cursor=new, region=region, line=0)


def move_cell(s: ViewState, nb: Notebook, delta: int) -> ViewState:
    return _set_cursor(s, nb, s.cursor + delta)


def goto_cell(s: ViewState, nb: Notebook, index: int) -> ViewState:
    """Jump to a 0-based cell; negative counts from the end."""
    if index < 0:
        index += len(nb.cells)
    return _set_cursor(s, nb, index)


def toggle_region(s: ViewState, nb: Notebook) -> ViewState:
    cell = _cell(s, nb)
    if cell is None:
        return s
    if s.region is Region.OUTPUT:
        target = Region.INPUT
    elif s.mode is Mode.VISUAL:
        if not any(c.has_output_text for c in _range_cells(s, nb)):
            return s
        target = Region.OUTPUT
    elif cell.has_output_text:
        target = Region.OUTPUT
    else:
        return s
    out = replace(s, region=target)
    if s.mode in _LINE_MODES:
        out = replace(out, mode=Mode.LINE, line=0, anchor=0)
    return out


def toggle_fold(s: ViewState, nb: Notebook) -> ViewState:
    cell = _cell(s, nb)
    if cell is None:
        return s
    return replace(s, folded=s.folded ^ {cell.index})


def toggle_all_outputs(s: ViewState) -> ViewState:
    return replace(s, outputs_hidden=not s.outputs_hidden)


def toggle_markdown_raw(s: ViewState) -> ViewState:
    return replace(s, markdown_raw=not s.markdown_raw)


# -- modes -----------------------------------------------------------------


def enter_visual(s: ViewState) -> ViewState:
    if s.mode is Mode.NORMAL:
        return replace(s, mode=Mode.VISUAL, anchor=s.cursor)
    if s.mode is Mode.VISUAL:
        return replace(s, mode=Mode.NORMAL, anchor=s.cursor)
    return s


def enter_line(s: ViewState, nb: Notebook) -> ViewState:
    if s.mode is not Mode.NORMAL or _line_count(s, nb) == 0:
        return s
    return replace(s, mode=Mode.LINE, line=0, anchor=0)


def start_line_visual(s: ViewState) -> ViewState:
    if s.mode is Mode.LINE:
        return replace(s, mode=Mode.LINE_VISUAL, anchor=s.line)
    if s.mode is Mode.LINE_VISUAL:
        return replace(s, mode=Mode.LINE, anchor=s.line)
    return s


def escape(s: ViewState) -> ViewState:
    if s.mode is Mode.LINE_VISUAL:
        return replace(s, mode=Mode.LINE, anchor=s.line)
    if s.mode is Mode.LINE:
        return replace(s, mode=Mode.NORMAL, line=0, anchor=s.cursor)
    if s.mode is Mode.VISUAL:
        return replace(s, mode=Mode.NORMAL, anchor=s.cursor)
    return clear_search(s)


def _set_line(s: ViewState, nb: Notebook, index: int) -> ViewState:
    if s.mode not in _LINE_MODES:
        return s
    count = _line_count(s, nb)
    if count == 0:
        return s
    return replace(s, line=_clamp(index, 0, count - 1))


def move_line(s: ViewState, nb: Notebook, delta: int) -> ViewState:
    return _set_line(s, nb, s.line + delta)


def goto_line(s: ViewState, nb: Notebook, index: int) -> ViewState:
    """Jump to a 0-based line; negative counts from the end."""
    if index < 0:
        index += _line_count(s, nb)
    return _set_line(s, nb, index)


def visual_range(s: ViewState) -> tuple[int, int]:
    if s.mode is Mode.VISUAL:
        return _order(s.anchor, s.cursor)
    return (s.cursor, s.cursor)


def line_range(s: ViewState) -> tuple[int, int]:
    if s.mode is Mode.LINE_VISUAL:
        return _order(s.anchor, s.line)
    return (s.line, s.line)


# -- search ----------------------------------------------------------------


def _rank(cell: int, region: Region, line: int, start: int = 0) -> tuple[int, ...]:
    return (cell, 1 if region is Region.OUTPUT else 0, line, start)


def _first_from(hits: tuple[Hit, ...], pos: tuple[int, ...]) -> int | None:
    """Index of the first hit at or after pos, wrapping; None without hits."""
    if not hits:
        return None
    for i, h in enumerate(hits):
        if _rank(h.cell, h.region, h.line, h.start) >= pos:
            return i
    return 0


def _cursor_pos(s: ViewState) -> tuple[int, ...]:
    line = s.line if s.mode in _LINE_MODES else 0
    return _rank(s.cursor, s.region, line)


def _goto_hit(s: ViewState, hit: Hit) -> ViewState:
    if s.mode in _LINE_MODES:
        if hit.cell == s.cursor and hit.region is s.region:
            return replace(s, line=hit.line)
        return replace(
            s,
            cursor=hit.cell,
            region=hit.region,
            mode=Mode.NORMAL,
            anchor=hit.cell,
            line=0,
        )
    region = s.region if s.mode is Mode.VISUAL else hit.region
    return replace(s, cursor=hit.cell, region=region, line=0)


def _with_current(s: ViewState, search: Search) -> ViewState:
    out = replace(s, search=search)
    if search.current is None:
        return out
    return _goto_hit(out, search.hits[search.current])


def set_search(s: ViewState, nb: Notebook, query: str) -> ViewState:
    if not nb.cells:
        return s
    if not query:
        return clear_search(s)
    hits = find(nb, query)
    current = _first_from(hits, _cursor_pos(s))
    return _with_current(s, Search(query, hits, current))


def search_step(s: ViewState, nb: Notebook, delta: int) -> ViewState:
    if not nb.cells or s.search is None or not s.search.hits:
        return s
    sr = s.search
    n = len(sr.hits)
    if sr.current is None:
        current = 0 if delta >= 0 else n - 1
    else:
        current = (sr.current + delta) % n
    return _with_current(s, replace(sr, current=current))


def clear_search(s: ViewState) -> ViewState:
    return replace(s, search=None) if s.search is not None else s


# -- reload ----------------------------------------------------------------


def on_reload(s: ViewState, nb: Notebook) -> ViewState:
    """Re-validate state against a freshly loaded notebook."""
    n = len(nb.cells)
    last = max(n - 1, 0)
    out = replace(
        s,
        cursor=_clamp(s.cursor, 0, last),
        anchor=_clamp(s.anchor, 0, last) if s.mode is Mode.VISUAL else s.anchor,
        folded=frozenset(i for i in s.folded if i < n),
    )
    cell = _cell(out, nb)
    if cell is None:
        search = s.search and Search(s.search.query, (), None)
        return replace(
            out, region=Region.INPUT, mode=Mode.NORMAL, anchor=0, line=0, search=search
        )
    if (
        out.region is Region.OUTPUT
        and out.mode is not Mode.VISUAL
        and not cell.has_output_text
    ):
        out = replace(out, region=Region.INPUT)
    if out.mode in _LINE_MODES:
        count = _line_count(out, nb)
        if count == 0:
            out = replace(out, mode=Mode.NORMAL, line=0, anchor=out.cursor)
        else:
            out = replace(
                out,
                line=_clamp(out.line, 0, count - 1),
                anchor=_clamp(out.anchor, 0, count - 1)
                if out.mode is Mode.LINE_VISUAL
                else out.anchor,
            )
    if s.search is not None:
        hits = find(nb, s.search.query)
        current = _first_from(hits, _cursor_pos(out))
        out = replace(out, search=Search(s.search.query, hits, current))
    return out


# -- yank ------------------------------------------------------------------


def _clean(text: str) -> str:
    return text.rstrip("\n")


def _join(pieces: list[str]) -> str:
    return "\n\n".join(p for p in map(_clean, pieces) if p.strip())


def _range_cells(s: ViewState, nb: Notebook) -> tuple[Cell, ...]:
    if not nb.cells:
        return ()
    lo, hi = visual_range(s)
    last = len(nb.cells) - 1
    lo, hi = _clamp(lo, 0, last), _clamp(hi, 0, last)
    return nb.cells[lo : hi + 1]


def _selected_lines(s: ViewState, nb: Notebook) -> str:
    cell = _cell(s, nb)
    lines = cell.lines(s.region) if cell else []
    if not lines:
        return ""
    lo, hi = line_range(s)
    return "\n".join(
        lines[_clamp(lo, 0, len(lines) - 1) : _clamp(hi, 0, len(lines) - 1) + 1]
    )


def _cell_all(cell: Cell) -> list[str]:
    pieces = [cell.source]
    if cell.has_output_text:
        pieces.append(cell.output_text)
    return pieces


def yank(s: ViewState, nb: Notebook) -> str:
    """Text for `y`: the current region, or the selection in a mode."""
    cell = _cell(s, nb)
    if cell is None:
        return ""
    if s.mode in _LINE_MODES:
        return _selected_lines(s, nb)
    if s.mode is Mode.VISUAL:
        cells = _range_cells(s, nb)
        if s.region is Region.INPUT:
            return _join([c.source for c in cells if c.kind == "code"])
        return _join([c.output_text for c in cells if c.has_output_text])
    return _clean(cell.text(s.region))


def yank_all(s: ViewState, nb: Notebook) -> str:
    """Text for `Y`: whole cell(s) in order, input then output."""
    cell = _cell(s, nb)
    if cell is None:
        return ""
    if s.mode in _LINE_MODES:
        return _clean(cell.text(s.region))
    cells = _range_cells(s, nb) if s.mode is Mode.VISUAL else (cell,)
    return _join([p for c in cells for p in _cell_all(c)])


def selection_text(s: ViewState, nb: Notebook) -> str:
    """Exactly what `y` would copy."""
    return yank(s, nb)
