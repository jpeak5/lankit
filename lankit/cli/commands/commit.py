import click
from lankit.cli.__main__ import cli


@cli.command(name="commit")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--message", "-m", type=str, default=None, metavar="MSG",
              help="Label for this snapshot (e.g. 'add IoT VLAN')")
def commit(config_path, message):
    """Save the current router config as a named snapshot.

    Connects to the router, exports the current running config, and saves
    it to ~/.lankit/snapshots/. Use after 'lankit apply' to checkpoint a
    known-good state you can roll back to later.

    \b
    Examples:
      lankit commit
      lankit commit --message "add IoT VLAN"
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import snapshots
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    label = message.replace(" ", "-") if message else "commit"

    console.print(f"Connecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            config_export = conn.export_config()
            snap_path = snapshots.save(cfg.router.ip, config_export, label=label)
            all_snaps = snapshots.list_snapshots(cfg.router.ip)
            console.print(f"[green]✓[/green] Snapshot saved: [bold]{snap_path.name}[/bold]")
            console.print(f"  [dim]Total snapshots: {len(all_snaps)}[/dim]")
    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)
