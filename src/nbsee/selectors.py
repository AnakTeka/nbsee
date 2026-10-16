"""Cell selectors such as "3", "3:7", "-1", "1,4:6" (1-based, inclusive)."""

from __future__ import annotations

import re

_INT_RE = re.compile(r"[+-]?[0-9]+", re.ASCII)


class SelectorError(ValueError):
    """Raised for a malformed or out-of-range cell selector."""


def _number(token: str, spec: str) -> int:
    token = token.strip()
    if not _INT_RE.fullmatch(token):
        raise SelectorError(f"invalid cell number {token!r} in {spec!r}")
    value = int(token)
    if value == 0:
        raise SelectorError(f"cell numbers start at 1 (got 0 in {spec!r})")
    return value


def _resolve(number: int, count: int) -> int:
    """Map a 1-based (or negative, from the end) number to a 0-based index."""
    index = number - 1 if number > 0 else count + number
    if not 0 <= index < count:
        raise SelectorError(
            f"cell {number} out of range (notebook has {count} "
            f"cell{'' if count == 1 else 's'})"
        )
    return index


def _parse_item(item: str, spec: str, count: int) -> range:
    if not item.strip():
        raise SelectorError(f"empty item in {spec!r}")
    if ":" not in item:
        index = _resolve(_number(item, spec), count)
        return range(index, index + 1)
    if count == 0:
        raise SelectorError("notebook has no cells")
    head, _, tail = item.partition(":")
    start = _resolve(_number(head, spec), count) if head.strip() else 0
    stop = _resolve(_number(tail, spec), count) if tail.strip() else count - 1
    if start > stop:
        raise SelectorError(f"range {item.strip()!r} is backwards")
    return range(start, stop + 1)


def parse_cells(spec: str, count: int) -> list[int]:
    """1-based inclusive spec -> sorted, de-duplicated 0-based indices."""
    picked: set[int] = set()
    for item in spec.split(","):
        picked.update(_parse_item(item, spec, count))
    return sorted(picked)
