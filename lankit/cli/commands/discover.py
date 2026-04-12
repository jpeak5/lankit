import click
from lankit.cli.__main__ import cli


@cli.command(name="discover")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml (used for router credentials)")
@click.option("--new", is_flag=True, default=False,
              help="Start the guided network.yml creation wizard")
@click.option("--router", "router_ip", type=str, default=None,
              metavar="IP", help="Router IP (overrides network.yml)")
def discover(config_path, new, router_ip):
    """Scan the router for connected devices and network state.

    Shows all DHCP leases with OUI vendor lookup and private MAC detection.
    Use --new to start the guided setup wizard that creates network.yml.

    \b
    Examples:
      lankit discover --new           # first-run wizard
      lankit discover                 # scan connected devices
      lankit discover --router 192.168.88.1
    """
    if new:
        _run_wizard()
        return

    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    if router_ip:
        # Minimal config: just router IP, try default credentials
        ip = router_ip
        ssh_user = "admin"
        ssh_key = "~/.ssh/lankit"
    else:
        try:
            cfg = load(Path(config_path) if config_path else None)
            ip = cfg.router.ip
            ssh_user = cfg.router.ssh_user
            ssh_key = cfg.ssh_key
        except ConfigError as e:
            console.print(f"[bold red]Config error:[/bold red] {e}")
            console.print("\nTip: Use [bold]lankit discover --new[/bold] to create network.yml")
            raise SystemExit(1)

    console.print(f"Scanning [bold]{ip}[/bold]...")
    try:
        with RouterConnection(ip, ssh_user, ssh_key) as conn:
            identity = conn.identity()
            version = conn.version()
            console.print(f"  [bold]{identity}[/bold]  RouterOS {version}\n")

            # DHCP leases
            leases_raw, _ = conn.run_tolerant(
                "/ip dhcp-server lease print detail without-paging"
            )
            leases = _parse_leases(leases_raw)

            # ARP table (catches devices without DHCP)
            arp_raw, _ = conn.run_tolerant("/ip arp print without-paging")
            arp = _parse_arp(arp_raw)

            # Merge: prefer lease data, supplement with ARP
            devices = {entry["mac"]: entry for entry in leases}
            for entry in arp:
                if entry["mac"] not in devices:
                    entry.setdefault("hostname", "")
                    entry.setdefault("server", "—")
                    entry.setdefault("status", "ARP")
                    devices[entry["mac"]] = entry

            if not devices:
                console.print("[dim]No devices found.[/dim]")
                return

            # OUI lookup
            _enrich_vendors(devices)

            # Display
            table = Table(title=f"Devices on {identity}", box=box.SIMPLE_HEAD)
            table.add_column("IP", no_wrap=True)
            table.add_column("MAC", no_wrap=True, style="dim")
            table.add_column("Hostname")
            table.add_column("Vendor")
            table.add_column("DHCP Server", style="dim")
            table.add_column("Status", style="dim")

            for mac, d in sorted(devices.items(), key=lambda x: _ip_sort_key(x[1].get("ip", ""))):
                vendor = d.get("vendor", "")
                if d.get("private_mac"):
                    vendor = "[dim]private MAC[/dim]"
                table.add_row(
                    d.get("ip", ""),
                    mac,
                    d.get("hostname", ""),
                    vendor,
                    d.get("server", ""),
                    d.get("status", ""),
                )

            console.print(table)
            console.print(f"[dim]{len(devices)} device(s)[/dim]")

    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)


def _parse_leases(text: str) -> list[dict]:
    """Parse RouterOS DHCP lease print detail output into list of dicts."""
    import re
    leases = []
    for block in re.split(r'\n(?=\s*\d+\s+)', text):
        block = block.strip()
        if not block:
            continue
        entry = {}
        for key, pattern in [
            ("ip",       r'address=(\S+)'),
            ("mac",      r'mac-address=(\S+)'),
            ("hostname", r'host-name="?([^"\s]+)"?'),
            ("server",   r'server=(\S+)'),
            ("status",   r'status=(\S+)'),
        ]:
            m = re.search(pattern, block)
            if m:
                entry[key] = m.group(1)
        if entry.get("mac"):
            leases.append(entry)
    return leases


def _parse_arp(text: str) -> list[dict]:
    """Parse RouterOS ARP table output."""
    import re
    entries = []
    for line in text.splitlines():
        ip_m = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
        mac_m = re.search(r'([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})', line)
        if ip_m and mac_m:
            entries.append({"ip": ip_m.group(1), "mac": mac_m.group(1).upper()})
    return entries


def _enrich_vendors(devices: dict) -> None:
    """Add 'vendor' and 'private_mac' fields using mac-vendor-lookup."""
    try:
        from mac_vendor_lookup import MacLookup, VendorNotFoundError
        m = MacLookup()
    except ImportError:
        return

    for mac, d in devices.items():
        # Detect locally-administered (private/randomized) MAC via bit 1 of first byte
        try:
            first_byte = int(mac.replace(":", "").replace("-", "")[:2], 16)
            if first_byte & 0x02:
                d["private_mac"] = True
                continue
        except ValueError:
            pass
        try:
            d["vendor"] = m.lookup(mac)
        except Exception:
            d["vendor"] = ""


def _ip_sort_key(ip: str) -> tuple:
    try:
        return tuple(int(p) for p in ip.split("."))
    except (ValueError, AttributeError):
        return (999, 999, 999, 999)


def _run_wizard():
    """Interactive wizard to create an initial network.yml."""
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from pathlib import Path
    import yaml

    console = Console()
    console.print("\n[bold]lankit setup wizard[/bold]\n")
    console.print("This will create a [bold]network.yml[/bold] in the current directory.")
    console.print("You can edit it further before running [bold]lankit apply[/bold].\n")

    if Path("network.yml").exists():
        overwrite = Confirm.ask("network.yml already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("[dim]Cancelled.[/dim]")
            return

    household = Prompt.ask("Household name (used in SSIDs)", default="Home")
    domain = Prompt.ask("Internal domain suffix", default="internal")
    router_ip = Prompt.ask("Router IP address", default="192.168.88.1")
    ssh_user = Prompt.ask("Router SSH user", default="admin")
    wan = Prompt.ask("WAN interface name", default="ether1")
    lan = Prompt.ask("LAN bridge interface name", default="bridge")
    dns_ip = Prompt.ask("DNS server (Pi-hole) IP", default="10.10.10.2")
    dns_mac = Prompt.ask("DNS server MAC address (format AA:BB:CC:DD:EE:FF)")

    # Write a minimal but valid network.yml
    config_template = f"""\
household_name: {household}
internal_domain: {domain}

segments:
  trusted:
    vlan_id: 10
    comment: "Primary household devices"
    subnet: "10.10.10.0/24"
    ssid: "{household}-trusted"
    wifi_bands: [5ghz]
    ssid_hidden: false
    bandwidth_up: null
    bandwidth_down: null
    dns: filtered
    force_dns: false
    internet: full
    client_isolation: false

  iot:
    vlan_id: 30
    comment: "Smart home devices"
    subnet: "10.10.30.0/24"
    ssid: "{household}-iot"
    wifi_bands: [2ghz]
    ssid_hidden: false
    bandwidth_up: null
    bandwidth_down: null
    dns: filtered
    force_dns: true
    internet: egress_only
    client_isolation: true

  guest:
    vlan_id: 40
    comment: "Visitor WiFi"
    subnet: "10.10.40.0/24"
    ssid: "{household}-guest"
    wifi_bands: [2ghz, 5ghz]
    ssid_hidden: false
    bandwidth_up: "20"
    bandwidth_down: "50"
    dns: filtered
    force_dns: true
    internet: full
    client_isolation: true

permissions:
  trusted:
    can_reach: []

privacy:
  query_logging: full          # full | anonymous | none
  query_retention: "7d"
  dashboard_visibility: admin_only   # admin_only | household | per_device
  apple_private_relay: allow         # allow | block

wifi_password_source: prompt   # vault | env | prompt
vpn: none                      # wireguard | ipsec | none
dnssec: true

hosts:
  dns_server:
    hostname: dns.{domain}
    segment: trusted
    ip: "{dns_ip}"
    mac: "{dns_mac}"
    services: [pihole, unbound]
    enabled: true

router:
  ip: "{router_ip}"
  ssh_user: {ssh_user}
  ssh_key: "~/.ssh/lankit"
  wan_interface: {wan}
  lan_interface: {lan}

failsafe_seconds: 120
ssh_key: "~/.ssh/lankit"
"""

    Path("network.yml").write_text(config_template)
    console.print(f"\n[green]✓[/green] Created [bold]network.yml[/bold]")
    console.print("\nNext steps:")
    console.print("  1. Review and edit [bold]network.yml[/bold]")
    console.print("  2. [bold]lankit generate[/bold]   — render router scripts")
    console.print("  3. [bold]lankit apply[/bold]      — push to the router")
    console.print("  4. [bold]lankit provision[/bold]  — set up Pi-hole + Unbound")
