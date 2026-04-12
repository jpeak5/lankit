import click
from lankit.cli.__main__ import cli


@cli.command(name="explain")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--output", "-o", type=click.Path(), default=None,
              metavar="FILE", help="Write to file instead of stdout (e.g. RULES.md)")
def explain(config_path, output):
    """Describe network rules in plain English.

    Generates a human-readable summary of every segment: what it can reach,
    how DNS works, internet access, bandwidth limits, and device isolation.
    Suitable for sharing with household members.

    No router connection required — reads only network.yml.

    \b
    Examples:
      lankit explain
      lankit explain --output RULES.md
    """
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    lines = _render(cfg)
    text = "\n".join(lines)

    if output:
        Path(output).write_text(text + "\n")
        console.print(f"[green]✓[/green] Written to [bold]{output}[/bold]")
    else:
        console.print(text)


def _render(cfg) -> list[str]:
    from lankit.core.config import Config

    lines = [
        f"# {cfg.household_name} — Network Rules",
        f"",
        f"Generated from network.yml. These rules apply once provisioned.",
        f"",
    ]

    # Build a reverse permission index: what can reach this segment?
    inbound: dict[str, list[str]] = {name: [] for name in cfg.segments}
    for src, perm in cfg.permissions.items():
        for target in perm.can_reach:
            if target in inbound:
                inbound[target].append(src)

    for name, seg in cfg.segments.items():
        perm = cfg.permissions.get(name)
        can_reach = perm.can_reach if perm else []
        reachable_by = inbound.get(name, [])

        lines.append(f"## {name.title()} — VLAN {seg.vlan_id}")
        lines.append(f"")
        lines.append(f"*{seg.comment}*")
        lines.append(f"")

        # WiFi
        if seg.has_wifi:
            bands = " + ".join(b.replace("ghz", " GHz") for b in seg.wifi_bands)
            hidden = " (hidden SSID)" if seg.ssid_hidden else ""
            lines.append(f"WiFi: **{seg.ssid}** — {bands}{hidden}")
        else:
            lines.append(f"WiFi: wired only")

        # Subnet
        lines.append(f"Subnet: `{seg.subnet}`  gateway: `{seg.gateway}`")
        lines.append(f"")

        # Internet
        internet_desc = {
            "full": "Unrestricted internet access.",
            "egress_only": "Can reach the internet outbound. The internet cannot initiate connections inward.",
            "none": "No internet access.",
        }.get(seg.internet, seg.internet)
        lines.append(f"**Internet:** {internet_desc}")

        # DNS
        dns_desc = {
            "filtered": "Pi-hole (ad blocking active, queries logged per privacy settings).",
            "unfiltered": "Public DNS (1.1.1.1 / 8.8.8.8) — no filtering, no logging.",
            "none": "No DNS resolution.",
        }.get(seg.dns, seg.dns)
        force = " All DNS traffic is intercepted, even if a device has hardcoded DNS." if seg.force_dns else ""
        lines.append(f"**DNS:** {dns_desc}{force}")

        # Bandwidth
        if seg.bandwidth_up or seg.bandwidth_down:
            up = seg.bandwidth_up or "unlimited"
            down = seg.bandwidth_down or "unlimited"
            lines.append(f"**Bandwidth:** {up} up / {down} down (combined for all devices in this segment).")

        # Outbound permissions
        if can_reach:
            lines.append(f"**Can reach:** {', '.join(can_reach)}.")
        else:
            lines.append(f"**Can reach:** nothing — isolated from all other segments.")

        # Inbound permissions
        if reachable_by:
            lines.append(f"**Reachable from:** {', '.join(reachable_by)}.")

        # Client isolation
        if seg.client_isolation:
            lines.append(f"**Device isolation:** ON — devices in this segment cannot see each other.")
        else:
            lines.append(f"**Device isolation:** off — devices can communicate with each other.")

        lines.append(f"")

    # Privacy section
    p = cfg.privacy
    lines += [
        "## Privacy & DNS Logging",
        "",
        f"**Query logging:** {p.query_logging}",
    ]
    if p.query_logging != "none":
        lines.append(f"**Retention:** {p.query_retention}")
    lines += [
        f"**Dashboard visibility:** {p.dashboard_visibility}",
        f"**Apple Private Relay:** {p.apple_private_relay}",
        f"**DNSSEC:** {'enabled' if cfg.dnssec else 'disabled'}",
        "",
    ]

    return lines
