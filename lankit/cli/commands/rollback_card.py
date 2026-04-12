import click
from lankit.cli.__main__ import cli


@cli.command(name="rollback-card")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--output", "-o", type=click.Path(), default="rollback-card.txt",
              metavar="FILE", help="Output file (default: rollback-card.txt)")
def rollback_card(config_path, output):
    """Generate a printed emergency rollback card.

    Produces a plain-text card with step-by-step router recovery instructions.
    Print it and keep it near your router. Can be generated independently of
    provisioning — no router connection required.

    \b
    Examples:
      lankit rollback-card
      lankit rollback-card --output ~/Desktop/rollback-card.txt
    """
    from lankit.core.config import load, ConfigError
    from lankit.core import snapshots
    from pathlib import Path
    from datetime import datetime, timezone
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    latest_snap = snapshots.latest(cfg.router.ip)
    snap_info = str(latest_snap) if latest_snap else "(none yet — run lankit apply first)"

    lines = [
        "=" * 72,
        f"  LANKIT EMERGENCY ROLLBACK CARD",
        f"  {cfg.household_name} — generated {now}",
        "=" * 72,
        "",
        "SITUATION: Router config is broken and the network is down.",
        "",
        "OPTION 1 — FACTORY RESET (fastest, loses all config)",
        "-" * 72,
        "  1. Locate the reset button on the router.",
        "  2. With router powered on, hold reset for 5 seconds until",
        "     the LED flashes. Release.",
        "  3. Wait 2 minutes for boot.",
        f"  4. Connect via WiFi: MikroTik (default) or cable to any LAN port.",
        f"  5. Browse to http://192.168.88.1 or SSH: ssh admin@192.168.88.1",
        "  6. Re-run: lankit apply (from your laptop on the same network)",
        "",
        "OPTION 2 — RESTORE LAST SNAPSHOT (preserves config history)",
        "-" * 72,
        f"  Latest snapshot: {snap_info}",
        "",
        f"  1. Connect laptop to router via Ethernet.",
        f"  2. SSH: ssh -i {cfg.router.ssh_key} {cfg.router.ssh_user}@{cfg.router.ip}",
        f"     (try 192.168.88.1 if {cfg.router.ip} is unreachable)",
        f"  3. From your laptop terminal:",
        f"       lankit restore",
        f"     Or manually upload and import the snapshot:",
        f"       scp <snapshot.rsc> {cfg.router.ssh_user}@{cfg.router.ip}:/",
        f"       ssh {cfg.router.ssh_user}@{cfg.router.ip} '/import file=snapshot.rsc'",
        "",
        "OPTION 3 — WINBOX ROLLBACK (Windows/Mac GUI)",
        "-" * 72,
        "  1. Download WinBox from mikrotik.com/download",
        "  2. Connect via MAC address (no IP needed)",
        "  3. Files → Upload the .rsc snapshot",
        "  4. New Terminal → /import file=snapshot.rsc",
        "",
        "ROUTER CREDENTIALS",
        "-" * 72,
        f"  IP:       {cfg.router.ip}",
        f"  SSH user: {cfg.router.ssh_user}",
        f"  SSH key:  {cfg.router.ssh_key}",
        f"  Domain:   {cfg.internal_domain}",
        "",
        "SEGMENT GATEWAYS (for static IP fallback)",
        "-" * 72,
    ]

    for name, seg in cfg.segments.items():
        lines.append(f"  {name:<15} VLAN {seg.vlan_id:<5}  gateway: {seg.gateway}")

    lines += [
        "",
        "DNS SERVER",
        "-" * 72,
    ]
    dns = cfg.hosts.get("dns_server")
    if dns:
        lines.append(f"  {dns.hostname}  {dns.ip}  (segment: {dns.segment})")
        lines.append(f"  If DNS is down: set manual DNS 1.1.1.1 on your device")

    lines += [
        "",
        "SNAPSHOTS LOCATION",
        "-" * 72,
        f"  ~/.lankit/snapshots/",
        f"  Run 'lankit restore' to browse and restore interactively.",
        "",
        "=" * 72,
        "  Keep this card in a drawer near the router.",
        "  Regenerate after each provisioning run: lankit rollback-card",
        "=" * 72,
        "",
    ]

    card_text = "\n".join(lines)
    Path(output).write_text(card_text)
    console.print(f"[green]✓[/green] Rollback card written to [bold]{output}[/bold]")
    console.print(f"  [dim]Print it and keep it near the router.[/dim]")
