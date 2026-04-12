import click
from lankit.cli.__main__ import cli


@cli.command(name="overview")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml (default: ./network.yml)")
def overview(config_path):
    """Show a summary of network.yml — segments, hosts, permissions, privacy.

    Reads only network.yml. No router connection required.
    """
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    console.print(f"\n[bold]{cfg.household_name}[/bold] — [dim]{cfg.internal_domain}[/dim]\n")

    # ── Segments ─────────────────────────────────────────────────────────────
    seg_table = Table(title="Segments", box=box.SIMPLE_HEAD, show_lines=False)
    seg_table.add_column("Name", style="bold cyan")
    seg_table.add_column("VLAN", justify="right")
    seg_table.add_column("Subnet")
    seg_table.add_column("WiFi")
    seg_table.add_column("DNS")
    seg_table.add_column("Internet")
    seg_table.add_column("BW up/down")

    dns_style = {"filtered": "green", "unfiltered": "yellow", "none": "dim"}
    inet_style = {"full": "green", "egress_only": "yellow", "none": "red"}

    for name, seg in cfg.segments.items():
        wifi = ""
        if seg.has_wifi:
            bands = "+".join(b.replace("ghz", "G") for b in seg.wifi_bands)
            wifi = f"{seg.ssid} ({bands})"
            if seg.ssid_hidden:
                wifi += " [dim][hidden][/dim]"
        bw = ""
        if seg.bandwidth_up or seg.bandwidth_down:
            bw = f"{seg.bandwidth_up or '—'} / {seg.bandwidth_down or '—'}"

        seg_table.add_row(
            name,
            str(seg.vlan_id),
            f"{seg.subnet}",
            wifi,
            f"[{dns_style.get(seg.dns, '')}]{seg.dns}[/]",
            f"[{inet_style.get(seg.internet, '')}]{seg.internet}[/]",
            bw,
        )

    console.print(seg_table)

    # ── Permissions ───────────────────────────────────────────────────────────
    if cfg.permissions:
        perm_table = Table(title="Permissions", box=box.SIMPLE_HEAD)
        perm_table.add_column("Segment", style="bold cyan")
        perm_table.add_column("Can reach")
        for name, perm in cfg.permissions.items():
            targets = ", ".join(perm.can_reach) if perm.can_reach else "[dim]none[/dim]"
            perm_table.add_row(name, targets)
        console.print(perm_table)

    # ── Hosts ─────────────────────────────────────────────────────────────────
    host_table = Table(title="Hosts", box=box.SIMPLE_HEAD)
    host_table.add_column("Name", style="bold cyan")
    host_table.add_column("Hostname")
    host_table.add_column("Segment")
    host_table.add_column("IP")
    host_table.add_column("Services")
    for name, h in cfg.hosts.items():
        svcs = ", ".join(h.services) if h.services else "[dim]—[/dim]"
        enabled = "" if h.enabled else " [dim][disabled][/dim]"
        host_table.add_row(name, h.hostname + enabled, h.segment, h.ip, svcs)
    console.print(host_table)

    # ── Privacy ───────────────────────────────────────────────────────────────
    p = cfg.privacy
    privacy_table = Table(title="Privacy", box=box.SIMPLE_HEAD, show_header=False)
    privacy_table.add_column("Key", style="dim")
    privacy_table.add_column("Value")
    privacy_table.add_row("query_logging", p.query_logging)
    privacy_table.add_row("query_retention", p.query_retention)
    privacy_table.add_row("dashboard_visibility", p.dashboard_visibility)
    privacy_table.add_row(
        "apple_private_relay",
        f"[red]block[/red]" if p.apple_private_relay == "block" else "allow"
    )
    privacy_table.add_row("dnssec", "✓" if cfg.dnssec else "✗")
    console.print(privacy_table)
    console.print()
