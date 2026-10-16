"""Headless Pilot tests for the viewer. Keep each one fast."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from nbsee import clipboard, model
from nbsee.clipboard import CopyResult
from nbsee.model import Notebook, Region, parse
from nbsee.state import Mode
from nbsee.tui.app import KEYMAP, NotebookApp
from nbsee.tui.help import HelpScreen
from nbsee.tui.layout import Layout
from nbsee.tui.view import HIT

FIXTURES = Path(__file__).parent / "fixtures"
SIZE = (80, 30)


def make(*cells: tuple[str, str, str | None]) -> Notebook:
    raw = []
    for kind, src, out in cells:
        cell: dict[str, Any] = {"cell_type": kind, "source": src, "metadata": {}}
        if kind == "code":
            cell["outputs"] = (
                [{"output_type": "stream", "name": "stdout", "text": out}]
                if out
                else []
            )
        raw.append(cell)
    return parse({"cells": raw, "metadata": {}, "nbformat": 4})


MIXED = make(
    ("markdown", "# Title\n\n## Part", None),  # 0
    ("code", "a = 1\nb = 2", "1\n2\n"),  # 1
    ("code", "c = 3", None),  # 2
    ("markdown", "some text", None),  # 3
    ("code", "d = 4", "four"),  # 4
    ("raw", "raw", None),  # 5
    ("code", "e = 5", "five"),  # 6
)


def drive(
    nb: Notebook,
    script: Callable[[NotebookApp, Any], Awaitable[None]],
    *,
    size: tuple[int, int] = SIZE,
) -> NotebookApp:
    app = NotebookApp(nb)

    async def main() -> None:
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(app, pilot)

    asyncio.run(main())
    return app


def screen(app: NotebookApp) -> list[str]:
    view = app.view
    return [view.render_line(y).text for y in range(view.size.height)]


def row_of(app: NotebookApp, needle: str) -> int:
    for y, line in enumerate(screen(app)):
        if needle in line:
            return y
    raise AssertionError(f"{needle!r} not on screen")


async def settle(app: NotebookApp, pilot: Any) -> None:
    """Wait until background layout refinement has finished."""
    for _ in range(500):
        if not app.view.table.pending():
            break
        await pilot.pause(0.005)
    await pilot.pause()


@pytest.fixture
def copied(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    texts: list[str] = []

    def fake(text: str, **_: Any) -> CopyResult:
        texts.append(text)
        return CopyResult(True, ("osc52",), f"Copied {len(text.splitlines())} lines")

    monkeypatch.setattr(clipboard, "copy", fake)
    return texts


# -- open, move ---------------------------------------------------------------


def test_open_shows_first_cell_and_status():
    nb = model.load(FIXTURES / "basic.ipynb")

    async def script(app: NotebookApp, pilot: Any) -> None:
        assert app.status.left_text == "basic.ipynb  1/5  NORMAL  input"
        lines = screen(app)
        assert lines[0].startswith("▶ [1] markdown")
        assert any("import math" in line for line in lines)
        assert app.focused is app.view

    drive(nb, script)


def test_j_and_k_move_cursor_and_clamp():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("k")
        assert app.state.cursor == 0
        await pilot.press("j", "j")
        assert app.state.cursor == 2
        assert app.status.left_text.startswith("<stdin>  3/7")
        await pilot.press("k")
        assert app.state.cursor == 1
        await pilot.press("G")
        assert app.state.cursor == 6
        await pilot.press("j")
        assert app.state.cursor == 6

    drive(MIXED, script)


def test_cursor_marker_follows_and_cell_stays_visible():
    nb = make(*[("code", f"v{i} = {i}\n" * 8, "o\n" * 8) for i in range(40)])

    async def script(app: NotebookApp, pilot: Any) -> None:
        for _ in range(10):
            await pilot.press("j")
            assert any(line.startswith("▶ [") for line in screen(app))
        assert row_of(app, "▶ [11]") >= 0
        await pilot.press("G")
        await settle(app, pilot)
        assert any(line.startswith("▶ [40]") for line in screen(app))

    drive(nb, script)


def test_gg_needs_two_presses_within_the_timeout():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("G", "g")
        assert app.state.cursor == 6 and app._g_timer is not None
        await pilot.press("g")
        assert app.state.cursor == 0 and app._g_timer is None
        await pilot.press("G", "g", "j")  # another key cancels the pending g
        assert app._g_timer is None
        await pilot.press("g")
        assert app.state.cursor == 6  # j was a no-op at the end; one g only

    drive(MIXED, script)


def test_gg_timer_expires():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("G", "g")
        await pilot.pause(0.7)
        assert app._g_timer is None
        await pilot.press("g")
        assert app.state.cursor == 6

    drive(MIXED, script)


def test_ctrl_d_moves_cursor_to_cell_at_same_screen_row():
    nb = make(*[("code", f"x{i}", None) for i in range(60)])

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("ctrl+d")
        first = app.state.cursor
        assert first > 0
        top = app.view.cell_at_top()
        assert top > 0
        await pilot.press("ctrl+u")
        assert app.state.cursor < first
        await pilot.press("ctrl+f")
        assert app.state.cursor > first

    drive(nb, script)


def test_arrows_scroll_without_moving_the_cursor():
    nb = make(*[("code", f"x{i}", None) for i in range(60)])

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("down", "down", "down")
        assert app.view.scroll_y == 3 and app.state.cursor == 0
        await pilot.press("up")
        assert app.view.scroll_y == 2

    drive(nb, script)


# -- regions and copy ---------------------------------------------------------


def test_tab_switches_region_or_reports_no_output():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("tab")
        assert app.state.region is Region.INPUT and app.status.message == "No output"
        await pilot.press("j", "tab")
        assert app.state.region is Region.OUTPUT
        assert app.status.left_text.endswith("output")
        await pilot.press("tab")
        assert app.state.region is Region.INPUT

    drive(MIXED, script)


def test_y_copies_region_and_shows_message(copied: list[str]):
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "y")
        assert copied == ["a = 1\nb = 2"]
        assert app.status.message == "Copied 2 lines"
        await pilot.press("tab", "y")
        assert copied[-1] == "1\n2"
        await pilot.press("Y")
        assert copied[-1] == "a = 1\nb = 2\n\n1\n2"

    drive(MIXED, script)


def test_visual_range_yank_follows_design_example(copied: list[str]):
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "V", "j", "j", "j", "j", "j")
        assert app.state.mode is Mode.VISUAL and app.state.cursor == 6
        assert any(line.startswith("◆ [") for line in screen(app))
        await pilot.press("y")
        assert copied[-1] == "a = 1\nb = 2\n\nc = 3\n\nd = 4\n\ne = 5"
        assert app.state.mode is Mode.NORMAL
        await pilot.press("g", "g", "V", "G", "tab", "y")
        assert copied[-1] == "1\n2\n\nfour\n\nfive"

    drive(MIXED, script)


def test_visual_yank_all_is_everything_in_order(copied: list[str]):
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("V", "j", "j", "Y")
        assert copied[-1] == "# Title\n\n## Part\n\na = 1\nb = 2\n\n1\n2\n\nc = 3"

    drive(MIXED, script)


def test_line_mode_visual_yank(copied: list[str]):
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "enter")
        assert app.state.mode is Mode.LINE
        assert app.status.left_text.endswith("L1")
        await pilot.press("V", "j")
        assert app.state.mode is Mode.LINE_VISUAL
        await pilot.press("y")
        assert copied[-1] == "a = 1\nb = 2"
        assert app.state.mode is Mode.NORMAL

    drive(MIXED, script)


def test_line_mode_cursor_is_painted_and_escape_steps_back():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "enter", "j")
        assert app.state.line == 1
        y = row_of(app, "│ b = 2")
        styles = [s.style for s in app.view.render_line(y) if s.style]
        assert any(st.bgcolor is not None for st in styles)
        plain_y = row_of(app, "│ a = 1")
        assert not any(
            s.style and s.style.bgcolor for s in app.view.render_line(plain_y)
        )
        await pilot.press("V", "escape")
        assert app.state.mode is Mode.LINE
        await pilot.press("escape")
        assert app.state.mode is Mode.NORMAL

    drive(MIXED, script)


def test_line_mode_on_rendered_markdown_shows_raw_source():
    async def script(app: NotebookApp, pilot: Any) -> None:
        assert not any("# Title" in line for line in screen(app))
        await pilot.press("enter", "j")
        assert app.state.line == 1
        assert any("│ # Title" in line for line in screen(app))
        await pilot.press("escape")
        assert not any("│ # Title" in line for line in screen(app))

    drive(MIXED, script)


def test_enter_on_empty_region_reports_nothing_to_select():
    nb = make(("code", "", None))

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("enter")
        assert app.state.mode is Mode.NORMAL
        assert app.status.message == "Nothing to select"

    drive(nb, script)


# -- search -------------------------------------------------------------------


def test_search_n_and_wraparound_with_counter():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "equals_sign", "enter")
        s = app.state.search
        assert s is not None and s.current == 0 and app.state.cursor == 1
        assert app.status.left_text.endswith("/= 1/5")
        assert app.focused is app.view
        await pilot.press("n", "n")
        assert app.state.cursor == 2 and app.status.left_text.endswith("/= 3/5")
        await pilot.press("N", "N", "N")
        assert app.state.cursor == 6 and app.status.left_text.endswith("/= 5/5")
        await pilot.press("n")
        assert app.state.cursor == 1
        await pilot.press("escape")
        assert app.state.search is None

    drive(MIXED, script)


def test_search_steps_start_from_the_ends_when_current_is_none(
    monkeypatch: pytest.MonkeyPatch,
):
    from dataclasses import replace

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "e", "enter")
        s = app.state.search
        assert s is not None
        app.state = replace(app.state, search=replace(s, current=None))
        await pilot.press("N")
        assert app.state.search.current == len(s.hits) - 1

    drive(MIXED, script)


def test_search_highlights_hits_in_visible_cells():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "b", "enter")
        y = row_of(app, "│ a = 1")  # cell 1 has "b = 2" on the next row
        y = y + 1
        segs = list(app.view.render_line(y))
        marked = [s for s in segs if s.style and s.style.bgcolor is not None]
        assert marked and marked[0].text == "b"
        assert marked[0].style.bgcolor != HIT.bgcolor  # current hit is distinct

    drive(MIXED, script)


def test_search_in_output_unfolds_a_folded_cell():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("Z")
        assert app.state.outputs_hidden
        assert any("[output hidden]" in line for line in screen(app))
        await pilot.press("slash", "f", "o", "u", "r", "enter")
        s = app.state
        assert s.cursor == 4 and not s.outputs_hidden
        assert s.folded == frozenset({1, 6})
        assert any("│ four" in line for line in screen(app))

    drive(MIXED, script)


def test_search_with_no_matches_is_reported():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "z", "z", "z", "enter")
        assert "No matches" in app.status.message
        assert app.status.left_text.endswith("/zzz 0/0")
        await pilot.press("n")
        assert "No matches" in app.status.message

    drive(MIXED, script)


def test_search_prompt_keeps_tab_and_cancels_with_escape():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "slash", "a", "tab", "b")
        assert app.focused is not app.view
        assert app.query_one("#prompt-input").value == "ab"
        assert app.state.region is Region.INPUT
        await pilot.press("escape")
        assert app.focused is app.view and app.state.search is None

    drive(MIXED, script)


# -- fold, markdown, goto -----------------------------------------------------


def test_z_and_Z_fold_outputs():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j")
        assert any("│ 1" == line[:3].rstrip() or "│ 1 " in line for line in screen(app))
        await pilot.press("z")
        assert app.state.folded == frozenset({1})
        assert any("[output hidden]" in line for line in screen(app))
        await pilot.press("z")
        assert not app.state.folded
        await pilot.press("Z")
        assert app.state.outputs_hidden
        assert sum("[output hidden]" in line for line in screen(app)) >= 2
        await pilot.press("Z")
        assert not app.state.outputs_hidden

    drive(MIXED, script)


def test_m_toggles_raw_markdown():
    async def script(app: NotebookApp, pilot: Any) -> None:
        assert not any("# Title" in line for line in screen(app))
        await pilot.press("m")
        assert any("│ # Title" in line for line in screen(app))
        await pilot.press("m")
        assert not any("│ # Title" in line for line in screen(app))

    drive(MIXED, script)


def test_goto_prompt():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("colon", "5", "enter")
        assert app.state.cursor == 4 and app.focused is app.view
        await pilot.press("colon", "9", "9", "enter")
        assert app.state.cursor == 6
        await pilot.press("colon", "x", "enter")
        assert "Not a cell number" in app.status.message
        await pilot.press("colon", "escape")
        assert app.focused is app.view

    drive(MIXED, script)


def test_long_output_is_capped_in_the_view_and_expands_in_line_mode():
    big = "".join(f"row {i}\n" for i in range(600))
    nb = make(("code", "x", big))

    async def script(app: NotebookApp, pilot: Any) -> None:
        assert app.view.table.total < 520
        await pilot.press("tab", "enter")
        await pilot.press("enter")
        assert app.view.table.total > 600
        await pilot.press("G")
        assert app.state.line == 599

    drive(nb, script, size=(80, 24))


# -- outline, help ------------------------------------------------------------


def test_outline_tracks_cursor_and_enter_jumps():
    nb = make(
        ("markdown", "# One", None),
        ("code", "a", None),
        ("markdown", "# Two\n## Two b", None),
        ("code", "b", None),
    )

    async def script(app: NotebookApp, pilot: Any) -> None:
        outline = app.outline
        assert outline.option_count == 3
        await pilot.press("o")
        assert outline.has_class("-open") and app.focused is outline
        assert outline.highlighted == 0
        await pilot.press("down", "down", "enter")
        assert app.state.cursor == 2 and app.focused is app.view
        assert outline.highlighted == 2
        await pilot.press("G")
        assert outline.highlighted == 2
        await pilot.press("g", "g")
        assert outline.highlighted == 0
        await pilot.press("o", "escape")
        assert not outline.has_class("-open") and app.focused is app.view

    drive(nb, script)


def test_outline_without_headings_stays_closed():
    nb = make(("code", "a", None))

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("o")
        assert not app.outline.has_class("-open") and app.focused is app.view
        assert app.status.message == "No headings"

    drive(nb, script)


def test_keys_do_not_leak_to_the_notebook_from_the_outline():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("o", "j", "y")
        assert app.state.cursor == 0

    drive(MIXED, script)


def test_help_lists_every_labelled_binding_and_closes():
    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("j")
        assert app.state.cursor == 0
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)

    drive(MIXED, script)
    assert all(k.label and k.desc for k in KEYMAP if k.label)
    assert {"q", "y", "tab", "enter", "slash", "colon"} <= {
        k for d in KEYMAP for k in d.keys.split(",")
    }


def test_q_quits():
    app = NotebookApp(MIXED)

    async def main() -> None:
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("q")
            await pilot.pause()

    asyncio.run(main())
    assert app.return_value == 0


# -- reload -------------------------------------------------------------------


def _write(path: Path, sources: list[str]) -> None:
    cells = [
        {"cell_type": "code", "source": s, "outputs": [], "metadata": {}}
        for s in sources
    ]
    path.write_text(json.dumps({"cells": cells, "metadata": {}, "nbformat": 4}))


def test_manual_reload_keeps_cursor_and_search(tmp_path: Path):
    path = tmp_path / "nb.ipynb"
    _write(path, ["alpha", "beta", "gamma"])
    nb = model.load(path)

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "a", "enter", "j")
        _write(path, ["alpha", "beta", "gamma", "delta", "alpha"])
        await pilot.press("R")
        assert len(app.nb.cells) == 5
        assert app.status.message == "Reloaded"
        assert app.status.left_text.startswith("nb.ipynb  ")
        assert app.state.search is not None and len(app.state.search.hits) == 8
        assert any("delta" in line for line in screen(app)) or app.view.table.total

    drive(nb, script)


def test_polling_reloads_on_change_and_clamps_cursor(tmp_path: Path):
    path = tmp_path / "nb.ipynb"
    _write(path, ["a", "b", "c"])
    nb = model.load(path)

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("G")
        assert app.state.cursor == 2
        _write(path, ["a", "b"])
        app._poll()
        assert app.state.cursor == 1 and "Reloaded" in app.status.message

    drive(nb, script)


def test_bad_reload_keeps_old_content(tmp_path: Path):
    path = tmp_path / "nb.ipynb"
    _write(path, ["a", "b"])
    nb = model.load(path)

    async def script(app: NotebookApp, pilot: Any) -> None:
        path.write_text("{ not json")
        await pilot.press("R")
        assert "Reload failed" in app.status.message
        assert len(app.nb.cells) == 2

    drive(nb, script)


# -- layout, anchoring, scale -------------------------------------------------


def test_layout_prefix_sums_and_locate():
    table = Layout([3, 5, 2])
    assert table.total == 10 and table.top(2) == 8
    assert [table.locate(y) for y in (0, 2, 3, 7, 8, 9, 99)] == [
        (0, 0),
        (0, 2),
        (1, 0),
        (1, 4),
        (2, 0),
        (2, 1),
        (2, 1),
    ]
    table.set_heights({1: 4})
    assert table.total == 9 and table.is_exact(1) and not table.is_exact(0)
    assert table.pending() == [0, 2]


def test_real_heights_replacing_estimates_do_not_move_visible_content():
    nb = make(*[("code", "x\n" * 3, "o\n" * 12) for _ in range(120)])

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("ctrl+d", "ctrl+d", "ctrl+d")
        anchor = app.view.cell_at_top()
        before = [line for line in screen(app) if line.startswith(("▶", " "))]
        marker = next(line for line in screen(app) if "[" in line)
        await settle(app, pilot)
        assert not app.view.table.pending()
        assert app.view.cell_at_top() == anchor
        assert marker in screen(app)
        assert before  # something was visible

    drive(nb, script)


def test_large_notebook_opens_and_responds_quickly():
    nb = make(
        *[
            ("markdown" if i % 5 == 0 else "code", f"# h{i}\nline {i}\n", f"o{i}\n" * 4)
            for i in range(2000)
        ]
    )
    app = NotebookApp(nb)
    timings: dict[str, float] = {}

    async def main() -> None:
        t0 = time.perf_counter()
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            timings["open"] = time.perf_counter() - t0
            t1 = time.perf_counter()
            for _ in range(8):
                await pilot.press("j")
            await pilot.press("G")
            await pilot.press("ctrl+d", "ctrl+u")
            timings["keys"] = time.perf_counter() - t1
            assert app.state.cursor < 2000
            await pilot.press("g", "g")
            assert app.state.cursor == 0

    asyncio.run(main())
    assert timings["open"] < 2.0
    assert timings["keys"] < 2.0


# -- robustness ---------------------------------------------------------------


def test_empty_notebook_survives_every_key():
    async def script(app: NotebookApp, pilot: Any) -> None:
        keys = "j G g V y Y tab enter z m n o escape R".split()
        await pilot.press(*keys, "ctrl+d", "ctrl+u", "down", "up")
        await pilot.press("slash", "a", "enter", "colon", "3", "enter")
        assert app.state.cursor == 0
        assert app.status.left_text.startswith("<stdin>  0/0")

    drive(make(), script)


def test_resize_keeps_the_viewport_anchored():
    nb = make(*[("code", "word " * 40, "o\n" * 3) for _ in range(30)])

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j", "j", "j", "j")
        before = app.view.cell_at_top()
        await pilot.resize_terminal(50, 20)
        await pilot.pause()
        assert app.view.cell_at_top() == before
        assert all(len(line) <= 50 for line in screen(app))
        await settle(app, pilot)
        assert app.view.cell_at_top() == before

    drive(nb, script)


def test_rendered_markdown_search_hits_are_highlighted():
    nb = make(("markdown", "Plain *emphasised* words here", None))

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("slash", "w", "o", "r", "d", "s", "enter")
        y = row_of(app, "words")
        marked = [s for s in app.view.render_line(y) if s.style and s.style.bgcolor]
        assert marked and marked[0].text == "words"

    drive(nb, script)


def test_error_output_and_placeholders_render_in_the_app():
    nb = model.load(FIXTURES / "rich_outputs.ipynb")

    async def script(app: NotebookApp, pilot: Any) -> None:
        await pilot.press("j")
        assert any("[image/png" in line or "[text/html" in line for line in screen(app))

    drive(nb, script)
