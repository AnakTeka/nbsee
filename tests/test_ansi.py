import pytest

from nbsee.ansi import sanitize, strip_background, to_plain

RED = "\x1b[31m"
RESET = "\x1b[0m"


def test_sanitize_keeps_sgr_only():
    assert sanitize(f"{RED}hi{RESET}") == f"{RED}hi{RESET}"
    assert sanitize("a\x1b[2Kb\x1b[1;1Hc\x1b[?25lc") == "abcc"


def test_sanitize_drops_osc_with_either_terminator():
    assert sanitize("a\x1b]0;title\x07b") == "ab"
    assert sanitize("a\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\b") == "alinkb"


def test_sanitize_drops_lone_escape_and_control_chars():
    assert sanitize("a\x1bb\x00c\x07d\x7fe") == "abcde"
    assert sanitize("tab\tnl\nok") == "tab\tnl\nok"


def test_sanitize_normalises_crlf():
    assert sanitize("a\r\nb\r\n") == "a\nb\n"


def test_sanitize_carriage_return_overwrites_line():
    assert sanitize("10%\r50%\r100%\ndone") == "100%\ndone"
    assert sanitize("keep\nonly\r\n") == "keep\nonly\n"


def test_sanitize_splits_glued_traceback_header():
    glued = "Traceback (most recent call last)Cell In[1]"
    assert sanitize(glued) == "Traceback (most recent call last)\nCell In[1]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\x1b[41mx\x1b[0m", "x\x1b[0m"),
        ("\x1b[31;41mx", "\x1b[31mx"),
        ("\x1b[41mx", "x"),
        ("\x1b[48;5;7mx", "x"),
        ("\x1b[48;2;1;2;3mx", "x"),
        ("\x1b[38;5;28;48;5;7mx", "\x1b[38;5;28mx"),
        ("\x1b[38;2;1;2;3mx", "\x1b[38;2;1;2;3mx"),
        ("\x1b[1;103mx", "\x1b[1mx"),
        ("\x1b[49mx", "x"),
        ("\x1b[mx", "\x1b[mx"),
        ("\x1b[0mx", "\x1b[0mx"),
        ("\x1b[1;32mx", "\x1b[1;32mx"),
    ],
)
def test_strip_background(text, expected):
    assert strip_background(text) == expected


def test_to_plain_removes_everything():
    raw = f"{RED}err\x1b[0m\x1b[2K\x1b]0;t\x07\r\nok\x1b[48;5;7m!"
    assert to_plain(raw) == "err\nok!"
    assert "\x1b" not in to_plain("\x1b[1;31;44mA\x1b[0m\x1bZ")
