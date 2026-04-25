"""
lankit.core.passwords
~~~~~~~~~~~~~~~~~~~~~

Load and store WiFi passwords from the configured source (vault, env, prompt).

Usage:
    from lankit.core.passwords import load_wifi_passwords
    passwords = load_wifi_passwords(cfg)   # {segment_name: passphrase}
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import click
import yaml

from lankit.core.config import Config, ConfigError

VAULT_FILE = Path("wifi-vault.yml")
_LANKIT_DIR = Path.home() / ".lankit"
_VAULT_PASSWORD_FILE = _LANKIT_DIR / "vault-password"


def _ansible_vault_bin() -> str:
    """Find ansible-vault, preferring the same venv as the running Python."""
    _name = "ansible-vault"
    venv_bin = Path(sys.executable).parent / _name
    if venv_bin.exists():
        return str(venv_bin)
    found = shutil.which(_name)
    if found:
        return found
    raise FileNotFoundError(
        "ansible-vault not found. Install ansible-core: pip install ansible-core"
    )


# ─── Public API ───────────────────────────────────────────────────────────────

def read_vault() -> dict[str, str]:
    """Decrypt and return the current vault contents. Empty dict if not present."""
    if not VAULT_FILE.exists() or not _VAULT_PASSWORD_FILE.exists():
        return {}
    result = subprocess.run(
        [
            _ansible_vault_bin(), "decrypt",
            "--vault-password-file", str(_VAULT_PASSWORD_FILE),
            "--output", "-",
            str(VAULT_FILE),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    data = yaml.safe_load(result.stdout)
    return data if isinstance(data, dict) else {}


def load_wifi_passwords(cfg: Config) -> dict[str, str]:
    """Return {segment_name: passphrase} for all WiFi segments.

    Raises ConfigError if passwords are missing or misconfigured.
    """
    wifi_segments = [name for name, seg in cfg.segments.items() if seg.has_wifi]

    if cfg.wifi_password_source == "vault":
        return _load_from_vault(wifi_segments)
    elif cfg.wifi_password_source == "env":
        return _load_from_env(wifi_segments)
    elif cfg.wifi_password_source == "prompt":
        return _load_from_prompt(wifi_segments)
    else:
        raise ConfigError(f"Unknown wifi_password_source: {cfg.wifi_password_source!r}")


def save_to_vault(passwords: dict[str, str]) -> Path:
    """Encrypt passwords into wifi-vault.yml. Returns the vault file path."""
    vp_file = _ensure_vault_password_file()

    plaintext = yaml.dump(passwords, default_flow_style=False, allow_unicode=True)

    # Write to a tempfile, encrypt in-place, then move to final location.
    # Never leaves a plaintext file at the destination.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(plaintext)
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            [
                _ansible_vault_bin(), "encrypt",
                "--vault-password-file", str(vp_file),
                str(tmp_path),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise ConfigError(f"ansible-vault encrypt failed:\n{result.stderr.strip()}")

        tmp_path.rename(VAULT_FILE)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return VAULT_FILE


def vault_password_file() -> Path:
    return _VAULT_PASSWORD_FILE


def ensure_vault_password_file() -> Path:
    """Create ~/.lankit/vault-password if it doesn't exist. Returns the path."""
    return _ensure_vault_password_file()


# ─── Loaders ──────────────────────────────────────────────────────────────────

def _load_from_vault(segment_names: list[str]) -> dict[str, str]:
    if not VAULT_FILE.exists():
        raise ConfigError(
            f"{VAULT_FILE} not found.\n"
            "Run: lankit secrets"
        )
    if not _VAULT_PASSWORD_FILE.exists():
        raise ConfigError(
            f"Vault password file not found: {_VAULT_PASSWORD_FILE}\n"
            "Run: lankit secrets"
        )

    result = subprocess.run(
        [
            _ansible_vault_bin(), "decrypt",
            "--vault-password-file", str(_VAULT_PASSWORD_FILE),
            "--output", "-",
            str(VAULT_FILE),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ConfigError(f"Failed to decrypt {VAULT_FILE}:\n{result.stderr.strip()}")

    data = yaml.safe_load(result.stdout)
    if not isinstance(data, dict):
        raise ConfigError(f"{VAULT_FILE}: expected a YAML mapping after decryption")

    passwords = {}
    missing = []
    for name in segment_names:
        if name not in data:
            missing.append(name)
        else:
            passwords[name] = data[name]

    if missing:
        raise ConfigError(
            f"wifi-vault.yml is missing entries for: {', '.join(missing)}\n"
            "Run: lankit secrets  to update the vault."
        )
    return passwords


def _load_from_env(segment_names: list[str]) -> dict[str, str]:
    passwords = {}
    missing = []
    for name in segment_names:
        var = f"LANKIT_WIFI_{name.upper()}"
        val = os.environ.get(var)
        if not val:
            missing.append(var)
        else:
            passwords[name] = val

    if missing:
        raise ConfigError(
            "Missing environment variables:\n"
            + "".join(f"  {v}\n" for v in missing)
            + "\nRun: lankit secrets  for setup instructions."
        )
    return passwords


def _load_from_prompt(segment_names: list[str]) -> dict[str, str]:
    passwords = {}
    for name in segment_names:
        pw = click.prompt(f"  WiFi password for '{name}'", hide_input=True)
        passwords[name] = pw
    return passwords


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_vault_password_file() -> Path:
    _LANKIT_DIR.mkdir(mode=0o700, exist_ok=True)
    if not _VAULT_PASSWORD_FILE.exists():
        password = secrets.token_urlsafe(32)
        _VAULT_PASSWORD_FILE.write_text(password + "\n")
        _VAULT_PASSWORD_FILE.chmod(0o600)
    return _VAULT_PASSWORD_FILE
