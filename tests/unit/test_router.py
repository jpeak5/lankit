"""RouterConnection output parsing, pinned to real RouterOS output.

Fixture strings below were captured from a CHR running RouterOS 7.24.2
(the production version) on 2026-11-14.
"""

from lankit.core.router import RouterConnection, parse_exact_file_size


# ── parse_exact_file_size ────────────────────────────────────────────────────

def test_exact_size_parses_bare_integer_with_crlf():
    assert parse_exact_file_size("79442\r\n", "") == 79442


def test_missing_file_error_on_stdout_is_not_a_size():
    # 7.24.2 prints this on stdout with stderr empty.
    assert parse_exact_file_size("no such item (/file/get; line 1)\n", "") is None


def test_any_stderr_is_a_failure():
    assert parse_exact_file_size("79442\r\n", "something went wrong") is None


def test_empty_output_is_not_a_size():
    assert parse_exact_file_size("", "") is None


def test_humanised_size_is_not_a_size():
    assert parse_exact_file_size("77.6KiB\r\n", "") is None


# ── verify_uploaded_file ─────────────────────────────────────────────────────

class _Stub(RouterConnection):
    """RouterConnection with run_tolerant replaced; never opens a socket."""

    def __init__(self, reply):
        super().__init__("192.0.2.1", "admin", "~/.ssh/none")
        self.reply = reply
        self.commands = []

    def run_tolerant(self, command, timeout=30):
        self.commands.append(command)
        return self.reply


def test_verify_accepts_exact_byte_count():
    conn = _Stub(("79442\r\n", ""))
    assert conn.verify_uploaded_file("lankit-revert.rsc", 79442) is True


def test_verify_queries_exact_size_not_print():
    conn = _Stub(("79442\r\n", ""))
    conn.verify_uploaded_file("lankit-revert.rsc", 79442)
    assert conn.commands == [':put [/file get [find name="lankit-revert.rsc"] size]']


def test_verify_rejects_truncated_upload():
    conn = _Stub(("40960\r\n", ""))
    assert conn.verify_uploaded_file("lankit-revert.rsc", 79442) is False


def test_verify_rejects_missing_file():
    conn = _Stub(("no such item (/file/get; line 1)\n", ""))
    assert conn.verify_uploaded_file("lankit-revert.rsc", 79442) is False


def test_regression_7_24_print_output_never_verified():
    """The old implementation parsed `/file print detail`, which on 7.24.2
    humanises the size. Its regex read 77 here and rejected a perfect
    79442-byte upload — the 2026-11-14 production abort. The new query must
    not depend on this format at all."""
    print_output = ("0  name=lankit-revert.rsc type=script size=77.6KiB "
                    "last-modified=2026-11-15 04:53:14\r\n")
    conn = _Stub((print_output, ""))
    assert conn.verify_uploaded_file("lankit-revert.rsc", 79442) is False


# ── compute_fire_time (#61) ──────────────────────────────────────────────────

import pytest
from lankit.core.router import RouterError, compute_fire_time

CLOCK_7_24 = ("                  time: 04:53:14  \r\n"
              "                  date: 2026-11-15\r\n"
              "  time-zone-autodetect: yes       \r\n")
CLOCK_LEGACY = "    time: 04:53:14\r\n    date: nov/15/2026\r\n"


def test_fire_time_iso_clock_emits_iso_date():
    assert compute_fire_time(CLOCK_7_24, 120) == ("2026-11-15", "04:55:14")


def test_fire_time_legacy_clock_emits_legacy_date():
    assert compute_fire_time(CLOCK_LEGACY, 120) == ("nov/15/2026", "04:55:14")


def test_fire_time_wraps_midnight_and_year():
    clock = "time: 23:59:30\ndate: 2026-12-31\n"
    assert compute_fire_time(clock, 120) == ("2027-01-01", "00:01:30")


def test_fire_time_unparseable_date_raises_instead_of_year_2000():
    with pytest.raises(RouterError):
        compute_fire_time("time: 04:53:14\ndate: ???\n", 120)


def test_fire_time_missing_time_raises():
    with pytest.raises(RouterError):
        compute_fire_time("date: 2026-11-15\n", 120)


# ── reschedule_scheduler read-back (#65) ─────────────────────────────────────

class _Sched(RouterConnection):
    """Scripted replies keyed by command prefix; records what was sent."""

    def __init__(self, replies, clock=CLOCK_7_24):
        super().__init__("192.0.2.1", "admin", "~/.ssh/none")
        self.replies, self.clock, self.sent = replies, clock, []

    def run(self, command, timeout=30):
        self.sent.append(command)
        return self.clock

    def run_tolerant(self, command, timeout=30):
        self.sent.append(command)
        for prefix, reply in self.replies.items():
            if command.startswith(prefix):
                return reply
        return ("", "")


# 7.x `print` (no detail) is a column table: no `start-time=` text in it.
PRINT_TABLE_7_24 = ("Columns: NAME, START-DATE, START-TIME, INTERVAL\r\n"
                    "# NAME             START-DATE  START-TIME  INTERVAL\r\n"
                    "0 lankit-failsafe  2026-11-15  04:55:14    1d\r\n")


def test_reschedule_confirms_with_exact_get_values():
    conn = _Sched({
        "/system scheduler print": (PRINT_TABLE_7_24, ""),
        ':put [/system scheduler get [find name="lankit-failsafe"] start-time]': ("04:55:14\r\n", ""),
        ':put [/system scheduler get [find name="lankit-failsafe"] start-date]': ("2026-11-15\r\n", ""),
    })
    assert conn.reschedule_scheduler("lankit-failsafe", 120) is True


def test_reschedule_rejects_when_get_shows_old_time():
    conn = _Sched({
        "/system scheduler print": (PRINT_TABLE_7_24, ""),
        ':put [/system scheduler get [find name="lankit-failsafe"] start-time]': ("04:50:00\r\n", ""),
        ':put [/system scheduler get [find name="lankit-failsafe"] start-date]': ("2026-11-15\r\n", ""),
    })
    assert conn.reschedule_scheduler("lankit-failsafe", 120) is False


def test_reschedule_absent_scheduler_is_false():
    conn = _Sched({"/system scheduler print": ("Columns: NAME\r\n", "")})
    assert conn.reschedule_scheduler("lankit-failsafe", 120) is False


# ── routeros_stdout_error / run() / add_failsafe_scheduler ───────────────────

from lankit.core.router import routeros_stdout_error


@pytest.mark.parametrize("out", [
    "no such item (/system/scheduler/get; line 1)\r\n",
    "item referred by 'place-before' does not exist (11) (/ip/firewall/filter/add; line 1)\r\n",
    "Script Error: bad parameter hw-offload (line 149 column 19) (:import; line 1)\r\n",
])
def test_stdout_errors_captured_on_7_24_are_recognised(out):
    assert routeros_stdout_error(out) == out.strip()


@pytest.mark.parametrize("out", [
    "name: CHR\r\n",
    "05:16:00\r\n",
    "Script file loaded and executed successfully\r\n",
    '/ip firewall filter\nadd chain=input comment="line 1 (not an error)"\n',
    "",
])
def test_normal_output_is_not_an_error(out):
    assert routeros_stdout_error(out) is None


class _Armer(_Sched):
    """add_failsafe_scheduler with a scripted read-back."""

    def __init__(self, fields):
        super().__init__({})
        self.fields = fields

    def run(self, command, timeout=30):
        self.sent.append(command)
        return self.clock if command == "/system clock print" else ""

    def scheduler_field(self, name, field):
        return self.fields.get(field)


def test_add_failsafe_confirms_installed_time():
    conn = _Armer({"start-date": "2026-11-15", "start-time": "04:55:14"})
    conn.add_failsafe_scheduler("lankit-failsafe", "{:log info x}", 120)


def test_add_failsafe_raises_when_not_confirmed():
    conn = _Armer({})   # scheduler absent after add: e.g. the add failed on stdout
    with pytest.raises(RouterError, match="not confirmed"):
        conn.add_failsafe_scheduler("lankit-failsafe", "{:log info x}", 120)
