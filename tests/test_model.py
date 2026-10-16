import io
import json
from pathlib import Path

import pytest

from nbsee.model import NotebookError, Region, load, parse

FIXTURES = Path(__file__).parent / "fixtures"


def nb_with(*cells, **top):
    return parse({"cells": list(cells), **top})


def code(source="", outputs=None, **extra):
    cell = {"cell_type": "code", "source": source, **extra}
    if outputs is not None:
        cell["outputs"] = outputs
    return cell


def test_basic_fixture():
    nb = load(FIXTURES / "basic.ipynb")
    assert nb.path == str(FIXTURES / "basic.ipynb")
    assert nb.language == "python"
    assert nb.nbformat == 4
    assert [c.kind for c in nb.cells] == ["markdown", "code", "code", "raw", "code"]
    assert [c.index for c in nb.cells] == [0, 1, 2, 3, 4]
    assert [c.execution_count for c in nb.cells] == [None, 1, 2, None, 3]
    assert nb.cells[0].headings == ((1, "Title"), (2, "Setup"))
    assert nb.cells[1].output_text == "3.141592653589793"
    assert nb.cells[2].output_text == "2"
    assert not nb.cells[4].has_output_text


def test_empty_fixture():
    nb = load(FIXTURES / "empty.ipynb")
    assert nb.cells == ()


def test_errors_fixture():
    nb = load(FIXTURES / "errors.ipynb")
    err, stderr, progress = nb.cells
    assert err.has_error
    assert err.outputs[0].ename == "ValueError"
    assert "\x1b" not in err.output_text
    assert "ValueError: invalid literal" in err.output_text
    assert stderr.outputs[0].stream == "stderr"
    assert not stderr.has_error
    assert progress.output_text == "100%|##########| 3/3"


def test_traceback_pieces_are_on_separate_lines():
    err = load(FIXTURES / "errors.ipynb").cells[0]
    lines = err.output_text.splitlines()
    assert lines[0].startswith("-----")
    assert lines[1].startswith("ValueError")


def test_rich_outputs_fixture():
    a, b, c, d = load(FIXTURES / "rich_outputs.ipynb").cells
    assert a.outputs[0].size == (300, 200)  # from metadata, not the 3x2 PNG
    assert a.outputs[0].plain == "<Figure size 3x2>"
    assert b.outputs[0].size == (3, 2)  # from the PNG header
    assert b.outputs[0].placeholder == "[image/png 3×2]"
    assert c.outputs[0].plain == "   a\n0  1"
    assert c.outputs[0].mimes == ("text/html", "text/plain")
    assert c.outputs[0].placeholder is None
    assert d.outputs[0].plain == ""
    assert d.outputs[0].placeholder == "[text/html]"
    assert not d.has_output_text


def test_backticks_fixture_round_trips_source():
    nb = load(FIXTURES / "backticks.ipynb")
    assert "```" in nb.cells[0].source
    assert '"````"' in nb.cells[1].source


def test_nbformat3_is_rejected():
    with pytest.raises(NotebookError, match="nbformat 3"):
        load(FIXTURES / "nbformat3.ipynb")


def test_malformed_json_reports_position():
    with pytest.raises(NotebookError, match=r"invalid JSON at line 2 col 1"):
        load(FIXTURES / "malformed.json")


def test_load_errors(tmp_path):
    with pytest.raises(NotebookError, match="file not found"):
        load(tmp_path / "nope.ipynb")
    with pytest.raises(NotebookError, match="is a directory"):
        load(tmp_path)
    binary = tmp_path / "b.ipynb"
    binary.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(NotebookError, match="cannot read"):
        load(binary)


def test_load_from_stream_has_no_path():
    nb = load(io.StringIO(json.dumps({"cells": []})))
    assert nb.path is None


@pytest.mark.parametrize("data", [[], "x", 3, None])
def test_top_level_must_be_object(data):
    with pytest.raises(NotebookError, match="not a notebook"):
        parse(data)


@pytest.mark.parametrize("cells", [None, {}, "x"])
def test_cells_must_be_a_list(cells):
    with pytest.raises(NotebookError, match="missing cells"):
        parse({"cells": cells})


def test_source_may_be_list_or_string_or_missing():
    nb = nb_with(
        {"cell_type": "markdown", "source": ["a\n", "b"]},
        {"cell_type": "markdown", "source": "c"},
        {"cell_type": "markdown"},
    )
    assert [c.source for c in nb.cells] == ["a\nb", "c", ""]


def test_odd_cells_degrade_gracefully():
    nb = nb_with(
        "junk",
        {"cell_type": "weird", "source": "x"},
        code("1", outputs="nope", execution_count="3"),
        code("2", outputs=[None, 5, {"output_type": "mystery"}]),
    )
    assert [c.kind for c in nb.cells] == ["raw", "raw", "code", "code"]
    assert nb.cells[2].outputs == ()
    assert nb.cells[2].execution_count is None
    assert nb.cells[3].outputs == ()


def test_only_code_cells_have_outputs():
    cell = {
        "cell_type": "markdown",
        "source": "x",
        "outputs": [{"output_type": "stream"}],
    }
    assert nb_with(cell).cells[0].outputs == ()


def test_traceback_join_rules():
    def tb(pieces):
        out = {"output_type": "error", "ename": "E", "evalue": "v", "traceback": pieces}
        return nb_with(code("", [out])).cells[0].output_text

    assert tb(["a", "b"]) == "a\nb"
    assert tb(["a\n", "b\n"]) == "a\nb"
    assert tb([]) == "E: v"


def test_error_without_traceback_uses_name_and_value():
    out = {"output_type": "error", "ename": "KeyError", "evalue": "'k'"}
    cell = nb_with(code("", [out])).cells[0]
    assert cell.output_text == "KeyError: 'k'"
    assert cell.has_error


def test_stream_and_display_variants():
    outs = [
        {"output_type": "stream", "name": "stdout", "text": ["a", "b\n"]},
        {"output_type": "display_data", "data": {"text/plain": ["x", "y"]}},
        {"output_type": "display_data", "data": {"application/json": {}}},
        {"output_type": "execute_result", "data": None},
    ]
    cell = nb_with(code("", outs)).cells[0]
    assert [o.kind for o in cell.outputs] == ["stream", "display", "display", "result"]
    assert cell.output_text == "ab\nxy"
    assert cell.outputs[2].placeholder == "[application/json]"
    assert cell.outputs[3].placeholder is None


def test_png_size_is_robust_to_garbage():
    def size(data, meta=None):
        out = {"output_type": "display_data", "data": {"image/png": data}}
        if meta is not None:
            out["metadata"] = meta
        return nb_with(code("", [out])).cells[0].outputs[0].size

    assert size("!!!not base64") is None
    assert size("aGVsbG8=") is None
    assert size("") is None
    assert size("aGVsbG8=", {"image/png": {"width": 9, "height": 4}}) == (9, 4)
    assert size("aGVsbG8=", {"image/png": {"width": "9"}}) is None
    assert size("aGVsbG8=", "bad") is None


def test_language_detection():
    assert nb_with().language == "python"
    assert nb_with(metadata="bad").language == "python"
    assert nb_with(metadata={"language_info": {"name": "Julia"}}).language == "julia"
    assert nb_with(metadata={"kernelspec": {"language": "R"}}).language == "r"
    assert nb_with(metadata={"kernelspec": {"name": "ir"}}).language == "python"


def test_cell_text_lines_and_regions():
    cell = nb_with(
        code("a\nb", [{"output_type": "stream", "name": "stdout", "text": "o"}])
    ).cells[0]
    assert cell.text(Region.INPUT) == "a\nb"
    assert cell.text(Region.OUTPUT) == "o"
    assert cell.lines(Region.INPUT) == ["a", "b"]
    assert nb_with(code("")).cells[0].lines(Region.OUTPUT) == []


def test_headings_ignore_fenced_code_and_non_headings():
    src = "# One\n```\n# not\n```\n#nope\n####### seven\n### Three ###\n#\n"
    cell = nb_with({"cell_type": "markdown", "source": src}).cells[0]
    assert cell.headings == ((1, "One"), (3, "Three"))
    assert nb_with(code("# comment")).cells[0].headings == ()
