"""Benchmark the viewer on a generated notebook.

    uv run python scripts/bench.py [--cells 5000] [--keep]

Reports load() time, time to first paint, and per-keypress latency (action +
repaint of the visible lines) for `j` and Ctrl+d, during and after background
layout refinement. Targets: first paint < 1 s, keypress p95 < 16 ms.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import tempfile
import time
from pathlib import Path

from nbsee import model
from nbsee.tui.app import NotebookApp, tune_gc
from nbsee.tui.view import NotebookView

SIZE = (120, 40)
TARGET_FIRST_PAINT = 1.0
TARGET_P95_MS = 16.0

TRACEBACK = [
    "\x1b[0;31m---------------------------------------------------------\x1b[0m",
    "\x1b[0;31mValueError\x1b[0m        Traceback (most recent call last)",
    "\x1b[0;32m~/work/model.py\x1b[0m in \x1b[0;36mfit\x1b[0;34m()\x1b[0m\n",
    "\x1b[0;31mValueError\x1b[0m: shapes (3,4) and (5,6) not aligned",
]


def code_source(rng: random.Random, i: int) -> str:
    lines = [f"def step_{i}(x):", "    total = 0"]
    for j in range(rng.randint(2, 25)):
        lines.append(f"    total += x * {j} + len('item-{i}-{j}')  # accumulate")
    lines.append("    return total")
    return "\n".join(lines)


def markdown_source(rng: random.Random, i: int) -> str:
    head = f"## Section {i}\n\n" if i % 25 == 0 else ""
    body = " ".join(f"word{rng.randint(0, 999)}" for _ in range(rng.randint(10, 60)))
    return f"{head}Notes for **cell {i}**:\n\n- {body}\n- second *item*"


def make_output(rng: random.Random, i: int) -> list[dict]:
    roll = rng.random()
    if roll < 0.35:
        return []
    if roll < 0.90:
        n = rng.randint(1, 12)
        text = "".join(f"result {i}.{k} = {rng.random():.6f}\n" for k in range(n))
        return [{"output_type": "stream", "name": "stdout", "text": text}]
    if roll < 0.95:
        n = rng.randint(800, 3000)
        text = "".join(
            f"epoch {k} loss={rng.random():.5f} acc={rng.random():.4f}\n"
            for k in range(n)
        )
        return [{"output_type": "stream", "name": "stdout", "text": text}]
    if roll < 0.98:
        return [
            {
                "output_type": "error",
                "ename": "ValueError",
                "evalue": "bad",
                "traceback": TRACEBACK,
            }
        ]
    return [
        {
            "output_type": "display_data",
            "data": {
                "image/png": "iVBORw0KGgo=",
                "text/plain": "<Figure size 640x480>",
            },
            "metadata": {},
        }
    ]


def generate(path: Path, count: int) -> None:
    rng = random.Random(7)
    cells = []
    for i in range(count):
        if rng.random() < 0.25:
            cells.append(
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": markdown_source(rng, i),
                }
            )
        else:
            cells.append(
                {
                    "cell_type": "code",
                    "metadata": {},
                    "execution_count": i,
                    "source": code_source(rng, i),
                    "outputs": make_output(rng, i),
                }
            )
    nb = {
        "cells": cells,
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb))


def stats(samples: list[float]) -> str:
    ms = sorted(s * 1000 for s in samples)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    return (
        f"p50 {statistics.median(ms):6.2f} ms  p95 {p95:6.2f} ms  max {ms[-1]:6.2f} ms"
    )


async def timed_action(app: NotebookApp, action: str) -> float:
    view = app.view
    t0 = time.perf_counter()
    await app.run_action(action)
    for y in range(view.size.height):  # what the compositor asks for after refresh
        view.render_line(y)
    return time.perf_counter() - t0


async def run_keys(
    app: NotebookApp, action: str, count: int
) -> tuple[list[float], list[float]]:
    latencies, lags = [], []
    for _ in range(count):
        latencies.append(await timed_action(app, action))
        t0 = time.perf_counter()
        await asyncio.sleep(0.02)  # key repeat pace; lets the refine worker run
        lags.append(max(0.0, time.perf_counter() - t0 - 0.02))
    return latencies, lags


async def bench(path: Path) -> None:
    t0 = time.perf_counter()
    nb = model.load(path)
    print(
        f"load():            {time.perf_counter() - t0:6.3f} s  "
        f"({len(nb.cells)} cells, {path.stat().st_size / 1e6:.1f} MB)"
    )

    first: list[float] = []
    original = NotebookView.render_line
    start = time.perf_counter()

    def spy(self: NotebookView, y: int):  # noqa: ANN202
        if not first and self._width and self.nb.cells:
            first.append(time.perf_counter() - start)
        return original(self, y)

    NotebookView.render_line = spy  # type: ignore[method-assign]
    tune_gc()
    app = NotebookApp(nb)
    try:
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            print(
                f"first paint:       {first[0]:6.3f} s  "
                f"(target < {TARGET_FIRST_PAINT} s)"
            )
            pending = len(app.view.table.pending())

            all_lat: list[list[float]] = []
            lat, lag = await run_keys(app, "move(1)", 200)
            all_lat.append(lat)
            print(
                f"j x200 (refining): {stats(lat)}   "
                f"[{pending} cells unmeasured at start]"
            )
            print(f"  event-loop lag:  {stats(lag)}")

            t1 = time.perf_counter()
            while app.view.table.pending():
                await asyncio.sleep(0.05)
            print(f"layout refined in {time.perf_counter() - t1:6.2f} s more")

            lat, _ = await run_keys(app, "move(1)", 200)
            all_lat.append(lat)
            print(f"j x200 (settled):  {stats(lat)}")
            lat, _ = await run_keys(app, "page(0.5)", 50)
            all_lat.append(lat)
            print(f"Ctrl+d x50:        {stats(lat)}")
            jump = await timed_action(app, "bottom")
            print(f"G (jump to end):   {jump * 1000:6.2f} ms")
            worst = max(
                sorted(s * 1000 for s in lat)[int(len(lat) * 0.95) - 1]
                for lat in all_lat
            )
            ok = first[0] < TARGET_FIRST_PAINT and worst < TARGET_P95_MS
            print(f"targets: {'PASS' if ok else 'FAIL'} (worst p95 {worst:.1f} ms)")
    finally:
        NotebookView.render_line = original  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cells", type=int, default=5000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nbsee-bench-") as tmp:
        path = Path(tmp) / "big.ipynb"
        generate(path, args.cells)
        asyncio.run(bench(path))


if __name__ == "__main__":
    main()
