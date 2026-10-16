import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import nbsee
from nbsee import cli
from nbsee.cli import main
from nbsee.export import export, summarize
from nbsee.model import load

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = str(FIXTURES / "basic.ipynb")


def run(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.fixture
def viewer(monkeypatch):
    """Pretend stdout is a tty and record what the viewer would receive."""
    calls = []

    def fake(nb, start):
        calls.append((nb, start))
        return 7

    monkeypatch.setattr(cli, "_run_viewer", fake)
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    return calls


def test_cat_defaults_to_markdown(capsys):
    code, out, err = run(capsys, "cat", BASIC)
    assert (code, err) == (0, "")
    assert out == export(load(BASIC))
    assert out.startswith("<!-- cell 1 · markdown -->\n# Title")


@pytest.mark.parametrize("fmt", ["md", "text", "py", "json"])
def test_cat_formats(capsys, fmt):
    code, out, _ = run(capsys, "cat", BASIC, "-f", fmt)
    assert code == 0
    assert out == export(load(BASIC), fmt=fmt)
    run(capsys, "cat", BASIC, "--format", fmt)


def test_cat_cells_selection(capsys):
    code, out, _ = run(capsys, "cat", BASIC, "-c", "2,-1")
    assert code == 0
    assert out == export(load(BASIC), [1, 4])
    assert run(capsys, "cat", BASIC, "--cells", "2,-1")[1] == out


def test_cat_only_and_no_outputs(capsys):
    nb = load(BASIC)
    assert run(capsys, "cat", BASIC, "--only", "input")[1] == export(nb, only="input")
    assert run(capsys, "cat", BASIC, "--only", "output")[1] == export(nb, only="output")
    assert run(capsys, "cat", BASIC, "--no-outputs")[1] == export(nb, only="input")


def test_cat_only_conflicts_with_no_outputs(capsys):
    code, out, err = run(capsys, "cat", BASIC, "--only", "output", "--no-outputs")
    assert (code, out) == (1, "")
    assert "not allowed with" in err and err.startswith("usage:")


@pytest.mark.parametrize("spec", ["0", "9", "x", "3:1", "1,,2", ""])
def test_bad_selector_exits_1_with_one_line_message(capsys, spec):
    code, out, err = run(capsys, "cat", BASIC, "-c", spec)
    assert (code, out) == (1, "")
    assert err.startswith("nbsee: ") and err.count("\n") == 1
    assert "Traceback" not in err


def test_cat_from_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(Path(BASIC).read_text()))
    code, out, _ = run(capsys, "cat", "-", "-c", "1")
    assert code == 0
    assert out == export(load(BASIC), [0])


def test_ls_from_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(Path(BASIC).read_text()))
    code, out, _ = run(capsys, "ls", "-")
    assert code == 0
    assert out.splitlines()[1].endswith("import math")


def test_stdin_errors(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("{nope"))
    code, out, err = run(capsys, "cat", "-")
    assert (code, out) == (2, "")
    assert err.startswith("nbsee: invalid JSON")

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert run(capsys, "ls", "-")[0] == 2

    monkeypatch.setattr("sys.stdin", io.StringIO("[1]"))
    assert (
        run(capsys, "ls", "-")[2]
        == "nbsee: not a notebook: top level is not a JSON object\n"
    )


def test_stdin_undecodable_bytes(capsys, monkeypatch):
    stdin = io.TextIOWrapper(io.BytesIO(b"\xff\xfe"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stdin)
    code, _, err = run(capsys, "cat", "-")
    assert code == 2 and err.startswith("nbsee: cannot read input")


def test_ls_golden(capsys, monkeypatch):
    monkeypatch.setattr(
        cli.shutil, "get_terminal_size", lambda: os.terminal_size((200, 24))
    )
    code, out, _ = run(capsys, "ls", BASIC)
    assert code == 0
    assert out == (
        "   1  markdown    5L  -                Title\n"
        "   2  code        2L  stdout           import math\n"
        "   3  code        1L  result           1 + 1\n"
        "   4  raw         1L  -                raw note\n"
        "   5  code        1L  -                x = 1\n"
    )


def test_ls_uses_terminal_width(capsys, monkeypatch):
    monkeypatch.setattr(
        cli.shutil, "get_terminal_size", lambda: os.terminal_size((20, 24))
    )
    out = run(capsys, "ls", BASIC)[1]
    assert out.splitlines() == summarize(load(BASIC), 20)
    assert all(len(line) <= 20 for line in out.splitlines())


def test_ls_json(capsys):
    code, out, _ = run(capsys, "ls", BASIC, "--json")
    data = json.loads(out)
    assert code == 0
    assert [d["index"] for d in data] == [1, 2, 3, 4, 5]
    assert data[1]["outputs"] == ["stdout"]


def test_empty_notebook(capsys, viewer):
    empty = str(FIXTURES / "empty.ipynb")
    assert run(capsys, "ls", empty) == (0, "", "")
    assert run(capsys, "cat", empty) == (0, "", "")
    assert run(capsys, "cat", empty, "-f", "text") == (0, "", "")
    assert run(capsys, "cat", empty, "-f", "json") == (0, "[]\n", "")
    assert run(capsys, "ls", empty, "--json") == (0, "[]\n", "")
    code, out, err = run(capsys, "view", empty)
    assert (code, out, err) == (1, "", "nbsee: notebook has no cells\n")
    assert viewer == []


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("missing.ipynb", "nbsee: file not found:"),
        ("malformed.json", "nbsee: invalid JSON at line 2 col 1"),
        ("nbformat3.ipynb", "nbsee: nbformat 3 notebooks are not supported"),
        ("", "nbsee: is a directory:"),
    ],
)
@pytest.mark.parametrize("command", ["cat", "ls"])
def test_notebook_errors_exit_2(capsys, command, name, message):
    code, out, err = run(capsys, command, str(FIXTURES / name))
    assert (code, out) == (2, "")
    assert err.startswith(message) and err.count("\n") == 1


def test_view_is_default_command(capsys, viewer):
    code, out, _ = run(capsys, BASIC)
    assert (code, out) == (7, "")
    ((nb, start),) = viewer
    assert nb.path == BASIC and start == 0


def test_view_cell_is_one_based(capsys, viewer):
    run(capsys, "view", BASIC, "--cell", "3")
    run(capsys, BASIC, "--cell", "-1")
    assert [start for _, start in viewer] == [2, 4]


@pytest.mark.parametrize("cell", ["0", "6", "x"])
def test_view_bad_cell(capsys, viewer, cell):
    code, _, err = run(capsys, "view", BASIC, "--cell", cell)
    assert code == 1 and err.startswith(("nbsee: ", "usage:"))
    assert viewer == []


def test_view_refuses_stdin_and_non_tty(capsys, viewer, monkeypatch):
    code, _, err = run(capsys, "view", "-")
    assert code == 1 and "stdin" in err

    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    code, _, err = run(capsys, "view", BASIC)
    assert code == 1 and "needs a terminal" in err
    assert viewer == []


def test_view_load_errors_exit_2(capsys, viewer):
    assert run(capsys, "view", str(FIXTURES / "malformed.json"))[0] == 2
    assert run(capsys, "view", str(FIXTURES / "nbformat3.ipynb"))[0] == 2
    assert viewer == []


def test_double_dash_opens_file_named_like_a_command(
    capsys, viewer, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    code, _, err = run(capsys, "--", "cat")
    assert code == 2 and "file not found: cat" in err
    (tmp_path / "cat").write_text('{"cells": [{"cell_type": "raw", "source": "x"}]}')
    assert run(capsys, "--", "cat")[0] == 7


def test_usage_errors_exit_1(capsys):
    for argv in (
        [],
        ["--bogus"],
        ["-"],
        ["cat"],
        ["ls", "a", "b"],
        ["cat", "x", "-f", "xml"],
    ):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (1, ""), argv
        assert "usage:" in err and "nbsee:" in err, argv


def test_version_and_help(capsys):
    code, out, _ = run(capsys, "--version")
    assert (code, out) == (0, f"nbsee {nbsee.__version__}\n")
    code, out, _ = run(capsys, "cat", "--help")
    assert code == 0 and "--only" in out


def test_color_resolution(capsys, monkeypatch):
    errors = str(FIXTURES / "errors.ipynb")
    args = ("cat", errors, "-f", "text")
    monkeypatch.delenv("NO_COLOR", raising=False)

    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    assert "\x1b" not in run(capsys, *args)[1]
    assert "\x1b" not in run(capsys, *args, "--color", "never")[1]
    assert "\x1b[" in run(capsys, *args, "--color", "always")[1]

    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    assert "\x1b[" in run(capsys, *args)[1]
    assert "\x1b" not in run(capsys, *args, "--color", "never")[1]
    monkeypatch.setenv("NO_COLOR", "1")
    assert "\x1b" not in run(capsys, *args)[1]
    assert "\x1b[" in run(capsys, *args, "--color", "always")[1]


def test_color_is_ignored_for_non_text_formats(capsys):
    errors = str(FIXTURES / "errors.ipynb")
    for fmt in ("md", "py", "json"):
        out = run(capsys, "cat", errors, "-f", fmt, "--color", "always")[1]
        assert "\x1b" not in out


def test_broken_pipe_is_quiet(capsys, monkeypatch):
    class Closed(io.StringIO):
        def write(self, text):
            raise BrokenPipeError

    monkeypatch.setattr("sys.stdout", Closed())
    assert main(["cat", BASIC]) == 0


def test_main_without_argv_reads_sys_argv(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["nbsee", "ls", BASIC])
    assert main() == 0
    assert "import math" in capsys.readouterr().out


def test_python_dash_m_entry_point():
    proc = subprocess.run(
        [sys.executable, "-m", "nbsee", "cat", BASIC, "-c", "1"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("<!-- cell 1 · markdown -->")
