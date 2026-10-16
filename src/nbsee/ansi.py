"""Terminal escape-sequence handling for notebook output text."""

from __future__ import annotations

import re

_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_LONE_ESC_RE = re.compile(r"\x1b(?!\[[0-9;]*m)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1a\x1c-\x1f\x7f]")
# IPython tracebacks sometimes render "Traceback ...Cell ..." without a newline.
_TRACEBACK_CELL_RE = re.compile(r"(Traceback \(most recent call last\))\s*Cell")


def _collapse_carriage_returns(text: str) -> str:
    """Emulate a terminal: a bare CR overwrites the line (progress bars)."""
    if "\r" not in text:
        return text
    lines = []
    for line in text.split("\n"):
        if "\r" in line:
            segments = [seg for seg in line.split("\r") if seg]
            line = segments[-1] if segments else ""
        lines.append(line)
    return "\n".join(lines)


def sanitize(text: str) -> str:
    """Keep SGR color codes; drop every other escape and control character."""
    text = _collapse_carriage_returns(text.replace("\r\n", "\n"))
    text = _OSC_RE.sub("", text)
    text = _CSI_RE.sub(lambda m: m.group(0) if m.group(0).endswith("m") else "", text)
    text = _LONE_ESC_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return _TRACEBACK_CELL_RE.sub(r"\1\nCell", text)


def strip_background(text: str) -> str:
    """Remove SGR background colors (40-47, 49, 100-107, 48;5;n, 48;2;r;g;b)."""

    def repl(match: re.Match[str]) -> str:
        raw = match.group(1)
        if raw == "":
            return match.group(0)
        try:
            params = [int(p) for p in raw.split(";") if p != ""]
        except ValueError:
            return match.group(0)
        keep: list[int] = []
        i = 0
        while i < len(params):
            p = params[i]
            if p in (38, 48) and i + 1 < len(params) and params[i + 1] in (5, 2):
                width = 3 if params[i + 1] == 5 else 5
                if p == 38:
                    keep.extend(params[i : i + width])
                i += width
                continue
            if p == 48 or p == 49 or 40 <= p <= 47 or 100 <= p <= 107:
                i += 1
                continue
            keep.append(p)
            i += 1
        if not keep:
            return ""
        return f"\x1b[{';'.join(str(p) for p in keep)}m"

    return _SGR_RE.sub(repl, text)


def to_plain(text: str) -> str:
    """Remove all escape sequences; result is what a user sees and copies."""
    return _SGR_RE.sub("", sanitize(text))
