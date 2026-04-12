import click
from lankit.cli.__main__ import cli


@cli.command(name="matrix")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
def matrix(config_path):
    """Show the segment-to-segment connectivity matrix.

    Displays a grid of every segment pair and whether connections are
    allowed (✓), denied (—), or isolated (iso). Derived entirely from
    network.yml permissions — no router connection required.

    This is the ground truth for what lankit will configure. Use
    'lankit probe' (coming) to verify it against the live router.

    \b
    Examples:
      lankit matrix
    """
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    segments = list(cfg.segments.keys())
    permissions = cfg.permissions

    # Build allow set: (src, dst) → allowed
    allowed: set[tuple[str, str]] = set()
    for src, perm in permissions.items():
        for dst in perm.can_reach:
            allowed.add((src, dst))

    # Column headers: segments + internet
    cols = segments + ["internet"]

    table = Table(
        title=f"{cfg.household_name} — Connectivity Matrix",
        box=box.SIMPLE_HEAD,
        show_lines=False,
        caption="✓ allowed  —  denied  iso  client isolation active",
    )
    table.add_column("from \\ to", style="bold cyan", no_wrap=True)
    for col in cols:
        table.add_column(col, justify="center", no_wrap=True)

    for src in segments:
        row = [src]
        seg = cfg.segments[src]
        for dst in cols:
            if dst == src:
                row.append("[dim]·[/dim]")
            elif dst == "internet":
                inet = seg.internet
                if inet == "full":
                    row.append("[green]✓[/green]")
                elif inet == "egress_only":
                    row.append("[yellow]out[/yellow]")
                else:
                    row.append("[dim]—[/dim]")
            elif (src, dst) in allowed:
                dst_seg = cfg.segments[dst]
                if dst_seg.client_isolation:
                    row.append("[yellow]✓[/yellow]")  # allowed but dest has client iso
                else:
                    row.append("[green]✓[/green]")
            else:
                row.append("[dim]—[/dim]")
        table.add_row(*row)

    console.print()
    console.print(table)
    console.print(
        "[dim]Use [bold]lankit probe[/bold] to verify this matrix against your live router.[/dim]\n"
    )
