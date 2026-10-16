"""Clipboard strategy: OSC 52 plus native tools. Pure planning, injectable I/O."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

Backend = Literal["osc52", "pbcopy", "wl-copy", "xclip", "xsel"]

LARGE_PAYLOAD = 100_000
TIMEOUT = 5

_ARGV: dict[str, list[str]] = {
    "pbcopy": ["pbcopy"],
    "wl-copy": ["wl-copy"],
    "xclip": ["xclip", "-selection", "clipboard"],
    "xsel": ["xsel", "--clipboard", "--input"],
}
_MODES = ("auto", "osc52", "native")


@dataclass(frozen=True)
class CopyResult:
    ok: bool
    via: tuple[str, ...]  # backends that succeeded, in order
    message: str  # status-bar text, e.g. "Copied 12 lines · osc52+wl-copy"


def _in_ssh(env: Mapping[str, str]) -> bool:
    return bool(env.get("SSH_CONNECTION") or env.get("SSH_TTY"))


def _native(
    env: Mapping[str, str], platform: str, which: Callable[[str], str | None]
) -> tuple[Backend, ...]:
    """First available native tool, as a 0- or 1-tuple."""
    candidates: list[Backend] = []
    if platform == "darwin":
        candidates.append("pbcopy")
    if env.get("WAYLAND_DISPLAY"):
        candidates.append("wl-copy")
    if env.get("DISPLAY"):
        candidates += ["xclip", "xsel"]
    for name in candidates:
        if which(name):
            return (name,)
    return ()


def plan(
    env: Mapping[str, str],
    platform: str,
    which: Callable[[str], str | None],
    mode: str | None = None,
) -> tuple[Backend, ...]:
    """Decide which backends to try, in order."""
    if mode is None:
        mode = env.get("NBSEE_CLIPBOARD", "auto")
    if mode not in _MODES:
        mode = "auto"
    if mode == "osc52":
        return ("osc52",)
    if mode == "native":
        return _native(env, platform, which)
    if _in_ssh(env):
        return ("osc52",)
    return ("osc52", *_native(env, platform, which))


def _run(argv: list[str], data: bytes) -> int:
    try:
        proc = subprocess.run(
            argv,
            input=data,
            timeout=TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    return proc.returncode


def _hint(env: Mapping[str, str], platform: str, tried: list[str]) -> str:
    if platform == "darwin":
        return ""
    if env.get("WAYLAND_DISPLAY"):
        return "install wl-clipboard"
    if env.get("DISPLAY"):
        return "install xclip"
    if env.get("TMUX") and tried in ([], ["osc52"]):
        return "enable set-clipboard in tmux"
    return ""


def copy(
    text: str,
    *,
    osc52: Callable[[str], None] | None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[list[str], bytes], int] | None = None,
) -> CopyResult:
    """Copy `text` using every planned backend; ok if any succeeded."""
    if not text:
        return CopyResult(False, (), "Nothing to copy")
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    runner = run or _run
    data = text.encode("utf-8")

    tried: list[str] = []
    via: list[str] = []
    for backend in plan(env, platform, which):
        if backend == "osc52":
            if osc52 is None:
                continue
            tried.append(backend)
            try:
                osc52(text)
            except Exception:  # noqa: BLE001 - a terminal quirk must not crash the UI
                continue
            via.append(backend)
        else:
            tried.append(backend)
            if runner(_ARGV[backend], data) == 0:
                via.append(backend)

    if not via:
        hint = _hint(env, platform, tried)
        what = "+".join(tried) if tried else "no backend available"
        msg = f"Copy failed ({what})"
        return CopyResult(False, (), f"{msg}: {hint}" if hint else msg)

    lines = max(1, len(text.splitlines()))
    unit = "line" if lines == 1 else "lines"
    msg = f"Copied {lines} {unit} · {'+'.join(via)}"
    if "osc52" in via and len(data) > LARGE_PAYLOAD:
        msg += " (large: some terminals truncate OSC 52)"
    return CopyResult(True, tuple(via), msg)
