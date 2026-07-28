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
        "OPTION 1 — FACTORY RESET (last resort, loses all config)",
        "-" * 72,
        "  1. Locate the reset button on the router.",
        "  2. With router powered on, hold reset for 5 seconds until",
        "     the LED flashes. Release.",
        "  3. Wait 2 minutes for boot.",
        "  4. Connect via WiFi: MikroTik (default) or cable to any LAN port.",
        "  5. Browse to http://192.168.88.1 or SSH: ssh admin@192.168.88.1",
        "  6. Fresh start — re-run: lankit apply (from your laptop, same network)",
        "     Or restore your last snapshot (works now only, because the reset",
        f"     config is empty). Latest snapshot: {snap_info}",
        "       scp <snapshot.rsc> admin@192.168.88.1:lankit-restore.rsc",
        "       ssh admin@192.168.88.1 '/import file=lankit-restore.rsc'",
        "",
        "OPTION 2 — RESTORE LAST KNOWN-GOOD STATE (preferred; keeps segments/WiFi)",
        "-" * 72,
        "  Use this whenever the router is still reachable — no reset needed.",
        "  Removes everything lankit added since the last good apply and rebuilds",
        "  it to match that state. This converges the router to the last known-",
        "  good state — it is NOT a byte-for-byte undo of everything that",
        "  happened since; manual changes you made on the router by hand are",
        "  left alone (lankit only touches what it tagged).",
        "",
        f"  1. Connect laptop to router via Ethernet.",
        f"  2. SSH: ssh -i {cfg.router.ssh_key} {cfg.router.ssh_user}@{cfg.router.ip}",
        f"     (try 192.168.88.1 if {cfg.router.ip} is unreachable)",
        f"  3. From your laptop terminal:",
        f"       lankit rollback",
        "     It uploads and imports the revert bundle, then verifies the result",
        "     on the router — you get a clear pass or fail, never a false 'done'.",
        "  4. If it prints 'did NOT complete': follow the next-steps it prints,",
        "     then run: lankit probe",
        "",
        "  WiFi may drop for a few seconds partway through — the wired network",
        "  (192.168.88.0/24, any LAN port) stays up the whole time. Plug in if",
        "  you need to stay connected during the revert.",
        "",
        "OPTION 3 — MANUAL ON-ROUTER REVERT (no laptop with lankit installed)",
        "-" * 72,
        "  Same mechanism as Option 2, run directly on the router. Use this if",
        "  you have SSH or Winbox access but not a machine with lankit installed.",
        "",
        "  Via SSH:",
        f"    ssh {cfg.router.ssh_user}@{cfg.router.ip}",
        "    /import file=lankit-revert.rsc",
        "",
        "  Via Winbox (Windows/Mac GUI):",
        "    1. Download WinBox from mikrotik.com/download",
        "    2. Connect via MAC address (no IP needed)",
        "    3. New Terminal → /import file=lankit-revert.rsc",
        "",
        "  lankit-revert.rsc is already on the router from the last apply. It's",
        "  safe to re-run if it doesn't finish the first time. Afterward, from a",
        "  laptop with lankit installed, run: lankit probe",
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

    wifi_segments = cfg.segments_with_wifi()
    if wifi_segments:
        lines += [
            "",
            "WIFI NETWORKS (passwords are on the password card, not here)",
            "-" * 72,
        ]
        for name, seg in wifi_segments.items():
            hidden = " (hidden)" if seg.ssid_hidden else ""
            lines.append(f"  {name:<15} SSID: {seg.ssid}{hidden}")
        lines.append("  Full passwords: lankit password-card")

    lines += [
        "",
        "DNS SERVER",
        "-" * 72,
    ]
    dns = cfg.hosts.get("dns_server")
    if dns:
        lines.append(f"  {dns.hostname}  {dns.ip}  (segment: {dns.segment})")
        lines.append(f"  If DNS is down: set manual DNS 1.1.1.1 on your device")

    if dns and dns.enabled and "pihole" in dns.services:
        lines += [
            "",
            "SERVICES",
            "-" * 72,
            f"  Pi-hole dashboard:  http://{dns.ip}/admin",
            f"  Verify DNS:         nslookup google.com {dns.ip}",
        ]

    lines += [
        "",
        "SNAPSHOTS LOCATION",
        "-" * 72,
        f"  ~/.lankit/snapshots/",
        "  Human-readable history and the source for the factory-reset restore",
        "  above. Day-to-day recovery uses 'lankit rollback' (Option 2), not a",
        "  manual snapshot import — run 'lankit restore' to pick a different",
        "  point in that history if the most recent one isn't what you want.",
        "",
        "=" * 72,
        "  Keep this card in a drawer near the router.",
        "  Regenerate BOTH cards after any network change — segments, SSIDs,",
        "  and hosts can all drift out of date otherwise:",
        "    lankit rollback-card && lankit password-card",
        "=" * 72,
        "",
    ]

    card_text = "\n".join(lines)
    Path(output).write_text(card_text)
    console.print(f"[green]✓[/green] Rollback card written to [bold]{output}[/bold]")
    console.print(f"  [dim]Print it and keep it near the router.[/dim]")
