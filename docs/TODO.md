nbsee TODOs
===========

Known issues (v0.2)
- Rendered markdown has no exact source-line ↔ row mapping: line mode shows
  the cell as raw source, and search highlights are matched by text.
- A single very long output line is wrapped in full (no width cap).
- After a terminal resize the cursor is not forced back into view.
- A render that misses the cache (e.g. a 500-line output entering view) can
  take 20–45 ms.

Ideas
- Inline images (kitty / iTerm2 / sixel).
- Filter view: show only cells matching a search or tag.
- `nbsee diff a.ipynb b.ipynb`.
- Config file for theme and clipboard defaults.
