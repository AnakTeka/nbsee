"""Render a notebook (or some of its cells) as md, text, py or json."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from io import StringIO
from typing import Literal

from rich.console import Console
from rich.syntax import Syntax

from .ansi import sanitize, strip_background
from .model import Cell, Notebook, Output

Format = Literal["md", "text", "py", "json"]
Only = Literal["input", "output"] | None

_HEADER_WIDTH = 72
_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_BACKTICKS_RE = re.compile(r"`+")
# An output piece is ("text", body) or ("label", "[image/png 3×2]").
_Piece = tuple[Literal["text", "label"], str]


def _colored(text: str) -> str:
    cleaned = strip_background(sanitize(text))
    return cleaned + _RESET if "\x1b" in cleaned else cleaned


def _output_pieces(cell: Cell, color: bool) -> list[_Piece]:
    """Outputs as text bodies (consecutive ones merged) and placeholder labels."""
    pieces: list[_Piece] = []
    for out in cell.outputs:
        if out.plain.strip():
            body = _colored(out.text or "") if color else out.plain
            body = body.rstrip("\n")
            if pieces and pieces[-1][0] == "text":
                pieces[-1] = ("text", f"{pieces[-1][1]}\n{body}")
            else:
                pieces.append(("text", body))
        elif out.placeholder:
            pieces.append(("label", out.placeholder))
    return pieces


def _fence(body: str, info: str = "") -> str:
    """Fenced block whose fence is longer than any backtick run in `body`."""
    longest = max((len(m.group()) for m in _BACKTICKS_RE.finditer(body)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{body}\n{fence}"


def _comment(text: str) -> str:
    return "\n".join(f"# {line}" if line else "#" for line in text.splitlines())


def _highlight(source: str, language: str) -> str:
    console = Console(
        file=StringIO(), force_terminal=True, color_system="standard", no_color=False
    )
    text = Syntax(source, language, theme="ansi_dark").highlight(source)
    console.print(text, soft_wrap=True, end="", highlight=False)
    return console.file.getvalue()  # type: ignore[attr-defined]


def _in_label(cell: Cell) -> str | None:
    if cell.execution_count is None:
        return None
    return f"In[{cell.execution_count}]"


def _source(cell: Cell) -> str:
    return cell.source.rstrip("\n")


def _md_cell(nb: Notebook, cell: Cell, want_in: bool, want_out: bool) -> str:
    tags = [str(cell.index + 1), cell.kind, _in_label(cell)]
    lines = ["<!-- cell " + " · ".join(t for t in tags if t) + " -->"]
    parts: list[str] = []
    if want_in and _source(cell).strip():
        if cell.kind == "markdown":
            parts.append(_source(cell))
        else:
            parts.append(
                _fence(_source(cell), nb.language if cell.kind == "code" else "text")
            )
    if want_out:
        for kind, body in _output_pieces(cell, color=False):
            parts.append(_fence(body, "text") if kind == "text" else f"*{body}*")
    if parts:
        lines.append("\n\n".join(parts))
    return "\n".join(lines)


def _text_cell(
    nb: Notebook, cell: Cell, want_in: bool, want_out: bool, color: bool
) -> str:
    label = " ".join(
        p for p in (f"[{cell.index + 1}]", cell.kind, _in_label(cell)) if p
    )
    head = f"── {label} "
    head += "─" * max(2, _HEADER_WIDTH - len(head))
    lines = [f"{_DIM}{head}{_RESET}" if color else head]
    if want_in and _source(cell):
        if color and cell.kind == "code":
            lines.append(_highlight(_source(cell), nb.language))
        else:
            lines.append(_source(cell))
    pieces = _output_pieces(cell, color) if want_out else []
    if pieces and want_in:
        lines.append("┄ output")
    lines.extend(body for _, body in pieces)
    return "\n".join(lines)


def _py_cell(cell: Cell, want_in: bool, want_out: bool) -> str:
    tag = {"code": "", "markdown": " [markdown]", "raw": " [raw]"}[cell.kind]
    lines = [f"# %%{tag}"]
    if want_in and _source(cell):
        lines.append(_source(cell) if cell.kind == "code" else _comment(_source(cell)))
    if want_out:
        lines.extend(_comment(body) for _, body in _output_pieces(cell, False))
    return "\n".join(lines)


def _json_output(out: Output) -> dict[str, object]:
    return {
        "kind": out.kind,
        "text": out.plain if out.text else None,
        "mimes": list(out.mimes),
    }


def _json_cell(cell: Cell, want_in: bool, want_out: bool) -> dict[str, object]:
    item: dict[str, object] = {"index": cell.index + 1, "kind": cell.kind}
    if want_in:
        item["source"] = cell.source
    item["execution_count"] = cell.execution_count
    if want_out:
        item["outputs"] = [_json_output(o) for o in cell.outputs]
    return item


def _has_output(cell: Cell) -> bool:
    return bool(_output_pieces(cell, color=False))


def export(
    nb: Notebook,
    cells: Sequence[int] | None = None,
    *,
    fmt: Format = "md",
    only: Only = None,
    outputs: bool = True,
    color: bool = False,
) -> str:
    """Render the given 0-based `cells` (default: all) in the given format."""
    if only == "output" and not outputs:
        raise ValueError("only='output' contradicts outputs=False")
    want_in = only != "output"
    want_out = only == "output" or (only is None and outputs and fmt != "py")
    picked = nb.cells if cells is None else tuple(nb.cells[i] for i in cells)
    if only == "output":
        picked = tuple(c for c in picked if _has_output(c))

    if fmt == "json":
        data = [_json_cell(c, want_in, want_out) for c in picked]
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if fmt == "md":
        blocks = [_md_cell(nb, c, want_in, want_out) for c in picked]
    elif fmt == "text":
        blocks = [_text_cell(nb, c, want_in, want_out, color) for c in picked]
    else:
        blocks = [_py_cell(c, want_in, want_out) for c in picked]
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _output_labels(cell: Cell) -> list[str]:
    labels: list[str] = []
    for out in cell.outputs:
        image = next((m for m in out.mimes if m.startswith("image/")), None)
        if out.kind == "error":
            label = f"err:{out.ename or 'error'}"
        elif image:
            label = image
        elif out.kind == "stream":
            label = out.stream or "stream"
        elif out.kind == "result":
            label = "result"
        elif out.text or out.mimes:
            label = "display" if out.text else out.mimes[0]
        else:
            continue
        if label not in labels:
            labels.append(label)
    return labels


def _summary(cell: Cell) -> str:
    if cell.headings:
        return cell.headings[0][1]
    return next((ln.strip() for ln in cell.source.splitlines() if ln.strip()), "")


def summarize(nb: Notebook, width: int = 100) -> list[str]:
    """One line per cell for `nbsee ls`, truncated to `width` columns."""
    rows = []
    for cell in nb.cells:
        outs = ",".join(_output_labels(cell)) or "-"
        line = (
            f"{cell.index + 1:>4}  {cell.kind:<8} {len(cell.source.splitlines()):>4}L"
            f"  {outs:<16} {_summary(cell)}"
        ).rstrip()
        if width > 0 and len(line) > width:
            line = line[: max(width - 1, 0)] + "…"
        rows.append(line)
    return rows


def summarize_json(nb: Notebook) -> str:
    data = [
        {
            "index": c.index + 1,
            "kind": c.kind,
            "lines": len(c.source.splitlines()),
            "execution_count": c.execution_count,
            "outputs": _output_labels(c),
            "summary": _summary(c),
        }
        for c in nb.cells
    ]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
