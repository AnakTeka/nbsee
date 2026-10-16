"""NotebookApp: key bindings -> pure state transitions -> view refresh."""

from __future__ import annotations

import gc
import os
from dataclasses import replace

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Input, Label, OptionList

from .. import clipboard, model, state
from ..model import Notebook, NotebookError, Region
from ..search import Hit
from ..state import Mode, ViewState
from .help import HelpScreen
from .keys import KEYMAP
from .outline import OutlinePane
from .statusbar import StatusBar
from .view import NotebookView

GG_TIMEOUT = 0.6
MESSAGE_SECONDS = 4.0
POLL_SECONDS = 1.0
_LINE_MODES = (Mode.LINE, Mode.LINE_VISUAL)


class PromptInput(Input):
    """Single-line prompt; Tab and Esc stay local to it."""

    BINDINGS = [
        Binding("escape", "app.cancel_prompt", show=False),
        Binding("tab,shift+tab", "noop", show=False),
    ]

    def action_noop(self) -> None:
        """Swallow focus-cycling keys."""


class PromptBar(Horizontal):
    DEFAULT_CSS = """
    PromptBar { height: 1; display: none; }
    PromptBar.-open { display: block; }
    PromptBar > Label { width: 2; padding: 0 0 0 1; }
    PromptBar > Input { border: none; height: 1; padding: 0; }
    """

    def compose(self) -> ComposeResult:
        yield Label("/", id="prompt-label")
        yield PromptInput(id="prompt-input")


def _with_shift_aliases(keys: str) -> str:
    """Bind "V" also as "shift+v": some terminals (kitty protocol) report that."""
    names = keys.split(",")
    extra = [f"shift+{k.lower()}" for k in names if len(k) == 1 and k.isupper()]
    return ",".join(names + extra)


class NotebookApp(App[int]):
    CSS = """
    #body { height: 1fr; }
    NotebookView { width: 1fr; height: 1fr; }
    """
    TITLE = "nbsee"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding(
            _with_shift_aliases(k.keys),
            k.action,
            k.desc,
            show=False,
            priority=k.priority,
        )
        for k in KEYMAP
    ]

    def __init__(self, nb: Notebook, start: int = 0) -> None:
        super().__init__()
        self.nb = nb
        if nb.path:
            self.sub_title = os.path.basename(nb.path)
        last = max(len(nb.cells) - 1, 0)
        start = max(0, min(start, last))
        self.state = ViewState(cursor=start, anchor=start)
        self._prompt: str | None = None
        self._g_timer: Timer | None = None
        self._message_timer: Timer | None = None
        self._stamp = self._file_stamp()

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            yield OutlinePane(self.nb)
            yield NotebookView(self.nb, self.state)
        yield PromptBar()
        yield StatusBar()

    @property
    def view(self) -> NotebookView:
        return self.query_one(NotebookView)

    @property
    def outline(self) -> OutlinePane:
        return self.query_one(OutlinePane)

    @property
    def status(self) -> StatusBar:
        return self.query_one(StatusBar)

    def on_mount(self) -> None:
        self.view.focus()
        self._show_state()
        self.outline.track(self.state.cursor)
        if self.nb.path:
            self.set_interval(POLL_SECONDS, self._poll)

    # -- state plumbing ----------------------------------------------------

    def _set(
        self, new: ViewState, *, hit: Hit | None = None, reveal: bool = True
    ) -> None:
        old, self.state = self.state, new
        if new is old and hit is None:
            return
        self.view.set_state(new, hit=hit, reveal=reveal)
        self._show_state()
        if new.cursor != old.cursor and self.outline.has_class("-open"):
            self.outline.track(new.cursor)

    def _show_state(self) -> None:
        self.status.show(self.nb, self.state)

    def flash(self, message: str) -> None:
        self.status.flash(message)
        if self._message_timer is not None:
            self._message_timer.stop()
        self._message_timer = self.set_timer(MESSAGE_SECONDS, self._clear_message)

    def _clear_message(self) -> None:
        self._message_timer = None
        self.status.flash("")

    def _current_hit(self, s: ViewState) -> Hit | None:
        sr = s.search
        if sr is None or sr.current is None:
            return None
        return sr.hits[sr.current]

    def _show_output(self, s: ViewState, cell: int) -> ViewState:
        """Make sure a cell's output is not folded or hidden."""
        if s.outputs_hidden:
            keep = frozenset(c.index for c in self.nb.cells if c.outputs)
            return replace(s, outputs_hidden=False, folded=keep - {cell})
        if cell in s.folded:
            return replace(s, folded=s.folded - {cell})
        return s

    def _after_search(self, s: ViewState) -> None:
        hit = self._current_hit(s)
        if hit is not None and hit.region is Region.OUTPUT:
            s = self._show_output(s, hit.cell)
        self._set(s, hit=hit)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if len(self.screen_stack) > 1:  # help overlay owns the keyboard
            return False
        focused = self.focused
        if isinstance(focused, Input):
            return action == "cancel_prompt"
        if isinstance(focused, OutlinePane):
            return action in ("outline", "escape", "quit", "help")
        return True

    # -- key handling ------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if event.key != "g":
            self._cancel_g()

    def _cancel_g(self) -> None:
        if self._g_timer is not None:
            self._g_timer.stop()
            self._g_timer = None

    def action_g(self) -> None:
        if self._g_timer is None:
            self._g_timer = self.set_timer(GG_TIMEOUT, self._cancel_g)
            return
        self._cancel_g()
        s = self.state
        if s.mode in _LINE_MODES:
            self._set(state.goto_line(s, self.nb, 0))
        else:
            self._set(state.goto_cell(s, self.nb, 0))

    def action_bottom(self) -> None:
        s = self.state
        if s.mode in _LINE_MODES:
            self._set(state.goto_line(s, self.nb, -1))
        else:
            self._set(state.goto_cell(s, self.nb, -1))

    def action_move(self, delta: int) -> None:
        s = self.state
        if s.mode in _LINE_MODES:
            self._set(state.move_line(s, self.nb, delta))
        else:
            self._set(state.move_cell(s, self.nb, delta))

    def action_scroll(self, delta: int) -> None:
        self.view.scroll_lines(delta)

    def action_page(self, pages: float) -> None:
        cell = self.view.page(pages)
        self._set(state.goto_cell(self.state, self.nb, cell), reveal=False)

    def action_toggle_region(self) -> None:
        s = state.toggle_region(self.state, self.nb)
        if s.region is self.state.region:
            self.flash("No output")
            return
        if s.region is Region.OUTPUT:
            s = self._show_output(s, s.cursor)
        self._set(s)

    def action_fold(self) -> None:
        self._set(state.toggle_fold(self.state, self.nb))

    def action_fold_all(self) -> None:
        self._set(state.toggle_all_outputs(self.state))

    def action_markdown_raw(self) -> None:
        self._set(state.toggle_markdown_raw(self.state))

    def action_enter(self) -> None:
        s = self.state
        if s.mode in _LINE_MODES:
            if s.region is Region.OUTPUT:
                self.view.expand_output(s.cursor)
            return
        new = state.enter_line(s, self.nb)
        if new is s:
            self.flash("Nothing to select")
            return
        if new.region is Region.OUTPUT:
            new = self._show_output(new, new.cursor)
        self._set(new)

    def action_visual(self) -> None:
        s = self.state
        if s.mode in _LINE_MODES:
            self._set(state.start_line_visual(s))
        else:
            self._set(state.enter_visual(s))

    def action_escape(self) -> None:
        if self.outline.has_focus:
            self.action_outline()
            return
        self._set(state.escape(self.state))

    def action_step(self, delta: int) -> None:
        s = self.state
        if s.search is None:
            self.flash("No search")
            return
        if not s.search.hits:
            self.flash(f"No matches for {s.search.query!r}")
            return
        self._after_search(state.search_step(s, self.nb, delta))

    # -- copy --------------------------------------------------------------

    def action_yank(self) -> None:
        self._copy(state.yank(self.state, self.nb))

    def action_yank_all(self) -> None:
        self._copy(state.yank_all(self.state, self.nb))

    def _copy(self, text: str) -> None:
        result = clipboard.copy(text, osc52=self.copy_to_clipboard)
        self.flash(result.message)
        s = self.state
        if s.mode in (Mode.VISUAL, Mode.LINE_VISUAL):
            while s.mode is not Mode.NORMAL:
                s = state.escape(s)
        self._set(s)

    # -- prompts -----------------------------------------------------------

    def action_prompt(self, kind: str) -> None:
        self._prompt = kind
        bar = self.query_one(PromptBar)
        bar.add_class("-open")
        self.query_one("#prompt-label", Label).update("/" if kind == "search" else ":")
        field = self.query_one(PromptInput)
        field.value = ""
        field.focus()

    def action_cancel_prompt(self) -> None:
        self._close_prompt()

    def _close_prompt(self) -> None:
        self._prompt = None
        self.query_one(PromptBar).remove_class("-open")
        self.view.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        kind, text = self._prompt, event.value.strip()
        self._close_prompt()
        if kind == "search":
            self._submit_search(text)
        elif kind == "goto":
            self._submit_goto(text)

    def _submit_search(self, query: str) -> None:
        s = state.set_search(self.state, self.nb, query)
        if s.search is not None and not s.search.hits:
            self.flash(f"No matches for {query!r}")
        self._after_search(s)

    def _submit_goto(self, text: str) -> None:
        if not text.lstrip("+-").isdigit():
            self.flash(f"Not a cell number: {text!r}")
            return
        self._set(state.goto_cell(self.state, self.nb, int(text) - 1))

    # -- outline, help -----------------------------------------------------

    def action_outline(self) -> None:
        outline = self.outline
        if not outline.has_class("-open"):
            if not outline.option_count:
                self.flash("No headings")
                return
            outline.add_class("-open")
            outline.track(self.state.cursor)
            outline.focus()
        elif outline.has_focus:
            outline.remove_class("-open")
            self.view.focus()
        else:
            outline.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        cell = self.outline.cell_at(event.option_index)
        self.view.focus()
        self._set(state.goto_cell(self.state, self.nb, cell))

    def action_help(self) -> None:
        self.push_screen(HelpScreen(KEYMAP))

    def action_quit(self) -> None:
        self.exit(0)

    # -- reload ------------------------------------------------------------

    def _file_stamp(self) -> tuple[int, int] | None:
        if not self.nb.path:
            return None
        try:
            st = os.stat(self.nb.path)
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    def _poll(self) -> None:
        stamp = self._file_stamp()
        if stamp is not None and stamp != self._stamp:
            self._reload(auto=True)

    def action_reload(self) -> None:
        self._reload(auto=False)

    def _reload(self, *, auto: bool) -> None:
        if not self.nb.path:
            self.flash("Nothing to reload")
            return
        self._stamp = self._file_stamp()
        try:
            nb = model.load(self.nb.path)
        except NotebookError as e:
            self.flash(f"Reload failed: {e}")
            return
        self.nb = nb
        self.state = state.on_reload(self.state, nb)
        self.outline.populate(nb)
        self.outline.track(self.state.cursor)
        self.view.load(nb, self.state)
        self._show_state()
        self.flash("Reloaded (file changed)" if auto else "Reloaded")


def tune_gc() -> None:
    """Keep full collections from landing on a keypress.

    The parsed notebook and the render cache hold a few hundred thousand small
    objects; scanning them costs tens of milliseconds.
    """
    gc.freeze()
    gc.set_threshold(20_000, 20, 100)


def run(nb: Notebook, start: int = 0) -> int:
    """Run the viewer until quit; returns the process exit code."""
    tune_gc()
    result = NotebookApp(nb, start=start).run()
    return result or 0
