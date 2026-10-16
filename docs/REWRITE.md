nbsee v0.2 Rewrite — Implementation Brief
=========================================

Read this file, `docs/DESIGN.md` (the spec), and `AGENTS.md` before starting.
DESIGN.md is the source of truth for behavior; this file defines **who builds
what**, the **interfaces between packages**, and the **definition of done**.

Branch: `rewrite`. Do **not** commit, push, or change branches — the lead
reviews and commits. Do not touch files outside your package's ownership list
(other agents are editing the same tree in parallel). If you believe a shared
file (e.g. `model.py`, `pyproject.toml`) needs a change, stop and report it
instead of editing.


Ground rules (all packages)
---------------------------
- Python >= 3.11, `from __future__ import annotations`, full type hints on
  public functions, PEP 8, line length 88. Match the style of `model.py`.
- Small pure functions; no global mutable state. Docstrings: one line, only
  where the name is not enough.
- Treat `.ipynb` as untrusted. Never `eval`/`exec`, never `shell=True`, never
  render HTML. Copy/search/export use **plain text** (`ansi.to_plain`, or the
  model's `Output.plain` / `Cell.output_text`, which are already plain).
- No new runtime dependencies. stdlib + `rich` + `textual` only.
- Tests: `pytest`, in `tests/test_<module>.py`. Never spawn real clipboard
  tools or require a display. Build notebooks inline with `model.parse({...})`
  or use `tests/fixtures/*.ipynb`.
- Before reporting done, all of these must pass:
  ```
  uv run pytest
  uv run ruff check .
  uv run ruff format --check .
  ```
  Run `uv run ruff format <your files>` to fix formatting. Other agents'
  in-progress files may fail temporarily; report but don't fix them.
- Final report: files created/changed, public API summary, test count,
  deviations from DESIGN.md (with reason), and open issues.


Foundation (done — read, don't modify)
--------------------------------------
`src/nbsee/model.py`
- `NotebookError(Exception)` — user-facing message.
- `Region(Enum)`: `INPUT`, `OUTPUT`.
- `Output(kind, text, stream, mimes, ename, evalue, size)`; `.plain`,
  `.placeholder` (e.g. `"[image/png 800×600]"` for text-less outputs).
- `Cell(index, kind, source, outputs, execution_count)`; `.output_text`
  (plain, joined by `\n`), `.has_output_text`, `.has_error`, `.headings`
  (`((level, title), ...)`), `.text(region)`, `.lines(region)`.
- `Notebook(cells, language, path, nbformat)`.
- `parse(data, path=None) -> Notebook`, `load(path | Path | IO[str]) -> Notebook`.

`src/nbsee/ansi.py`
- `sanitize(s)` keep SGR only, collapse `\r` overwrites; `strip_background(s)`;
  `to_plain(s)`.

Indexing: cells are 0-based internally and **1-based in all user-facing
text** (CLI args, headers, status bar, messages).


WP1 — CLI, selectors, export, fixtures
--------------------------------------
Owns: `src/nbsee/cli.py`, `src/nbsee/selectors.py`, `src/nbsee/export.py`,
`src/nbsee/__main__.py`, `src/nbsee/__init__.py`, `tests/fixtures/`,
`tests/test_model.py`, `tests/test_ansi.py`, `tests/test_selectors.py`,
`tests/test_export.py`, `tests/test_cli.py`, and deleting
`tests/test_notebook_parsing.py` (replace with `test_model.py`).

`selectors.py`
```python
class SelectorError(ValueError): ...


def parse_cells(spec: str, count: int) -> list[int]:
    """1-based inclusive spec -> sorted, de-duplicated 0-based indices."""
```
Grammar: comma list of `N`, `A:B`, `:B`, `A:`, negatives count from the end
(`-1` = last; `-3:` = last three). Errors: empty item, non-integer, zero,
out of range, `A > B` after resolution.

`export.py`
```python
Format = Literal["md", "text", "py", "json"]
Only = Literal["input", "output"] | None
def export(nb: Notebook, cells: Sequence[int] | None = None, *, fmt: Format = "md",
           only: Only = None, outputs: bool = True, color: bool = False) -> str
def summarize(nb: Notebook, width: int = 100) -> list[str]   # for `ls`
def summarize_json(nb: Notebook) -> str
```
Formats per DESIGN.md. Specifics:
- `md`: markdown cells verbatim; code as ```` ```<language> ````; outputs as
  ```` ```text ```` blocks; placeholders as a line like `*[image/png 800×600]*`;
  error outputs in ```` ```text ```` too. Each cell preceded by an HTML-free
  marker comment line `<!-- cell 3 · code · In[7] -->` so LLMs keep indices.
  Choose fence length longer than any backtick run inside the content.
- `text`: header `── [3] code In[7] ──` (pad with `─`), source, then outputs;
  with `color=True` keep sanitized ANSI in outputs and highlight code with
  Rich (render via `rich.console.Console(file=StringIO, force_terminal=True)`).
  With `color=False` output must contain no `\x1b`.
- `py`: jupytext percent format; markdown/raw cells as `# %% [markdown]` with
  every line commented `# `; outputs omitted unless `only="output"`.
- `json`: `json.dumps(..., ensure_ascii=False, indent=2)` of the list in
  DESIGN.md, plain text only.
- `only="input"` drops outputs; `only="output"` emits only cells that have
  output text or placeholders. `outputs=False` == `only="input"`.
- `summarize`: `"{n:>4}  {kind:<8} {lines:>4}L  {outputs:<16} {summary}"`
  truncated to `width`. Outputs column like `stdout`, `stderr`, `result`,
  `err:ValueError`, `image/png`, comma-joined, de-duplicated, `-` if none.

`cli.py` — `main(argv: list[str] | None = None) -> int`
- Subcommands `view`, `ls`, `cat` per DESIGN.md. If `argv[0]` is not a
  subcommand and not an option, insert `view`. `--version` prints version.
- `view FILE [--cell N]` → lazy `from .tui.app import run` and
  `return run(notebook, start=N-1)`. `tui` doesn't exist yet: just import
  lazily; test `view` by monkeypatching `nbsee.cli._run_viewer` (a small
  wrapper you define) — never launch the TUI in tests. `view` refuses `-`
  and non-tty stdout with a clear message (exit 1).
- `--color auto` = on iff stdout is a tty and `NO_COLOR` is unset.
- `ls` width = `shutil.get_terminal_size().columns`.
- Errors: `NotebookError` → `nbsee: <msg>` on stderr, exit 2.
  `SelectorError`/usage → exit 1. Empty notebook: `ls`/`cat` print nothing
  and exit 0; `view` prints `nbsee: notebook has no cells`, exit 1.
- `__main__.py` becomes only: `from .cli import main` +
  `if __name__ == "__main__": raise SystemExit(main())`.
- `pyproject.toml` entry point must change to `nbsee.cli:main` — **report
  this; the lead edits pyproject.** `__init__.py`: `__version__` read via
  `importlib.metadata.version("nbsee")` with fallback `"0.0.0"`.
- Leave `src/nbsee/textual_app.py` alone (it will break when `__main__.py`
  is thinned; the TUI package replaces it later).

Fixtures (`tests/fixtures/`, hand-written small JSON, valid nbformat 4.5):
`basic.ipynb` (markdown with headings, code with stdout/result, raw cell),
`errors.ipynb` (ANSI-colored traceback incl. background colors, stderr
stream, tqdm-style `\r` progress), `rich_outputs.ipynb` (tiny real PNG with
and without metadata size, text/html with and without text/plain),
`empty.ipynb` (no cells), `nbformat3.ipynb`, `malformed.json` (invalid JSON),
`backticks.ipynb` (source containing ```` ``` ```` and ```` ```` ````).

Tests: model parsing edge cases (all fixtures + odd fields), ansi functions,
selectors grammar, every export format (golden strings for `basic.ipynb`
inline in the tests), CLI dispatch/exit codes/stdin via `capsys` and
`monkeypatch.setattr("sys.stdin", io.StringIO(...))`.


WP2 — Clipboard
---------------
Owns: `src/nbsee/clipboard.py`, `tests/test_clipboard.py`.

```python
Backend = Literal["osc52", "pbcopy", "wl-copy", "xclip", "xsel"]

@dataclass(frozen=True)
class CopyResult:
    ok: bool
    via: tuple[str, ...]   # backends that succeeded, in order
    message: str           # status-bar text, e.g. "Copied 12 lines · osc52+wl-copy"

def plan(env: Mapping[str, str], platform: str, which: Callable[[str], str | None],
         mode: str | None = None) -> tuple[Backend, ...]
def copy(text: str, *, osc52: Callable[[str], None] | None,
         env: Mapping[str, str] | None = None, platform: str | None = None,
         which: Callable[[str], str | None] = shutil.which,
         run: Callable[[list[str], bytes], int] | None = None) -> CopyResult
```
- `plan()` implements the DESIGN.md strategy: `mode` defaults to
  `env.get("NBSEE_CLIPBOARD", "auto")`; values `auto|osc52|native`, unknown →
  `auto`. SSH = `SSH_CONNECTION` or `SSH_TTY` set. `auto`: `osc52` always
  first, then the first available native tool when not in SSH. `native`:
  native only (even over SSH — the user asked). `osc52`: osc52 only.
  Native order: darwin → `pbcopy`; `WAYLAND_DISPLAY` → `wl-copy`; `DISPLAY` →
  `xclip`, else `xsel`. A tool is available iff `which(name)` is truthy.
- `osc52=None` means OSC 52 is unavailable (e.g. headless); skip it.
- `run` default: `subprocess.run(argv, input=data, timeout=5,
  stdout=DEVNULL, stderr=DEVNULL)` returning the return code; any
  `OSError`/`TimeoutExpired` counts as failure. Argvs: `["pbcopy"]`,
  `["wl-copy"]`, `["xclip", "-selection", "clipboard"]`,
  `["xsel", "--clipboard", "--input"]`. Encode UTF-8.
- Empty text → `ok=False, message="Nothing to copy"`, nothing invoked.
- Message: `Copied N line(s)` (count `text.splitlines()`, min 1) + ` · ` +
  `+`.join(via). Failure lists what was tried and a hint (e.g. "install
  wl-clipboard" on Wayland, "enable set-clipboard in tmux" when `TMUX` set
  and only osc52). Payload > 100_000 bytes with osc52 in `via` appends
  ` (large: some terminals truncate OSC 52)`.
- Tests cover every branch with fake `env`/`which`/`run`/`osc52`.


WP3 — State and search
----------------------
Owns: `src/nbsee/state.py`, `src/nbsee/search.py`, `tests/test_state.py`,
`tests/test_search.py`. No Textual or Rich imports.

`search.py`
```python
@dataclass(frozen=True, slots=True)
class Hit:
    cell: int; region: Region; line: int; start: int; end: int   # offsets in cell.lines(region)[line]

def case_sensitive(query: str) -> bool          # smart-case: any uppercase char
def find(nb: Notebook, query: str) -> tuple[Hit, ...]
```
Order: cell, then INPUT before OUTPUT, line, start. Non-overlapping,
left-to-right. Empty query → `()`. Must be linear in total text; no regex
from user input (use `str.find`; lower-case both sides when insensitive).

`state.py`
```python
class Mode(Enum): NORMAL = "normal"; VISUAL = "visual"; LINE = "line"; LINE_VISUAL = "line-visual"

@dataclass(frozen=True)
class Search:
    query: str
    hits: tuple[Hit, ...]
    current: int | None        # index into hits

@dataclass(frozen=True)
class ViewState:               # fields exactly as DESIGN.md "State"
    ...
```
All transitions are pure `(state, nb, ...) -> ViewState`, clamp instead of
raising, and are no-ops on an empty notebook:
- `move_cell(s, nb, delta)`, `goto_cell(s, nb, index)` (negative = from end).
  Changing cell resets `region` to INPUT and `line` to 0 — except in VISUAL,
  where `region` is kept (it selects what the range yanks).
- `toggle_region(s, nb)`: only switches to OUTPUT if the cell
  `has_output_text`; in LINE modes resets `line` to 0 and leaves LINE_VISUAL
  for LINE.
- `toggle_fold(s, nb)` (current cell), `toggle_all_outputs(s)`,
  `toggle_markdown_raw(s)`.
- `enter_visual(s)` (anchor = cursor; from VISUAL → NORMAL),
  `enter_line(s, nb)` (no-op if the region has no lines),
  `start_line_visual(s)` (LINE → LINE_VISUAL with anchor = line; LINE_VISUAL
  → LINE), `escape(s)`: LINE_VISUAL→LINE→NORMAL, VISUAL→NORMAL, NORMAL clears
  search.
- `move_line(s, nb, delta)`, `goto_line(s, nb, index)` (LINE modes only).
- `set_search(s, nb, query)`: compute hits; `current` = first hit at or after
  the cursor position (wrap), and move the cursor (and in LINE modes the
  line, if the hit is in the current region) to it. `search_step(s, nb, ±1)`
  wraps. `clear_search(s)`. In VISUAL, search moves extend the range.
- `on_reload(s, nb)`: clamp cursor/line, drop folds ≥ cell count, recompute
  hits for the same query.
- `visual_range(s) -> tuple[int, int]` (inclusive, ordered; cursor..cursor
  outside VISUAL), `line_range(s) -> tuple[int, int]`.
- `yank(s, nb) -> str` for `y`, `yank_all(s, nb) -> str` for `Y`, with the
  semantics in DESIGN.md "Modes and keymap". Strip trailing newlines from
  each piece; join cells with `"\n\n"`. In LINE modes `y` = selected lines
  joined by `\n`, `Y` = whole region.
Tests: table-style tests for every transition and both yank functions,
including empty notebook, markdown-only notebook, cells without output,
visual over mixed cells (the DESIGN.md example), search wraparound and
smart-case.


WP4 — TUI (after WP1–3 land)
----------------------------
Owns: `src/nbsee/render.py`, `src/nbsee/tui/`, `scripts/bench.py`,
`tests/test_render.py`, `tests/test_tui.py`; deletes
`src/nbsee/textual_app.py`.

- Entry: `nbsee.tui.app.run(nb: Notebook, start: int = 0) -> int`.
- Follow DESIGN.md "Viewer" exactly: Line-API `ScrollView` virtualization,
  lazy layout with estimated heights and scroll anchoring, LRU content cache,
  overlays for cursor/selection/search, 500-line output cap in the view,
  status bar, outline, help, reload polling.
- `render.py` is Textual-free (Rich only): `render_cell(cell, language, width,
  *, markdown_raw, folded, theme) -> list[Segment lines]` plus
  `estimate_height(cell, width)`. Unit-test with fixed width.
- The app holds one `ViewState` and calls `state.py` functions; it must not
  reimplement navigation/yank logic. Copy goes through `clipboard.copy` with
  `osc52=self.copy_to_clipboard`.
- `tests/test_tui.py`: Pilot smoke tests via `App.run_test()` (allowed; keep
  each < 1 s): open fixture, j/k, Tab, y (assert clipboard text via patched
  `clipboard.copy`), V range yank, `/` search + n, `z`/`Z`, line mode, `:N`.
- `scripts/bench.py`: generate a 5,000-cell notebook in a temp dir, time
  `load()`, app startup to first paint, and 200 `j` presses via Pilot.


WP5 — Docs and cleanup (lead)
-----------------------------
README, AGENTS.md (tests may use Pilot; clipboard tools list), TODO.md,
CHANGELOG, pyproject entry point and version `0.2.0`.
