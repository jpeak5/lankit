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
    """Restore the router to the state a specific snapshot captured.

    Under lankit's tag-scoped revert mechanism (docs/design/
    tag-scoped-revert.md), restoring a snapshot means: remove every
    resource lankit owns on the router, then rebuild the lankit state that
    was live when that snapshot was captured, from the snapshot's paired
    known-good archive. The raw snapshot is never imported over a live
    config (RouterOS /import is additive — it cannot remove anything).
    The result is verified against the archive's expected tag set; a
    success mark is only printed when verification passes.

    Without arguments, shows an interactive list of available snapshots.
    Pass a snapshot file path to restore directly.

    \b
    Examples:
      lankit restore                          # interactive picker
      lankit restore --list                   # list without restoring
      lankit restore ~/.lankit/snapshots/x.rsc
    """
    from lankit.core.config import load, ConfigError
    from lankit.core import snapshots
    from lankit.cli.commands import _revert_shared as shared
    from datetime import datetime, timezone
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

    def _snapshot_table(title: str) -> Table:
        table = Table(title=title, box=box.SIMPLE_HEAD)
        table.add_column("#", justify="right", style="dim")
        table.add_column("File")
        table.add_column("Size")
        table.add_column("Restorable state")
        for i, p in enumerate(all_snaps, 1):
            size = f"{p.stat().st_size // 1024} KB"
            aid = snapshots.archive_id_for(cfg.router.ip, p)
            basis = f"known-good archive [dim]{aid}[/dim]" if aid else "[yellow]unpaired[/yellow]"
            table.add_row(str(i), p.name, size, basis)
        return table

    # ── List mode ─────────────────────────────────────────────────────────────
    if list_only:
        if not all_snaps:
            console.print("[yellow]No snapshots found.[/yellow]")
            return
        console.print(_snapshot_table("Snapshots"))
        return

    # ── Determine target snapshot ─────────────────────────────────────────────
    if snapshot_file:
        target = Path(snapshot_file)
    elif all_snaps:
        console.print(_snapshot_table("Available Snapshots"))
        choice = Prompt.ask("Restore which snapshot?", default=str(len(all_snaps)))
        try:
            target = all_snaps[int(choice) - 1]
        except (ValueError, IndexError):
            console.print("[red]Invalid selection.[/red]")
            raise SystemExit(1)
    else:
        console.print("[yellow]No snapshots found. Run lankit apply first.[/yellow]")
        raise SystemExit(1)

    status, archive = shared.resolve_paired_archive(cfg.router.ip, target)

    # Same ENH-008 (#16) context block rollback shows — age, label meaning,
    # size, pairing — so both commands answer "what am I converging to?"
    # the same way. A path given on the command line may not be in the
    # index at all; fall back to file metadata for it.
    meta = next((s for s in snapshots.list_metadata(cfg.router.ip) if s.path == target), None)
    if meta is None:
        meta = snapshots.Snapshot(
            path=target, router_ip=cfg.router.ip,
            timestamp=datetime.fromtimestamp(target.stat().st_mtime, tz=timezone.utc),
            label=target.stem,
        )
    shared.print_snapshot_context(console, meta, status, archive,
                                  heading="Will restore snapshot:")

    if status == "paired":
        if not yes:
            if not Confirm.ask("Proceed?"):
                console.print("[dim]Cancelled.[/dim]")
                return
    else:
        # No usable paired archive (design §9) — honest refusal path.
        # Always prompts, even with --yes: a remove-only revert is a
        # different, more drastic operation than what was asked for.
        choice = shared.prompt_unpaired_snapshot_path(target, status, cfg, console)
        if choice != "remove-only":
            return
        archive = None

    if archive is not None:
        success_line = (
            f"[green]✓[/green] Restored [bold]{target.name}[/bold] — verified "
            f"({len(archive.manifest.expected_tags)} lankit resource(s))."
        )
    else:
        success_line = ""  # remove-only success wording is handled in the shared flow
    shared.execute_revert_operation(
        cfg, target, archive, console,
        snap_label="pre-restore",
        success_line=success_line,
        failure_line="[bold red]✗ Restore did NOT complete[/bold red] — "
                     "router may be in a mixed state.",
        escalation_lines=[
            "Retry: [bold]lankit restore[/bold] (this or a different snapshot), "
            "or [bold]lankit rollback[/bold].",
            "If still wrong: the printed rollback card ([bold]lankit rollback-card[/bold]).",
        ],
    )
