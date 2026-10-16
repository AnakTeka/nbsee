"""Status bar: file, position, mode, region, search counter, transient message."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from ..model import Notebook, Region
from ..state import Mode, ViewState

_MODE_LABEL = {
    Mode.NORMAL: "NORMAL",
    Mode.VISUAL: "VISUAL",
    Mode.LINE: "LINE",
    Mode.LINE_VISUAL: "LINE-VISUAL",
}


def status_text(nb: Notebook, state: ViewState) -> str:
    """Left-hand status line, e.g. `nb.ipynb  3/42  NORMAL  input  /loss 2/9`."""
    name = Path(nb.path).name if nb.path else "<stdin>"
    parts = [
        name,
        f"{state.cursor + 1}/{len(nb.cells)}" if nb.cells else "0/0",
        _MODE_LABEL[state.mode],
        "output" if state.region is Region.OUTPUT else "input",
    ]
    if state.mode in (Mode.LINE, Mode.LINE_VISUAL):
        parts.append(f"L{state.line + 1}")
    if state.search is not None:
        s = state.search
        shown = 0 if s.current is None else s.current + 1
        parts.append(f"/{s.query} {shown}/{len(s.hits)}")
    return "  ".join(parts)


class StatusBar(Horizontal):
    DEFAULT_CSS = """
    StatusBar { height: 1; background: $panel; }
    StatusBar > #status-left { width: 1fr; padding: 0 1; }
    StatusBar > #status-message { width: auto; padding: 0 1; color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.left_text = ""
        self.message = ""

    def compose(self) -> ComposeResult:
        yield Static(id="status-left")
        yield Static(id="status-message")

    def show(self, nb: Notebook, state: ViewState) -> None:
        self.left_text = status_text(nb, state)
        self.query_one("#status-left", Static).update(Text(self.left_text))

    def flash(self, message: str) -> None:
        self.message = message
        self.query_one("#status-message", Static).update(Text(message))
