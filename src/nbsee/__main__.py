import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any


def _read_file(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(x) for x in value)
    return str(value)


def _cell_input_text(cell: dict[str, Any]) -> str:
    return _as_text(cell.get("source", ""))


def _cell_output_text(cell: dict[str, Any]) -> str:
    if cell.get("cell_type") != "code":
        return ""
    outs: list[str] = []
    for out in cell.get("outputs", []) or []:
        data = out.get("data") if isinstance(out, dict) else None
        if isinstance(data, dict) and "text/plain" in data:
            outs.append(_as_text(data.get("text/plain")))
            continue
        if out.get("output_type") == "stream" and "text" in out:
            outs.append(_as_text(out.get("text")))
            continue
        if out.get("output_type") == "error" and "traceback" in out:
            tb = out.get("traceback")
            if isinstance(tb, list):
                parts = [str(x) for x in tb]
                # Jupyter tracebacks are often lists of lines. Sometimes newlines are
                # embedded, sometimes they're separate elements; preserve embedded ones.
                if any("\n" in p for p in parts):
                    outs.append("".join(parts))
                else:
                    outs.append("\n".join(parts))
            else:
                outs.append(_as_text(tb))
            continue
    return "\n".join(x for x in outs if x)


def _load_notebook(path: str) -> list[dict[str, Any]]:
    nb = _read_file(path)
    if (
        not isinstance(nb, dict)
        or "cells" not in nb
        or not isinstance(nb["cells"], list)
    ):
        raise ValueError("Not a valid .ipynb: missing cells[]")
    return nb["cells"]


def _copy_to_clipboard(text: str) -> tuple[bool, str]:
    if not text:
        return False, "Nothing to copy"
    if shutil.which("xclip") is None:
        return False, "xclip not found"
    try:
        p = subprocess.Popen(
            ["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE
        )
        assert p.stdin is not None
        p.stdin.write(text.encode("utf-8"))
        p.stdin.close()
        rc = p.wait(timeout=5)
        if rc != 0:
            return False, f"xclip failed (code {rc})"
        return True, f"Copied {len(text)} chars to clipboard"
    except FileNotFoundError:
        return False, "xclip not found"
    except Exception as e:
        return False, f"Copy failed: {e}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rich/Textual viewer for Jupyter notebooks"
    )
    parser.add_argument("path", help="Path to .ipynb file")
    args = parser.parse_args(argv)

    if not os.path.exists(args.path):
        sys.stderr.write(f"File not found: {args.path}\n")
        return 2
    try:
        cells = _load_notebook(args.path)
    except Exception as e:
        sys.stderr.write(f"Failed to read notebook: {e}\n")
        return 2
    if not cells:
        sys.stderr.write("Notebook has no cells\n")
        return 1

    try:
        from .textual_app import run_textual_app
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"Rich/Textual UI not available: {e}\n")
        return 2
    return run_textual_app(cells)


if __name__ == "__main__":
    raise SystemExit(main())
