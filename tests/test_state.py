from __future__ import annotations

from dataclasses import replace

import pytest

from nbsee import state as st
from nbsee.model import Notebook, Region, parse
from nbsee.state import Mode, Search, ViewState

IN, OUT = Region.INPUT, Region.OUTPUT


def make(*cells: tuple[str, str, str | None]) -> Notebook:
    raw = []
    for kind, src, out in cells:
        cell: dict = {"cell_type": kind, "source": src, "metadata": {}}
        if kind == "code":
            cell["outputs"] = (
                [{"output_type": "stream", "name": "stdout", "text": out}]
                if out
                else []
            )
        raw.append(cell)
    return parse({"cells": raw, "metadata": {}, "nbformat": 4})


NB = make(
    ("markdown", "# Title", None),  # 0
    ("code", "a = 1\nb = 2", "1\n2\n"),  # 1
    ("code", "c = 3", None),  # 2
    ("markdown", "some text", None),  # 3
    ("code", "d = 4", "four"),  # 4
    ("raw", "raw", None),  # 5
    ("code", "e = 5", "five"),  # 6
)
EMPTY = make()
MD_ONLY = make(("markdown", "# a", None), ("markdown", "b", None))
NO_OUT = make(("code", "x = 1", None), ("code", "y = 2", None))


def S(**kw) -> ViewState:
    return ViewState(**kw)


def visual(anchor: int, cursor: int, region: Region = IN) -> ViewState:
    return S(mode=Mode.VISUAL, anchor=anchor, cursor=cursor, region=region)


# -- cell navigation -------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "delta", "expected"),
    [
        (0, 1, 1),
        (3, -1, 2),
        (0, -1, 0),
        (6, 1, 6),
        (2, 100, 6),
        (5, -100, 0),
        (4, 0, 4),
    ],
)
def test_move_cell_clamps(start, delta, expected):
    assert st.move_cell(S(cursor=start), NB, delta).cursor == expected


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, 0), (3, 3), (6, 6), (99, 6), (-1, 6), (-3, 4), (-7, 0), (-99, 0)],
)
def test_goto_cell(index, expected):
    assert st.goto_cell(S(cursor=2), NB, index).cursor == expected


def test_cell_change_resets_region_and_line():
    s = S(cursor=1, region=OUT, line=1)
    s = st.move_cell(s, NB, 1)
    assert (s.cursor, s.region, s.line) == (2, IN, 0)


def test_unchanged_cursor_returns_same_state():
    s = S(cursor=6, region=OUT)
    assert st.move_cell(s, NB, 1) is s
    assert st.goto_cell(s, NB, 99) is s


def test_visual_keeps_region_and_anchor_on_move():
    s = visual(1, 1, OUT)
    s = st.move_cell(s, NB, 3)
    assert (s.cursor, s.anchor, s.region, s.mode) == (4, 1, OUT, Mode.VISUAL)
    s = st.goto_cell(s, NB, -1)
    assert (s.cursor, s.anchor, s.region) == (6, 1, OUT)


@pytest.mark.parametrize("mode", [Mode.LINE, Mode.LINE_VISUAL])
def test_cell_change_leaves_line_modes(mode):
    s = S(cursor=1, mode=mode, line=1, anchor=0)
    s = st.move_cell(s, NB, 1)
    assert (s.mode, s.line, s.cursor, s.anchor) == (Mode.NORMAL, 0, 2, 2)


# -- region / folds / toggles ---------------------------------------------


@pytest.mark.parametrize(
    ("cursor", "region", "expected"),
    [
        (1, IN, OUT),  # has output
        (2, IN, IN),  # no output
        (0, IN, IN),  # markdown
        (1, OUT, IN),
        (2, OUT, IN),  # always allowed back to input
    ],
)
def test_toggle_region(cursor, region, expected):
    assert st.toggle_region(S(cursor=cursor, region=region), NB).region is expected


def test_toggle_region_whitespace_only_output_is_not_output():
    nb = make(("code", "x", "  \n"))
    assert st.toggle_region(S(), nb).region is IN


def test_toggle_region_in_line_modes_resets_line():
    s = S(cursor=1, mode=Mode.LINE_VISUAL, line=1, anchor=0)
    s = st.toggle_region(s, NB)
    assert (s.region, s.mode, s.line, s.anchor) == (OUT, Mode.LINE, 0, 0)
    s = st.toggle_region(replace(s, line=1), NB)
    assert (s.region, s.mode, s.line) == (IN, Mode.LINE, 0)


def test_toggle_region_visual_needs_any_output_in_range():
    assert st.toggle_region(visual(2, 3), NB).region is IN  # no output at all
    assert st.toggle_region(visual(2, 4), NB).region is OUT  # cell 4 has output
    s = st.toggle_region(visual(2, 4, OUT), NB)
    assert s.region is IN and s.mode is Mode.VISUAL


def test_toggle_fold_and_all():
    s = st.toggle_fold(S(cursor=1), NB)
    assert s.folded == {1}
    s = st.toggle_fold(st.move_cell(s, NB, 3), NB)
    assert s.folded == {1, 4}
    s = st.toggle_fold(st.goto_cell(s, NB, 1), NB)
    assert s.folded == {4}
    assert st.toggle_all_outputs(S()).outputs_hidden is True
    assert st.toggle_all_outputs(S(outputs_hidden=True)).outputs_hidden is False
    assert st.toggle_markdown_raw(S()).markdown_raw is True
    assert st.toggle_markdown_raw(S(markdown_raw=True)).markdown_raw is False


# -- modes -----------------------------------------------------------------


def test_enter_visual_toggles():
    s = st.enter_visual(S(cursor=3))
    assert (s.mode, s.anchor) == (Mode.VISUAL, 3)
    s = st.enter_visual(replace(s, cursor=5))
    assert (s.mode, s.anchor) == (Mode.NORMAL, 5)


def test_enter_visual_ignored_in_line_modes():
    s = S(mode=Mode.LINE)
    assert st.enter_visual(s) is s


@pytest.mark.parametrize(
    ("cursor", "region", "mode"),
    [
        (1, IN, Mode.LINE),
        (1, OUT, Mode.LINE),
        (2, OUT, Mode.NORMAL),  # no output lines
        (0, IN, Mode.LINE),
    ],
)
def test_enter_line(cursor, region, mode):
    assert st.enter_line(S(cursor=cursor, region=region), NB).mode is mode


def test_enter_line_empty_source_and_not_from_visual():
    nb = make(("code", "", None))
    assert st.enter_line(S(), nb).mode is Mode.NORMAL
    assert st.enter_line(visual(0, 0), NB).mode is Mode.VISUAL


def test_line_visual_toggle_and_escape_chain():
    s = st.enter_line(S(cursor=1), NB)
    s = st.move_line(s, NB, 1)
    s = st.start_line_visual(s)
    assert (s.mode, s.anchor, s.line) == (Mode.LINE_VISUAL, 1, 1)
    s = st.move_line(s, NB, -1)
    assert st.line_range(s) == (0, 1)
    s2 = st.start_line_visual(s)
    assert s2.mode is Mode.LINE
    s = st.escape(s)
    assert s.mode is Mode.LINE
    s = st.escape(s)
    assert (s.mode, s.line) == (Mode.NORMAL, 0)


def test_start_line_visual_only_from_line():
    s = S()
    assert st.start_line_visual(s) is s
    assert st.start_line_visual(visual(0, 1)).mode is Mode.VISUAL


def test_escape_visual_and_normal_clears_search():
    s = st.escape(visual(1, 4))
    assert (s.mode, s.anchor) == (Mode.NORMAL, 4)
    s = st.set_search(S(), NB, "a")
    assert s.search is not None
    s = st.escape(s)
    assert s.search is None
    plain = S()
    assert st.escape(plain) is plain


def test_escape_visual_keeps_search():
    s = replace(visual(1, 4), search=Search("x", (), None))
    assert st.escape(s).search is not None


@pytest.mark.parametrize(
    ("line", "delta", "expected"), [(0, 1, 1), (0, -1, 0), (1, 5, 1), (1, -5, 0)]
)
def test_move_line_clamps(line, delta, expected):
    s = S(cursor=1, mode=Mode.LINE, line=line)  # two input lines
    assert st.move_line(s, NB, delta).line == expected


@pytest.mark.parametrize(
    ("index", "expected"), [(0, 0), (1, 1), (9, 1), (-1, 1), (-9, 0)]
)
def test_goto_line(index, expected):
    s = S(cursor=1, mode=Mode.LINE)
    assert st.goto_line(s, NB, index).line == expected


def test_line_moves_ignored_outside_line_modes():
    s = S(cursor=1)
    assert st.move_line(s, NB, 1) is s
    assert st.goto_line(s, NB, 1) is s


def test_ranges():
    assert st.visual_range(visual(5, 2)) == (2, 5)
    assert st.visual_range(visual(2, 5)) == (2, 5)
    assert st.visual_range(S(cursor=3, anchor=0)) == (3, 3)
    assert st.line_range(S(mode=Mode.LINE_VISUAL, anchor=4, line=2)) == (2, 4)
    assert st.line_range(S(mode=Mode.LINE, anchor=4, line=2)) == (2, 2)


# -- empty / degenerate notebooks -----------------------------------------


@pytest.mark.parametrize("nb", [EMPTY])
def test_empty_notebook_is_noop(nb):
    s = S()
    assert st.move_cell(s, nb, 1) == s
    assert st.goto_cell(s, nb, -1) == s
    assert st.toggle_region(s, nb) == s
    assert st.toggle_fold(s, nb) == s
    assert st.enter_line(s, nb) == s
    assert st.move_line(s, nb, 1) == s
    assert st.goto_line(s, nb, 0) == s
    assert st.set_search(s, nb, "x") == s
    assert st.search_step(s, nb, 1) == s
    assert st.on_reload(s, nb) == s
    assert st.yank(s, nb) == "" and st.yank_all(s, nb) == ""
    assert st.yank(visual(0, 3), nb) == ""
    assert st.yank_all(visual(0, 3), nb) == ""


def test_markdown_only_notebook():
    assert st.toggle_region(S(), MD_ONLY).region is IN
    assert st.enter_line(S(), MD_ONLY).mode is Mode.LINE
    assert st.yank(S(), MD_ONLY) == "# a"
    assert st.yank_all(S(), MD_ONLY) == "# a"
    v = visual(0, 1)
    assert st.yank(v, MD_ONLY) == ""  # no code cells
    assert st.yank(replace(v, region=OUT), MD_ONLY) == ""
    assert st.yank_all(v, MD_ONLY) == "# a\n\nb"


def test_cells_without_output():
    assert st.yank(S(region=OUT), NO_OUT) == ""
    assert st.yank_all(S(), NO_OUT) == "x = 1"
    assert st.yank(visual(0, 1, OUT), NO_OUT) == ""
    assert st.yank_all(visual(0, 1), NO_OUT) == "x = 1\n\ny = 2"


# -- yank ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (S(cursor=0), "# Title"),
        (S(cursor=1), "a = 1\nb = 2"),
        (S(cursor=1, region=OUT), "1\n2"),  # trailing newline stripped
        (S(cursor=2, region=OUT), ""),
        (S(cursor=5), "raw"),
    ],
)
def test_yank_normal(state, expected):
    assert st.yank(state, NB) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (S(cursor=0), "# Title"),
        (S(cursor=1), "a = 1\nb = 2\n\n1\n2"),
        (S(cursor=2), "c = 3"),
        (S(cursor=4, region=OUT), "d = 4\n\nfour"),  # region ignored
    ],
)
def test_yank_all_normal(state, expected):
    assert st.yank_all(state, NB) == expected


def test_visual_yank_input_is_code_only():
    # cells 2-5 (1-based): code, code, markdown, code
    s = visual(1, 4)
    assert st.yank(s, NB) == "a = 1\nb = 2\n\nc = 3\n\nd = 4"
    assert st.selection_text(s, NB) == st.yank(s, NB)


def test_visual_yank_input_ignores_markdown_and_raw():
    assert st.yank(visual(3, 3), NB) == ""
    assert st.yank(visual(0, 6), NB) == "a = 1\nb = 2\n\nc = 3\n\nd = 4\n\ne = 5"


def test_visual_yank_reversed_anchor():
    assert st.yank(visual(4, 1), NB) == st.yank(visual(1, 4), NB)


def test_visual_yank_output_only_cells_with_output():
    assert st.yank(visual(0, 6, OUT), NB) == "1\n2\n\nfour\n\nfive"
    assert st.yank(visual(2, 3, OUT), NB) == ""


def test_visual_yank_all_is_everything_in_order():
    expected = "a = 1\nb = 2\n\n1\n2\n\nc = 3\n\nsome text\n\nd = 4\n\nfour"
    assert st.yank_all(visual(1, 4), NB) == expected
    assert st.yank_all(visual(4, 1, OUT), NB) == expected  # region ignored


def test_visual_yank_all_includes_raw_and_title():
    got = st.yank_all(visual(0, 6), NB)
    assert got.startswith("# Title\n\na = 1")
    assert "\n\nraw\n\n" in got and got.endswith("e = 5\n\nfive")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (S(cursor=1, mode=Mode.LINE, line=0), "a = 1"),
        (S(cursor=1, mode=Mode.LINE, line=1), "b = 2"),
        (S(cursor=1, mode=Mode.LINE, line=1, region=OUT), "2"),
        (S(cursor=1, mode=Mode.LINE_VISUAL, anchor=0, line=1), "a = 1\nb = 2"),
        (S(cursor=1, mode=Mode.LINE_VISUAL, anchor=1, line=0), "a = 1\nb = 2"),
        (S(cursor=1, mode=Mode.LINE, line=99), "b = 2"),  # clamped
    ],
)
def test_yank_line_modes(state, expected):
    assert st.yank(state, NB) == expected


def test_yank_all_line_modes_is_whole_region():
    s = S(cursor=1, mode=Mode.LINE, line=1)
    assert st.yank_all(s, NB) == "a = 1\nb = 2"
    assert st.yank_all(replace(s, region=OUT), NB) == "1\n2"


def test_yank_out_of_range_state_is_clamped():
    assert st.yank(S(cursor=99), NB) == "e = 5"
    assert st.yank(visual(0, 99), NB).endswith("e = 5")


# -- search ----------------------------------------------------------------

SNB = make(
    ("code", "alpha\nbeta", "Beta out"),  # 0
    ("markdown", "gamma beta", None),  # 1
    ("code", "x", None),  # 2
    ("code", "BETA", None),  # 3
)


def test_set_search_picks_first_hit_at_or_after_cursor_and_moves():
    s = st.set_search(S(cursor=1), SNB, "beta")
    assert s.search is not None and len(s.search.hits) == 4
    assert s.search.current == 2  # cell 1 input
    assert s.cursor == 1


def test_set_search_wraps_when_nothing_after_cursor():
    s = st.set_search(S(cursor=3, region=OUT), SNB, "alpha")
    assert s.search.current == 0 and s.cursor == 0 and s.region is IN


def test_set_search_no_hits_keeps_position():
    s = st.set_search(S(cursor=2), SNB, "zzz")
    assert s.search == Search("zzz", (), None)
    assert s.cursor == 2


def test_set_search_empty_query_clears():
    s = st.set_search(S(), SNB, "beta")
    assert st.set_search(s, SNB, "").search is None


def test_search_step_wraps_both_directions():
    s = st.set_search(S(), SNB, "beta")  # hits: c0 in, c0 out, c1, c3
    seen = []
    for _ in range(5):
        s = st.search_step(s, SNB, 1)
        seen.append((s.search.current, s.cursor, s.region))
    assert seen == [
        (1, 0, OUT),
        (2, 1, IN),
        (3, 3, IN),
        (0, 0, IN),
        (1, 0, OUT),
    ]
    back = []
    for _ in range(3):
        s = st.search_step(s, SNB, -1)
        back.append(s.search.current)
    assert back == [0, 3, 2]


def test_search_step_without_search_or_hits_is_noop():
    s = S()
    assert st.search_step(s, SNB, 1) is s
    s = st.set_search(s, SNB, "zzz")
    assert st.search_step(s, SNB, 1) is s


def test_search_step_from_none_current():
    s = replace(S(), search=Search("beta", st.find(SNB, "beta"), None))
    assert st.search_step(s, SNB, 1).search.current == 0
    assert st.search_step(s, SNB, -1).search.current == 3


def test_search_smart_case_in_state():
    assert len(st.set_search(S(), SNB, "beta").search.hits) == 4
    assert len(st.set_search(S(), SNB, "Beta").search.hits) == 1
    assert len(st.set_search(S(), SNB, "BETA").search.hits) == 1


def test_search_hit_in_output_switches_region():
    s = st.set_search(S(), SNB, "out")
    assert (s.cursor, s.region) == (0, OUT)
    s = st.escape(s)
    assert s.search is None and s.region is OUT


def test_search_in_visual_extends_range_and_keeps_region():
    s = visual(0, 0)
    s = st.set_search(s, SNB, "gamma")
    assert (s.cursor, s.anchor, s.region, s.mode) == (1, 0, IN, Mode.VISUAL)
    s = st.set_search(s, SNB, "BETA")
    assert st.visual_range(s) == (0, 3)
    s = replace(visual(1, 1, OUT), search=None)
    s = st.set_search(s, SNB, "x")
    assert s.region is OUT and st.visual_range(s) == (1, 2)


def test_search_in_line_mode_moves_line_within_region():
    nb = make(("code", "one\ntwo\nthree\ntwo", None))
    s = S(mode=Mode.LINE)
    s = st.set_search(s, nb, "two")
    assert (s.mode, s.line) == (Mode.LINE, 1)
    s = st.search_step(s, nb, 1)
    assert s.line == 3
    s = st.search_step(s, nb, 1)
    assert s.line == 1


def test_search_in_line_visual_extends_line_range():
    nb = make(("code", "one\ntwo\nthree\ntwo", None))
    s = S(mode=Mode.LINE_VISUAL, anchor=0, line=0)
    s = st.set_search(s, nb, "three")
    assert st.line_range(s) == (0, 2)


def test_search_in_line_mode_other_cell_leaves_line_mode():
    s = S(cursor=0, mode=Mode.LINE)
    s = st.set_search(s, SNB, "gamma")
    assert (s.mode, s.cursor, s.line) == (Mode.NORMAL, 1, 0)


def test_clear_search():
    s = st.set_search(S(), SNB, "beta")
    assert st.clear_search(s).search is None
    plain = S()
    assert st.clear_search(plain) is plain


# -- reload ----------------------------------------------------------------


def test_on_reload_clamps_cursor_and_folds():
    small = make(("code", "a", None), ("code", "b", "o"))
    s = S(cursor=6, folded=frozenset({0, 1, 5}), markdown_raw=True)
    out = st.on_reload(s, small)
    assert out.cursor == 1
    assert out.folded == {0, 1}
    assert out.markdown_raw is True


def test_on_reload_resets_output_region_without_output():
    nb = make(("code", "a", None))
    assert st.on_reload(S(region=OUT), nb).region is IN
    assert st.on_reload(S(region=OUT), NB.__class__(NB.cells[1:2])).region is OUT


def test_on_reload_clamps_visual_anchor():
    small = make(("code", "a", None), ("code", "b", None))
    out = st.on_reload(visual(5, 6), small)
    assert (out.anchor, out.cursor, out.mode) == (1, 1, Mode.VISUAL)


def test_on_reload_clamps_lines_and_drops_line_mode_when_empty():
    nb = make(("code", "a\nb", None), ("code", "", None))
    out = st.on_reload(S(mode=Mode.LINE_VISUAL, line=9, anchor=7), nb)
    assert (out.line, out.anchor, out.mode) == (1, 1, Mode.LINE_VISUAL)
    out = st.on_reload(S(cursor=1, mode=Mode.LINE, line=3), nb)
    assert (out.mode, out.line) == (Mode.NORMAL, 0)


def test_on_reload_recomputes_hits():
    s = st.set_search(S(), SNB, "beta")
    new = make(("code", "beta beta", None), ("code", "x", None))
    out = st.on_reload(s, new)
    assert out.search.query == "beta"
    assert len(out.search.hits) == 2 and out.search.current == 0
    gone = st.on_reload(s, make(("code", "nothing", None)))
    assert gone.search == Search("beta", (), None)


def test_on_reload_to_empty_notebook():
    s = replace(st.set_search(S(cursor=2), SNB, "beta"), mode=Mode.VISUAL)
    out = st.on_reload(s, EMPTY)
    assert (out.cursor, out.anchor, out.mode, out.region) == (0, 0, Mode.NORMAL, IN)
    assert out.search == Search("beta", (), None)


def test_states_are_immutable():
    with pytest.raises(AttributeError):
        S().cursor = 3  # type: ignore[misc]
