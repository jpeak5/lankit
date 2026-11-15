"""_migration_bootstrap: every path to a remove-only failsafe needs a human.

A remove-only failsafe reverts to a bare/flat network. On 2026-11-14 a piped
`y` accepted that prompt against a production router; these tests pin that
it can no longer be answered from a pipe, and that declining to record a
known-good state no longer arms one silently.
"""

import io
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.prompt import Confirm

from lankit.cli.commands import apply as apply_mod
from lankit.core import revert


class _Conn:
    def __init__(self, tags):
        self.tags = tags

    def read_lankit_tags(self):
        return self.tags


class _Stdin:
    def __init__(self, tty):
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=200)


@pytest.fixture
def verdict(monkeypatch):
    """Control what the live-vs-network.yml comparison says."""
    state = {}

    def _set(ok):
        state["v"] = SimpleNamespace(ok=ok, missing={"lankit:a"} if not ok else set(),
                                     unexpected={"lankit:b"} if not ok else set())

    monkeypatch.setattr(revert, "expected_tags_for_config", lambda cfg, pw: {"lankit:a"})
    monkeypatch.setattr(revert, "verify_revert", lambda live, expected: state["v"])
    return _set


@pytest.fixture
def answers(monkeypatch):
    """Record every Confirm prompt and answer from a queue."""
    asked, queue = [], []

    def _ask(prompt, default=None, **kw):
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(Confirm, "ask", staticmethod(_ask))
    return SimpleNamespace(asked=asked, queue=queue)


def _run(cfg, console, conn=None):
    conn = conn or _Conn({"lankit:a", "lankit:b"})
    return apply_mod._migration_bootstrap(conn, cfg, {}, None, "hash", console)


def _out(console):
    return console.file.getvalue()


def test_mismatch_with_piped_stdin_refuses_without_prompting(cfg, console, verdict, answers, monkeypatch):
    verdict(ok=False)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    answers.queue.append(True)   # what a piped `y` would have supplied
    with pytest.raises(SystemExit) as e:
        _run(cfg, console)
    assert e.value.code == 1
    assert answers.asked == []
    assert "stdin is not a terminal" in _out(console)


def test_mismatch_at_terminal_yes_proceeds_remove_only(cfg, console, verdict, answers, monkeypatch):
    verdict(ok=False)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))
    answers.queue.append(True)
    assert _run(cfg, console) is None
    assert answers.asked == ["Continue with a remove-only failsafe for this one apply?"]


def test_mismatch_at_terminal_no_aborts(cfg, console, verdict, answers, monkeypatch):
    verdict(ok=False)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))
    answers.queue.append(False)
    with pytest.raises(SystemExit):
        _run(cfg, console)


def test_declining_to_record_known_good_is_gated_too(cfg, console, verdict, answers, monkeypatch):
    """Used to return None silently, arming a remove-only failsafe unannounced."""
    verdict(ok=True)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))
    answers.queue.extend([False, False])   # don't record; don't continue
    with pytest.raises(SystemExit):
        _run(cfg, console)
    assert answers.asked[1] == "Continue with a remove-only failsafe for this one apply?"
    assert "flat network" in _out(console)


def test_declining_to_record_with_piped_stdin_refuses(cfg, console, verdict, answers, monkeypatch):
    verdict(ok=True)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    answers.queue.append(False)            # the record prompt, answered from a pipe
    with pytest.raises(SystemExit):
        _run(cfg, console)
    assert len(answers.asked) == 1


def test_recording_known_good_needs_no_tty(cfg, console, verdict, answers, monkeypatch):
    """The safe direction stays scriptable: recording is not gated."""
    verdict(ok=True)
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    answers.queue.append(True)
    monkeypatch.setattr(revert, "save_known_good", lambda *a, **k: "aid-1")
    monkeypatch.setattr(revert, "load_known_good", lambda ip, aid: f"archive:{aid}")
    monkeypatch.setattr("lankit.core.generator.render_texts", lambda *a, **k: {})
    assert _run(cfg, console) == "archive:aid-1"


def test_genuine_first_apply_is_untouched(cfg, console, answers, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    assert _run(cfg, console, conn=_Conn(set())) is None
    assert answers.asked == []


def _run_segment(cfg, console):
    return apply_mod._migration_bootstrap(_Conn({"lankit:a"}), cfg, {}, "trusted", "hash", console)


def test_segment_apply_with_piped_stdin_refuses(cfg, console, answers, monkeypatch):
    """Used to warn and arm a remove-only failsafe with no confirmation."""
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    with pytest.raises(SystemExit):
        _run_segment(cfg, console)
    assert answers.asked == []


def test_segment_apply_at_terminal_yes_proceeds(cfg, console, answers, monkeypatch):
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))
    answers.queue.append(True)
    assert _run_segment(cfg, console) is None
    assert answers.asked == ["Continue with a remove-only failsafe for this one apply?"]
