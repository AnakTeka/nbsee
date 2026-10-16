"""Plain-text search across a notebook (smart-case, literal, linear)."""

from __future__ import annotations

from dataclasses import dataclass

from .model import Notebook, Region


@dataclass(frozen=True, slots=True)
class Hit:
    cell: int
    region: Region
    line: int
    start: int  # offsets into cell.lines(region)[line]
    end: int


def case_sensitive(query: str) -> bool:
    """Smart-case: sensitive only when the query has an uppercase character."""
    return any(c.isupper() for c in query)


def _fold(text: str) -> str:
    """Lower-case without changing length, so offsets stay valid."""
    if text.islower() or not text:
        return text
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _scan(line: str, needle: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = line.find(needle)
    while pos != -1:
        spans.append((pos, pos + len(needle)))
        pos = line.find(needle, pos + len(needle))
    return spans


def find(nb: Notebook, query: str) -> tuple[Hit, ...]:
    """All non-overlapping hits ordered by cell, INPUT before OUTPUT, line, start."""
    if not query:
        return ()
    sensitive = case_sensitive(query)
    needle = query if sensitive else _fold(query)
    hits: list[Hit] = []
    for cell in nb.cells:
        for region in (Region.INPUT, Region.OUTPUT):
            for number, line in enumerate(cell.lines(region)):
                haystack = line if sensitive else _fold(line)
                for start, end in _scan(haystack, needle):
                    hits.append(Hit(cell.index, region, number, start, end))
    return tuple(hits)
