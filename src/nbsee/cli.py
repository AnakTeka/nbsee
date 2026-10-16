"""Command-line interface: `nbsee [view] FILE`, `nbsee ls`, `nbsee cat`."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from collections.abc import Sequence
from typing import NoReturn

from . import __version__
from .export import export, summarize, summarize_json
from .model import Notebook, NotebookError, load
from .selectors import SelectorError, parse_cells

COMMANDS = ("view", "ls", "cat")
EXIT_USAGE = 1
EXIT_NOTEBOOK = 2


class UsageError(Exception):
    """Invalid command-line usage that argparse cannot detect itself."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"nbsee: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="nbsee", description="Read Jupyter notebooks in a terminal.")
    parser.add_argument("--version", action="version", version=f"nbsee {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    view = sub.add_parser("view", help="open the interactive viewer (default)")
    view.add_argument("file", metavar="FILE")
    view.add_argument("--cell", type=int, metavar="N", help="start at cell N (1-based)")

    ls = sub.add_parser("ls", help="one line per cell")
    ls.add_argument("file", metavar="FILE|-")
    ls.add_argument("--json", action="store_true", help="machine-readable output")

    cat = sub.add_parser("cat", help="print cells as md, text, py or json")
    cat.add_argument("file", metavar="FILE|-")
    cat.add_argument("-c", "--cells", metavar="SPEC", help="e.g. 3, 3:7, :5, -1, 1,4:6")
    cat.add_argument(
        "-f", "--format", choices=("md", "text", "py", "json"), default="md"
    )
    cat.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    what = cat.add_mutually_exclusive_group()
    what.add_argument("--only", choices=("input", "output"))
    what.add_argument("--no-outputs", action="store_true", help="same as --only input")
    return parser


def _with_default_command(argv: list[str]) -> list[str]:
    """`nbsee FILE` means `nbsee view FILE`; `nbsee -- cat` opens a file `cat`."""
    if argv and argv[0] not in COMMANDS and (argv[0] == "--" or argv[0][0] != "-"):
        return ["view", *argv]
    return argv


def _is_tty() -> bool:
    return sys.stdout.isatty()


def _use_color(choice: str) -> bool:
    if choice == "auto":
        return _is_tty() and not os.environ.get("NO_COLOR")
    return choice == "always"


def _run_viewer(nb: Notebook, start: int) -> int:
    from .tui.app import run

    return run(nb, start=start)


def _read(file: str) -> Notebook:
    return load(sys.stdin if file == "-" else file)


def _emit(text: str) -> int:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # Reader went away (e.g. `| head`); avoid a second error at exit.
        with contextlib.suppress(OSError, ValueError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0


def _cmd_view(args: argparse.Namespace) -> int:
    if args.file == "-":
        raise UsageError("the viewer needs a file; use `nbsee cat -` for stdin")
    if not _is_tty():
        raise UsageError("the viewer needs a terminal; use `nbsee cat` for pipes")
    nb = _read(args.file)
    if not nb.cells:
        raise UsageError("notebook has no cells")
    start = 0 if args.cell is None else parse_cells(str(args.cell), len(nb.cells))[0]
    return _run_viewer(nb, start)


def _cmd_ls(args: argparse.Namespace) -> int:
    nb = _read(args.file)
    if args.json:
        return _emit(summarize_json(nb))
    width = shutil.get_terminal_size().columns
    lines = summarize(nb, width)
    return _emit("".join(f"{line}\n" for line in lines))


def _cmd_cat(args: argparse.Namespace) -> int:
    nb = _read(args.file)
    if not nb.cells and args.format != "json":
        return 0
    cells = None if args.cells is None else parse_cells(args.cells, len(nb.cells))
    only = "input" if args.no_outputs else args.only
    color = args.format == "text" and _use_color(args.color)
    return _emit(export(nb, cells, fmt=args.format, only=only, color=color))


_HANDLERS = {"view": _cmd_view, "ls": _cmd_ls, "cat": _cmd_cat}


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _build_parser().parse_args(_with_default_command(raw))
        return _HANDLERS[args.command](args)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else int(e.code is not None)
    except NotebookError as e:
        print(f"nbsee: {e}", file=sys.stderr)
        return EXIT_NOTEBOOK
    except (SelectorError, UsageError) as e:
        print(f"nbsee: {e}", file=sys.stderr)
        return EXIT_USAGE
