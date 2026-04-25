"""
lankit.core.config
~~~~~~~~~~~~~~~~~~

Loads and validates network.yml. Resolves Jinja2 template variables
(e.g. {{ household_name }} in SSIDs). Exposes a Config object used
throughout the codebase.

Usage:
    from lankit.core.config import load
    cfg = load()           # reads network.yml from cwd or LANKIT_CONFIG
    cfg.segments           # dict of segment name → Segment
    cfg.permissions        # dict of segment name → Permission
    cfg.household_name     # str
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment
from jsonschema import Draft202012Validator, ValidationError

# Path to schema, relative to this file
_SCHEMA_PATH = Path(__file__).parent.parent.parent / "network.schema.json"
_DEFAULT_CONFIG = Path("network.yml")


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Segment:
    name: str
    vlan_id: int
    comment: str
    subnet: str
    ssid: Optional[str]
    wifi_bands: list[str]
    ssid_hidden: bool
    bandwidth_up: Optional[str]
    bandwidth_down: Optional[str]
    dns: str           # filtered | unfiltered | none
    force_dns: bool
    internet: str      # full | egress_only | none
    client_isolation: bool

    @property
    def gateway(self) -> str:
        """First host address in the subnet (x.x.x.1)."""
        base = self.subnet.rsplit(".", 1)[0]
        return f"{base}.1"

    @property
    def pool_range(self) -> str:
        """DHCP pool range (x.x.x.10 – x.x.x.254)."""
        base = self.subnet.rsplit(".", 1)[0]
        return f"{base}.10-{base}.254"

    @property
    def has_wifi(self) -> bool:
        return self.ssid is not None and len(self.wifi_bands) > 0


@dataclass
class Permission:
    can_reach: list[str]


@dataclass
class Host:
    hostname: str
    segment: str
    ip: str
    mac: str
    services: list[str]
    ssh_user: str = "pi"
    enabled: bool = True


@dataclass
class Privacy:
    query_logging: str       # full | anonymous | none
    query_retention: str     # e.g. "7d"
    dashboard_visibility: str  # admin_only | household | per_device
    apple_private_relay: str   # allow | block


@dataclass
class Router:
    ip: str
    ssh_user: str
    ssh_key: str
    wan_interface: str
    lan_interface: str


@dataclass
class TLS:
    cert: str
    key: str
    ca_cert: str


@dataclass
class Config:
    household_name: str
    internal_domain: str
    segments: dict[str, Segment]
    permissions: dict[str, Permission]
    privacy: Privacy
    wifi_password_source: str  # vault | env | prompt
    vpn: str                   # wireguard | ipsec | none
    dnssec: bool
    hosts: dict[str, Host]
    router: Router
    failsafe_seconds: int
    ssh_key: str
    portals: dict[str, bool]  # portal name → enabled
    tls: Optional[TLS] = None

    def dns_server_ip(self) -> str:
        """IP of the dns_server host."""
        return self.hosts["dns_server"].ip

    def segments_with_wifi(self) -> dict[str, Segment]:
        return {n: s for n, s in self.segments.items() if s.has_wifi}

    def segments_needing_dns(self) -> dict[str, Segment]:
        """Segments that should have DNS pointed at Pi-hole."""
        return {n: s for n, s in self.segments.items() if s.dns != "none"}

    def segments_with_force_dns(self) -> dict[str, Segment]:
        return {n: s for n, s in self.segments.items() if s.force_dns}

    def segments_with_bandwidth(self) -> dict[str, Segment]:
        return {
            n: s for n, s in self.segments.items()
            if s.bandwidth_up or s.bandwidth_down
        }


# ─── Loading ──────────────────────────────────────────────────────────────────

def load(path: Optional[Path] = None) -> Config:
    """Load, validate, and return Config from network.yml.

    Raises ConfigError with a human-readable message on any problem.
    """
    config_path = _resolve_path(path)
    raw = _read_yaml(config_path)
    _validate_schema(raw, config_path)
    raw = _resolve_templates(raw)
    _validate_cross_references(raw)
    return _build_config(raw, config_path)


def _resolve_path(path: Optional[Path]) -> Path:
    if path:
        return Path(path)
    env_path = os.environ.get("LANKIT_CONFIG")
    if env_path:
        return Path(env_path)
    if _DEFAULT_CONFIG.exists():
        return _DEFAULT_CONFIG
    raise ConfigError(
        "network.yml not found.\n"
        "Run lankit from your lankit directory, or set LANKIT_CONFIG."
    )


def _read_yaml(path: Path) -> dict:
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a YAML mapping at the top level")
        return data
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: YAML parse error\n{e}") from e


def _validate_schema(raw: dict, path: Path) -> None:
    with open(_SCHEMA_PATH) as f:
        schema = json.load(f)

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))

    if not errors:
        return

    lines = [f"  {path} has {len(errors)} error(s):\n"]
    for err in errors:
        location = " → ".join(str(p) for p in err.absolute_path) or "(root)"
        lines.append(f"  {location}: {err.message}")

    # Special guidance for unfilled CHOOSE markers
    choose_fields = [
        "privacy.query_logging",
        "privacy.query_retention",
        "privacy.dashboard_visibility",
        "privacy.apple_private_relay",
        "wifi_password_source",
        "vpn",
    ]
    null_chooses = [
        f for f in choose_fields
        if _get_nested(raw, f.split(".")) is None
    ]
    if null_chooses:
        lines.append(
            "\n  The following fields require a decision (replace with a valid value):\n"
            + "".join(f"    • {f}\n" for f in null_chooses)
        )

    raise ConfigError("\n".join(lines))


def _resolve_templates(raw: dict) -> dict:
    """Resolve {{ household_name }} and similar in string values."""
    env = Environment()
    context = {"household_name": raw.get("household_name", "")}

    def _resolve(obj):
        if isinstance(obj, str):
            try:
                return env.from_string(obj).render(context)
            except Exception:
                return obj
        if isinstance(obj, dict):
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(i) for i in obj]
        return obj

    return _resolve(raw)


def _validate_cross_references(raw: dict) -> None:
    """Validate that can_reach names refer to defined segments."""
    segment_names = set(raw.get("segments", {}).keys())
    errors = []

    for seg_name, perm in raw.get("permissions", {}).items():
        if seg_name not in segment_names:
            errors.append(
                f"  permissions.{seg_name}: segment '{seg_name}' is not defined in segments"
            )
        for target in perm.get("can_reach", []):
            if target not in segment_names:
                errors.append(
                    f"  permissions.{seg_name}.can_reach: '{target}' is not a defined segment"
                )

    for host_name, host in raw.get("hosts", {}).items():
        if isinstance(host, dict) and host.get("segment") not in segment_names:
            errors.append(
                f"  hosts.{host_name}.segment: '{host['segment']}' is not a defined segment"
            )

    if errors:
        raise ConfigError("Cross-reference errors in network.yml:\n" + "\n".join(errors))


def _build_config(raw: dict, config_path: Optional[Path] = None) -> Config:
    segments = {
        name: Segment(name=name, **{
            k: v for k, v in seg.items()
        })
        for name, seg in raw["segments"].items()
    }

    permissions = {
        name: Permission(can_reach=perm["can_reach"])
        for name, perm in raw.get("permissions", {}).items()
    }

    privacy_raw = raw["privacy"]
    privacy = Privacy(
        query_logging=privacy_raw["query_logging"],
        query_retention=privacy_raw["query_retention"],
        dashboard_visibility=privacy_raw["dashboard_visibility"],
        apple_private_relay=privacy_raw["apple_private_relay"],
    )

    hosts = {}
    for name, h in raw["hosts"].items():
        hosts[name] = Host(
            hostname=h["hostname"],
            segment=h["segment"],
            ip=h["ip"],
            mac=h["mac"],
            services=h["services"],
            ssh_user=h.get("ssh_user", "pi"),
            enabled=h.get("enabled", True),
        )

    router_raw = raw["router"]
    router = Router(
        ip=router_raw["ip"],
        ssh_user=router_raw["ssh_user"],
        ssh_key=router_raw["ssh_key"],
        wan_interface=router_raw["wan_interface"],
        lan_interface=router_raw["lan_interface"],
    )

    portals = {
        name: bool(p.get("enabled", False))
        for name, p in raw.get("portals", {}).items()
    }

    tls = None
    if tls_raw := raw.get("tls"):
        config_dir = (config_path.parent if config_path else Path.cwd()).resolve()
        def _resolve_tls_path(p: str) -> str:
            resolved = Path(p).expanduser()
            if not resolved.is_absolute():
                resolved = config_dir / resolved
            return str(resolved.resolve())
        tls = TLS(
            cert=_resolve_tls_path(tls_raw["cert"]),
            key=_resolve_tls_path(tls_raw["key"]),
            ca_cert=_resolve_tls_path(tls_raw["ca_cert"]),
        )

    return Config(
        household_name=raw["household_name"],
        internal_domain=raw["internal_domain"],
        segments=segments,
        permissions=permissions,
        privacy=privacy,
        wifi_password_source=raw["wifi_password_source"],
        vpn=raw["vpn"],
        dnssec=raw["dnssec"],
        hosts=hosts,
        router=router,
        failsafe_seconds=raw["failsafe_seconds"],
        ssh_key=raw["ssh_key"],
        portals=portals,
        tls=tls,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_nested(obj: dict, keys: list[str]):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


class ConfigError(Exception):
    """Raised when network.yml is invalid or incomplete."""
    pass
