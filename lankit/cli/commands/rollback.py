import click
from lankit.cli.__main__ import cli


@cli.command(name="rollback")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
def rollback(config_path, yes):
    """Revert the router to the previous config snapshot.

    Restores the snapshot saved before the last 'lankit apply', effectively
    undoing the most recent provisioning run. The current config is saved
    before reverting so you can undo the undo with 'lankit restore'.

    \b
    See also:
      lankit restore   — restore any specific snapshot interactively
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import snapshots
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    prev = snapshots.previous(cfg.router.ip)
    if not prev:
        console.print("[yellow]No previous snapshot found.[/yellow]")
        console.print("Run [bold]lankit apply[/bold] at least once to create snapshots.")
        raise SystemExit(1)

    console.print(f"Will restore: [bold]{prev.name}[/bold]")

    if not yes:
        confirmed = Confirm.ask("Proceed with rollback?")
        if not confirmed:
            console.print("[dim]Cancelled.[/dim]")
            return

    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            # Save current state before rolling back
            current = conn.export_config()
            saved = snapshots.save(cfg.router.ip, current, label="pre-rollback")
            console.print(f"  Saved current config: [dim]{saved.name}[/dim]")

            # Restore
            content = prev.read_text()
            conn.upload(content, "lankit-rollback.rsc")
            out, err = conn.run_tolerant("/import file=lankit-rollback.rsc")
            if err:
                console.print(f"  [yellow]Warning:[/yellow] {err.strip()}")

            console.print(f"[green]✓[/green] Rolled back to [bold]{prev.name}[/bold]")
    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)
