import pytest

from nbsee.selectors import SelectorError, parse_cells


@pytest.mark.parametrize(
    ("spec", "count", "expected"),
    [
        ("1", 5, [0]),
        ("5", 5, [4]),
        ("3:4", 5, [2, 3]),
        ("3:3", 5, [2]),
        (":2", 5, [0, 1]),
        ("4:", 5, [3, 4]),
        (":", 3, [0, 1, 2]),
        ("-1", 5, [4]),
        ("-5", 5, [0]),
        ("-3:", 5, [2, 3, 4]),
        ("2:-2", 5, [1, 2, 3]),
        ("-4:-2", 5, [1, 2, 3]),
        ("1,3:4,-1", 5, [0, 2, 3, 4]),
        ("3,1,3", 5, [0, 2]),
        ("1:3,2:4", 5, [0, 1, 2, 3]),
        (" 2 , 4 : 5 ", 5, [1, 3, 4]),
        ("+2", 5, [1]),
        ("1", 1, [0]),
    ],
)
def test_valid_specs(spec, count, expected):
    assert parse_cells(spec, count) == expected


@pytest.mark.parametrize(
    ("spec", "count", "message"),
    [
        ("", 5, "empty item"),
        ("1,,2", 5, "empty item"),
        ("1,", 5, "empty item"),
        (",1", 5, "empty item"),
        ("a", 5, "invalid cell number"),
        ("1.5", 5, "invalid cell number"),
        ("1_0", 10, "invalid cell number"),
        ("1:x", 5, "invalid cell number"),
        ("1:2:3", 5, "invalid cell number"),
        ("0", 5, "start at 1"),
        ("0:3", 5, "start at 1"),
        ("2:0", 5, "start at 1"),
        ("6", 5, "out of range"),
        ("-6", 5, "out of range"),
        ("1:6", 5, "out of range"),
        ("9:", 5, "out of range"),
        ("4:2", 5, "backwards"),
        ("-1:2", 5, "backwards"),
        ("1", 0, "out of range"),
        (":", 0, "no cells"),
    ],
)
def test_invalid_specs(spec, count, message):
    with pytest.raises(SelectorError, match=message):
        parse_cells(spec, count)


def test_error_message_is_one_based_and_pluralised():
    with pytest.raises(
        SelectorError, match=r"cell 7 out of range \(notebook has 1 cell\)"
    ):
        parse_cells("7", 1)
    with pytest.raises(SelectorError, match=r"notebook has 3 cells"):
        parse_cells("7", 3)
