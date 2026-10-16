import json
from pathlib import Path

import pytest

from nbsee.export import export, summarize, summarize_json
from nbsee.model import load, parse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return load(FIXTURES / f"{name}.ipynb")


def nb_of(*cells, **top):
    return parse({"cells": list(cells), **top})


def code(source, *outputs, count=None):
    return {
        "cell_type": "code",
        "source": source,
        "outputs": list(outputs),
        "execution_count": count,
    }


def stream(text, name="stdout"):
    return {"output_type": "stream", "name": name, "text": text}


BASIC_MD = """\
<!-- cell 1 · markdown -->
# Title

Intro text.

## Setup

<!-- cell 2 · code · In[1] -->
```python
import math
print(math.pi)
```

```text
3.141592653589793
```

<!-- cell 3 · code · In[2] -->
```python
1 + 1
```

```text
2
```

<!-- cell 4 · raw -->
```text
raw note
```

<!-- cell 5 · code · In[3] -->
```python
x = 1
```
"""


def rule(label):
    return f"── {label} ".ljust(72, "─")


BASIC_TEXT = f"""\
{rule("[1] markdown")}
# Title

Intro text.

## Setup

{rule("[2] code In[1]")}
import math
print(math.pi)
┄ output
3.141592653589793

{rule("[3] code In[2]")}
1 + 1
┄ output
2

{rule("[4] raw")}
raw note

{rule("[5] code In[3]")}
x = 1
"""

BASIC_PY = """\
# %% [markdown]
# # Title
#
# Intro text.
#
# ## Setup

# %%
import math
print(math.pi)

# %%
1 + 1

# %% [raw]
# raw note

# %%
x = 1
"""


def test_md_golden():
    assert export(fixture("basic")) == BASIC_MD


def test_text_golden():
    out = export(fixture("basic"), fmt="text")
    assert out == BASIC_TEXT
    assert all(len(h) == 72 for h in out.splitlines() if h.startswith("──"))


def test_py_golden():
    assert export(fixture("basic"), fmt="py") == BASIC_PY


def test_json_golden_shape():
    data = json.loads(export(fixture("basic"), [1, 3], fmt="json"))
    assert data == [
        {
            "index": 2,
            "kind": "code",
            "source": "import math\nprint(math.pi)",
            "execution_count": 1,
            "outputs": [{"kind": "stream", "text": "3.141592653589793\n", "mimes": []}],
        },
        {
            "index": 4,
            "kind": "raw",
            "source": "raw note",
            "execution_count": None,
            "outputs": [],
        },
    ]


def test_json_is_pretty_unicode_and_plain():
    nb = nb_of(code("é", stream("\x1b[31mred\x1b[0m")))
    out = export(nb, fmt="json")
    assert "é" in out and "\\u" not in out
    assert "\x1b" not in out and out.endswith("]\n")
    assert out.startswith('[\n  {\n    "index": 1')


def test_json_only_variants():
    nb = fixture("basic")
    only_in = json.loads(export(nb, [1], fmt="json", only="input"))
    assert "outputs" not in only_in[0] and "source" in only_in[0]
    only_out = json.loads(export(nb, [1, 4], fmt="json", only="output"))
    assert [c["index"] for c in only_out] == [2]
    assert "source" not in only_out[0]


def test_cell_selection_keeps_original_numbers():
    out = export(fixture("basic"), [2, 4])
    assert out.startswith("<!-- cell 3 · code · In[2] -->")
    assert "cell 5" in out and "cell 1" not in out


def test_only_input_and_no_outputs_are_the_same():
    nb = fixture("basic")
    assert export(nb, only="input") == export(nb, outputs=False)
    assert "3.14159" not in export(nb, only="input")
    assert "┄ output" not in export(nb, fmt="text", outputs=False)


def test_only_output_drops_sources_and_cells_without_output():
    out = export(fixture("basic"), only="output")
    assert out == (
        "<!-- cell 2 · code · In[1] -->\n```text\n3.141592653589793\n```\n\n"
        "<!-- cell 3 · code · In[2] -->\n```text\n2\n```\n"
    )
    text = export(fixture("basic"), fmt="text", only="output")
    assert "import math" not in text and "┄ output" not in text
    assert "[1] markdown" not in text


def test_only_output_py_emits_commented_outputs():
    out = export(fixture("basic"), fmt="py", only="output")
    assert out == "# %%\n# 3.141592653589793\n\n# %%\n# 2\n"


def test_only_output_contradiction_is_rejected():
    with pytest.raises(ValueError):
        export(fixture("basic"), only="output", outputs=False)


def test_empty_selection_and_empty_notebook():
    empty = fixture("empty")
    assert export(empty) == ""
    assert export(empty, fmt="py") == ""
    assert export(empty, fmt="json") == "[]\n"
    assert export(fixture("basic"), [], fmt="text") == ""
    assert export(fixture("basic"), [0], only="output") == ""


def test_md_fences_outgrow_backticks_in_sources_and_outputs():
    out = export(fixture("backticks"))
    assert out == (
        "<!-- cell 1 · markdown -->\n"
        "Use fences:\n\n```python\nprint(1)\n```\n\n"
        "<!-- cell 2 · code · In[1] -->\n"
        "`````python\n"
        's = """\n```\nnested\n```\n"""\nt = "````"\n'
        "`````\n\n"
        "````text\n```\nout\n```\n````\n"
    )


def test_md_uses_notebook_language():
    nb = nb_of(code("1"), metadata={"language_info": {"name": "julia"}})
    assert "```julia\n1\n```" in export(nb)


def test_md_placeholders_keep_order_and_merge_text():
    out = export(fixture("rich_outputs"), only="output")
    assert out == (
        "<!-- cell 1 · code · In[1] -->\n```text\n<Figure size 3x2>\n```\n\n"
        "<!-- cell 2 · code · In[2] -->\n*[image/png 3×2]*\n\n"
        "<!-- cell 3 · code · In[3] -->\n```text\n   a\n0  1\n```\n\n"
        "<!-- cell 4 · code · In[4] -->\n*[text/html]*\n"
    )
    mixed = nb_of(
        code(
            "x",
            stream("a\n"),
            stream("b\n", "stderr"),
            {"output_type": "display_data", "data": {"image/png": "x"}},
            stream("c"),
        )
    )
    assert export(mixed, only="output").splitlines()[1:] == [
        "```text",
        "a",
        "b",
        "```",
        "",
        "*[image/png]*",
        "",
        "```text",
        "c",
        "```",
    ]


def test_md_output_without_trailing_blank_lines_and_whitespace_only_skipped():
    nb = nb_of(code("x", stream("a\n\n\n"), stream("  \n")))
    assert export(nb, only="output") == "<!-- cell 1 · code -->\n```text\na\n```\n"


def test_md_empty_code_cell_has_only_marker():
    assert export(nb_of(code(""))) == "<!-- cell 1 · code -->\n"


def test_errors_are_plain_in_md():
    out = export(fixture("errors"), [0])
    assert "\x1b" not in out
    assert "```text\n" in out and "ValueError" in out


@pytest.mark.parametrize("fmt", ["md", "text", "py", "json"])
@pytest.mark.parametrize("name", ["basic", "errors", "rich_outputs", "backticks"])
def test_uncolored_output_has_no_escapes(fmt, name):
    assert "\x1b" not in export(fixture(name), fmt=fmt, color=False)
    assert "\x1b" not in export(fixture(name), fmt=fmt, only="output")


def test_color_only_affects_text_format():
    nb = fixture("errors")
    assert "\x1b" not in export(nb, fmt="md", color=True)
    assert "\x1b" not in export(nb, fmt="py", color=True)


def test_text_color_keeps_sgr_drops_background_and_resets():
    out = export(fixture("errors"), [0], fmt="text", color=True)
    assert "\x1b[0;31mValueError" in out
    assert "\x1b[43m" not in out and "48;5" not in out
    assert "\x1b[38;5;28m" in out  # foreground survives
    assert out.endswith("\x1b[0m\n")


def test_text_color_highlights_code_and_dims_header():
    out = export(fixture("basic"), [1], fmt="text", color=True)
    assert out.startswith("\x1b[2m── [2] code In[1] ")
    assert "\x1b[94mimport\x1b[0m" in out
    assert "math" in out


def test_text_color_strips_control_sequences_from_outputs():
    nb = nb_of(code("x", stream("a\x1b[2Jb\x1b]0;t\x07\x1b[32mg")))
    out = export(nb, fmt="text", color=True, only="output")
    assert "\x1b[2J" not in out and "\x1b]" not in out
    assert "ab\x1b[32mg\x1b[0m" in out


def test_text_color_unknown_language_still_works():
    nb = nb_of(code("foo bar"), metadata={"language_info": {"name": "nosuchlang"}})
    assert "foo bar" in export(nb, fmt="text", color=True)


def test_py_cell_with_empty_markdown_lines():
    nb = nb_of({"cell_type": "markdown", "source": "a\n\nb"})
    assert export(nb, fmt="py") == "# %% [markdown]\n# a\n#\n# b\n"


def test_summarize_golden():
    assert summarize(fixture("basic")) == [
        "   1  markdown    5L  -                Title",
        "   2  code        2L  stdout           import math",
        "   3  code        1L  result           1 + 1",
        "   4  raw         1L  -                raw note",
        "   5  code        1L  -                x = 1",
    ]


def test_summarize_output_labels():
    rows = summarize(fixture("errors")) + summarize(fixture("rich_outputs"))
    assert [r.split()[3] for r in rows] == [
        "err:ValueError",
        "stderr",
        "stderr",
        "image/png",
        "image/png",
        "result",
        "result",
    ]


def test_summarize_dedupes_and_joins_labels():
    nb = nb_of(code("x", stream("a"), stream("b"), stream("c", "stderr")))
    assert summarize(nb)[0].split()[3] == "stdout,stderr"


def test_summarize_truncates_to_width():
    nb = nb_of(code("x" * 200))
    (row,) = summarize(nb, width=40)
    assert len(row) == 40 and row.endswith("…")
    assert len(summarize(nb, width=500)[0]) > 200
    assert summarize(fixture("empty")) == []


def test_summarize_summary_rules():
    nb = nb_of(
        {"cell_type": "markdown", "source": "intro\n## Real heading"},
        {"cell_type": "markdown", "source": "\n\n  just text  "},
        code("\n\n  y = 2\nz"),
        code(""),
    )
    assert [row[39:] for row in summarize(nb)] == [
        "Real heading",
        "just text",
        "y = 2",
        "",
    ]


def test_summarize_json():
    data = json.loads(summarize_json(fixture("errors")))
    assert data[0] == {
        "index": 1,
        "kind": "code",
        "lines": 1,
        "execution_count": 4,
        "outputs": ["err:ValueError"],
        "summary": "int('x')",
    }
    assert summarize_json(fixture("empty")) == "[]\n"
