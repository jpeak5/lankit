import click
from lankit.cli.__main__ import cli


@cli.command(name="snapshots")
@click.option("--router", "-r", "router_ip", type=str, default=None, metavar="IP",
              help="Router IP (default: from network.yml)")
@click.option("--capture", is_flag=True, default=False,
              help="Connect to the router and save the current config as a new snapshot")
@click.option("--label", "-l", type=str, default="manual", metavar="LABEL",
              help="Label for --capture snapshot  [default: manual]")
@click.option("--delete", "-d", "delete_idx", type=str, default=None, metavar="N|latest",
              help="Delete snapshot by index (1-based) or 'latest'")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
def snapshots(router_ip, capture, label, delete_idx, config_path):
    """List and manage local router config snapshots.

    Snapshots are full /export verbose outputs saved to ~/.lankit/snapshots/
    whenever lankit apply or lankit rollback runs. Up to 10 are kept per router.
    Use --capture to take one on demand.

    \b
    Examples:
      lankit snapshots
      lankit snapshots --capture
      lankit snapshots --capture --label before-experiment
      lankit snapshots --delete latest
      lankit snapshots --delete 2
    """
    from lankit.core import snapshots as snap
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    # Resolve router IP (and keep cfg for ssh key / user)
    cfg = None
    if not router_ip:
        try:
            cfg = load(Path(config_path) if config_path else None)
            router_ip = cfg.router.ip
        except ConfigError as e:
            console.print(f"[bold red]Config error:[/bold red] {e}")
            raise SystemExit(1)

    # ── Capture mode ──────────────────────────────────────────────────────────
    if capture:
        if cfg is None:
            try:
                cfg = load(Path(config_path) if config_path else None)
            except ConfigError as e:
                console.print(f"[bold red]Config error:[/bold red] {e}")
                raise SystemExit(1)
        console.print(f"\nConnecting to [bold]{router_ip}[/bold]...")
        try:
            with RouterConnection(router_ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
                config_export = conn.export_config()
            snap_path = snap.save(router_ip, config_export, label=label)
            console.print(f"[green]✓[/green] Snapshot saved: [dim]{snap_path}[/dim]\n")
        except RouterError as e:
            console.print(f"[bold red]Router error:[/bold red] {e}")
            raise SystemExit(1)
        return

    snaps = snap.list_metadata(router_ip)

    if delete_idx is not None:
        if not snaps:
            console.print(f"[yellow]No snapshots found for {router_ip}.[/yellow]")
            raise SystemExit(0)

        if delete_idx == "latest":
            target = snaps[-1]
        else:
            try:
                idx = int(delete_idx)
            except ValueError:
                console.print(f"[bold red]Invalid index:[/bold red] {delete_idx!r} — use a number or 'latest'")
                raise SystemExit(1)
            if idx < 1 or idx > len(snaps):
                console.print(f"[bold red]Index out of range:[/bold red] {idx} (have {len(snaps)} snapshots)")
                raise SystemExit(1)
            target = snaps[idx - 1]

        console.print(f"  Deleting [dim]{target.path.name}[/dim] …")
        snap.delete(router_ip, target.path)
        console.print(f"  [green]✓[/green] Deleted.")
        return

    # List mode
    snapshots_dir = Path.home() / ".lankit" / "snapshots"
    console.print(f"\n[bold]Snapshots[/bold] — {router_ip}")
    console.print(f"[dim]  Location: {snapshots_dir}[/dim]")
    console.print(f"[dim]  Kept: up to 10 per router[/dim]\n")

    if not snaps:
        console.print("  [dim]No snapshots yet. Run [bold]lankit snapshots --capture[/bold] to take one.[/dim]\n")
        return

    table = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Timestamp (UTC)", no_wrap=True)
    table.add_column("Label", style="bold cyan")
    table.add_column("Size", justify="right", style="dim")
    table.add_column("File", style="dim")

    for i, s in enumerate(snaps, 1):
        ts_str = s.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        marker = " [dim]← latest[/dim]" if i == len(snaps) else ""
        table.add_row(
            str(i),
            ts_str + marker,
            s.label,
            f"{s.size_kb} KB",
            s.path.name,
        )

    console.print(table)
    console.print(f"  {len(snaps)} snapshot(s). To delete: [bold]lankit snapshots --delete N[/bold]\n")
