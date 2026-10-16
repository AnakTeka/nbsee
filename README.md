nbsee
=====

[![PyPI version](https://img.shields.io/pypi/v/nbsee)](https://pypi.org/project/nbsee/)
[![Python versions](https://img.shields.io/pypi/pyversions/nbsee)](https://pypi.org/project/nbsee/)
[![License](https://img.shields.io/pypi/l/nbsee)](https://pypi.org/project/nbsee/)

Fast terminal reader for Jupyter notebooks: find something, copy it. Works
locally, over SSH, on Linux (X11/Wayland) and macOS.

![nbsee demo: moving through cells, outline, search, copying lines and a range of cells](https://raw.githubusercontent.com/AnakTeka/nbsee/main/docs/demo.gif)

Features
- Interactive viewer with vim-style navigation; opens 5,000-cell notebooks instantly
- Copy a cell's code or output, a few lines, or a range of cells
- Clipboard over SSH and in tmux (OSC 52), plus `pbcopy` / `wl-copy` / `xclip` locally
- Search inputs and outputs, step through every hit
- Syntax highlighting, rendered Markdown, colored tracebacks, outline sidebar
- Placeholders for images and HTML outputs; nothing is ever executed
- Agent-friendly: `ls` / `cat` turn notebooks into compact, cell-numbered markdown or JSON

Install

**uv (recommended)**

```bash
# Run without installing
uvx nbsee notebook.ipynb

# Or install permanently
uv tool install nbsee
```

**pip**
```bash
pip install nbsee
```

Usage

```bash
nbsee notebook.ipynb              # open the viewer
nbsee view notebook.ipynb --cell 12

nbsee ls notebook.ipynb           # one line per cell
nbsee cat notebook.ipynb          # whole notebook as markdown
nbsee cat notebook.ipynb -c 3:7 -f py          # cells 3-7 as a percent-format script
nbsee cat notebook.ipynb -c -1 --only output   # output of the last cell
cat notebook.ipynb | nbsee cat - -f json
```

`cat` formats: `md` (default, good for LLMs), `text` (colored on a terminal),
`py` (jupytext percent format), `json`. Cell specs are 1-based and inclusive:
`3`, `3:7`, `:5`, `10:`, `-1`, `1,4:6`.

For agents and LLMs

Raw `.ipynb` is JSON full of base64 images, escaped source and ANSI-colored
tracebacks: expensive to read and easy to misparse. nbsee gives coding agents
(Claude Code, Codex, Cursor, …) a cheap, structured way in:

```bash
nbsee ls nb.ipynb                         # map: cell, type, outputs, summary
nbsee cat nb.ipynb -c 11 --only output    # just that traceback, as plain text
nbsee cat nb.ipynb -c 4:8                 # those cells as markdown
nbsee cat nb.ipynb -f json                # structured, for tools
```

- **Smaller:** the scikit-learn intro from the *Python Data Science Handbook*
  is 412 KB as `.ipynb`, 29 KB as `nbsee cat` (14× less), and 6.6 KB as
  `nbsee ls`. Notebooks with mostly text output shrink less (2–14× in our
  tests).
- **Addressable:** markdown output keeps cell numbers
  (`<!-- cell 3 · code · In[7] -->`), so an agent can cite and re-fetch exact
  cells.
- **Clean:** images and HTML become one-line placeholders
  (`[image/png 640×480]`), ANSI escapes are stripped, progress bars collapse
  to their final line.
- **Safe:** read-only; nothing is executed, so it is fine on untrusted
  notebooks.

Tip: add this to your `AGENTS.md` / `CLAUDE.md`:

```
To read a Jupyter notebook, run `nbsee ls <file>`, then
`nbsee cat <file> -c <cells>`, instead of reading the .ipynb directly.
```

Key bindings (press `?` in the viewer for the full list)

![nbsee viewer with the outline open and search hits highlighted in a training log](https://raw.githubusercontent.com/AnakTeka/nbsee/main/docs/screenshot.png)

| Key | Action |
|---|---|
| `j` / `k` | next / previous cell |
| `↓` / `↑`, `Ctrl+d` / `Ctrl+u`, `PgDn` / `PgUp` | scroll line / half page / page |
| `gg` / `G`, `:N` | first / last cell, go to cell N |
| `Tab` | switch between input and output |
| `y` / `Y` | copy current region / whole cell (input + output) |
| `V` | select a range of cells; `y` copies the code, `Y` everything |
| `Enter` | line mode: `j`/`k` move, `V` select lines, `y` copy |
| `/`, `n` / `N` | search, next / previous hit |
| `z` / `Z` | fold this output / all outputs |
| `m` | rendered / raw markdown |
| `o` | outline sidebar |
| `R` | reload (also automatic when the file changes) |
| `Esc` | leave mode / clear search |
| `q` | quit |

Clipboard

nbsee always sends OSC 52, which most modern terminals (kitty, WezTerm,
iTerm2, Alacritty, foot, Windows Terminal, …) support, including over SSH.
Locally it also runs `pbcopy` (macOS), `wl-copy` (Wayland) or `xclip`/`xsel`
(X11). In tmux, enable `set -g set-clipboard on`. Override with
`NBSEE_CLIPBOARD=osc52|native|auto`.

Development (uv)
- Run: `uv run nbsee path/to/notebook.ipynb`
- Tests: `uv run pytest`
- Lint/format: `uv run ruff check .` and `uv run ruff format --check .`
- Benchmark: `uv run python scripts/bench.py`
- Build: `uv build`
- Release: push a tag like `v0.2.0` to trigger `.github/workflows/publish.yml`
  (requires PyPI trusted publishing).
