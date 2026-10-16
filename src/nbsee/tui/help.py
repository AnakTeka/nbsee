"""`?` overlay generated from the app's binding table."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import groupby

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .keys import KeyDef


def help_table(keymap: Sequence[KeyDef]) -> Table:
    """Rows are grouped; a key without a label is folded into its neighbour."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for group, rows in groupby(keymap, key=lambda k: k.group):
        table.add_row(Text(""), Text(""))
        table.add_row(Text(group, style="bold underline"), Text(""))
        for key in rows:
            if key.label:
                table.add_row(key.label, key.desc)
    return table


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > VerticalScroll {
        width: 64; max-height: 90%; padding: 0 2;
        border: round $primary; background: $surface;
    }
    """
    BINDINGS = [
        Binding("escape,q,question_mark", "close", "Close", show=False),
    ]

    def __init__(self, keymap: Sequence[KeyDef]) -> None:
        super().__init__()
        self._keymap = keymap

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(Text("nbsee keys  (Esc to close)", style="bold"))
            yield Static(help_table(self._keymap), id="help-table")

    def action_close(self) -> None:
        self.dismiss(None)
