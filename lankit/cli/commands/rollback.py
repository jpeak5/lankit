import click
from lankit.cli.__main__ import cli


@cli.command(name="rollback")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt")
def rollback(config_path, yes):
    """Revert the router to the state before the last apply.

    Under lankit's tag-scoped revert mechanism (docs/design/
    tag-scoped-revert.md), rolling back to a snapshot means: remove every
    resource lankit owns on the router, then rebuild the lankit state that
    was live when that snapshot was captured, from the snapshot's paired
    known-good archive. The raw snapshot is never imported over a live
    config (RouterOS /import is additive — it cannot remove anything).
    The result is verified against the archive's expected tag set; a
    success mark is only printed when verification passes.

    The target is the snapshot taken just before the last apply. Running
    rollback again steps to the pre-apply snapshot before that one — each
    rollback goes further back in time, never forward onto the state you
    just undid. The target's age, label, size and what it restores are
    shown before you confirm.

    The current config is saved first so you can undo the undo with
    'lankit restore'.

    \b
    See also:
      lankit restore   — restore any specific snapshot interactively
    """
    from lankit.core.config import load, ConfigError
    from lankit.core import snapshots
    from lankit.cli.commands import _revert_shared as shared
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # Target selection (G1.5, review finding S2): walk backward through
    # pre-apply snapshots — never onto rollback's own markers or the
    # post-apply state an apply produced — so rolling back twice goes
    # further back in time, never forward onto the state just undone.
    target = snapshots.select_rollback_target(cfg.router.ip)
    if target is None:
        console.print("[yellow]No previous snapshot found.[/yellow]")
        console.print("Run [bold]lankit apply[/bold] at least once to create snapshots.")
        raise SystemExit(1)

    prev = target.snapshot.path
    status, archive = shared.resolve_paired_archive(cfg.router.ip, prev)

    # Where this target sits in the backward walk (ENH-008 #16 + S2: the
    # oscillation was invisible on screen; the depth line makes the
    # direction of travel explicit).
    walk_lines = []
    ordinal = {0: "first", 1: "second", 2: "third"}.get(target.depth, f"{target.depth + 1}th")
    if target.depth:
        walk_lines.append(
            f"Walk:     {ordinal} rollback since the last apply — stepping further back "
            f"(target {target.depth + 1} of {target.candidate_count})"
        )
    if target.at_oldest and target.candidate_count > 1:
        walk_lines.append("[yellow]This is the oldest snapshot available to roll back to — "
                          "rolling back again lands here too.[/yellow]")
    if target.fallback:
        walk_lines.append("[dim]No pre-apply snapshots on record for this router; "
                          "using the most recent non-operational snapshot.[/dim]")

    # How far back the history actually reaches (G1.8, issue #30). Rolling
    # back repeatedly walks toward this floor, and rollback itself writes a
    # pre-rollback snapshot — so the horizon is worth stating before the
    # user commits, not after they hit it.
    rot = snapshots.rotation_status(cfg.router.ip)
    if rot.oldest:
        pinned_note = f", {rot.pinned_count} pinned" if rot.pinned_count else ""
        walk_lines.append(
            f"History:  oldest snapshot on record is {shared.format_age(rot.oldest.timestamp)} "
            f"({rot.total} kept{pinned_note}) — nothing older can be rolled back to"
        )
        if rot.next_to_rotate:
            walk_lines.append("[dim]History is full: the next snapshot saved (including this "
                              "rollback's own) rotates the oldest out — 'lankit snapshots --pin' "
                              "keeps one.[/dim]")

    shared.print_snapshot_context(
        console, target.snapshot, status, archive,
        heading="Will roll back to snapshot:",
        extra_lines=walk_lines + [
            "[dim]Your current config is saved first — undo this rollback with "
            "'lankit restore'.[/dim]"
        ],
    )

    if status == "paired":
        if not yes:
            if not Confirm.ask("Proceed with rollback?"):
                console.print("[dim]Cancelled.[/dim]")
                return
    else:
        # No usable paired archive (design §9) — honest refusal path.
        # Always prompts, even with --yes: a remove-only revert is a
        # different, more drastic operation than what was asked for.
        choice = shared.prompt_unpaired_snapshot_path(prev, status, cfg, console)
        if choice != "remove-only":
            return
        archive = None

    if archive is not None:
        success_line = (
            f"[green]✓[/green] Rolled back to [bold]{prev.name}[/bold] — verified "
            f"({len(archive.manifest.expected_tags)} lankit resource(s))."
        )
    else:
        success_line = ""  # remove-only success wording is handled in the shared flow
    shared.execute_revert_operation(
        cfg, prev, archive, console,
        snap_label="pre-rollback",
        success_line=success_line,
        failure_line="[bold red]✗ Rollback did NOT complete[/bold red] — "
                     "router may be in a mixed state.",
        escalation_lines=[
            "Retry: [bold]lankit rollback[/bold], or [bold]lankit restore[/bold] "
            "to pick a different snapshot.",
            "If still wrong: the printed rollback card ([bold]lankit rollback-card[/bold]).",
        ],
    )
