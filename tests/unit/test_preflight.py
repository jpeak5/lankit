"""import_failure (#66) and preflight_router (#63).

Import outputs below were captured from RouterOS 7.24.2 over SSH exec, where
/import failures arrive on stdout with stderr empty.
"""

import io

import pytest
from rich.console import Console

from lankit.cli.commands._revert_shared import (
    import_failure,
    parse_routeros_version,
    preflight_router,
)

OK_7_24 = "Script file loaded and executed successfully\r\n"
FAIL_7_24 = ("Script Error: failure: already have such address "
             "(/ip/address/add; line 13) (:import; line 1)\r\n")
BAD_PARAM_7_24 = "Script Error: bad parameter hw-offload (line 149 column 19) (:import; line 1)\r\n"


# ── import_failure ───────────────────────────────────────────────────────────

def test_clean_import_is_not_a_failure():
    assert import_failure(OK_7_24, "") is None


@pytest.mark.parametrize("out", [FAIL_7_24, BAD_PARAM_7_24])
def test_script_error_on_stdout_is_a_failure(out):
    assert import_failure(out, "") == out.strip()


def test_script_error_wins_even_beside_informational_text():
    out = "loading file... warning: something\r\n" + FAIL_7_24
    assert import_failure(out, "") == FAIL_7_24.strip()


def test_stderr_error_still_detected():
    assert import_failure("", "failure: duplicate entry") == "failure: duplicate entry"


def test_stderr_informational_is_not_a_failure():
    assert import_failure("", "warning: script file loaded") is None


# ── parse_routeros_version ───────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("7.24.2 (stable)", (7, 24)),
    ("7.21", (7, 21)),
    ("6.49.15 (stable)", (6, 49)),
    ("unknown", None),
    ("", None),
])
def test_parse_routeros_version(text, want):
    assert parse_routeros_version(text) == want


# ── preflight_router ─────────────────────────────────────────────────────────

class _Conn:
    def __init__(self, version="7.24.2 (stable)", tags=()):
        self._version, self._tags = version, set(tags)
        self.tag_reads = 0

    def version(self):
        return self._version

    def read_lankit_tags(self):
        self.tag_reads += 1
        return self._tags


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=200)


def test_supported_version_passes(console):
    preflight_router(_Conn(), console)


def test_minimum_version_passes(console):
    preflight_router(_Conn(version="7.21"), console)


@pytest.mark.parametrize("version", ["7.20.8 (long-term)", "6.49.15 (stable)", "unknown"])
def test_old_or_unreadable_version_refused(console, version):
    conn = _Conn(version=version)
    with pytest.raises(SystemExit):
        preflight_router(conn, console)
    assert "not supported" in console.file.getvalue()


def test_version_passed_in_is_not_re_read(console):
    class NoVersion(_Conn):
        def version(self):
            raise AssertionError("should use the version apply already read")
    preflight_router(NoVersion(), console, version="7.24.2 (stable)")
