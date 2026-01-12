import json

import pytest

from nbsee.__main__ import _as_text, _cell_input_text, _cell_output_text, _load_notebook


def test_as_text_handles_none_and_lists():
    assert _as_text(None) == ""
    assert _as_text(["a", "b", 3]) == "ab3"
    assert _as_text("x") == "x"


def test_cell_input_text_reads_source():
    assert _cell_input_text({"source": "print(1)\n"}) == "print(1)\n"
    assert _cell_input_text({"source": ["a\n", "b\n"]}) == "a\nb\n"


def test_cell_output_text_supports_text_plain_stream_and_error():
    cell = {
        "cell_type": "code",
        "outputs": [
            {"output_type": "execute_result", "data": {"text/plain": ["1", "\n"]}},
            {"output_type": "stream", "name": "stdout", "text": ["hello", "\n"]},
            {"output_type": "error", "traceback": ["Traceback...", "\n", "Boom"]},
        ],
    }
    assert _cell_output_text(cell) == "1\n\nhello\n\nTraceback...\nBoom"


def test_cell_output_text_ignores_non_code_cells():
    assert (
        _cell_output_text({"cell_type": "markdown", "outputs": [{"text": "x"}]}) == ""
    )


def test_load_notebook_requires_cells_list(tmp_path):
    good = tmp_path / "good.ipynb"
    good.write_text(json.dumps({"cells": [{"cell_type": "markdown", "source": "hi"}]}))
    assert _load_notebook(str(good)) == [{"cell_type": "markdown", "source": "hi"}]

    bad = tmp_path / "bad.ipynb"
    bad.write_text(json.dumps({"metadata": {}}))
    with pytest.raises(ValueError, match="missing cells"):
        _load_notebook(str(bad))
