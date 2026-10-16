"""The key binding table: drives Textual bindings and the `?` help."""

from __future__ import annotations

from typing import NamedTuple


class KeyDef(NamedTuple):
    group: str
    keys: str  # Textual key names, comma separated
    action: str
    label: str  # shown in help; empty hides the row
    desc: str
    priority: bool = False


KEYMAP: tuple[KeyDef, ...] = (
    KeyDef("Move", "j", "move(1)", "j / k", "Next / previous cell (line in LINE)"),
    KeyDef("Move", "k", "move(-1)", "", ""),
    KeyDef("Move", "down", "scroll(1)", "Down / Up", "Scroll one line"),
    KeyDef("Move", "up", "scroll(-1)", "", ""),
    KeyDef(
        "Move", "ctrl+d", "page(0.5)", "Ctrl+d / Ctrl+u", "Half page, cursor follows"
    ),
    KeyDef("Move", "ctrl+u", "page(-0.5)", "", ""),
    KeyDef("Move", "ctrl+f,pagedown", "page(1)", "Ctrl+f / PgDn", "Page down"),
    KeyDef("Move", "ctrl+b,pageup", "page(-1)", "Ctrl+b / PgUp", "Page up"),
    KeyDef("Move", "g", "g", "gg", "First cell (line)"),
    KeyDef("Move", "G", "bottom", "G", "Last cell (line)"),
    KeyDef("Move", "colon", "prompt('goto')", ":N Enter", "Go to cell N"),
    KeyDef("Copy", "y", "yank", "y", "Copy region / selection"),
    KeyDef("Copy", "Y", "yank_all", "Y", "Copy whole cell(s): input + output"),
    KeyDef("Cell", "tab", "toggle_region", "Tab", "Switch input / output", True),
    KeyDef("Cell", "z", "fold", "z", "Fold / unfold this output"),
    KeyDef("Cell", "Z", "fold_all", "Z", "Fold / unfold all outputs"),
    KeyDef("Cell", "m", "markdown_raw", "m", "Rendered / raw markdown"),
    KeyDef("Modes", "enter", "enter", "Enter", "LINE mode (expand output in LINE)"),
    KeyDef("Modes", "V", "visual", "V", "VISUAL range (line range in LINE)"),
    KeyDef("Modes", "escape", "escape", "Esc", "Leave mode / clear search"),
    KeyDef("Search", "slash", "prompt('search')", "/", "Search (smart-case)"),
    KeyDef("Search", "n", "step(1)", "n / N", "Next / previous hit"),
    KeyDef("Search", "N", "step(-1)", "", ""),
    KeyDef("Other", "o", "outline", "o", "Outline sidebar"),
    KeyDef("Other", "R", "reload", "R", "Reload from disk"),
    KeyDef("Other", "question_mark", "help", "?", "This help"),
    KeyDef("Other", "q", "quit", "q", "Quit"),
)
