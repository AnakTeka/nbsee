"""Vertical layout table: prefix sums of cell heights, y <-> (cell, row)."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from itertools import accumulate


class Layout:
    """Heights of all cells; each is an estimate until `set_heights` fixes it."""

    def __init__(self, estimates: Iterable[int] = ()) -> None:
        self.reset(estimates)

    def reset(self, estimates: Iterable[int]) -> None:
        self._heights = list(estimates)
        self._exact = [False] * len(self._heights)
        self._rebuild()

    def _rebuild(self) -> None:
        self._starts = [0, *accumulate(self._heights)]

    def __len__(self) -> int:
        return len(self._heights)

    @property
    def total(self) -> int:
        return self._starts[-1]

    def top(self, cell: int) -> int:
        return self._starts[cell]

    def height(self, cell: int) -> int:
        return self._heights[cell]

    def is_exact(self, cell: int) -> bool:
        return self._exact[cell]

    def locate(self, y: int) -> tuple[int, int]:
        """(cell, row within the cell) at virtual row y, clamped to the ends."""
        if not self._heights:
            return 0, 0
        y = max(0, min(y, self.total - 1))
        cell = bisect_right(self._starts, y) - 1
        return cell, y - self._starts[cell]

    def set_heights(self, exact: Mapping[int, int]) -> None:
        for cell, height in exact.items():
            self._heights[cell] = height
            self._exact[cell] = True
        self._rebuild()

    def set_estimates(self, estimates: Mapping[int, int]) -> None:
        """Replace heights with fresh estimates (marks them inexact)."""
        for cell, height in estimates.items():
            self._heights[cell] = height
            self._exact[cell] = False
        self._rebuild()

    def pending(self) -> list[int]:
        return [i for i, ok in enumerate(self._exact) if not ok]
