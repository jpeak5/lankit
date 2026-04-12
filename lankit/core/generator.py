"""
lankit.core.generator
~~~~~~~~~~~~~~~~~~~~~

Renders Jinja2 templates into RouterOS .rsc scripts.
Templates live in ansible/roles/router/templates/.
Output goes to ansible/generated/.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from lankit.core.config import Config

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "ansible" / "roles" / "router" / "templates"
_OUTPUT_DIR = Path(__file__).parent.parent.parent / "ansible" / "generated"

_TEMPLATES = [
    "01-vlans.rsc.j2",
    "02-dhcp.rsc.j2",
    "03-firewall.rsc.j2",
    "04-wifi.rsc.j2",
    "05-dns.rsc.j2",
    "06-dns-redirect.rsc.j2",
    "07-bandwidth.rsc.j2",
]


def generate_all(
    cfg: Config,
    output_dir: Optional[Path] = None,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: Optional[str] = None,
) -> list[Path]:
    """Render all router templates. Returns list of output file paths.

    wifi_passphrases: {segment_name: passphrase}. If None, placeholder
    values are used (safe for previewing; not suitable for deployment).

    segment_filter: if set, only generate infrastructure for this one segment.
    Global sections (NAT, default-deny, VLAN filtering, etc.) are omitted.
    """
    out = output_dir or _OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    context = _build_context(cfg, wifi_passphrases, segment_filter)
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    outputs = []
    for template_name in _TEMPLATES:
        output_name = template_name.removesuffix(".j2")
        output_path = out / output_name
        template = env.get_template(template_name)
        output_path.write_text(template.render(**context))
        outputs.append(output_path)

    return outputs


def generate_one(
    cfg: Config,
    template_name: str,
    output_dir: Optional[Path] = None,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: Optional[str] = None,
) -> Path:
    """Render a single template by name (e.g. '03-firewall.rsc.j2')."""
    out = output_dir or _OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    context = _build_context(cfg, wifi_passphrases, segment_filter)
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    if not template_name.endswith(".j2"):
        template_name += ".j2"

    output_name = template_name.removesuffix(".j2")
    output_path = out / output_name
    template = env.get_template(template_name)
    output_path.write_text(template.render(**context))
    return output_path


def _build_context(
    cfg: Config,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: Optional[str] = None,
) -> dict:
    """Build the template context dict from Config.

    segment_filter: when set, only that segment is included in the context.
    include_globals is False so templates skip system-wide sections.
    """
    dns = cfg.hosts["dns_server"]
    app = cfg.hosts.get("app_server")

    if wifi_passphrases is None:
        # Placeholder values — safe for previewing, not for deployment
        wifi_passphrases = {
            name: f"CHANGEME-{name}"
            for name, seg in cfg.segments.items()
            if seg.has_wifi
        }

    if segment_filter is not None:
        if segment_filter not in cfg.segments:
            raise ValueError(f"Unknown segment: {segment_filter!r}")
        segments = {segment_filter: cfg.segments[segment_filter]}
        permissions = {
            k: v for k, v in cfg.permissions.items() if k == segment_filter
        }
        include_globals = False
    else:
        segments = cfg.segments
        permissions = cfg.permissions
        include_globals = True

    return {
        "segments":           segments,
        "permissions":        permissions,
        "include_globals":    include_globals,
        "household_name":     cfg.household_name,
        "internal_domain":    cfg.internal_domain,
        "dns_server_ip":      dns.ip,
        "dns_server_mac":     dns.mac,
        "dns_server_segment": dns.segment,
        "dns_server_vlan_id": cfg.segments[dns.segment].vlan_id,
        "dns_server_gateway": str(ipaddress.ip_network(cfg.segments[dns.segment].subnet, strict=False).network_address + 1),
        "app_server_ip":      app.ip if app and app.enabled else None,
        "wan_interface":      cfg.router.wan_interface,
        "lan_interface":      cfg.router.lan_interface,
        "wifi_passphrases":   wifi_passphrases,
    }
