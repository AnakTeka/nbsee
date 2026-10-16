nbsee Design (v0.2 rewrite)
===========================

Status: draft for review. This replaces the MVP design; the old code is cut over
in one go (no compatibility layer).

Goal
----
A fast terminal reader for Jupyter notebooks that makes it trivial to **find**
something and **copy** it — code, output, a few lines, or a range of cells —
interactively (TUI) or non-interactively (`cat` / `ls` for pipes, SSH, and AI
agents).

Non-goals
- Executing notebooks, talking to kernels, or editing `.ipynb` files.
- Rendering HTML/JS outputs. Rich outputs get a placeholder, never executed.
- Inline images (possible later via kitty/iTerm2/sixel; not in v0.2).

Principles
- `.ipynb` is untrusted input: parse JSON only, never execute or render active
  content. Strip terminal control sequences before display.
- Parse once into an immutable model; everything else reads from it.
- UI logic lives in a pure, testable state module; widgets stay thin.
- What you copy is exactly the plain text you see (no ANSI escapes).

Supported environments
- Python >= 3.11, Textual >= 8.2, Rich >= 15.
- Linux (X11 + Wayland), macOS, and any of these over SSH / inside tmux.


Architecture
------------

```
src/nbsee/
  __init__.py     __version__
  __main__.py     `from .cli import main` (keeps `python -m nbsee`)
  cli.py          argparse, subcommand dispatch, exit codes
  model.py        Notebook / Cell / Output dataclasses + load()
  ansi.py         sanitize(), strip_background(), to_plain()
  selectors.py    parse cell specs like "3", "3:7", "-1", "1,4:6"
  export.py       Notebook -> text | md | py | json   (used by `cat`)
  clipboard.py    copy(text) with OSC 52 + native backends
  state.py        ViewState + pure transition functions (no Textual imports)
  search.py       find hits across cells, smart-case
  render.py       Cell -> Rich renderables (+ per-width cache)
  tui/
    app.py        NotebookApp: key bindings -> state transitions -> refresh
    view.py       NotebookView (Line-API ScrollView, virtualized)
    outline.py    heading tree sidebar
    statusbar.py  mode / position / search counter / transient messages
    help.py       `?` overlay generated from the binding table
```

Dependency direction: `model` ← `ansi`/`selectors`/`search`/`export` ←
`state` ← `render` ← `tui`. `cli` uses `model` + `export` directly and only
imports `tui` for the interactive viewer (so `cat`/`ls` start fast).


Data model (`model.py`)
-----------------------

```python
CellKind = Literal["code", "markdown", "raw"]
OutputKind = Literal["stream", "result", "display", "error"]

@dataclass(frozen=True, slots=True)
class Output:
    kind: OutputKind
    text: str | None          # text/plain, stream text, or joined traceback (raw, may hold ANSI)
    stream: str | None        # "stdout" / "stderr" for streams
    mimes: tuple[str, ...]    # all mime types present, e.g. ("image/png", "text/plain")
    ename: str | None         # error name/value for error outputs
    evalue: str | None

    @property
    def placeholder(self) -> str | None   # "[image/png 800×600]", "[text/html]"; None if text

@dataclass(frozen=True, slots=True)
class Cell:
    index: int                # 0-based internally; shown 1-based everywhere
    kind: CellKind
    source: str
    outputs: tuple[Output, ...]
    execution_count: int | None
    # cached derived values: output_text (plain), has_error, headings

@dataclass(frozen=True, slots=True)
class Notebook:
    path: str | None          # None when read from stdin
    language: str             # from metadata.language_info/kernelspec, default "python"
    cells: tuple[Cell, ...]

def load(source: str | Path | IO[str]) -> Notebook   # raises NotebookError
```

Parsing rules
- Accept nbformat 4.x. nbformat 3 (`worksheets`) → clear `NotebookError`.
- `source` / `text` may be a string or list of strings; join without separators.
- Output text precedence per output: `text/plain` → stream `text` → traceback.
  Tracebacks: join with `""` if any element contains `\n`, else with `\n`.
- Image dimensions come from `metadata[mime]` width/height when present; for
  PNG, decode only the IHDR header bytes. Never decode full payloads.
- Missing/odd fields degrade gracefully; only a missing `cells` list is fatal.


Text handling (`ansi.py`)
-------------------------
- `sanitize(s)`: normalize CR/LF, drop OSC and non-SGR CSI sequences and C0
  control chars, keep SGR colors. (Ported from the current implementation.)
- `strip_background(s)`: drop SGR background colors (tracebacks render badly
  otherwise).
- `to_plain(s)`: remove all escapes. Used for copying, searching, `cat` without
  color, and measuring.


CLI (`cli.py`)
--------------

```
nbsee FILE                          open the viewer (same as `nbsee view FILE`)
nbsee view FILE [--cell N]          open the viewer, cursor at cell N
nbsee ls   FILE|- [--json]          one line per cell: index, kind, lines, outputs, summary
nbsee cat  FILE|- [-c SPEC] [--only input|output] [--no-outputs]
                  [-f md|text|py|json] [--color auto|always|never]
```

- If the first argument is not a known subcommand, it is treated as `FILE` for
  `view` (so `nbsee nb.ipynb` keeps working). `nbsee -- cat` opens a file named
  `cat`.
- `-` reads the notebook from stdin (`cat`/`ls` only; the viewer needs a tty).
- Cell specs (`selectors.py`), 1-based and inclusive: `3`, `3:7`, `:5`, `10:`,
  `-1` (last), comma lists `1,4:6,-1`. Out-of-range → error, exit 1.
- Formats:
  - `md` (default): see below. Readable in a terminal, ideal for piping into
    LLMs, and stable for scripts (same output on a tty or a pipe).
  - `text`: `── [3] code ──` style headers, input, then output; ANSI
    colors kept only when `--color` resolves to on (tty and no `NO_COLOR`).
  - `md`: markdown cells verbatim, code in fenced blocks tagged with the
    notebook language, outputs in ```` ```text ```` blocks. Good for LLMs.
  - `py`: percent format (`# %%`, `# %% [markdown]` with commented markdown);
    outputs omitted unless `--only output`.
  - `json`: `[{index, kind, source, execution_count, outputs: [{kind, text, mimes}]}]`.
- `ls` summary: first markdown heading, or first non-empty source line,
  truncated to terminal width; outputs as `stdout`, `err:ValueError`,
  `image/png`, etc.
- Exit codes: `0` success, `1` usage / bad selector / empty notebook,
  `2` file not found / invalid JSON / not a notebook. Messages go to stderr,
  one line, no tracebacks.


Clipboard (`clipboard.py`)
--------------------------

```python
@dataclass(frozen=True)
class CopyResult:
    ok: bool
    via: tuple[str, ...]      # e.g. ("osc52",) or ("osc52", "wl-copy")
    message: str

def copy(text: str, *, osc52: Callable[[str], None], env=os.environ, which=shutil.which) -> CopyResult
```

Strategy (`NBSEE_CLIPBOARD=auto` default):
1. Always emit **OSC 52** (via Textual's `App.copy_to_clipboard`). Works locally,
   over SSH, and in tmux with `set -g set-clipboard on`.
2. If **not** in an SSH session (`SSH_CONNECTION`/`SSH_TTY` unset), also run the
   native tool, first match wins:
   - macOS: `pbcopy`
   - `WAYLAND_DISPLAY` set: `wl-copy`
   - `DISPLAY` set: `xclip -selection clipboard`, else `xsel --clipboard --input`
3. Over SSH, never run native tools (they'd target the remote clipboard).

Overrides: `NBSEE_CLIPBOARD=osc52|native|auto`. OSC 52 success can't be
confirmed by the terminal, so the status message says how it was sent
("Copied 12 lines · osc52+wl-copy"). Payloads > 100 KB show a warning, since
some terminals/tmux truncate OSC 52.

External processes are limited to these clipboard tools, invoked with a fixed
argv (no shell), text on stdin, 5 s timeout.


Viewer
------

### Layout

```
┌ outline (o) ──┐ ┌─────────────────────────────────────────────┐
│ Intro         │ │ ▶ [3] code  In[7]                           │
│  Setup        │ │ ╭───────────────────────────────────────────╮│
│  Training ◀   │ │ │ model.fit(x, y)                           ││
│ Results       │ │ ╰───────────────────────────────────────────╯│
│               │ │ ╭ Output ───────────────────────────────────╮│
│               │ │ │ Epoch 1/10 ...                            ││
│               │ │ ╰───────────────────────────────────────────╯│
└───────────────┘ └─────────────────────────────────────────────┘
 nb.ipynb  3/42  NORMAL  input   /loss 2/9          Copied 3 lines · osc52
```

- Cells: header (`▶` cursor, `◆` in visual range, index, kind, `In[n]`),
  input panel, output panel. Error outputs get a red border and `Error` title.
- Code: Rich `Syntax` with the notebook language, wrapped.
- Markdown: rendered with Rich `Markdown`; `m` toggles raw source for all
  markdown cells. Copy always copies the raw source.
- Non-text outputs: dim placeholder lines (`[image/png 800×600]`).
- The outline sidebar is hidden by default; it lists markdown headings and
  tracks the cursor.
- Status bar replaces Textual's `Footer`; full bindings live in `?` help.

### Rendering and scrolling (`tui/view.py`, `render.py`)
Requirement: open and scroll a 5,000-cell / 50 MB notebook smoothly; cost of
any keypress is proportional to what is on screen, not notebook size.

- `NotebookView` is a single Line-API `ScrollView` (no widget per cell). It
  implements `render_line(y)` and paints only visible lines.
- Each cell renders to a list of `Strip`s at the current width. A layout
  table (prefix sums of cell heights) maps virtual y ↔ (cell, line) in
  O(log n) via bisect.
- Lazy layout: unrendered cells use an estimated height (source lines +
  output lines, ignoring wrap). Cells are rendered on demand when they enter
  the viewport (plus a small margin) and a background worker refines the rest
  in chunks. When a real height replaces an estimate above the viewport, the
  scroll offset is adjusted so the visible content does not jump.
- Content cache: LRU keyed by `(cell index, width, markdown_raw, folded,
  theme)`. Width changes invalidate lazily (re-render on demand, not all at
  once).
- Cursor, region focus, visual range, line cursor and search highlights are
  overlays applied in `render_line`, so navigation never re-runs `Syntax` or
  `Markdown`.
- Huge outputs are capped in the view (first 500 lines + "… N more lines",
  expand with `Enter` in LINE mode); copy and `cat` always use the full text.
- Search runs over the model's plain text (not rendered strips), so it is
  O(total text) once per query and does not force rendering.
- Positions are exact virtual coordinates, so half-page moves, "cell at top of
  screen" and line-cursor placement are simple arithmetic. (Fixes the current
  screen-vs-virtual coordinate bug.)
- A benchmark script (`scripts/bench.py`) generates large notebooks and
  measures open time and per-keypress latency; tracked before release.

### State (`state.py`)
Pure data plus functions `(state, notebook, …) -> state`. No Textual imports.

```python
class Mode(Enum): NORMAL, VISUAL, LINE, LINE_VISUAL
class Region(Enum): INPUT, OUTPUT

@dataclass(frozen=True)
class ViewState:
    cursor: int = 0                 # cell index
    region: Region = Region.INPUT   # resets to INPUT when the cursor moves
    mode: Mode = Mode.NORMAL
    anchor: int = 0                 # cell anchor (VISUAL) or line anchor (LINE_VISUAL)
    line: int = 0                   # line cursor within the region (LINE modes)
    outputs_hidden: bool = False    # global fold
    folded: frozenset[int] = frozenset()   # per-cell output folds
    markdown_raw: bool = False
    search: Search | None = None    # query, hits, current

def selection_text(state, nb) -> str   # exactly what `y` would copy
```

### Modes and keymap

Normal (cell level)

| Key | Action |
|---|---|
| `j` / `k` | next / previous cell |
| `↓` / `↑` | scroll one line |
| `Ctrl+d` / `Ctrl+u` | half page down/up; cursor moves to the cell at the same screen position |
| `Ctrl+f` / `Ctrl+b`, `PgDn` / `PgUp` | page down/up |
| `gg` / `G` | first / last cell |
| `:N` Enter | go to cell N |
| `Tab` | toggle region input ↔ output (no-op with message if no output) |
| `y` | copy current region (plain text) |
| `Y` | copy whole cell: input, blank line, output |
| `z` | fold/unfold this cell's output |
| `Z` | fold/unfold all outputs |
| `m` | toggle rendered/raw markdown |
| `Enter` | enter LINE mode in the current region |
| `V` | enter VISUAL (cell range) |
| `/` | search; `n` / `N` next / previous hit |
| `Esc` | clear search highlights / leave mode |
| `o` | toggle outline sidebar (focus moves to it; `Enter` jumps, `o`/`Esc` returns) |
| `R` | reload from disk (also automatic when the file changes) |
| `?` | help overlay |
| `q` | quit |

Visual (cell range)
- `j`/`k`/`gg`/`G`/`n`/`N` extend the range; `Tab` picks which region is
  yanked for every cell (input or output); `y` copies and exits.
- `y` with region INPUT copies **code cells only** (runnable code), joined by
  a blank line. With region OUTPUT it copies the outputs of the range.
- `Y` copies **everything in order**: markdown and code sources plus their
  outputs, each cell separated by a blank line (paste-ready for notes/LLMs).

Line (inside one cell region)
- A line cursor appears; `j`/`k` move, `gg`/`G` first/last line, `Tab`
  switches region, `y` copies the current line.
- `V` starts a line range (LINE_VISUAL); `y` copies the lines and returns to
  NORMAL. `Esc` steps back one mode.

Search
- `/query` Enter. Smart-case: case-insensitive unless the query has an
  uppercase letter. Searches input and plain-text output.
- All hits in visible cells are highlighted; `n`/`N` step **per hit** (not per
  cell), wrap around, and the status bar shows `3/17`.
- `Tab` and other priority bindings are disabled while the prompt has focus
  (fixes Tab stealing input today).

### Reload
Poll the file's mtime every second. On change, re-`load()`, keep the cursor on
the same index (clamped), keep folds, re-run the search. Parse errors show in
the status bar and keep the old content.


Testing
-------
- `tests/fixtures/*.ipynb`: basic, ANSI tracebacks, images/html, empty,
  malformed, nbformat 3, large generated notebook.
- Unit tests (bulk of coverage): `model`, `ansi`, `selectors`, `search`,
  `export` (golden outputs), `state` transitions incl. `selection_text`,
  `clipboard` strategy with injected `env`/`which`/runner (never spawns real
  tools).
- `render`: render cells with a fixed-width `Console(record=True)` and compare
  plain text.
- TUI smoke tests with Textual's `run_test()` Pilot: open, navigate, copy
  (OSC 52 captured), search. Fast and headless; AGENTS.md updated to allow
  these.


Cutover plan
------------
1. **Model + CLI:** `model`, `ansi`, `selectors`, `export`, `cli ls/cat`, with
   tests. Useful on its own, and gives the TUI a solid base.
2. **State + clipboard:** `state`, `search`, `clipboard`, with tests.
3. **TUI at A-level parity:** view, status bar, normal/visual modes, search,
   copy. Delete `textual_app.py` and the old helpers in `__main__.py`.
4. **B features:** line mode, outline, markdown rendering, per-cell folds,
   `:N`, help, reload.
5. **Docs + release:** README, TODO, AGENTS.md, CHANGELOG; release `0.2.0`.
