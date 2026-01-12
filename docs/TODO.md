nbsee TODOs
============

MVP Scope (Interactive viewer)
- [x] CLI skeleton: `nbsee <path.ipynb>` opens TUI
- [x] Validate path and read file
- [x] Parse notebook JSON (cells, types, sources)
- [x] Multi-cell stacked layout with scrolling
- [x] Render Input region (code/markdown/raw)
- [x] Render Output region for code cells (text/plain only)
- [x] Focus toggling: `Tab` cycles Input/Output
- [x] Navigation: `J`/`K` select cells; `↑/↓` scroll; `PgUp/PgDn` page
- [x] Copy: `y` copies focused region via `xclip -selection clipboard`
- [x] Feedback: status line for copy success/failure
- [ ] Handle errors (file not found, bad JSON, not a notebook, bad cell index) — basic done, refine messages
- [ ] Tests for parsing and selection logic
- [ ] Basic docs and examples

Rich/Textual UI
- [x] Use Rich/Textual as the default (and only) UI
- [ ] Style tuning for panels, themes, and readability
- [ ] Add cell collapse/expand interactions
- [ ] Add search ("/") and go-to (":N")
- [x] Global fold toggle for outputs (`z`)
- [x] Visual selection of inputs with `Shift+V`, adjust with `j/k`, yank with `y`

Copying Experience
- [ ] Linux/X11 clipboard via `xclip -selection clipboard`
- [ ] Detect missing `xclip` and show helpful message
- [ ] Optional later: Wayland (`wl-copy`) and macOS (`pbcopy`) support

Rendering
- [x] Simple, readable headers and focus markers (Rich panels)
- [x] Python syntax highlighting via Rich `Syntax`

Packaging
- [ ] `pyproject.toml` with console script entry `nbsee`
- [x] Depend on `rich` and `textual` directly (no extras)
- [ ] Document `uv` workflow (run, venv, install editable)

Post-MVP / Later
- [ ] Cell range display `--range A:B`
- [ ] Colorized code (Pygments)
- [ ] Rich outputs (HTML/images) placeholders
- [ ] Interactive TUI mode (Textual)
- [ ] Search/filter cells
- [ ] Copy multiple cells/ranges
