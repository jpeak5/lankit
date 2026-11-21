"""lankit provision must never put secrets on the command line or in its
echoed command: extra-vars travel in a private @file."""

import json
import os
import stat
import subprocess

from click.testing import CliRunner

from lankit.cli.__main__ import cli
import lankit.cli.commands.provision  # noqa: F401  (registers the command)

SECRET = "s3cret-pihole-pw"


def test_password_stays_out_of_argv_and_output(minimal_yml, monkeypatch):
    monkeypatch.setattr("lankit.core.passwords.read_vault", lambda: {"pihole_password": SECRET})
    monkeypatch.setattr("lankit.cli.commands.provision._read_public_key", lambda p: "ssh-ed25519 AAAA test")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        path = next(a for a in cmd if a.startswith("@"))[1:]
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        seen["vars"] = json.load(open(path))
        seen["path"] = path
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CliRunner().invoke(cli, ["provision", "--config", str(minimal_yml), "--check"])

    assert result.exit_code == 0, result.output
    assert SECRET not in " ".join(seen["cmd"])
    assert SECRET not in result.output
    assert seen["vars"]["lankit_pihole_password"] == SECRET
    assert seen["mode"] == 0o600
    assert not os.path.exists(seen["path"])     # removed afterwards
