"""
lankit.core.router
~~~~~~~~~~~~~~~~~~

RouterOS SSH connection and command execution via paramiko.
Used by apply, commit, rollback, discover, and test-failsafe commands.
"""

from __future__ import annotations

import errno
import re
import socket
import time
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import paramiko

if TYPE_CHECKING:
    from lankit.core.revert import MasterWifiFacts


class RouterError(Exception):
    """Raised when a router operation fails."""
    pass


def parse_exact_file_size(out: str, err: str) -> Optional[int]:
    """Parse the output of `:put [/file get [find name=...] size]`.

    Only a bare integer counts. RouterOS reports a missing file as
    `no such item (/file/get; line 1)` on *stdout* with stderr empty
    (observed on 7.24.2), so anything other than digits is a failure —
    never a size. Pure; no I/O."""
    if err.strip():
        return None
    text = out.strip()
    return int(text) if text.isdigit() else None


_ROUTEROS_ERROR = re.compile(r"\(/[^\s;()]+; line \d+\)|^\s*Script Error:")


def routeros_stdout_error(out: str) -> Optional[str]:
    """The first RouterOS error line in stdout, or None.

    RouterOS 7.x reports runtime errors on stdout over SSH exec, ending in
    the menu path and line, e.g. `no such item (/system/scheduler/get; line 1)`
    or `item referred by 'place-before' does not exist (11)
    (/ip/firewall/filter/add; line 1)`, and `/import` halts start with
    `Script Error:`. Pure."""
    for line in (out or "").splitlines():
        if _ROUTEROS_ERROR.search(line):
            return line.strip()
    return None


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec")


def compute_fire_time(clock_output: str, seconds: int) -> tuple[str, str]:
    """(start-date, start-time) `seconds` after the time in `/system clock print`.

    Accepts both date formats RouterOS has printed: ISO `2026-11-15` (7.10+)
    and legacy `nov/15/2026`. The start-date is emitted in the same format the
    router printed. Raises RouterError when either the date or the time can't
    be parsed: the old fallback to year 2000 was the *only* path on 7.x (#61)
    and depended on undocumented scheduler behaviour for past start dates.
    Pure; no I/O."""
    import re
    from datetime import datetime, timedelta

    time_m = re.search(r'time:\s*(\d{1,2}):(\d{2}):(\d{2})', clock_output)
    iso_m = re.search(r'date:\s*(\d{4})-(\d{2})-(\d{2})', clock_output)
    old_m = re.search(r'date:\s*([a-z]{3})/(\d{1,2})/(\d{4})', clock_output, re.I)
    if not time_m or not (iso_m or old_m):
        raise RouterError(f"Could not parse router clock output: {clock_output!r}")
    hh, mm, ss = (int(g) for g in time_m.groups())
    if iso_m:
        y, mo, d = (int(g) for g in iso_m.groups())
    else:
        mon = old_m.group(1).lower()
        if mon not in _MONTHS:
            raise RouterError(f"Unrecognised month in router clock output: {clock_output!r}")
        y, mo, d = int(old_m.group(3)), _MONTHS.index(mon) + 1, int(old_m.group(2))

    fire = datetime(y, mo, d, hh, mm, ss) + timedelta(seconds=seconds)
    if iso_m:
        start_date = fire.strftime("%Y-%m-%d")
    else:
        start_date = f"{_MONTHS[fire.month - 1]}/{fire.day:02d}/{fire.year}"
    return start_date, fire.strftime("%H:%M:%S")


class RouterConnection:
    """Context manager for a persistent SSH connection to MikroTik RouterOS."""

    def __init__(self, ip: str, ssh_user: str, ssh_key: str, timeout: int = 15):
        self.ip = ip
        self.ssh_user = ssh_user
        self.ssh_key = str(Path(ssh_key).expanduser())
        self.timeout = timeout
        self._client: Optional[paramiko.SSHClient] = None

    def __enter__(self) -> "RouterConnection":
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self._client.connect(
                self.ip,
                username=self.ssh_user,
                key_filename=self.ssh_key,
                timeout=self.timeout,
                look_for_keys=False,
            )
        except Exception as e:
            raise self._classify_connect_error(e) from e
        return self

    def _classify_connect_error(self, e: Exception) -> RouterError:
        """Turn a raw connection exception into a RouterError with a diagnosis.

        Classifies into auth failure, no route, and refused/timeout; anything
        unrecognised falls back to the generic message.
        """
        text = str(e) or e.__class__.__name__
        low = text.lower()

        # Authentication: rejected key, bad/encrypted key file, passphrase
        if isinstance(e, paramiko.AuthenticationException) or (
            "private key" in low and "not a valid" in low
        ) or "passphrase" in low:
            return RouterError(
                f"Authentication failed for {self.ssh_user}@{self.ip} ({text})\n"
                "lankit uses key-based SSH auth. Check:\n"
                f"  • Key path (router.ssh_key in network.yml): {self.ssh_key}\n"
                f"  • Key permissions: chmod 600 {self.ssh_key}\n"
                f"  • Test by hand: ssh -i {self.ssh_key} {self.ssh_user}@{self.ip}"
            )

        # No route: laptop can't even reach the router's subnet
        if (
            isinstance(e, OSError)
            and getattr(e, "errno", None) in (errno.EHOSTUNREACH, errno.ENETUNREACH)
        ) or "no route to host" in low or "network is unreachable" in low:
            return RouterError(
                f"No route to {self.ip} ({text})\n"
                f"The router IP in network.yml ({self.ip}) is unreachable from here.\n"
                "  • Is this machine on the router's LAN (not guest WiFi or a VPN)?\n"
                "  • Has the router's IP address changed?"
            )

        # Refused / timed out: router off, wrong network, or SSH disabled
        if isinstance(e, (TimeoutError, ConnectionRefusedError, socket.timeout)) or (
            isinstance(e, paramiko.SSHException)
            and ("timed out" in low or "timeout" in low)
        ) or "connection refused" in low or "timed out" in low:
            return RouterError(
                f"Could not connect to {self.ip} ({text})\n"
                "Is the router powered on and reachable?\n"
                f"  • Try pinging it: ping {self.ip}\n"
                "  • Are you on the right network? (not a VPN, not guest WiFi)\n"
                "  • Is SSH enabled on the router? RouterOS: /ip service enable ssh"
            )

        return RouterError(f"Cannot connect to {self.ip} as {self.ssh_user}: {text}")

    def __exit__(self, *args) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def run(self, command: str, timeout: int = 30) -> str:
        """Run a command, return stdout. Raises RouterError on non-empty
        stderr, or on a RouterOS error on stdout (where 7.x reports them over
        SSH exec, with stderr empty and exit status 0)."""
        if not self._client:
            raise RouterError("Not connected")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace").strip()
        if err:
            raise RouterError(f"Command error: {err}")
        stdout_error = routeros_stdout_error(out)
        if stdout_error:
            raise RouterError(f"Command error: {stdout_error}")
        return out

    def run_tolerant(self, command: str, timeout: int = 30) -> tuple[str, str]:
        """Run a command, return (stdout, stderr) without raising."""
        if not self._client:
            raise RouterError("Not connected")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        return (
            stdout.read().decode(errors="replace"),
            stderr.read().decode(errors="replace"),
        )

    def upload(self, content: str, remote_path: str) -> None:
        """Upload a string as a file via SFTP."""
        if not self._client:
            raise RouterError("Not connected")
        sftp = self._client.open_sftp()
        try:
            with sftp.open(remote_path, "w") as f:
                f.write(content)
        finally:
            sftp.close()

    def _compute_fire_time(self, seconds: int) -> tuple[str, str]:
        """Read router clock and compute (start-date, start-time) `seconds` from now.

        Parses both date and time so midnight (and month/year) boundaries advance
        correctly. Raises RouterError if the clock cannot be parsed.
        """
        return compute_fire_time(self.run("/system clock print"), seconds)

    def add_failsafe_scheduler(self, name: str, revert_cmd: str, seconds: int) -> None:
        """Install a one-shot RouterOS scheduler job that reverts in `seconds`.

        interval=1d is belt-and-suspenders: the on-event script self-deletes
        before importing, so it is truly one-shot.

        Raises RouterError if the clock cannot be parsed or the scheduler
        cannot be installed. Caller should track whether this completed.

        Call cancel_failsafe_scheduler() to disarm on success, or
        reschedule_scheduler() to extend the safety window before a long
        interactive step.
        """
        start_date, start_time = self._compute_fire_time(seconds)
        self.run(
            f'/system scheduler add name="{name}" '
            f'start-date={start_date} start-time={start_time} interval=1d '
            f'on-event={{{revert_cmd}}} comment="lankit:failsafe"'
        )
        # Never report armed without proof: 7.x errors don't reach stderr,
        # so read the installed fire time back exactly.
        got = (self.scheduler_field(name, "start-date"),
               self.scheduler_field(name, "start-time"))
        if got != (start_date, start_time):
            raise RouterError(
                f"Failsafe scheduler not confirmed: expected {start_date} {start_time}, "
                f"router shows {got[0]} {got[1]}"
            )

    def reschedule_scheduler(self, name: str, seconds: int) -> bool:
        """Push a named scheduler's fire time to `seconds` from now, in place.

        Useful for extending a safety window before a long interactive step
        without creating an unprotected gap (vs. cancel-then-reinstall).

        Returns True if the new fire time is confirmed by a read-back.
        Returns False if the scheduler is absent or the read-back does not
        show the expected start-time. Raises RouterError if the router clock
        cannot be parsed.
        """
        out, _ = self.run_tolerant(f'/system scheduler print where name="{name}"')
        if name not in out:
            return False

        start_date, start_time = self._compute_fire_time(seconds)
        self.run_tolerant(
            f'/system scheduler set [find name="{name}"] '
            f'start-date={start_date} start-time={start_time}'
        )
        # Read back exact values with `get`: on 7.x `print` is a column table
        # with no `start-time=` text, so the old check always failed (#65).
        return (self.scheduler_field(name, "start-time") == start_time
                and self.scheduler_field(name, "start-date") == start_date)

    def scheduler_field(self, name: str, field: str) -> Optional[str]:
        """Exact value of one field of a named scheduler, via `get`, or None
        if the scheduler is absent. Never parses `print` output (#65): on
        7.x an absent item reports `no such item` on stdout, which is not a
        value."""
        out, err = self.run_tolerant(
            f':put [/system scheduler get [find name="{name}"] {field}]'
        )
        value = out.strip()
        if err.strip() or not value or "no such item" in value:
            return None
        return value

    def cancel_failsafe_scheduler(self, name: str) -> bool:
        """Disarm the failsafe scheduler. Returns True if confirmed gone.

        Safe to call when the scheduler is already absent. Returns False
        (rather than raising) when the post-cancel read shows it still present,
        so callers can decide how to handle the partial failure.
        """
        self.run_tolerant(f'/system scheduler remove [find name="{name}"]')
        out, _ = self.run_tolerant(f'/system scheduler print where name="{name}"')
        return name not in out

    def export_config(self) -> str:
        """Return the full /export verbose output."""
        return self.run("/export verbose", timeout=60)

    def read_lankit_tags(self) -> set[str]:
        """Read every lankit:/kit:-prefixed comment tag currently live on
        the router, across every table lankit writes to (see
        lankit.core.revert.TAG_TABLES). Skips (rather than errors on) any
        table whose print command fails — e.g. WiFi tables on hardware
        without the wifi package. Single canonical source for this: used
        by probe's config audit, apply's migration bootstrap, and the
        revert-verification path (decline/import-error) that must never
        print a success mark over a silent partial failure."""
        from lankit.core import revert
        raw_outputs: dict[str, str] = {}
        for table in revert.TAG_TABLES:
            out, err = self.run_tolerant(table.print_cmd)
            if not err:
                raw_outputs[table.id] = out
        return revert.read_live_tags(raw_outputs)

    def read_master_wifi_facts(self) -> Optional["MasterWifiFacts"]:
        """Capture wifi1/wifi2's current SSID and hidden state, for the
        revert bundle's master-WiFi restore block. Returns None (fail-open,
        design §5.3) if the print command errors or either master's fields
        can't be read — the caller should then omit master_wifi_facts from
        build_remove_pass rather than guess."""
        from lankit.core import revert
        out, err = self.run_tolerant("/interface wifi print detail without-paging")
        if err:
            return None
        return revert.read_wifi_master_facts(out)

    def verify_uploaded_file(self, remote_path: str, expected_size: int) -> bool:
        """Confirm a file landed on the router with exactly the expected
        byte size before anything depends on it being complete — most
        importantly, the failsafe scheduler must never be armed against a
        partially-uploaded revert bundle (design §6).

        Reads the size with `/file get`, which returns an exact byte count.
        `/file print` cannot be used: RouterOS humanises the size there
        (`size=77.6KiB` for 79442 bytes on 7.24.2, and in the SIZE column on
        6.49), so a `size=(\\d+)` parse read 77 and rejected every bundle
        over 1 KiB — the failsafe could never arm."""
        out, err = self.run_tolerant(
            f':put [/file get [find name="{remote_path}"] size]'
        )
        return parse_exact_file_size(out, err) == expected_size

    def identity(self) -> str:
        """Return the router system identity."""
        out = self.run("/system identity print")
        for line in out.splitlines():
            if "name:" in line:
                return line.split("name:")[-1].strip()
        return out.strip()

    def version(self) -> str:
        """Return RouterOS version string."""
        out = self.run("/system resource print")
        for line in out.splitlines():
            if "version:" in line:
                return line.split("version:")[-1].strip()
        return "unknown"


