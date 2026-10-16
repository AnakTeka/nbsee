from __future__ import annotations

from pathlib import Path

import pytest

from nbsee import model
from nbsee.model import Cell, Notebook, Output, Region, parse
from nbsee.render import (
    FRAME,
    LEFT,
    OUTPUT_CAP,
    estimate_height,
    measure_height,
    render_cell,
    wrap_starts,
)

FIXTURES = Path(__file__).parent / "fixtures"
IN, OUT = Region.INPUT, Region.OUTPUT


def code(source: str, *outputs: Output, index: int = 0, count: int | None = 1) -> Cell:
    return Cell(index, "code", source, tuple(outputs), count)


def stream(text: str) -> Output:
    return Output("stream", text, stream="stdout")


def render(cell: Cell, width: int = 30, **kw):
    return render_cell(cell, "python", width, **kw)


def span_text(rc, row: int, x0: int, x1: int) -> str:
    return rc.plain[row][x0:x1]


def test_code_cell_with_output_matches_expected_text():
    rc = render(code("x = 1\nprint(x)", stream("1\n")), width=20)
    assert rc.plain == [
        "  [1] code  In[1]   ",
        "╭──────────────────╮",
        "│ x = 1            │",
        "│ print(x)         │",
        "╰──────────────────╯",
        "╭ Output ──────────╮",
        "│ 1                │",
        "╰──────────────────╯",
        "                    ",
    ]
    assert rc.input.panel == range(1, 5)
    assert rc.output is not None and rc.output.panel == range(5, 8)


def test_markdown_cell_has_no_execution_count_and_is_not_exact():
    cell = Cell(0, "markdown", "# Title\n\nbody text")
    rc = render(cell)
    assert rc.plain[0].strip() == "[1] markdown"
    assert not rc.exact
    assert any("body text" in row for row in rc.plain)
    assert all("#" not in row for row in rc.plain[2:])


def test_markdown_raw_shows_source_and_is_exact():
    cell = Cell(0, "markdown", "# Title\n\nbody text")
    rc = render(cell, markdown_raw=True)
    assert rc.exact
    assert rc.plain[2].startswith("│ # Title")
    assert rc.input.lines[2].row == 4  # blank line 1 is row 3


def test_error_output_has_red_border_and_error_title():
    nb = model.load(FIXTURES / "errors.ipynb")
    rc = render_cell(nb.cells[0], nb.language, 60)
    assert "Error" in rc.plain[rc.output.panel.start]
    top = rc.lines[rc.output.panel.start][0]
    assert top.style is not None and top.style.color.name == "red"


def test_ansi_colors_are_kept_but_text_is_plain():
    cell = code("x", stream("\x1b[31mred\x1b[0m plain"))
    rc = render(cell)
    row = rc.output.content.start
    assert rc.plain[row].startswith("│ red plain")
    red = [s for s in rc.lines[row] if s.text == "red"]
    assert red and red[0].style.color is not None


def test_non_text_outputs_become_placeholders():
    nb = model.load(FIXTURES / "rich_outputs.ipynb")
    rc = render_cell(nb.cells[1], nb.language, 60)
    assert any(row.startswith("│ [image/") for row in rc.plain)
    assert rc.output is not None and rc.output.total == 0


def test_folded_output_is_a_single_hidden_row():
    cell = code("x", stream("a\nb\nc\n"))
    shown, hidden = render(cell), render(cell, folded=True)
    assert hidden.output is None
    assert hidden.plain[hidden.hidden_row].strip() == "[output hidden]"
    assert (
        hidden.height
        == shown.height - (shown.output.panel.stop - shown.output.panel.start) + 1
    )


def test_cell_without_output_has_no_output_block():
    rc = render(code("x"))
    assert rc.output is None and rc.hidden_row is None
    assert render(code("x"), folded=True).hidden_row is None


def test_output_lines_follow_the_models_line_numbering():
    cell = code("x", stream("a\n"), stream("b\n"))
    assert cell.lines(OUT) == ["a", "b"]
    rc = render(cell)
    shown = [rc.plain[g.row][LEFT - 1 + 1 : -2].strip() for g in rc.output.lines]
    assert shown == ["a", "b"]


def test_wrapped_line_geometry_maps_columns_to_rows():
    source = "alpha beta gamma delta epsilon"
    rc = render(code(source), width=20)  # content width 16
    geom = rc.input.lines[0]
    assert geom.rows == 2
    assert rc.plain[geom.row].startswith("│ alpha beta gamma")
    assert rc.plain[geom.row + 1].startswith("│ delta epsilon")
    col = source.index("epsilon")
    row, x = geom.locate(col)
    assert row == geom.row + 1
    assert span_text(rc, row, LEFT + x, LEFT + x + 7) == "epsilon"


def test_spans_split_across_wrapped_rows():
    rc = render(code("aaaa bbbb cccc dddd"), width=14)  # content width 10
    geom = rc.input.lines[0]
    assert geom.rows == 2
    pieces = geom.spans(5, 14)  # "bbbb cccc"
    assert [p[0] for p in pieces] == [geom.row, geom.row + 1]
    text = "".join(span_text(rc, r, LEFT + a, LEFT + b) for r, a, b in pieces)
    assert text.replace(" ", "") == "bbbbcccc"


def test_long_word_is_folded_and_every_char_is_mapped():
    source = "x" * 25
    rc = render(code(source), width=14)
    geom = rc.input.lines[0]
    assert geom.rows == 3
    rows = {geom.locate(c)[0] for c in range(25)}
    assert rows == {geom.row, geom.row + 1, geom.row + 2}


def test_tabs_are_expanded_and_columns_stay_mapped():
    rc = render(code("a\tb"), width=30)
    geom = rc.input.lines[0]
    assert rc.plain[geom.row].startswith("│ a   b")
    row, x = geom.locate(2)  # the "b"
    assert (row, x) == (geom.row, 4)


def test_wide_characters_use_cell_widths():
    rc = render(code("日本語 abc"), width=30)
    geom = rc.input.lines[0]
    assert geom.locate(4) == (geom.row, 7)
    assert geom.spans(4, 7) == [(geom.row, 7, 10)]


def test_control_characters_are_neutralised_without_shifting_columns():
    rc = render(code("a\x1b[31mb"), width=30)
    assert "\x1b" not in "".join(rc.plain)
    assert rc.input.lines[0].locate(4)[1] == 4


def test_each_line_is_a_geometry_entry_even_when_empty():
    cell = code("a\n\nb")
    rc = render(cell)
    rows = [g.row for g in rc.input.lines]
    assert rows == [2, 3, 4]
    assert [rc.input.line_at(r) for r in range(1, 6)] == [None, 0, 1, 2, None]


def test_locate_row_reports_region_and_line():
    rc = render(code("a\nb", stream("x\ny\n")))
    assert rc.locate_row(0) is None
    assert rc.locate_row(3) == (IN, 1)
    out_first = rc.output.lines[0].row
    assert rc.locate_row(out_first + 1) == (OUT, 1)
    assert rc.locate_row(rc.output.panel.start) is None


def test_output_is_capped_and_expandable():
    text = "".join(f"line {i}\n" for i in range(OUTPUT_CAP + 40))
    cell = code("x", stream(text))
    rc = render(cell)
    assert len(rc.output.lines) == OUTPUT_CAP
    assert rc.output.total == OUTPUT_CAP + 40
    assert any("… 40 more lines" in row for row in rc.plain)
    full = render(cell, expand_output=True)
    assert len(full.output.lines) == OUTPUT_CAP + 40
    assert not any("more lines" in row for row in full.plain)


def test_estimate_height_matches_simple_cells_and_caps_outputs():
    cell = code("a\nb", stream("1\n2\n3"))
    assert estimate_height(cell, 40) == render(cell, width=40).height
    assert (
        estimate_height(cell, 40, folded=True)
        == render(cell, width=40, folded=True).height
    )
    huge = code("x", stream("y\n" * 5000))
    assert estimate_height(huge, 40) == 1 + 1 + 2 + 1 + OUTPUT_CAP + 1 + 2


def test_estimate_height_is_cheap_for_cells_without_outputs():
    assert estimate_height(Cell(0, "markdown", ""), 80) == 5


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("short", 10, [0]),
        ("aaa bbb ccc", 7, [0, 8]),
        ("aaa bbb ccc", 5, [0, 4, 8]),
        ("abcdefghij", 4, [0, 4, 8]),
        ("", 5, [0]),
    ],
)
def test_wrap_starts(text, width, expected):
    words = " " in text
    assert wrap_starts(text, width, words=words) == expected


def test_rendering_uses_the_notebook_language_without_crashing():
    nb: Notebook = parse(
        {
            "cells": [{"cell_type": "code", "source": "let x = 1;", "outputs": []}],
            "metadata": {"language_info": {"name": "rust"}},
            "nbformat": 4,
        }
    )
    rc = render_cell(nb.cells[0], nb.language, 30)
    assert rc.plain[2].startswith("│ let x = 1;")


def test_frame_constants_match_the_drawn_frame():
    rc = render(code("x"), width=20)
    assert (LEFT, FRAME) == (2, 4)
    assert rc.plain[2][:LEFT] == "│ " and rc.plain[2][-LEFT:] == " │"


@pytest.mark.parametrize("name", ["basic", "errors", "rich_outputs", "backticks"])
@pytest.mark.parametrize("kw", [{}, {"markdown_raw": True}, {"folded": True}])
@pytest.mark.parametrize("width", [14, 40, 90])
def test_measure_height_equals_rendered_height(name, kw, width):
    nb = model.load(FIXTURES / f"{name}.ipynb")
    for cell in nb.cells:
        rc = render_cell(cell, nb.language, width, **kw)
        assert measure_height(cell, width, **kw) == rc.height


def test_measure_height_with_cap_tabs_wrapping_and_placeholders():
    long_line = "word " * 30
    cell = code(
        "a\tb\n" + long_line,
        stream("x\ty\n" * 700),
        Output("display", None, mimes=("image/png",)),
    )
    for kw in ({}, {"expand_output": True}):
        assert measure_height(cell, 30, **kw) == render(cell, 30, **kw).height


def test_code_styles_carry_no_background():
    rc = render(code("def f(x):\n    return 'a' + str(x)"))
    assert all(s.style is None or s.style.bgcolor is None for s in rc.lines[3])
