"""The dns-server role's watchdog script, run against stubbed systemctl,
dig and logger: it must restart a hung (active but silent) pihole-FTL, and
only that."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "ansible/roles/dns-server/files/lankit-dns-watchdog.sh"


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"

    def stub(name, body):
        p = bin_dir / name
        p.write_text(f"#!/bin/sh\necho \"{name} $*\" >> {calls}\n{body}\n")
        p.chmod(0o755)

    def _run(*, active=True, answers=False, stamp_age=None):
        stub("systemctl", f'[ "$1" = is-active ] && exit {0 if active else 3}; exit 0')
        stub("dig", f"exit {0 if answers else 9}")
        stub("logger", "exit 0")
        stamp = tmp_path / "stamp"
        if stamp_age is not None:
            import time
            stamp.write_text(str(int(time.time()) - stamp_age))
        calls.write_text("")
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                   LANKIT_WATCHDOG_STAMP=str(stamp), LANKIT_WATCHDOG_SPACING="0")
        rc = subprocess.run(["sh", str(SCRIPT)], env=env).returncode
        return rc, calls.read_text().splitlines()

    return _run


def test_healthy_ftl_is_left_alone(run):
    rc, calls = run(answers=True)
    assert rc == 0
    assert "systemctl restart pihole-FTL" not in calls
    assert sum(c.startswith("dig") for c in calls) == 1


def test_hung_ftl_is_restarted_after_three_probes(run):
    rc, calls = run(answers=False)
    assert rc == 0
    assert sum(c.startswith("dig") for c in calls) == 3
    assert calls[-1] == "systemctl restart pihole-FTL"


def test_stopped_ftl_is_not_touched(run):
    rc, calls = run(active=False)
    assert rc == 0
    assert calls == ["systemctl is-active --quiet pihole-FTL"]


def test_no_second_restart_within_cooldown(run):
    rc, calls = run(answers=False, stamp_age=60)
    assert rc == 1
    assert "systemctl restart pihole-FTL" not in calls


def test_restart_allowed_after_cooldown(run):
    rc, calls = run(answers=False, stamp_age=3600)
    assert calls[-1] == "systemctl restart pihole-FTL"
