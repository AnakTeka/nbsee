# Repository Guidelines

## Project Structure & Module Organization

- `src/nbsee/`: Python package (src-layout).
  - `src/nbsee/__main__.py`: CLI entry point (`nbsee <path.ipynb>`), notebook parsing, clipboard helper.
  - `src/nbsee/textual_app.py`: Textual-based TUI (rendering, key bindings, navigation).
- `docs/`: design and planning notes (`docs/DESIGN.md`, `docs/TODO.md`).
- `tests/`: `pytest` unit tests.
- `pyproject.toml`: packaging metadata and console script wiring.
- Local-only: `.venv/`, `.uv-cache/` (don’t commit).

## Build, Test, and Development Commands

This repo uses `uv` for fast Python workflows.

- Create/activate a venv: `uv venv && source .venv/bin/activate`
- Install editable: `uv pip install -e .`
- Run without installing: `uv run -m nbsee path/to/notebook.ipynb`
- Run tests (installs `dev` deps automatically): `uv run pytest`
- Lint/format check: `uv run ruff check .` and `uv run ruff format --check .`
- Build distributions: `uv build` (creates `dist/`)
- Run installed CLI: `nbsee path/to/notebook.ipynb`

Clipboard support is Linux/X11-focused; install `xclip` and ensure it’s on `PATH`.

## Coding Style & Naming Conventions

- Python: follow PEP 8, 4-space indentation, and keep functions small/pure where practical.
- Types: prefer `typing` annotations for public helpers and non-trivial return values.
- Naming: modules `snake_case.py`; functions/vars `snake_case`; classes `CapWords`.
- UI behavior: keep key bindings and UX consistent with `docs/DESIGN.md`.

This repo uses `ruff` for linting/formatting checks.

## Testing Guidelines

Tests use `pytest` and live under `tests/` with `test_*.py` naming.

- Run locally: `uv run pytest`
- Keep tests focused on pure logic (e.g., parsing and text extraction); avoid invoking `xclip` or launching the Textual UI in unit tests.

## Commit & Pull Request Guidelines

- Git history is not established yet; use conventional, imperative subjects (e.g., `feat: add search prompt`, `fix: handle missing cells`).
- PRs should include: a short description, manual verification steps (commands + a sample `.ipynb`), and any UX/keybinding changes reflected in `docs/DESIGN.md` / `README.md`.
  - If the change affects release/publishing, update `.github/workflows/publish.yml` as needed.

## Security & Configuration Notes

- Treat `.ipynb` files as untrusted input: never execute notebook content; only parse JSON and render text outputs.
- Keep external process calls limited to clipboard tools (currently `xclip`).
