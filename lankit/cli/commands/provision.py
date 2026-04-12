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
    """Run Ansible to provision network hosts (Pi-hole, Unbound, etc.).

    Runs ansible/site.yml against the hosts defined in network.yml.
    Generates a temporary Ansible inventory from network.yml before running.

    \b
    Examples:
      lankit provision
      lankit provision --host dns_server
      lankit provision --tags pihole
      lankit provision --check
    """
    import subprocess
    import tempfile
    import os
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # Ansible dirs relative to cwd (lankit repo root)
    ansible_dir = Path("ansible")
    playbook = ansible_dir / "site.yml"
    if not playbook.exists():
        console.print(f"[bold red]Playbook not found:[/bold red] {playbook}")
        console.print("Expected ansible/site.yml in the lankit directory.")
        raise SystemExit(1)

    # Generate ephemeral inventory
    inventory_lines = ["[dns_server]"]
    for name, h in cfg.hosts.items():
        if not h.enabled:
            continue
        if host and name != host:
            continue
        ssh_key = str(Path(cfg.ssh_key).expanduser())
        inventory_lines.append(
            f"{h.hostname} ansible_host={h.ip} ansible_user={h.ssh_user} "
            f"ansible_ssh_private_key_file={ssh_key}"
        )
    inventory_lines.append("")

    # Pass lankit variables as extra-vars
    dns = cfg.hosts.get("dns_server")
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
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as inv_f:
        inv_f.write("\n".join(inventory_lines))
        inv_path = inv_f.name

    try:
        cmd = [
            "ansible-playbook",
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
