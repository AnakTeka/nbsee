from __future__ import annotations

import pytest

from nbsee.model import Region, parse
from nbsee.search import Hit, case_sensitive, find


def make(*cells: tuple[str, str, str | None]):
    """Cells are (kind, source, stdout-or-None)."""
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


@pytest.mark.parametrize(
    ("query", "expected"),
    [("abc", False), ("", False), ("aBc", True), ("ÄB", True), ("123", False)],
)
def test_case_sensitive(query, expected):
    assert case_sensitive(query) is expected


def test_empty_query_and_empty_notebook():
    assert find(make(("code", "x", None)), "") == ()
    assert find(make(), "x") == ()


def test_order_and_offsets():
    nb = make(
        ("code", "foo = 1\nbar foo", "out foo\nfoo foo"),
        ("markdown", "no match", None),
        ("code", "foo", None),
    )
    assert find(nb, "foo") == (
        Hit(0, Region.INPUT, 0, 0, 3),
        Hit(0, Region.INPUT, 1, 4, 7),
        Hit(0, Region.OUTPUT, 0, 4, 7),
        Hit(0, Region.OUTPUT, 1, 0, 3),
        Hit(0, Region.OUTPUT, 1, 4, 7),
        Hit(2, Region.INPUT, 0, 0, 3),
    )


def test_non_overlapping_left_to_right():
    nb = make(("code", "aaaa", None))
    hits = find(nb, "aa")
    assert [(h.start, h.end) for h in hits] == [(0, 2), (2, 4)]


def test_smart_case():
    nb = make(("code", "Foo foo FOO", None))
    assert [h.start for h in find(nb, "foo")] == [0, 4, 8]  # insensitive
    assert [h.start for h in find(nb, "Foo")] == [0]  # sensitive
    assert [h.start for h in find(nb, "FOO")] == [8]


def test_hits_in_outputs_only():
    nb = make(("code", "x = 1", "needle here"))
    hits = find(nb, "needle")
    assert [(h.cell, h.region, h.line) for h in hits] == [(0, Region.OUTPUT, 0)]


def test_query_is_literal_not_regex():
    nb = make(("code", "a.c abc (x) [y]", None))
    assert [h.start for h in find(nb, "a.c")] == [0]
    assert [h.start for h in find(nb, "(x)")] == [8]
    assert [h.start for h in find(nb, "[y")] == [12]
    assert find(nb, ".*") == ()


def test_ansi_output_is_searched_as_plain():
    nb = parse(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": "",
                    "outputs": [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": "\x1b[31mred\x1b[0m text",
                        }
                    ],
                }
            ]
        }
    )
    assert find(nb, "red text") == (Hit(0, Region.OUTPUT, 0, 0, 8),)


def test_offsets_survive_length_changing_lowercase():
    nb = make(("code", "İx x", None))  # 'İ'.lower() is two characters
    assert [(h.start, h.end) for h in find(nb, "x")] == [(1, 2), (3, 4)]
