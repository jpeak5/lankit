import click
from lankit.cli.__main__ import cli


@cli.command(name="apply")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--no-generate", is_flag=True, default=False,
              help="Skip re-generating scripts (use existing ansible/generated/)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print what would be applied without connecting to the router")
@click.option("--script", "-s", type=str, default=None, metavar="NAME",
              help="Apply only this script (e.g. 03-firewall.rsc)")
@click.option("--segment", type=str, default=None, metavar="NAME",
              help="Provision only this segment (omits global rules like NAT, default-deny)")
def apply(config_path, no_generate, dry_run, script, segment):
    """Apply generated RouterOS scripts to the router.

    Workflow:
      1. Optionally regenerate scripts from network.yml
      2. Save a pre-apply config snapshot (for rollback)
      3. Upload and import each .rsc script on the router
      4. Prompt to keep or revert changes

    If you disconnect or decline, the router retains the changes.
    Use 'lankit rollback' to revert to the pre-apply snapshot.

    With --segment NAME, only that segment's VLAN, DHCP, firewall rules, WiFi,
    DNS entry, and bandwidth queue are generated and applied. Global rules
    (NAT, default-deny, VLAN filtering, hide-master-wifi, etc.) are omitted.
    The failsafe still runs — the pre-apply snapshot includes all previously
    provisioned segments, so a revert won't undo earlier work.
    Recommended order: admin → trusted → servers → iot → others → lankit apply.

    \b
    Examples:
      lankit apply
      lankit apply --script 03-firewall.rsc
      lankit apply --dry-run
      lankit apply --segment iot
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.generator import generate_all
    from lankit.core.passwords import load_wifi_passwords
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import snapshots
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm
    import time

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # Validate --segment early so we fail fast before connecting
    if segment and segment not in cfg.segments:
        known = ", ".join(cfg.segments.keys())
        console.print(f"[bold red]Unknown segment:[/bold red] {segment!r}  (known: {known})")
        raise SystemExit(1)

    # Load WiFi passwords before generating — prompts interactively if source=prompt
    try:
        wifi_passphrases = load_wifi_passwords(cfg)
    except ConfigError as e:
        console.print(f"[bold red]Password error:[/bold red] {e}")
        raise SystemExit(1)

    generated_dir = Path("ansible/generated")

    # ── Step 1: Generate ─────────────────────────────────────────────────────
    if not no_generate:
        if segment:
            console.print(f"[dim]Generating segment-only scripts for [bold]{segment}[/bold]...[/dim]")
        else:
            console.print("[dim]Generating scripts from network.yml...[/dim]")
        try:
            generate_all(cfg, generated_dir, wifi_passphrases, segment_filter=segment)
        except Exception as e:
            console.print(f"[bold red]Generate failed:[/bold red] {e}")
            raise SystemExit(1)

    # Determine which scripts to apply
    if script:
        scripts = [generated_dir / script]
        if not scripts[0].exists():
            console.print(f"[bold red]Script not found:[/bold red] {scripts[0]}")
            raise SystemExit(1)
    else:
        scripts = sorted(generated_dir.glob("*.rsc"))
        if not scripts:
            console.print("[yellow]No .rsc scripts found in ansible/generated/[/yellow]")
            raise SystemExit(1)

    if dry_run:
        mode = f"segment [bold]{segment}[/bold]" if segment else "all segments"
        console.print(f"[bold]Dry run:[/bold] would apply {len(scripts)} script(s) ({mode}) to {cfg.router.ip}:")
        for s in scripts:
            console.print(f"  [dim]{s.name}[/dim]")
        return

    failsafe_secs = cfg.failsafe_seconds
    failsafe_name = "lankit-failsafe"
    scheduler_installed = False
    apply_start = None

    # ── Step 2: Connect and snapshot ─────────────────────────────────────────
    mode_label = f"segment [bold]{segment}[/bold]" if segment else "all segments"
    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            identity = conn.identity()
            version = conn.version()
            console.print(f"  Connected: [bold]{identity}[/bold]  RouterOS {version}")

            console.print("  Saving pre-apply snapshot...")
            pre_config = conn.export_config()
            snap_label = f"pre-apply-{segment}" if segment else "pre-apply"
            snap_path = snapshots.save(cfg.router.ip, pre_config, label=snap_label)
            console.print(f"  Snapshot: [dim]{snap_path}[/dim]")

            # ── Step 3: Install failsafe, upload and import scripts ───────────
            # RouterOS SFTP root is the flash filesystem — no /tmp, no subdirs.
            # Upload bare filenames; /import finds them in the flash root.
            # The pre-apply snapshot includes all previously provisioned segments,
            # so the failsafe is safe in --segment mode — a revert won't undo
            # earlier work, only the current segment's additions.
            # Self-deleting one-shot: the scheduler removes itself before
            # importing, so it cannot fire a second time even if cancel is
            # delayed or the session dies at an unlucky moment.
            revert_cmd = (
                f'/system scheduler remove [find name="{failsafe_name}"];'
                f' /import file=lankit-restore.rsc'
            )
            console.print(f"  Installing failsafe scheduler ({failsafe_secs}s)...")
            conn.upload(pre_config, "lankit-restore.rsc")
            conn.add_failsafe_scheduler(failsafe_name, revert_cmd, failsafe_secs)
            scheduler_installed = True
            apply_start = time.time()

            console.print(f"\nApplying {len(scripts)} script(s) ({mode_label}):")
            import_error = None
            for rsc in scripts:
                console.print(f"  → [cyan]{rsc.name}[/cyan]")
                content = rsc.read_text()
                conn.upload(content, rsc.name)
                out, err = conn.run_tolerant(f"/import file={rsc.name}")
                if err:
                    err_lower = err.lower()
                    # RouterOS emits informational lines on stderr in some versions;
                    # only treat output as a hard failure on unambiguous error indicators.
                    _INFO_PATTERNS = (
                        "loading file",
                        "script file loaded",
                        "warning",
                    )
                    _ERROR_PATTERNS = (
                        "bad command",
                        "syntax error",
                        "no such item",
                        "expected",
                        "failure",
                        "error",
                    )
                    is_info = any(p in err_lower for p in _INFO_PATTERNS)
                    is_error = any(p in err_lower for p in _ERROR_PATTERNS)
                    if is_error and not is_info:
                        import_error = (rsc.name, err.strip())
                        break
                    else:
                        console.print(f"    [yellow]Warning:[/yellow] {err.strip()}")

            if import_error:
                script_name, err_msg = import_error
                console.print(f"\n[bold red]Import error in {script_name}:[/bold red] {err_msg}")
                console.print("[red]Cancelling failsafe and restoring pre-apply snapshot...[/red]")
                conn.cancel_failsafe_scheduler(failsafe_name)
                _restore_snapshot(conn, snap_path, console)
                console.print("[yellow]✓[/yellow] Restored. No changes were kept.")
                raise SystemExit(1)

            # ── Step 4: Confirm ───────────────────────────────────────────────
            elapsed = int(time.time() - apply_start) if apply_start else 0
            remaining = max(0, failsafe_secs - elapsed)
            console.print()
            console.print(f"[dim]Failsafe active — router will auto-revert in "
                          f"~{remaining}s if this session ends.[/dim]")
            try:
                keep = Confirm.ask(
                    f"[bold]Keep these changes?[/bold] "
                    f"(router will retain them; 'no' rolls back to snapshot)"
                )
            except KeyboardInterrupt:
                console.print("\n[yellow]Apply interrupted.[/yellow]")
                console.print("  Attempting to cancel failsafe scheduler...")
                cancelled = conn.cancel_failsafe_scheduler(failsafe_name)
                if cancelled:
                    console.print("  [green]✓[/green] Failsafe disarmed. Router retains applied changes.")
                    console.print("  Run [bold]lankit rollback[/bold] to undo if needed.")
                else:
                    console.print(f"  [yellow]⚠[/yellow] Failsafe may still be active — "
                                  f"router will auto-revert in ≤{remaining}s.")
                    console.print(f"  Verify: ssh {cfg.router.ssh_user}@{cfg.router.ip} "
                                  f"'/system scheduler print'")
                raise SystemExit(0)

            if keep:
                cancelled = conn.cancel_failsafe_scheduler(failsafe_name)
                # Save post-apply snapshot
                post_config = conn.export_config()
                post_label = f"post-apply-{segment}" if segment else "post-apply"
                post_snap = snapshots.save(cfg.router.ip, post_config, label=post_label)
                if cancelled:
                    console.print(f"\n[green]✓[/green] Changes applied. Failsafe disarmed.")
                else:
                    console.print(f"\n[green]✓[/green] Changes applied.")
                    console.print(f"  [yellow]⚠[/yellow] Failsafe scheduler may still be active.")
                    console.print(f"  Verify: ssh {cfg.router.ssh_user}@{cfg.router.ip} "
                                  f"'/system scheduler print'")
                console.print(f"  Post-apply snapshot: [dim]{post_snap}[/dim]")
                console.print("  Run [bold]lankit rollback[/bold] to undo if needed.")
            else:
                cancelled = conn.cancel_failsafe_scheduler(failsafe_name)
                if not cancelled:
                    console.print(f"  [yellow]⚠[/yellow] Failsafe may still be active — "
                                  f"will auto-revert in ≤{remaining}s regardless.")
                console.print("\nRolling back to pre-apply snapshot...")
                _restore_snapshot(conn, snap_path, console)
                console.print("[yellow]✓[/yellow] Rolled back.")

    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        if scheduler_installed:
            console.print(f"  [dim]Failsafe scheduler is active — router will "
                          f"auto-revert in ≤{failsafe_secs}s.[/dim]")
        else:
            console.print("  [dim]Failsafe was not installed — use "
                          "[bold]lankit rollback[/bold] to restore manually.[/dim]")
        raise SystemExit(1)


def _restore_snapshot(conn, snap_path, console):
    """Upload and import a snapshot file onto the router."""
    try:
        content = snap_path.read_text()
    except (FileNotFoundError, PermissionError, IsADirectoryError) as e:
        console.print(f"  [bold red]Restore failed:[/bold red] Cannot read {snap_path}: {e}")
        console.print("  Manual intervention required.")
        console.print("  Run [bold]lankit rollback[/bold] to restore from a known-good snapshot.")
        raise SystemExit(1)
    conn.upload(content, "lankit-restore.rsc")
    out, err = conn.run_tolerant("/import file=lankit-restore.rsc")
    if err:
        console.print(f"  [yellow]Warning during restore:[/yellow] {err.strip()}")
