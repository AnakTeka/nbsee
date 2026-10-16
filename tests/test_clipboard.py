from __future__ import annotations

import subprocess

import pytest

from nbsee import clipboard
from nbsee.clipboard import copy, plan

ALL = {"pbcopy", "wl-copy", "xclip", "xsel"}


def which_of(*names: str):
    return lambda n: f"/bin/{n}" if n in names else None


NONE = which_of()
EVERYTHING = which_of(*ALL)


class Recorder:
    def __init__(self, rc: int | Exception = 0) -> None:
        self.calls: list[tuple[list[str], bytes]] = []
        self.rc = rc

    def __call__(self, argv: list[str], data: bytes) -> int:
        self.calls.append((argv, data))
        if isinstance(self.rc, Exception):
            raise self.rc
        return self.rc


# plan ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "platform", "tools", "mode", "expected"),
    [
        ({}, "linux", ALL, None, ("osc52",)),
        ({"WAYLAND_DISPLAY": "w"}, "linux", ALL, None, ("osc52", "wl-copy")),
        ({"DISPLAY": ":0"}, "linux", ALL, None, ("osc52", "xclip")),
        ({"DISPLAY": ":0"}, "linux", {"xsel"}, None, ("osc52", "xsel")),
        ({"DISPLAY": ":0"}, "linux", set(), None, ("osc52",)),
        ({}, "darwin", ALL, None, ("osc52", "pbcopy")),
        ({}, "darwin", set(), None, ("osc52",)),
        # wayland without wl-copy falls back to XWayland tools
        (
            {"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"},
            "linux",
            {"xclip"},
            None,
            ("osc52", "xclip"),
        ),
        # SSH never runs native tools in auto
        (
            {"SSH_CONNECTION": "1", "DISPLAY": ":0"},
            "linux",
            ALL,
            None,
            ("osc52",),
        ),
        ({"SSH_TTY": "/dev/pts/1", "DISPLAY": ":0"}, "linux", ALL, None, ("osc52",)),
        # modes
        ({"DISPLAY": ":0"}, "linux", ALL, "osc52", ("osc52",)),
        ({"DISPLAY": ":0"}, "linux", ALL, "native", ("xclip",)),
        ({"SSH_TTY": "x", "DISPLAY": ":0"}, "linux", ALL, "native", ("xclip",)),
        ({}, "linux", set(), "native", ()),
        ({"DISPLAY": ":0"}, "linux", ALL, "bogus", ("osc52", "xclip")),
        ({"NBSEE_CLIPBOARD": "osc52", "DISPLAY": ":0"}, "linux", ALL, None, ("osc52",)),
        (
            {"NBSEE_CLIPBOARD": "native", "DISPLAY": ":0"},
            "linux",
            ALL,
            None,
            ("xclip",),
        ),
        (
            {"NBSEE_CLIPBOARD": "nope", "DISPLAY": ":0"},
            "linux",
            ALL,
            None,
            ("osc52", "xclip"),
        ),
        # explicit mode beats env
        ({"NBSEE_CLIPBOARD": "native"}, "linux", ALL, "osc52", ("osc52",)),
    ],
)
def test_plan(env, platform, tools, mode, expected):
    assert plan(env, platform, which_of(*tools), mode) == expected


# copy ----------------------------------------------------------------------


def do_copy(text="a\nb\n", *, env=None, platform="linux", tools=(), osc=True, run=None):
    sent: list[str] = []
    result = copy(
        text,
        osc52=sent.append if osc else None,
        env=env or {},
        platform=platform,
        which=which_of(*tools),
        run=run or Recorder(),
    )
    return result, sent


def test_empty_text_invokes_nothing():
    run = Recorder()
    sent: list[str] = []
    r = copy("", osc52=sent.append, env={"DISPLAY": ":0"}, platform="linux",
             which=EVERYTHING, run=run)  # fmt: skip
    assert (r.ok, r.via, r.message) == (False, (), "Nothing to copy")
    assert sent == [] and run.calls == []


def test_osc52_only_local():
    r, sent = do_copy()
    assert r.ok and r.via == ("osc52",)
    assert r.message == "Copied 2 lines · osc52"
    assert sent == ["a\nb\n"]


def test_osc52_plus_native_runs_fixed_argv_utf8():
    run = Recorder()
    r, sent = do_copy("héllo", env={"DISPLAY": ":0"}, tools=["xclip"], run=run)
    assert r.via == ("osc52", "xclip")
    assert r.message == "Copied 1 line · osc52+xclip"
    assert run.calls == [(["xclip", "-selection", "clipboard"], "héllo".encode())]


@pytest.mark.parametrize(
    ("env", "platform", "tool", "argv"),
    [
        ({}, "darwin", "pbcopy", ["pbcopy"]),
        ({"WAYLAND_DISPLAY": "w"}, "linux", "wl-copy", ["wl-copy"]),
        ({"DISPLAY": ":0"}, "linux", "xsel", ["xsel", "--clipboard", "--input"]),
    ],
)
def test_native_argvs(env, platform, tool, argv):
    run = Recorder()
    r, _ = do_copy(env=env, platform=platform, tools=[tool], run=run)
    assert r.via == ("osc52", tool)
    assert [c[0] for c in run.calls] == [argv]


def test_ssh_does_not_run_native():
    run = Recorder()
    r, _ = do_copy(env={"SSH_TTY": "x", "DISPLAY": ":0"}, tools=ALL, run=run)
    assert r.via == ("osc52",) and run.calls == []


def test_native_mode_without_osc52():
    run = Recorder()
    r, sent = do_copy(env={"NBSEE_CLIPBOARD": "native", "DISPLAY": ":0"},
                      tools=["xclip"], run=run)  # fmt: skip
    assert r.via == ("xclip",) and sent == []


def test_osc52_none_skipped():
    run = Recorder()
    r, _ = do_copy(env={"DISPLAY": ":0"}, tools=["xclip"], osc=False, run=run)
    assert r.via == ("xclip",)


def test_runner_nonzero_keeps_osc52_success():
    r, _ = do_copy(env={"DISPLAY": ":0"}, tools=["xclip"], run=Recorder(1))
    assert r.ok and r.via == ("osc52",)


def test_osc52_raises_native_still_used():
    def boom(_: str) -> None:
        raise RuntimeError("no tty")

    r = copy("x", osc52=boom, env={"DISPLAY": ":0"}, platform="linux",
             which=which_of("xclip"), run=Recorder())  # fmt: skip
    assert r.ok and r.via == ("xclip",)


def test_all_fail_x11_hint():
    r, _ = do_copy(env={"NBSEE_CLIPBOARD": "native", "DISPLAY": ":0"},
                   tools=["xclip"], run=Recorder(1))  # fmt: skip
    assert not r.ok and r.via == ()
    assert r.message == "Copy failed (xclip): install xclip"


def test_nothing_available_wayland_hint():
    r, _ = do_copy(env={"NBSEE_CLIPBOARD": "native", "WAYLAND_DISPLAY": "w"})
    assert not r.ok
    assert r.message == "Copy failed (no backend available): install wl-clipboard"


def test_nothing_available_no_hint():
    r, _ = do_copy(env={"NBSEE_CLIPBOARD": "native"})
    assert r.message == "Copy failed (no backend available)"


def test_osc52_none_and_no_native_fails():
    r, _ = do_copy(osc=False)
    assert not r.ok and "no backend available" in r.message


@pytest.mark.parametrize("exc", [OSError("x"), subprocess.TimeoutExpired(["x"], 5)])
def test_default_runner_failures_count_as_failure(monkeypatch, exc):
    def fake(*a, **k):
        raise exc

    monkeypatch.setattr(clipboard.subprocess, "run", fake)
    assert clipboard._run(["xclip"], b"x") == -1


def test_default_runner_uses_safe_args(monkeypatch):
    seen = {}

    def fake(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake)
    assert clipboard._run(["wl-copy"], b"hi") == 0
    assert seen["argv"] == ["wl-copy"] and seen["input"] == b"hi"
    assert seen["timeout"] == 5 and "shell" not in seen


def test_tmux_hint_when_only_osc52_failed():
    def boom(_: str) -> None:
        raise RuntimeError

    r = copy("x", osc52=boom, env={"TMUX": "1"}, platform="linux",
             which=NONE, run=Recorder())  # fmt: skip
    assert r.message == "Copy failed (osc52): enable set-clipboard in tmux"


def test_no_tmux_note_on_osc52_success():
    r, _ = do_copy(env={"TMUX": "1"})
    assert r.ok and "set-clipboard" not in r.message


def test_large_payload_warning():
    r, _ = do_copy("x" * 100_001)
    assert r.message.endswith("(large: some terminals truncate OSC 52)")


def test_large_payload_no_warning_without_osc52():
    r, _ = do_copy("x" * 100_001, env={"NBSEE_CLIPBOARD": "native", "DISPLAY": ":0"},
                   tools=["xclip"])  # fmt: skip
    assert "large" not in r.message


def test_payload_at_limit_no_warning():
    r, _ = do_copy("x" * 100_000)
    assert "large" not in r.message


@pytest.mark.parametrize(
    ("text", "n"), [("a", 1), ("a\n", 1), ("a\nb", 2), ("\n\n", 2), ("a\r\nb\r\n", 2)]
)
def test_line_counting(text, n):
    r, _ = do_copy(text)
    assert r.message.startswith(f"Copied {n} line")


def test_defaults_use_real_env_without_spawning(monkeypatch):
    monkeypatch.setenv("NBSEE_CLIPBOARD", "osc52")
    sent: list[str] = []
    r = copy("x", osc52=sent.append)
    assert r.via == ("osc52",) and sent == ["x"]
