Changelog
=========

0.2.0
-----
Full rewrite.

- New `nbsee ls` and `nbsee cat` (md, text, py, json; cell selectors; stdin).
- Virtualized viewer: large notebooks open instantly and scroll smoothly.
- Clipboard works everywhere: OSC 52 (SSH, tmux) plus `pbcopy`, `wl-copy`,
  `xclip`/`xsel`. Copies are plain text without ANSI escapes.
- Line mode (`Enter`) to copy individual lines; `Y` copies whole cells.
- Search highlights every hit and steps per hit with a `k/N` counter.
- Rendered Markdown (`m` toggles raw), outline sidebar (`o`), help (`?`),
  go to cell (`:N`), per-cell folds (`z`; `Z` for all), auto-reload (`R`).
- Placeholders for image/HTML outputs; colored tracebacks; progress bars
  collapsed to their final state.
- `z` now folds the current cell; the old global toggle moved to `Z`.
- Requires Python 3.11+, Textual 8, Rich 15. `xclip` is no longer required.

0.1.2
-----
- Initial Textual viewer.
