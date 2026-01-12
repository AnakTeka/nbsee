from __future__ import annotations

import re
from typing import Any

try:
    from rich import box as rich_box
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.reactive import reactive
    from textual.widgets import Footer, Input, Static

    _HAS_TEXTUAL = True
except Exception as e:  # pragma: no cover
    _HAS_TEXTUAL = False
    _IMPORT_ERROR = e

from .__main__ import _cell_input_text, _cell_output_text, _copy_to_clipboard

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)")
_ANSI_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_sgr_background(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(1)
        if raw == "":
            return match.group(0)

        try:
            params = [int(p) for p in raw.split(";") if p != ""]
        except ValueError:
            return match.group(0)

        keep: list[int] = []
        i = 0
        while i < len(params):
            p = params[i]
            # Drop background colors:
            # - 40-47, 100-107, 49
            # - 48;5;n and 48;2;r;g;b
            if p == 48:
                if i + 1 < len(params) and params[i + 1] == 5:
                    i += 3
                    continue
                if i + 1 < len(params) and params[i + 1] == 2:
                    i += 5
                    continue
                i += 1
                continue
            if p == 49 or 40 <= p <= 47 or 100 <= p <= 107:
                i += 1
                continue
            keep.append(p)
            i += 1

        if not keep:
            return ""
        return f"\x1b[{';'.join(str(p) for p in keep)}m"

    return _ANSI_SGR_RE.sub(repl, text)


def _sanitize_ansi_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_OSC_RE.sub("", text)

    def csi_repl(match: re.Match[str]) -> str:
        seq = match.group(0)
        return seq if seq.endswith("m") else ""

    text = _ANSI_CSI_RE.sub(csi_repl, text)
    text = re.sub(r"\x1b(?!\[[0-9;]*m)", "", text)
    text = _CONTROL_RE.sub("", text)
    # IPython tracebacks sometimes render "Traceback ...Cell ..." without a newline.
    text = re.sub(r"(Traceback \(most recent call last\))\s*Cell", r"\1\nCell", text)
    return text


def ensure_textual_available():
    if not _HAS_TEXTUAL:
        raise RuntimeError(
            f"Rich/Textual UI requested but not available: {_IMPORT_ERROR}"
        )


if _HAS_TEXTUAL:

    class CellView(Static):
        selected: bool = reactive(False)
        focus_region: str = reactive("input")  # input|output
        outputs_hidden: bool = reactive(False)
        in_visual: bool = reactive(False)  # in multi-cell visual selection range

        def __init__(self, index: int, total: int, cell: dict[str, Any]):
            super().__init__(id=f"cell-{index}")
            self.index = index
            self.total = total
            self.cell = cell

        def _render_input(self) -> Syntax | Text:
            ctype = self.cell.get("cell_type")
            source = _cell_input_text(self.cell)
            if ctype == "code":
                # Use Rich Syntax highlighter for Python
                return Syntax(source or "", "python", theme="monokai", word_wrap=True)
            # Markdown/raw rendered as plain text for now
            return Text(source or "", style="default")

        def _render_output(self) -> Text:
            txt = _cell_output_text(self.cell)
            if not (txt or "").strip():
                t = Text("[no text output]", style="grey50 italic")
                t.no_wrap = False
                t.overflow = "fold"
                return t

            txt = txt or ""
            if self._has_error_output():
                cleaned = _strip_sgr_background(_sanitize_ansi_text(txt))
                t = Text.from_ansi(f"{cleaned}\x1b[0m")
            elif "\x1b" in txt:
                cleaned = _sanitize_ansi_text(txt)
                t = Text.from_ansi(f"{cleaned}\x1b[0m")
            else:
                t = Text(_sanitize_ansi_text(txt), style="grey70")
            t.no_wrap = False
            t.overflow = "fold"
            return t

        def _has_error_output(self) -> bool:
            try:
                for out in self.cell.get("outputs", []) or []:
                    if out.get("output_type") == "error":
                        return True
            except Exception:
                pass
            txt = _cell_output_text(self.cell) or ""
            return "Traceback (most recent call last)" in txt

        def render(self):
            # Header: color indicates selection
            # Header styling
            if self.selected:
                header_style = "magenta bold"
                prefix = "▶ "
            elif self.in_visual:
                header_style = "yellow"
                prefix = "◆ "
            else:
                header_style = "grey50"
                prefix = "  "
            cell_type = self.cell.get("cell_type", "?")
            header_text = f"[ {self.index + 1}/{self.total} | {cell_type} ]"
            header = Text(prefix + header_text, style=header_style)

            # Input and Output as rounded panels with clear distinction
            input_border = (
                "cyan" if (self.selected and self.focus_region == "input") else "grey50"
            )
            if self.selected and self.focus_region == "output":
                output_border = "cyan"
            elif self._has_error_output():
                output_border = "red"
            else:
                output_border = "grey37"

            input_panel = Panel(
                self._render_input(),
                box=rich_box.ROUNDED,
                border_style=input_border,
                padding=(0, 1),
            )

            parts = [header, input_panel]
            out_text = _cell_output_text(self.cell)
            if out_text.strip():
                if self.outputs_hidden:
                    out_render = Text("[output hidden]", style="grey37 italic")
                else:
                    out_render = self._render_output()
                title = "Error" if self._has_error_output() else "Output"
                output_panel = Panel(
                    out_render,
                    title=title,
                    box=rich_box.ROUNDED,
                    border_style=output_border,
                    padding=(0, 1),
                )
                parts.append(output_panel)

            from rich.console import Group

            return Group(*parts)

    class NotebookApp(App):
        CSS = """
        Screen { layers: base; }
        VerticalScroll { padding: 0 1; }
        #searchbar { dock: top; padding: 0 1; }
        #footer { dock: bottom; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("j", "next_cell", "Next cell"),
            Binding("k", "prev_cell", "Prev cell"),
            # Override Textual's default focus-next so Tab toggles input/output
            Binding("tab", "toggle_focus", "Toggle focus", priority=True),
            Binding(
                "shift+tab",
                "toggle_focus",
                "Toggle focus",
                priority=True,
                show=False,
            ),
            Binding("y", "copy", "Copy"),
            Binding("ctrl+d", "half_down", "Half down"),
            Binding("ctrl+u", "half_up", "Half up"),
            Binding("z", "toggle_outputs", "Hide/show outputs"),
            Binding("shift+v", "toggle_visual", "Visual (inputs)"),
            Binding("/", "search_prompt", "Search"),
            Binding("escape", "cancel_search", "Cancel search", show=False),
            Binding("n", "search_next", "Next match"),
            Binding("shift+n", "search_prev", "Prev match", show=False),
            Binding("G", "go_last", "Last cell"),
            Binding("g", "go_prefix", "g (prefix)", show=False),
        ]

        def __init__(self, cells: list[dict[str, Any]]):
            super().__init__()
            self.cells = cells
            self.selected = 0
            self._cell_views: list[CellView] = []
            self.hide_outputs = False
            self.visual_mode = False
            self.visual_anchor = 0
            self.search_query: str = ""
            # For handling "gg" sequence
            self._g_pending = False
            self._g_timer = None

        def compose(self) -> ComposeResult:
            total = len(self.cells)
            views: list[CellView] = []
            for i, cell in enumerate(self.cells):
                cv = CellView(i, total, cell)
                views.append(cv)
            self._cell_views = views
            yield VerticalScroll(*views, id="cells")
            yield Footer(id="footer")

        def on_mount(self) -> None:
            self._update_selection(0)

        def action_next_cell(self) -> None:
            if self.selected < len(self._cell_views) - 1:
                self._update_selection(self.selected + 1)
                if self.visual_mode:
                    self._update_visual_flags()

        def action_prev_cell(self) -> None:
            if self.selected > 0:
                self._update_selection(self.selected - 1)
                if self.visual_mode:
                    self._update_visual_flags()

        def _scroll(self, delta: int) -> int:
            try:
                sc: VerticalScroll = self.query_one("#cells", VerticalScroll)
            except Exception:
                return 0
            # Determine current y offset and target
            current_y = 0
            try:
                off = getattr(sc, "scroll_offset", None)
                if off is not None and hasattr(off, "y"):
                    current_y = int(off.y)
                else:
                    current_y = int(getattr(sc, "scroll_y", 0))
            except Exception:
                pass
            target = max(0, current_y + int(delta))
            # Perform scroll using best available API
            try:
                sc.scroll_to(y=target, animate=False)
                return target
            except TypeError:
                try:
                    sc.scroll_to(0, target, animate=False)
                    return target
                except Exception:
                    pass
            except Exception:
                pass
            # Try duration=0 as a fallback to disable animation
            try:
                sc.scroll_to(y=target, duration=0)
                return target
            except Exception:
                pass
            try:
                sc.scroll_y = target  # type: ignore[attr-defined]
            except Exception:
                pass
            return target

        def _visible_cells(self) -> list[int]:
            # Return indices of cells currently intersecting the viewport
            try:
                sc: VerticalScroll = self.query_one("#cells", VerticalScroll)
            except Exception:
                return []
            try:
                sy = int(getattr(sc.scroll_offset, "y", getattr(sc, "scroll_y", 0)))
            except Exception:
                sy = int(getattr(sc, "scroll_y", 0))
            try:
                vh = (
                    int(getattr(sc.size, "height", 0))
                    or int(getattr(self.size, "height", 0))
                    or 20
                )
            except Exception:
                vh = 20
            vis: list[int] = []
            for i, cv in enumerate(self._cell_views):
                try:
                    y = int(getattr(cv.region, "y", 0))
                    h = max(1, int(getattr(cv.region, "height", 1)))
                except Exception:
                    continue
                if (y < sy + vh) and (y + h > sy):
                    vis.append(i)
            return vis

        def action_half_down(self) -> None:
            try:
                sc: VerticalScroll = self.query_one("#cells", VerticalScroll)
                height = (
                    int(getattr(sc.size, "height", 0))
                    or int(getattr(self.size, "height", 0))
                    or 20
                )
            except Exception:
                height = 20
            delta = max(5, height // 2)
            if self.visual_mode:
                self._set_visual(False)
            self._scroll(delta)
            vis = self._visible_cells()
            step = max(1, len(vis) // 2 or 1)
            new_idx = min(len(self._cell_views) - 1, self.selected + step)
            self._update_selection(new_idx, ensure_visible=False)

        def action_half_up(self) -> None:
            try:
                sc: VerticalScroll = self.query_one("#cells", VerticalScroll)
                height = (
                    int(getattr(sc.size, "height", 0))
                    or int(getattr(self.size, "height", 0))
                    or 20
                )
            except Exception:
                height = 20
            delta = -max(5, height // 2)
            if self.visual_mode:
                self._set_visual(False)
            self._scroll(delta)
            vis = self._visible_cells()
            step = max(1, len(vis) // 2 or 1)
            new_idx = max(0, self.selected - step)
            self._update_selection(new_idx, ensure_visible=False)

        # --- Search ---
        def _cell_text(self, idx: int) -> str:
            cell = self.cells[idx]
            return f"{_cell_input_text(cell)}\n{_cell_output_text(cell)}"

        def _search_from(self, start: int, step: int) -> int:
            if not self.search_query:
                return -1
            q = self.search_query.lower()
            n = len(self.cells)
            i = start
            for _ in range(n):
                text = self._cell_text(i).lower()
                if q in text:
                    return i
                i = (i + step) % n
            return -1

        def action_search_prompt(self) -> None:
            # Open a simple input bar at the top
            if self.query("#searchbar"):
                sb = self.query_one("#searchbar", Input)
                self.call_after_refresh(sb.focus)
                return
            sb = Input(placeholder="/ search", id="searchbar")
            sb.value = self.search_query
            self.mount(sb)
            self.call_after_refresh(sb.focus)

        def action_cancel_search(self) -> None:
            if self.query("#searchbar"):
                try:
                    self.query_one("#searchbar", Input).remove()
                except Exception:
                    pass

        def on_input_submitted(self, event: Input.Submitted) -> None:
            if event.input.id == "searchbar":
                self.search_query = event.value or ""
                event.input.remove()
                if self.search_query:
                    self.action_search_next()
                else:
                    self.notify("Search cleared")

        def on_input_blurred(self, event: Input.Blurred) -> None:
            if event.input.id == "searchbar":
                # Remove the bar when focus leaves
                try:
                    event.input.remove()
                except Exception:
                    pass

        def action_search_next(self) -> None:
            if not self.search_query:
                self.action_search_prompt()
                return
            start = (self.selected + 1) % len(self.cells)
            idx = self._search_from(start, +1)
            if idx >= 0:
                self._update_selection(idx)
                if self.visual_mode:
                    self._update_visual_flags()
                self.notify(f"Found at cell {idx + 1}")
            else:
                self.notify("No match")

        def action_search_prev(self) -> None:
            if not self.search_query:
                self.action_search_prompt()
                return
            start = (self.selected - 1) % len(self.cells)
            idx = self._search_from(start, -1)
            if idx >= 0:
                self._update_selection(idx)
                if self.visual_mode:
                    self._update_visual_flags()
                self.notify(f"Found at cell {idx + 1}")
            else:
                self.notify("No match")

        # --- Go to first/last (gg / G) ---
        def action_go_last(self) -> None:
            last = len(self._cell_views) - 1
            self._update_selection(last)
            if self.visual_mode:
                self._update_visual_flags()

        def _g_timeout(self) -> None:
            self._g_pending = False
            self._g_timer = None

        def action_go_prefix(self) -> None:
            # Handle the "gg" sequence with a short timeout
            if self._g_pending:
                if self._g_timer is not None:
                    try:
                        self._g_timer.pause()
                    except Exception:
                        pass
                    self._g_timer = None
                self._g_pending = False
                self._update_selection(0)
                if self.visual_mode:
                    self._update_visual_flags()
                return
            # Arm pending state and timer
            self._g_pending = True
            try:
                self._g_timer = self.set_timer(0.7, self._g_timeout)
            except Exception:
                self._g_timer = None

        def action_toggle_focus(self) -> None:
            if self.visual_mode:
                # In visual mode we always operate on input text; ignore toggling
                self.notify(
                    "Visual mode: focus locked to Input", severity="information"
                )
                return
            cv = self._cell_views[self.selected]
            # Only allow switching to output if there is text output
            if cv.focus_region == "input":
                txt = _cell_output_text(cv.cell)
                if not txt.strip():
                    # No output available; stay on input (optional hint)
                    self.notify("No text output for this cell", severity="warning")
                    return
                cv.focus_region = "output"
            else:
                cv.focus_region = "input"
            cv.refresh()

        def action_copy(self) -> None:
            # If in visual mode, copy all selected inputs
            if self.visual_mode:
                a, b = sorted((self.visual_anchor, self.selected))
                pieces: list[str] = []
                count = 0
                for i in range(a, b + 1):
                    cell = self.cells[i]
                    if cell.get("cell_type") != "code":
                        continue
                    src = _cell_input_text(cell)
                    if src.strip():
                        pieces.append(src.rstrip("\n"))
                        count += 1
                text = "\n\n".join(pieces)
                if not text:
                    self.notify("No code in selection", severity="warning")
                    return
                ok, msg = _copy_to_clipboard(text)
                self.notify(f"Copied {count} cell(s): {msg}")
                # Exit visual mode after yank
                self._set_visual(False)
                return

            # Normal single-cell copy of focused region
            cv = self._cell_views[self.selected]
            cell = cv.cell
            if cv.focus_region == "input":
                text = _cell_input_text(cell)
            else:
                text = _cell_output_text(cell)
                if not text.strip():
                    self.notify("No text output to copy", severity="warning")
                    return
            ok, msg = _copy_to_clipboard(text)
            self.notify(msg)

        def action_toggle_outputs(self) -> None:
            # Toggle global hide/show for outputs
            self.hide_outputs = not self.hide_outputs
            for cv in self._cell_views:
                cv.outputs_hidden = self.hide_outputs
                cv.refresh()

        def _update_selection(
            self, new_index: int, ensure_visible: bool = True
        ) -> None:
            self._cell_views[self.selected].selected = False
            self._cell_views[self.selected].refresh()
            self.selected = new_index
            sel = self._cell_views[self.selected]
            sel.selected = True
            sel.refresh()
            # scroll into view
            if ensure_visible:
                self.call_after_refresh(lambda: sel.scroll_visible())

        def _update_visual_flags(self) -> None:
            # Mark cells in visual range
            for i, cv in enumerate(self._cell_views):
                in_range = self.visual_mode and (
                    min(self.visual_anchor, self.selected)
                    <= i
                    <= max(self.visual_anchor, self.selected)
                )
                cv.in_visual = in_range
                cv.refresh()

        def _set_visual(self, on: bool) -> None:
            self.visual_mode = on
            if on:
                self.visual_anchor = self.selected
            # Update flags
            self._update_visual_flags()
            # Ensure focus is Input in visual mode
            if on:
                self._cell_views[self.selected].focus_region = "input"
            else:
                # Clear markers
                for cv in self._cell_views:
                    cv.in_visual = False
                    cv.refresh()

        def action_toggle_visual(self) -> None:
            self._set_visual(not self.visual_mode)
            self.notify("Visual mode" if self.visual_mode else "Visual off")

    def run_textual_app(cells: list[dict[str, Any]]) -> int:
        ensure_textual_available()
        app = NotebookApp(cells)
        app.run()
        return 0
else:

    def run_textual_app(cells: list[dict[str, Any]]) -> int:  # pragma: no cover
        ensure_textual_available()
        return 1
