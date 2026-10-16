"""Heading outline sidebar that tracks the cursor."""

from __future__ import annotations

from bisect import bisect_right

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..model import Notebook


class OutlinePane(OptionList):
    DEFAULT_CSS = """
    OutlinePane {
        width: 32;
        display: none;
        border-right: solid $primary-muted;
    }
    OutlinePane.-open { display: block; }
    """

    def __init__(self, nb: Notebook) -> None:
        super().__init__(id="outline")
        self.cells: list[int] = []  # cell index of each entry
        self.populate(nb)

    def populate(self, nb: Notebook) -> None:
        entries = [
            (c.index, level, title) for c in nb.cells for level, title in c.headings
        ]
        self.cells = [cell for cell, _, _ in entries]
        self.set_options(
            Option(Text(f"{'  ' * (level - 1)}{title}", no_wrap=True))
            for _, level, title in entries
        )

    def track(self, cursor: int) -> None:
        """Highlight the last heading at or before the cursor cell."""
        i = bisect_right(self.cells, cursor) - 1
        self.highlighted = i if i >= 0 else None

    def cell_at(self, index: int) -> int:
        return self.cells[index]
