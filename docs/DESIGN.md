Design Notes
============

Problem
- Quickly view Jupyter notebook cells in a terminal and easily copy either the code or the text/plain output.
- Avoid heavy rendering; keep it fast and focused on text.

Constraints / Non-goals (MVP)
- Do not render images, rich HTML, plots, or dataframes (treat them as placeholders or omit).
- Do not execute notebooks; viewing only.
- No in-place editing of notebooks.

CLI UX (proposed)
- `nbsee <notebook.ipynb>`: open an interactive viewer.
  - Multi-cell list layout with vertical scrolling provides context similar to a notebook editor.
  - Each cell renders: header `[idx/type]`, Input, separator, Output, and spacing.
  - Selection (J/K) highlights a cell header; focus marker (Tab) toggles Input/Output of the selected cell for copying.
- Exit codes: 0 on success; non-zero on IO/parse errors.

Keymap (MVP)
- `J` / `K`: next / previous cell (change selection)
- Arrow Up/Down: scroll the view line-by-line
- PageUp/PageDown: page scroll
- `Tab`: toggle focus between Input and Output for selected cell (no-op if no text output)
- `Ctrl+u` / `Ctrl+d`: scroll up/down by half a page
- `z`: toggle hide/show of all outputs (global fold)
- `Shift+V`: enter/exit visual selection for inputs; `j`/`k` adjust the range; `y` yanks (copies) all selected code cells to clipboard
- `/`: open a search prompt (inputs + outputs, case-insensitive)
- `n` / `N`: jump to next/previous match, wrapping around
- `gg` / `G`: jump to first/last cell
- `y`: copy focused region to clipboard using `xclip -selection clipboard`
- `G` / `gg`: jump to last / first cell (optional)
- `q`: quit

Copying Workflow
- Interactive: pressing `C` copies the currently focused region (Input or Output) to the clipboard via `xclip -selection clipboard` on Linux/X11.
- If Output is focused but the cell has no text/plain output, show a temporary inline message and do nothing.
- Piping-based copying is not required in MVP; we optimize for the interactive flow.

Rendering
- Multi-cell stacked view; soft-wrap long lines to terminal width.
- Rich/Textual UI only: panels with clear separation and high-quality `Syntax` highlighting.
- No horizontal scrolling in MVP; lines are wrapped.

Parsing
- Use Python stdlib `json` for MVP (avoid deps); consider `nbformat` later for robustness.
- Fields: `cells[]`, `cell_type`, `source`, `outputs[]`, `output_type`, `text`, `data["text/plain"]`.
- Be robust to missing fields.

Performance
- Stream parsing is not necessary for typical sizes.
- Avoid expensive re-renders; redraw only on navigation or copy feedback.

Structure (proposed)
- `nbsee/` package with `__main__.py` entry point.
- Single UI: Rich/Textual-based TUI with `textual_app.py`.
- `pyproject.toml` exposes console script `nbsee` and depends on `rich` and `textual`.

Error Handling
- Clear messages for: file not found, invalid JSON, not a notebook, cell out of range, missing output.
- In-view ephemeral notices for copy failures (e.g., xclip missing or no output to copy).

Future Ideas (post-MVP)
- Multi-cell list pane with scrolling and preview.
- Rich/HTML output fallback with `rich`/`textual`.
- Image placeholders with metadata (e.g., [image output: 800x600]).
- Search/filter cells by content or tags.
- Copy multiple cells or ranges.

Developer Workflow (uv)
- Quick run without install: `uv run -m nbsee path/to/notebook.ipynb`
- Create venv: `uv venv && source .venv/bin/activate`
- Install editable: `uv pip install -e .`
