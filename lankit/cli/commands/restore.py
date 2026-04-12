import click
from lankit.cli.__main__ import cli


@cli.command(name="restore")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.argument("snapshot_file", type=click.Path(exists=True), required=False)
@click.option("--list", "list_only", is_flag=True, default=False,
              help="List available snapshots without restoring")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
def restore(config_path, snapshot_file, list_only, yes):
    """Restore the router from a specific snapshot file.

    Without arguments, shows an interactive list of available snapshots.
    Pass a snapshot file path to restore directly.

    \b
    Examples:
      lankit restore                          # interactive picker
      lankit restore --list                   # list without restoring
      lankit restore ~/.lankit/snapshots/x.rsc
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import snapshots
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.prompt import Prompt, Confirm

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    all_snaps = snapshots.list_snapshots(cfg.router.ip)

    # ── List mode ─────────────────────────────────────────────────────────────
    if list_only:
        if not all_snaps:
            console.print("[yellow]No snapshots found.[/yellow]")
            return
        table = Table(title="Snapshots", box=box.SIMPLE_HEAD)
        table.add_column("#", justify="right", style="dim")
        table.add_column("File")
        table.add_column("Size")
        for i, p in enumerate(all_snaps, 1):
            size = f"{p.stat().st_size // 1024} KB"
            table.add_row(str(i), p.name, size)
        console.print(table)
        return

    # ── Determine target snapshot ─────────────────────────────────────────────
    if snapshot_file:
        target = Path(snapshot_file)
    elif all_snaps:
        if not all_snaps:
            console.print("[yellow]No snapshots found. Run lankit apply first.[/yellow]")
            raise SystemExit(1)
        table = Table(title="Available Snapshots", box=box.SIMPLE_HEAD)
        table.add_column("#", justify="right", style="dim")
        table.add_column("File")
        table.add_column("Size")
        for i, p in enumerate(all_snaps, 1):
            size = f"{p.stat().st_size // 1024} KB"
            table.add_row(str(i), p.name, size)
        console.print(table)
        choice = Prompt.ask("Restore which snapshot?", default=str(len(all_snaps)))
        try:
            target = all_snaps[int(choice) - 1]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            raise SystemExit(1)
    else:
        console.print("[yellow]No snapshots found. Run lankit apply first.[/yellow]")
        raise SystemExit(1)

    console.print(f"\nWill restore: [bold]{target.name}[/bold]")
    if not yes:
        confirmed = Confirm.ask("Proceed?")
        if not confirmed:
            console.print("[dim]Cancelled.[/dim]")
            return

    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            current = conn.export_config()
            saved = snapshots.save(cfg.router.ip, current, label="pre-restore")
            console.print(f"  Saved current config: [dim]{saved.name}[/dim]")

            content = target.read_text()
            conn.upload(content, "lankit-restore.rsc")
            out, err = conn.run_tolerant("/import file=lankit-restore.rsc")
            if err:
                console.print(f"  [yellow]Warning:[/yellow] {err.strip()}")

            console.print(f"[green]✓[/green] Restored from [bold]{target.name}[/bold]")
    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)
