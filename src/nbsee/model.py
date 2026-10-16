"""Immutable notebook model. `.ipynb` is untrusted: parse JSON only."""

from __future__ import annotations

import base64
import binascii
import json
import struct
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import IO, Any, Literal

from .ansi import to_plain

CellKind = Literal["code", "markdown", "raw"]
OutputKind = Literal["stream", "result", "display", "error"]

_OUTPUT_KINDS: dict[str, OutputKind] = {
    "stream": "stream",
    "execute_result": "result",
    "display_data": "display",
    "error": "error",
}


class Region(Enum):
    """Which part of a cell: its source or its (plain-text) outputs."""

    INPUT = "input"
    OUTPUT = "output"


class NotebookError(Exception):
    """Raised for unreadable or invalid notebooks. Message is user-facing."""


@dataclass(frozen=True, slots=True)
class Output:
    kind: OutputKind
    text: str | None  # raw text (may contain ANSI); None if no text representation
    stream: str | None = None  # "stdout" / "stderr"
    mimes: tuple[str, ...] = ()
    ename: str | None = None
    evalue: str | None = None
    size: tuple[int, int] | None = None  # (width, height) of the first image

    @property
    def plain(self) -> str:
        return to_plain(self.text) if self.text else ""

    @property
    def placeholder(self) -> str | None:
        """Label for outputs with no text, e.g. "[image/png 800×600]"."""
        if (self.text and self.text.strip()) or not self.mimes:
            return None
        mime = next((m for m in self.mimes if m.startswith("image/")), self.mimes[0])
        if self.size and mime.startswith("image/"):
            return f"[{mime} {self.size[0]}×{self.size[1]}]"
        return f"[{mime}]"


@dataclass(frozen=True)
class Cell:
    index: int  # 0-based; display as index + 1
    kind: CellKind
    source: str
    outputs: tuple[Output, ...] = ()
    execution_count: int | None = None

    @cached_property
    def output_text(self) -> str:
        """Plain text of all outputs that have text, joined by newlines."""
        return "\n".join(o.plain for o in self.outputs if o.plain)

    @cached_property
    def has_output_text(self) -> bool:
        return bool(self.output_text.strip())

    def text(self, region: Region) -> str:
        """Plain text of a region: source for INPUT, output_text for OUTPUT."""
        return self.source if region is Region.INPUT else self.output_text

    def lines(self, region: Region) -> list[str]:
        """`text(region).splitlines()`; an empty region has no lines."""
        return self.text(region).splitlines()

    @cached_property
    def has_error(self) -> bool:
        return any(o.kind == "error" for o in self.outputs)

    @cached_property
    def headings(self) -> tuple[tuple[int, str], ...]:
        """Markdown ATX headings as (level, title); empty for non-markdown."""
        if self.kind != "markdown":
            return ()
        found: list[tuple[int, str]] = []
        in_fence = False
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence or not stripped.startswith("#"):
                continue
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip().rstrip("#").strip()
            if 1 <= level <= 6 and title and stripped[level : level + 1] in (" ", ""):
                found.append((level, title))
        return tuple(found)


@dataclass(frozen=True)
class Notebook:
    cells: tuple[Cell, ...]
    language: str = "python"
    path: str | None = None
    nbformat: int = 4


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(x) for x in value)
    return str(value)


def _traceback_text(tb: Any) -> str:
    if not isinstance(tb, list):
        return _as_text(tb)
    # One element per frame/line, with or without a trailing newline.
    return "\n".join(str(x).removesuffix("\n") for x in tb)


def _png_size(data: Any) -> tuple[int, int] | None:
    """Read width/height from a base64 PNG's IHDR without decoding it all."""
    head = _as_text(data).replace("\n", "")[:44]
    try:
        raw = base64.b64decode(head[: len(head) - len(head) % 4])
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", raw[16:24])
    return (w, h)


def _image_size(out: dict[str, Any], data: dict[str, Any]) -> tuple[int, int] | None:
    meta = out.get("metadata")
    for mime in data:
        if not mime.startswith("image/"):
            continue
        m = meta.get(mime) if isinstance(meta, dict) else None
        if isinstance(m, dict):
            w, h = m.get("width"), m.get("height")
            if isinstance(w, int) and isinstance(h, int):
                return (w, h)
        if mime == "image/png":
            return _png_size(data[mime])
        return None
    return None


def _parse_output(out: Any) -> Output | None:
    if not isinstance(out, dict):
        return None
    kind = _OUTPUT_KINDS.get(str(out.get("output_type")))
    if kind is None:
        return None
    if kind == "stream":
        name = out.get("name")
        return Output(
            kind=kind,
            text=_as_text(out.get("text")) or None,
            stream=name if isinstance(name, str) else None,
        )
    if kind == "error":
        ename = out.get("ename")
        evalue = out.get("evalue")
        text = _traceback_text(out.get("traceback"))
        if not text and (ename or evalue):
            text = f"{ename}: {evalue}"
        return Output(
            kind=kind,
            text=text or None,
            ename=str(ename) if ename is not None else None,
            evalue=str(evalue) if evalue is not None else None,
        )
    data = out.get("data")
    data = data if isinstance(data, dict) else {}
    text = _as_text(data["text/plain"]) if "text/plain" in data else None
    return Output(
        kind=kind,
        text=text or None,
        mimes=tuple(str(m) for m in data),
        size=_image_size(out, data),
    )


def _parse_cell(index: int, raw: Any) -> Cell:
    if not isinstance(raw, dict):
        return Cell(index=index, kind="raw", source="")
    ctype = raw.get("cell_type")
    kind: CellKind = ctype if ctype in ("code", "markdown") else "raw"
    outputs: tuple[Output, ...] = ()
    count = None
    if kind == "code":
        outs = raw.get("outputs")
        parsed = (_parse_output(o) for o in outs) if isinstance(outs, list) else ()
        outputs = tuple(o for o in parsed if o is not None)
        ec = raw.get("execution_count")
        count = ec if isinstance(ec, int) else None
    return Cell(
        index=index,
        kind=kind,
        source=_as_text(raw.get("source")),
        outputs=outputs,
        execution_count=count,
    )


def _language(nb: dict[str, Any]) -> str:
    meta = nb.get("metadata")
    if not isinstance(meta, dict):
        return "python"
    for key in ("language_info", "kernelspec"):
        info = meta.get(key)
        if isinstance(info, dict):
            name = info.get("name") if key == "language_info" else info.get("language")
            if isinstance(name, str) and name:
                return name.lower()
    return "python"


def parse(data: Any, path: str | None = None) -> Notebook:
    """Build a Notebook from already-decoded JSON."""
    if not isinstance(data, dict):
        raise NotebookError("not a notebook: top level is not a JSON object")
    if "worksheets" in data and "cells" not in data:
        raise NotebookError("nbformat 3 notebooks are not supported")
    cells = data.get("cells")
    if not isinstance(cells, list):
        raise NotebookError("not a notebook: missing cells[]")
    nbformat = data.get("nbformat")
    return Notebook(
        cells=tuple(_parse_cell(i, c) for i, c in enumerate(cells)),
        language=_language(data),
        path=path,
        nbformat=nbformat if isinstance(nbformat, int) else 4,
    )


def load(source: str | Path | IO[str]) -> Notebook:
    """Load from a path or a text stream (e.g. sys.stdin)."""
    if isinstance(source, (str, Path)):
        path = str(source)
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            raise NotebookError(f"file not found: {path}") from None
        except IsADirectoryError:
            raise NotebookError(f"is a directory: {path}") from None
        except (OSError, UnicodeDecodeError) as e:
            raise NotebookError(f"cannot read {path}: {e}") from None
    else:
        path = None
        try:
            text = source.read()
        except UnicodeDecodeError as e:
            raise NotebookError(f"cannot read input: {e}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise NotebookError(
            f"invalid JSON at line {e.lineno} col {e.colno}: {e.msg}"
        ) from None
    return parse(data, path=path)
