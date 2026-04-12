import click
from lankit.cli.__main__ import cli


@cli.command(name="extend")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
def extend(config_path):
    """Interactive wizard to add a new network segment to network.yml.

    Walks through the required fields for a new segment, validates for
    conflicts (VLAN ID, subnet), and writes the updated network.yml.
    Does not apply changes — run 'lankit apply' after.

    \b
    Workflow:
      lankit extend       # fill in the wizard
      lankit generate     # preview the generated scripts
      lankit apply        # push to router
    """
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Prompt, Confirm
    from ruamel.yaml import YAML

    console = Console()

    config_file = Path(config_path) if config_path else Path("network.yml")

    try:
        cfg = load(config_file)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    console.print("\n[bold]Add a new network segment[/bold]\n")

    # ── Collect inputs ────────────────────────────────────────────────────────
    existing_names = set(cfg.segments.keys())
    existing_vlans = {s.vlan_id for s in cfg.segments.values()}
    existing_subnets = {s.subnet for s in cfg.segments.values()}

    # Segment name
    while True:
        name = Prompt.ask("Segment name (e.g. servers, cameras, lab)").strip().lower()
        if not name.isidentifier():
            console.print("[red]Name must be a valid identifier (letters, digits, underscore)[/red]")
            continue
        if name in existing_names:
            console.print(f"[red]Segment '{name}' already exists[/red]")
            continue
        break

    # VLAN ID
    while True:
        vlan_str = Prompt.ask("VLAN ID (1-4094)")
        try:
            vlan_id = int(vlan_str)
            if not (1 <= vlan_id <= 4094):
                raise ValueError
        except ValueError:
            console.print("[red]VLAN ID must be 1-4094[/red]")
            continue
        if vlan_id in existing_vlans:
            console.print(f"[red]VLAN {vlan_id} is already used[/red]")
            continue
        break

    # Subnet
    while True:
        subnet = Prompt.ask("Subnet (e.g. 10.10.50.0/24)")
        if subnet in existing_subnets:
            console.print(f"[red]Subnet {subnet} is already used[/red]")
            continue
        # Basic format check
        import re
        if not re.match(r'^\d+\.\d+\.\d+\.\d+/\d+$', subnet):
            console.print("[red]Expected format: x.x.x.0/prefix[/red]")
            continue
        break

    comment = Prompt.ask("Short description", default=f"{name.capitalize()} devices")

    # WiFi
    has_wifi = Confirm.ask("Does this segment have WiFi?", default=False)
    ssid = None
    wifi_bands = []
    ssid_hidden = False
    if has_wifi:
        ssid = Prompt.ask("SSID", default=f"{cfg.household_name}-{name}")
        bands_input = Prompt.ask("WiFi bands", default="2ghz,5ghz")
        wifi_bands = [b.strip() for b in bands_input.split(",")]
        ssid_hidden = Confirm.ask("Hide SSID?", default=False)

    # DNS
    dns = Prompt.ask("DNS", choices=["filtered", "unfiltered", "none"], default="filtered")
    force_dns = Confirm.ask("Force DNS (redirect all port 53 traffic to Pi-hole)?", default=(dns == "filtered"))

    # Internet
    internet = Prompt.ask("Internet access", choices=["full", "egress_only", "none"], default="full")

    # Isolation
    client_isolation = Confirm.ask("Client isolation (prevent device-to-device traffic)?", default=False)

    # Bandwidth
    bw_up = None
    bw_down = None
    if Confirm.ask("Set bandwidth limits?", default=False):
        bw_up_str = Prompt.ask("Upload limit (e.g. 10M, leave blank for none)", default="")
        bw_down_str = Prompt.ask("Download limit (e.g. 50M, leave blank for none)", default="")
        bw_up = bw_up_str if bw_up_str else None
        bw_down = bw_down_str if bw_down_str else None

    # ── Preview ───────────────────────────────────────────────────────────────
    console.print(f"\n[bold]New segment:[/bold]")
    console.print(f"  name:             {name}")
    console.print(f"  vlan_id:          {vlan_id}")
    console.print(f"  subnet:           {subnet}")
    console.print(f"  ssid:             {ssid or '(none)'}")
    console.print(f"  dns:              {dns}")
    console.print(f"  force_dns:        {force_dns}")
    console.print(f"  internet:         {internet}")
    console.print(f"  client_isolation: {client_isolation}")

    if not Confirm.ask("\nAdd this segment to network.yml?", default=True):
        console.print("[dim]Cancelled.[/dim]")
        return

    # ── Write to network.yml ──────────────────────────────────────────────────
    _yaml = YAML()
    _yaml.preserve_quotes = True
    with open(config_file) as f:
        raw = _yaml.load(f)

    raw["segments"][name] = {
        "vlan_id": vlan_id,
        "comment": comment,
        "subnet": subnet,
        "ssid": ssid,
        "wifi_bands": wifi_bands,
        "ssid_hidden": ssid_hidden,
        "bandwidth_up": bw_up,
        "bandwidth_down": bw_down,
        "dns": dns,
        "force_dns": force_dns,
        "internet": internet,
        "client_isolation": client_isolation,
    }

    with open(config_file, "w") as f:
        _yaml.dump(raw, f)

    console.print(f"\n[green]✓[/green] Added segment [bold]{name}[/bold] to {config_file}")
    console.print("\nNext steps:")
    console.print(f"  lankit generate   # preview scripts")
    console.print(f"  lankit apply      # push to router")
