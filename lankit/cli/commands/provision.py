import click
from lankit.cli.__main__ import cli


@cli.command(name="provision")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--host", "-H", type=str, default=None, metavar="NAME",
              help="Provision only this host (e.g. dns_server)")
@click.option("--tags", "-t", type=str, default=None, metavar="TAGS",
              help="Ansible tags to run (comma-separated)")
@click.option("--check", is_flag=True, default=False,
              help="Dry-run: check what would change without applying")
@click.option("--verbose", "-v", is_flag=True, default=False,
              help="Pass -v to ansible-playbook")
def provision(config_path, host, tags, check, verbose):
    """Run Ansible to provision network hosts.

    Provisions all enabled hosts defined in network.yml:
      - dns_server: Pi-hole (ad blocking) + Unbound (recursive DNS, DNSSEC)
      - app_server: Caddy web server + portal pages (me/apps/register.internal)
        — only runs if hosts.app_server.enabled is true in network.yml

    Generates a temporary Ansible inventory from network.yml before running.
    Hosts are grouped by their services: list, so a single Pi running both
    pihole and caddy will appear in both groups.

    \b
    Examples:
      lankit provision
      lankit provision --host dns_server
      lankit provision --host app_server
      lankit provision --tags pihole
      lankit provision --check
    """
    import shutil
    import subprocess
    import sys
    import tempfile
    import os
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console

    def _find_bin(name: str) -> str:
        venv_bin = Path(sys.executable).parent / name
        if venv_bin.exists():
            return str(venv_bin)
        found = shutil.which(name)
        if found:
            return found
        raise SystemExit(f"{name} not found — is ansible-core installed?")

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    from lankit.core.passwords import read_vault
    vault = read_vault()

    # Ansible dirs relative to cwd (lankit repo root)
    ansible_dir = Path("ansible")
    playbook = ansible_dir / "site.yml"
    if not playbook.exists():
        console.print(f"[bold red]Playbook not found:[/bold red] {playbook}")
        console.print("Expected ansible/site.yml in the lankit directory.")
        raise SystemExit(1)

    # Map services to Ansible groups — supports single-box (all services on one host)
    # and multi-box (concerns separated by hardware) equally.
    _SERVICE_GROUP = {
        "pihole":  "dns_server",
        "unbound": "dns_server",
        "caddy":   "app_server",
        "portal":  "app_server",
    }

    ssh_key = str(Path(cfg.ssh_key).expanduser())
    groups: dict[str, dict] = {}  # group → {host_name: Host}
    for name, h in cfg.hosts.items():
        # --host flag overrides the enabled guard (allows provisioning a host
        # that is still marked enabled: false while being set up for the first time)
        if not h.enabled and name != host:
            continue
        if host and name != host:
            continue
        for svc in h.services:
            group = _SERVICE_GROUP.get(svc)
            if group:
                groups.setdefault(group, {})[name] = h

    inventory_lines = []
    for group, hosts in groups.items():
        inventory_lines.append(f"[{group}]")
        for name, h in hosts.items():
            inventory_lines.append(
                f"{name} ansible_host={h.ip} ansible_user={h.ssh_user} "
                f"ansible_ssh_private_key_file={ssh_key}"
            )
        inventory_lines.append("")

    # Pass lankit variables as extra-vars
    dns = cfg.hosts.get("dns_server")
    app = cfg.hosts.get("app_server")
    extra_vars = {
        "lankit_dns_server_ip":      dns.ip if dns else "",
        "lankit_dns_server_gateway": _dns_gateway(cfg),
        "lankit_internal_domain":   cfg.internal_domain,
        "lankit_privacy_level":     _privacy_level_int(cfg.privacy.query_logging),
        "lankit_query_retention":   cfg.privacy.query_retention,
        "lankit_dnssec":            str(cfg.dnssec).lower(),
        "lankit_block_apple_relay": str(cfg.privacy.apple_private_relay == "block").lower(),
        "lankit_dns_hosts":         _build_dns_hosts(cfg),
        "lankit_ssh_public_key":    _read_public_key(cfg.ssh_key),
        "lankit_portals":           cfg.portals,
        "lankit_app_server_ip":     app.ip if app and app.enabled else "",
        "household_name":           cfg.household_name,
        "lankit_pihole_password":   vault.get("pihole_password", ""),
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as inv_f:
        inv_f.write("\n".join(inventory_lines))
        inv_path = inv_f.name

    try:
        cmd = [
            _find_bin("ansible-playbook"),
            str(playbook),
            "-i", inv_path,
            "--extra-vars", _format_extra_vars(extra_vars),
        ]
        if tags:
            cmd += ["--tags", tags]
        if check:
            cmd += ["--check", "--diff"]
        if verbose:
            cmd += ["-v"]
        if host:
            cmd += ["--limit", host]

        console.print(f"[dim]Running: {' '.join(cmd)}[/dim]\n")
        result = subprocess.run(cmd, cwd=str(ansible_dir.parent))
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    finally:
        os.unlink(inv_path)


def _dns_gateway(cfg) -> str:
    import ipaddress
    dns = cfg.hosts.get("dns_server")
    if not dns:
        return ""
    subnet = cfg.segments[dns.segment].subnet
    return str(ipaddress.ip_network(subnet, strict=False).network_address + 1)


def _privacy_level_int(query_logging: str) -> int:
    return {"full": 0, "anonymous": 1, "none": 3}.get(query_logging, 0)


_PORTAL_SUBDOMAINS = {
    "device":       "me",
    "network":      "network",
    "registration": "register",
}


def _build_dns_hosts(cfg) -> list[str]:
    """Build list of 'IP hostname' pairs for Pi-hole local DNS."""
    lines = []
    domain = cfg.internal_domain
    # Segment gateways
    for name, seg in cfg.segments.items():
        lines.append(f"{seg.gateway} {name}.{domain}")
    # Named hosts (use FQDN)
    for name, h in cfg.hosts.items():
        if h.enabled:
            lines.append(f"{h.ip} {h.hostname}.{domain}")
    # Portal subdomains — each enabled portal gets its own DNS name on app_server
    app = cfg.hosts.get("app_server")
    if app and app.enabled:
        for portal, subdomain in _PORTAL_SUBDOMAINS.items():
            if cfg.portals.get(portal):
                lines.append(f"{app.ip} {subdomain}.{domain}")
    return lines


def _read_public_key(private_key_path: str) -> str:
    from pathlib import Path
    pub = Path(str(Path(private_key_path).expanduser()) + ".pub")
    if not pub.exists():
        raise SystemExit(f"SSH public key not found: {pub}")
    return pub.read_text().strip()


def _format_extra_vars(d: dict) -> str:
    """Format a dict as an ansible --extra-vars JSON string."""
    import json
    return json.dumps(d)
