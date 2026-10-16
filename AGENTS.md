# Repository Guidelines

## Project Structure & Module Organization

- `src/nbsee/`: Python package (src-layout). Dependencies point one way:
  `model` ← `ansi`/`selectors`/`search`/`export` ← `state` ← `render` ← `tui`.
  - `cli.py`: entry point (`nbsee [view|ls|cat]`); `__main__.py` just calls it.
  - `model.py`: immutable Notebook/Cell/Output parsed from `.ipynb` JSON.
  - `ansi.py`, `selectors.py`, `search.py`, `export.py`: pure helpers.
  - `state.py`: pure view state machine (navigation, modes, yank). No Textual.
  - `clipboard.py`: OSC 52 + native clipboard backends.
  - `render.py`: Rich-only cell rendering with row geometry.
  - `tui/`: Textual app, virtualized `NotebookView`, status bar, outline, help.
- `scripts/bench.py`: large-notebook performance benchmark.
- `docs/`: design and planning notes (`docs/DESIGN.md`, `docs/REWRITE.md`, `docs/TODO.md`).
- `tests/`: `pytest` tests; sample notebooks in `tests/fixtures/`.
- `pyproject.toml`: packaging metadata and console script wiring.
- Local-only: `.venv/`, `.uv-cache/` (don’t commit).

## Build, Test, and Development Commands

This repo uses `uv` for fast Python workflows.

- Create/activate a venv: `uv venv && source .venv/bin/activate`
- Install editable: `uv pip install -e .`
- Run without installing: `uv run -m nbsee path/to/notebook.ipynb`
- Run tests (installs `dev` deps automatically): `uv run pytest`
- Benchmark: `uv run python scripts/bench.py`
- Lint/format check: `uv run ruff check .` and `uv run ruff format --check .`
- Build distributions: `uv build` (creates `dist/`)
- Run installed CLI: `nbsee path/to/notebook.ipynb`

Clipboard: OSC 52 works in most terminals (incl. SSH/tmux); locally nbsee also uses
`pbcopy`, `wl-copy`, or `xclip`/`xsel` if installed.

## Coding Style & Naming Conventions

- Python: follow PEP 8, 4-space indentation, and keep functions small/pure where practical.
- Types: prefer `typing` annotations for public helpers and non-trivial return values.
- Naming: modules `snake_case.py`; functions/vars `snake_case`; classes `CapWords`.
- UI behavior: keep key bindings and UX consistent with `docs/DESIGN.md`.

This repo uses `ruff` for linting/formatting checks.

## Testing Guidelines

Tests use `pytest` and live under `tests/` with `test_*.py` naming.

- Run locally: `uv run pytest`
- Most logic is pure (`model`, `state`, `export`, …); test it directly.
- TUI tests use Textual's headless `App.run_test()` Pilot; keep them fast.
- Never spawn real clipboard tools in tests; inject fakes into `clipboard.copy`.

## Commit & Pull Request Guidelines

- Use conventional, imperative subjects (e.g., `feat: add search prompt`, `fix: handle missing cells`).
- Keep commits logical: one coherent change per commit, with its tests.
- PRs should include: a short description, manual verification steps (commands + a sample `.ipynb`), and any UX/keybinding changes reflected in `docs/DESIGN.md` / `README.md`.
  - If the change affects release/publishing, update `.github/workflows/publish.yml` as needed.

## Security & Configuration Notes

- Treat `.ipynb` files as untrusted input: never execute notebook content; only parse JSON and render text outputs.
- Keep external process calls limited to clipboard tools (`pbcopy`, `wl-copy`, `xclip`, `xsel`),
  with fixed argv and no shell.
