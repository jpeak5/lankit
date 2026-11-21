"""
lankit.core.generator
~~~~~~~~~~~~~~~~~~~~~

Renders Jinja2 templates into RouterOS .rsc scripts.
Templates live in ansible/roles/router/templates/.
Output goes to ansible/generated/ beside the site's network.yml.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Iterable, Optional, Union

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from lankit.core.config import FLAT_LAN_PORTS, Config

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "ansible" / "roles" / "router" / "templates"
def generated_dir(cfg: Config) -> Path:
    """Where generated scripts go: `ansible/generated/` beside the site's
    network.yml. Never inside the lankit install: generated scripts carry
    plaintext WiFi passphrases (04-wifi.rsc), and a default pointing at the
    tool's own tree once left a household's keys in the tool's checkout."""
    return (cfg.config_dir or Path.cwd()) / "ansible" / "generated"


def _write_private(path: Path, text: str) -> None:
    """Write a generated script readable by the owner only (0600)."""
    path.write_text(text)
    path.chmod(0o600)


def _private_dir(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    return out

_TEMPLATES = [
    "01-vlans.rsc.j2",
    "02-dhcp.rsc.j2",
    "03-firewall.rsc.j2",
    "04-wifi.rsc.j2",
    "05-dns.rsc.j2",
    "06-dns-redirect.rsc.j2",
    "07-bandwidth.rsc.j2",
]

SegmentFilter = Union[str, Iterable[str], None]


def render_texts(
    cfg: Config,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: SegmentFilter = None,
    include_globals: Optional[bool] = None,
    template_names: Optional[list[str]] = None,
) -> dict[str, str]:
    """Render templates to {output_filename: text} without writing to disk.

    Pure rendering — no I/O beyond reading the template files themselves.
    Used by generate_all/generate_one (which write the result to output_dir)
    and by lankit.core.revert's known-good archive store (which needs
    rendered text to archive, not files on disk).

    segment_filter / include_globals: see _build_context.
    """
    context = _build_context(cfg, wifi_passphrases, segment_filter, include_globals)
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    names = template_names or _TEMPLATES
    result: dict[str, str] = {}
    for template_name in names:
        output_name = template_name.removesuffix(".j2")
        template = env.get_template(template_name)
        result[output_name] = template.render(**context)
    return result


def generate_all(
    cfg: Config,
    output_dir: Optional[Path] = None,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: SegmentFilter = None,
    include_globals: Optional[bool] = None,
) -> list[Path]:
    """Render all router templates to output_dir. Returns list of output file paths.

    wifi_passphrases: {segment_name: passphrase}. If None, placeholder
    values are used (safe for previewing; not suitable for deployment).

    segment_filter: a single segment name (str, existing behavior — forces
    include_globals=False unless overridden), an iterable of segment names
    (new — requires an explicit include_globals), or None for all segments.
    """
    out = _private_dir(output_dir or generated_dir(cfg))

    texts = render_texts(cfg, wifi_passphrases, segment_filter, include_globals)
    outputs = []
    for output_name, text in texts.items():
        output_path = out / output_name
        _write_private(output_path, text)
        outputs.append(output_path)
    return outputs


def generate_one(
    cfg: Config,
    template_name: str,
    output_dir: Optional[Path] = None,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: SegmentFilter = None,
    include_globals: Optional[bool] = None,
) -> Path:
    """Render a single template by name (e.g. '03-firewall.rsc.j2')."""
    out = _private_dir(output_dir or generated_dir(cfg))

    if not template_name.endswith(".j2"):
        template_name += ".j2"

    texts = render_texts(
        cfg, wifi_passphrases, segment_filter, include_globals,
        template_names=[template_name],
    )
    output_name = template_name.removesuffix(".j2")
    output_path = out / output_name
    _write_private(output_path, texts[output_name])
    return output_path


# 01-vlans keeps these on the flat VLAN 1, less any a host is wired to.
_FLAT_PORTS = FLAT_LAN_PORTS


def _host_port_vlans(cfg: Config, segments: dict) -> dict[int, list[str]]:
    """{vlan_id: [ports]} for host ports, ports sorted, VLANs ascending.

    Only segments in this render: the global remove takes out every lankit
    bridge-vlan row and re-adds only the rendered ones, so a host port on a
    segment outside the render would get a PVID with no VLAN row behind it
    and be cut off. Such a port is left as it is instead."""
    out: dict[int, list[str]] = {}
    for port, seg in cfg.host_ports().items():
        if seg not in segments:
            continue
        out.setdefault(cfg.segments[seg].vlan_id, []).append(port)
    return {v: sorted(ps) for v, ps in sorted(out.items())}


def _build_context(
    cfg: Config,
    wifi_passphrases: Optional[dict[str, str]] = None,
    segment_filter: SegmentFilter = None,
    include_globals: Optional[bool] = None,
) -> dict:
    """Build the template context dict from Config.

    segment_filter:
      - None (default): all segments.
      - a single str: that one segment. include_globals defaults to False
        if not given, preserving the original single-segment behavior
        (lankit apply --segment NAME).
      - any other iterable of segment names: that subset. include_globals
        must be passed explicitly — there's no sensible default once more
        than one segment can be selected (used by the known-good archive
        store to re-render a segment-apply's exact slice, design §3).
    """
    dns = cfg.hosts.get("dns_server")
    dns = dns if (dns and dns.enabled) else None
    app = cfg.hosts.get("app_server")

    if wifi_passphrases is None:
        # Placeholder values — safe for previewing, not for deployment
        wifi_passphrases = {
            name: f"CHANGEME-{name}"
            for name, seg in cfg.segments.items()
            if seg.has_wifi
        }

    is_single_str = isinstance(segment_filter, str)
    if segment_filter is None:
        names = None
    elif is_single_str:
        names = frozenset({segment_filter})
    else:
        names = frozenset(segment_filter)

    if names is not None:
        unknown = names - set(cfg.segments)
        if unknown:
            raise ValueError(f"Unknown segment(s): {', '.join(sorted(unknown))}")
        segments = {k: v for k, v in cfg.segments.items() if k in names}
        # Deliberately NOT filtering each permission's can_reach targets down
        # to `names` here: --segment X (the existing single-segment CLI use)
        # relies on forward references to segments applied later, matching
        # the documented incremental apply order (README/DESIGN.md) — a
        # permit rule naming an address-list that doesn't exist yet is a
        # harmless dead rule in RouterOS, not an import error, and it starts
        # working once that segment is applied. A caller that needs a fully
        # self-contained render for a *set* of segments (e.g. the known-good
        # archive re-render, design §3) should pre-filter can_reach itself —
        # e.g. via dataclasses.replace(cfg, permissions=...) before calling
        # this function — rather than have this function guess the intent
        # from segment_filter's shape.
        permissions = {k: v for k, v in cfg.permissions.items() if k in names}
        if include_globals is None:
            if is_single_str:
                include_globals = False
            else:
                raise ValueError(
                    "include_globals must be given explicitly when segment_filter "
                    "is a multi-segment collection (a single segment name still "
                    "defaults to include_globals=False for compatibility)"
                )
    else:
        segments = cfg.segments
        permissions = cfg.permissions
        if include_globals is None:
            include_globals = True

    return {
        "segments":           segments,
        "permissions":        permissions,
        "include_globals":    include_globals,
        "household_name":     cfg.household_name,
        "internal_domain":    cfg.internal_domain,
        "dns_server_ip":      cfg.dns_ip,
        # The service address (dns_server_ip) is the shared VIP when
        # dns_ha is set; the host's own address is what its lease pins.
        "dns_server_host_ip": dns.ip      if dns else None,
        "dns_server_mac":     dns.mac     if dns else None,
        "dns_server_segment": dns.segment if dns else None,
        "dns_server_vlan_id": cfg.segments[dns.segment].vlan_id if dns else None,
        "dns_server_gateway": str(ipaddress.ip_network(cfg.segments[dns.segment].subnet, strict=False).network_address + 1) if dns else None,
        "app_server_ip":      app.ip if app and app.enabled else None,
        "app_server_mac":     app.mac if app and app.enabled else None,
        "app_server_segment": app.segment if app and app.enabled else None,
        "wan_interface":      cfg.router.wan_interface,
        "lan_interface":      cfg.router.lan_interface,
        # Ports with a host wired straight to them become untagged access
        # ports on its segment; every other LAN port stays on the flat VLAN.
        "host_port_vlans":    _host_port_vlans(cfg, segments),
        "flat_ports":         [p for p in _FLAT_PORTS if p not in cfg.host_ports()],
        "wifi_passphrases":   wifi_passphrases,
    }
