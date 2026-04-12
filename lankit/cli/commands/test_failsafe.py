import click
from lankit.cli.__main__ import cli


@cli.command(name="test-failsafe")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--seconds", "-s", type=int, default=30, show_default=True, metavar="N",
              help="Seconds before the failsafe scheduler fires (minimum 10)")
def test_failsafe(config_path, seconds):
    """Test the scheduler-based failsafe auto-revert mechanism.

    Installs a timed RouterOS scheduler job that reverts a test change
    automatically if not cancelled. This is the same mechanism lankit apply
    uses to protect against partial applies and lost connections.

    \b
    Test sequence:
      Pre-flight  — clear any leftover sentinel or scheduler from a prior run
      Phase 1     — install failsafe scheduler + add disabled sentinel rule
      Phase 2     — verify sentinel IS present (within the failsafe window)
      Phase 3     — wait for the scheduler to fire
      Phase 4     — verify sentinel is GONE (scheduler reverted it)

    \b
    Examples:
      lankit test-failsafe
      lankit test-failsafe -s 15
    """
    import time
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from pathlib import Path
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TimeElapsedColumn

    console = Console()

    if seconds < 10:
        console.print("[bold red]Error:[/bold red] --seconds must be at least 10")
        raise SystemExit(1)

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    sentinel      = "lankit-failsafe-test"
    scheduler     = "lankit-failsafe"
    find_rule     = f'/ip firewall filter print where comment="{sentinel}"'
    remove_rule   = f'/ip firewall filter remove [find comment="{sentinel}"]'
    find_sched    = f'/system scheduler print where name="{scheduler}"'

    console.print(f"[bold]Failsafe test[/bold] — router: {cfg.router.ip}")
    console.print(f"Scheduler fires in: {seconds}s\n")

    # ── Pre-flight ────────────────────────────────────────────────────────────
    console.print("[bold]Pre-flight[/bold] — clearing any leftovers from a prior run")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            rule_out, _ = conn.run_tolerant(find_rule)
            if sentinel in rule_out:
                conn.run_tolerant(f'/ip firewall filter remove [find comment="{sentinel}"]')
                console.print("  [yellow]⚠[/yellow]  Removed leftover sentinel rule")
            else:
                console.print("  ✓ No leftover sentinel rule")

            sched_out, _ = conn.run_tolerant(find_sched)
            if scheduler in sched_out:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("  [yellow]⚠[/yellow]  Removed leftover scheduler job")
            else:
                console.print("  ✓ No leftover scheduler job")
    except RouterError as e:
        console.print(f"[bold red]Router error (pre-flight):[/bold red] {e}")
        raise SystemExit(1)

    # ── Phase 1: install scheduler + sentinel ─────────────────────────────────
    console.print("\n[bold]Phase 1[/bold] — install failsafe scheduler and sentinel rule")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            conn.add_failsafe_scheduler(
                name=scheduler,
                revert_cmd=remove_rule,
                seconds=seconds,
            )
            console.print(f"  ✓ Scheduler installed (fires in {seconds}s)")

            conn.run(
                f'/ip firewall filter add chain=input action=accept disabled=yes '
                f'comment="{sentinel}" place-before=0'
            )
            console.print(f"  ✓ Sentinel rule added")

            # ── Phase 2: verify present (within the window, same connection) ──
            console.print("\n[bold]Phase 2[/bold] — verify sentinel is present during failsafe window")
            rule_out, _ = conn.run_tolerant(find_rule)
            if sentinel not in rule_out:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("[bold red]✗ FAIL[/bold red] — sentinel rule not found immediately after adding.")
                raise SystemExit(1)
            console.print("  ✓ Sentinel confirmed present on router")

            sched_out, _ = conn.run_tolerant(find_sched)
            if scheduler not in sched_out:
                console.print("[bold red]✗ FAIL[/bold red] — scheduler job not found after installing.")
                raise SystemExit(1)
            console.print("  ✓ Scheduler job confirmed present")

    except RouterError as e:
        console.print(f"[bold red]Router error (phase 1/2):[/bold red] {e}")
        raise SystemExit(1)

    # ── Phase 3: wait for scheduler to fire ───────────────────────────────────
    buffer = 5
    wait = seconds + buffer
    console.print(f"\n[bold]Phase 3[/bold] — waiting {wait}s for scheduler to fire")
    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Waiting for failsafe scheduler...", total=wait)
        for _ in range(wait):
            time.sleep(1)
            progress.advance(task)

    # ── Phase 4: verify revert ────────────────────────────────────────────────
    console.print("\n[bold]Phase 4[/bold] — verify sentinel was reverted by scheduler")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            rule_out, _ = conn.run_tolerant(find_rule)
            if sentinel in rule_out:
                conn.run_tolerant(f'/ip firewall filter remove [find comment="{sentinel}"]')
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("[bold red]✗ FAIL[/bold red] — sentinel still present after scheduler window.")
                console.print("  The scheduler did not fire. Check /system scheduler on the router.")
                raise SystemExit(1)

            conn.cancel_failsafe_scheduler(scheduler)
            console.print("[green]✓ PASS[/green] — sentinel was removed by the failsafe scheduler.")
            console.print(f"  Scheduler-based failsafe is working correctly.\n")

    except RouterError as e:
        console.print(f"[bold red]Router error (phase 4):[/bold red] {e}")
        raise SystemExit(1)
