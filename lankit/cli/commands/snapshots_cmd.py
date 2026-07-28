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
@click.option("--pin", "pin_idx", type=str, default=None, metavar="N|latest",
              help="Exempt a snapshot from rotation, by index (1-based) or 'latest'")
@click.option("--unpin", "unpin_idx", type=str, default=None, metavar="N|latest",
              help="Return a pinned snapshot to normal rotation")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
def snapshots(router_ip, capture, label, delete_idx, pin_idx, unpin_idx, config_path):
    """List and manage local router config snapshots.

    Snapshots are full /export verbose outputs saved to ~/.lankit/snapshots/
    whenever lankit apply or lankit rollback runs. Use --capture to take one
    on demand.

    Up to 10 unpinned snapshots are kept per router; older ones rotate out as
    new ones arrive. Since every apply writes two, that is roughly five
    applies of history. Use --pin to hold a specific snapshot out of that
    rotation indefinitely — pinning raises the total kept rather than
    evicting something else to make room.

    \b
    Examples:
      lankit snapshots
      lankit snapshots --capture
      lankit snapshots --capture --label before-experiment
      lankit snapshots --pin 3
      lankit snapshots --unpin latest
      lankit snapshots --delete latest
      lankit snapshots --delete 2
    """
    from lankit.core import snapshots as snap
    from lankit.cli.commands._revert_shared import format_age
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

    def _resolve(selector):
        """Turn an 'N'|'latest' selector into a Snapshot, or exit with a
        message. Shared by --delete, --pin and --unpin."""
        if not snaps:
            console.print(f"[yellow]No snapshots found for {router_ip}.[/yellow]")
            raise SystemExit(0)
        if selector == "latest":
            return snaps[-1]
        try:
            idx = int(selector)
        except ValueError:
            console.print(f"[bold red]Invalid index:[/bold red] {selector!r} — use a number or 'latest'")
            raise SystemExit(1)
        if idx < 1 or idx > len(snaps):
            console.print(f"[bold red]Index out of range:[/bold red] {idx} (have {len(snaps)} snapshots)")
            raise SystemExit(1)
        return snaps[idx - 1]

    if delete_idx is not None:
        target = _resolve(delete_idx)
        console.print(f"  Deleting [dim]{target.path.name}[/dim] …")
        snap.delete(router_ip, target.path)
        console.print(f"  [green]✓[/green] Deleted.")
        return

    if pin_idx is not None or unpin_idx is not None:
        want_pinned = pin_idx is not None
        target = _resolve(pin_idx if want_pinned else unpin_idx)
        if snap.set_pinned(router_ip, target.path, want_pinned):
            if want_pinned:
                console.print(f"  [green]✓[/green] Pinned [dim]{target.path.name}[/dim] — "
                              f"it will not rotate out.")
                console.print("  [dim]It can still be removed explicitly with "
                              "[bold]lankit snapshots --delete[/bold].[/dim]")
            else:
                console.print(f"  [green]✓[/green] Unpinned [dim]{target.path.name}[/dim] — "
                              f"back in normal rotation.")
        else:
            state = "already pinned" if want_pinned else "not pinned"
            console.print(f"  [dim]No change — {target.path.name} is {state}.[/dim]")
        return

    # List mode
    snapshots_dir = Path.home() / ".lankit" / "snapshots"
    console.print(f"\n[bold]Snapshots[/bold] — {router_ip}")
    console.print(f"[dim]  Location: {snapshots_dir}[/dim]")
    status = snap.rotation_status(router_ip)
    console.print(f"[dim]  Kept: up to {status.max_unpinned} unpinned per router "
                  f"(pinned snapshots are kept on top of that)[/dim]\n")

    if not snaps:
        console.print("  [dim]No snapshots yet. Run [bold]lankit snapshots --capture[/bold] to take one.[/dim]\n")
        return

    # The File column was dropped when Age and Pin were added (G1.8): the
    # filename is <router>-<timestamp>-<label>.rsc, so it carried nothing the
    # first three columns don't, and keeping it truncated everything else to
    # ellipses. Every command that acts on a snapshot selects it by index.
    table = Table(box=box.SIMPLE_HEAD, show_lines=False, padding=(0, 1))
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Timestamp (UTC)", no_wrap=True)
    table.add_column("Label", style="bold cyan", no_wrap=True)
    table.add_column("Age", style="dim", no_wrap=True)
    table.add_column("Pin", justify="center", no_wrap=True)
    table.add_column("Size", justify="right", style="dim", no_wrap=True)

    # Which row the next save would evict: the oldest unpinned one, and only
    # when the history is actually full. Located by position rather than by
    # path — two snapshots saved in the same second share a filename (#57),
    # and a path comparison would flag every one of them.
    doomed_idx = None
    if status.next_to_rotate:
        doomed_idx = next(i for i, s in enumerate(snaps, 1) if not s.pinned)

    for i, s in enumerate(snaps, 1):
        ts_str = s.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        marker = " [dim]← latest[/dim]" if i == len(snaps) else ""
        if s.pinned:
            pin_cell = "[green]📌[/green]"
        elif i == doomed_idx:
            pin_cell = "[yellow]↻[/yellow]"
        else:
            pin_cell = ""
        table.add_row(
            str(i),
            ts_str + marker,
            s.label,
            format_age(s.timestamp),
            pin_cell,
            f"{s.size_kb} KB",
        )

    console.print(table)
    console.print(f"  {len(snaps)} snapshot(s) — {status.pinned_count} pinned, "
                  f"{status.unpinned_count}/{status.max_unpinned} rotating.")
    if status.next_to_rotate:
        console.print(f"  [yellow]↻[/yellow] The next snapshot saved will rotate out "
                      f"[dim]{status.next_to_rotate.path.name}[/dim] "
                      f"({format_age(status.next_to_rotate.timestamp)}). "
                      f"Keep it with: [bold]lankit snapshots --pin {doomed_idx}[/bold]")
    console.print(f"  To delete: [bold]lankit snapshots --delete N[/bold]\n")
