"""
lankit.core.router
~~~~~~~~~~~~~~~~~~

RouterOS SSH connection and command execution via paramiko.
Used by apply, commit, rollback, discover, and test-failsafe commands.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import paramiko


class RouterError(Exception):
    """Raised when a router operation fails."""
    pass


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
            raise RouterError(f"Cannot connect to {self.ip} as {self.ssh_user}: {e}") from e
        return self

    def __exit__(self, *args) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def run(self, command: str, timeout: int = 30) -> str:
        """Run a command, return stdout. Raises RouterError on non-empty stderr."""
        if not self._client:
            raise RouterError("Not connected")
        stdin, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace").strip()
        if err:
            raise RouterError(f"Command error: {err}")
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

    def add_failsafe_scheduler(self, name: str, revert_cmd: str, seconds: int) -> None:
        """Install a one-shot RouterOS scheduler job that reverts in `seconds`.

        Parses both date and time from the router clock so midnight boundaries
        are handled correctly (start-date advances to the next day when needed).
        interval=1d is belt-and-suspenders: the on-event script self-deletes
        before importing, so it is truly one-shot.

        Raises RouterError if the clock cannot be parsed or the scheduler
        cannot be installed. Caller should track whether this completed.

        Call cancel_failsafe_scheduler() to disarm on success.
        """
        import re
        from datetime import datetime, timedelta

        raw = self.run("/system clock print")
        date_m = re.search(r'date:\s*(\w{3})/(\d{1,2})/(\d{4})', raw)
        time_m = re.search(r'time:\s*(\d{1,2}):(\d{2}):(\d{2})', raw)
        if not time_m:
            raise RouterError(f"Could not parse router clock output: {raw!r}")

        _months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        if date_m:
            month = _months.get(date_m.group(1).lower(), 1)
            now_dt = datetime(
                int(date_m.group(3)), month, int(date_m.group(2)),
                int(time_m.group(1)), int(time_m.group(2)), int(time_m.group(3)),
            )
        else:
            # Date unavailable — fall back to year 2000 (midnight boundary still
            # handled correctly within the same day; next-day case is edge-only)
            now_dt = datetime(2000, 1, 1,
                              int(time_m.group(1)), int(time_m.group(2)), int(time_m.group(3)))

        fire_dt = now_dt + timedelta(seconds=seconds)
        _months_rev = {v: k for k, v in _months.items()}
        start_date = f"{_months_rev[fire_dt.month]}/{fire_dt.day:02d}/{fire_dt.year}"
        start_time = fire_dt.strftime("%H:%M:%S")

        self.run(
            f'/system scheduler add name="{name}" '
            f'start-date={start_date} start-time={start_time} interval=1d '
            f'on-event={{{revert_cmd}}} comment="lankit:failsafe"'
        )

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


