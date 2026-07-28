import click
from lankit.cli.__main__ import cli

from lankit.cli.commands._revert_shared import (
    FAILSAFE_NAME,
    FIRED_MARKER_NAME,
    REVERT_BUNDLE_NAME,
    build_failsafe_on_event,
)

# The sentinel is a real `lankit:`-tagged resource (design §12's G1.9
# amendment), not an arbitrarily-named rule: in --full mode the production
# remove-pass must delete it *because it is tagged*, exactly as it would
# delete any other lankit resource. /ip firewall filter is in
# revert.TAG_TABLES, so a tagged rule there is genuinely in scope.
SENTINEL_TAG = "lankit:test:sentinel"

# Default mode imports this instead of the production bundle: the real
# on-event shape, a minimal payload. Named distinctly so it can never be
# confused with lankit-revert.rsc on the router's file listing.
DRILL_SCRIPT_NAME = "lankit-drill.rsc"

DRILL_SCRIPT = (
    "# lankit test-failsafe drill payload — removes the test sentinel only.\n"
    "# Not a revert bundle: it touches no VLAN, DHCP, WiFi or firewall\n"
    "# policy. See docs/design/tag-scoped-revert.md §12 (G1.9).\n"
    f'/ip firewall filter remove [find comment="{SENTINEL_TAG}"]\n'
)


def _find_sentinel_cmd() -> str:
    return f'/ip firewall filter print where comment="{SENTINEL_TAG}"'


def _remove_sentinel_cmd() -> str:
    return f'/ip firewall filter remove [find comment="{SENTINEL_TAG}"]'


def _add_sentinel_cmd() -> str:
    return (
        f"/ip firewall filter add chain=input action=accept disabled=yes "
        f'comment="{SENTINEL_TAG}" place-before=0'
    )


def _check_log_markers(conn) -> tuple[bool, bool]:
    """(fired_logged, finished_logged) from the router's /log buffer.

    The buffer is small and volatile — a busy router can age these out
    within the drill's own window — so a missing marker is reported as
    weaker evidence, never as a failure on its own. The durable
    fired-marker (§7.2) is the authoritative signal.
    """
    out, _ = conn.run_tolerant('/log print where message~"lankit-failsafe"')
    return ("fired" in out), ("finished" in out)


def _fired_marker_state(conn) -> str | None:
    """'ok' / 'error' from the durable fired-marker's comment, or None if
    the marker isn't there (the on-event never completed)."""
    out, _ = conn.run_tolerant(
        f'/system scheduler print detail where name="{FIRED_MARKER_NAME}"'
    )
    if FIRED_MARKER_NAME not in out:
        return None
    if "lankit:failsafe-fired:error" in out:
        return "error"
    if "lankit:failsafe-fired:ok" in out:
        return "ok"
    return "unknown"


@cli.command(name="test-failsafe")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--seconds", "-s", type=int, default=30, show_default=True, metavar="N",
              help="Seconds before the failsafe scheduler fires (minimum 10)")
@click.option("--full", is_flag=True, default=False,
              help="Drill the REAL revert bundle — removes and rebuilds all lankit "
                   "state, with brief WiFi/DHCP churn. Lab routers only.")
def test_failsafe(config_path, seconds, full):
    """Rehearse the scheduler-based failsafe auto-revert mechanism.

    \b
    DEFAULT MODE — zero churn, safe on a live household network:
      Installs the real failsafe scheduler with the real on-event script
      (self-delete → import → log markers → durable fired-marker), but the
      file it imports is a one-line payload that removes a disabled test
      rule and nothing else. This proves the mechanism — the scheduler
      fires, deletes itself, triggers an import, and leaves evidence —
      without touching a single VLAN, DHCP lease, WiFi network or
      firewall policy. Nobody in the house notices.

    \b
    --full — genuine end-to-end certification, WITH disruption:
      Uploads and fires the actual revert bundle built from this router's
      known-good archive. Every lankit resource is removed and rebuilt for
      real. WiFi drops and DHCP re-issues for a few seconds. Use this on a
      lab router, or on production only when you accept the blip. Requires
      a known-good archive (i.e. at least one completed lankit apply).

    \b
    Test sequence (both modes):
      Pre-flight  — clear any leftover sentinel, scheduler or marker
      Phase 1     — upload the payload, install the scheduler, add the sentinel
      Phase 2     — verify the sentinel IS present inside the window
      Phase 3     — reschedule in place; verify start-time advances
      Phase 4     — wait for the rescheduled scheduler to fire
      Phase 5     — verify the sentinel is GONE, the fired-marker exists,
                    and (--full) that lankit state was rebuilt, not just removed

    \b
    Examples:
      lankit test-failsafe
      lankit test-failsafe -s 15
      lankit test-failsafe --full          # lab router
    """
    import re
    import time
    from lankit.core.config import load, ConfigError
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import revert
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm
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

    scheduler  = FAILSAFE_NAME
    find_rule  = _find_sentinel_cmd()
    find_sched = f'/system scheduler print where name="{scheduler}"'

    mode_name = "FULL — real revert bundle" if full else "default — zero-churn drill"
    console.print(f"[bold]Failsafe test[/bold] — router: {cfg.router.ip}")
    console.print(f"Mode: [bold]{mode_name}[/bold]")
    console.print(f"Scheduler fires in: {seconds}s\n")

    # ── --full gate ───────────────────────────────────────────────────────────
    archive = None
    if full:
        archive = revert.load_known_good(cfg.router.ip)
        if archive is None:
            console.print("[bold red]No known-good archive for this router.[/bold red]")
            console.print("  --full fires the real revert bundle, which rebuilds lankit state "
                          "from that archive — there is nothing to rebuild from yet.")
            console.print("  Run [bold]lankit apply[/bold] once first, or drop --full to run "
                          "the zero-churn drill.")
            raise SystemExit(1)
        console.print("[yellow]⚠  --full will remove and rebuild every lankit resource on "
                      f"[bold]{cfg.router.ip}[/bold] for real.[/yellow]")
        console.print("   WiFi will drop and DHCP will re-issue for a few seconds. Anyone "
                      "using the network will notice.")
        console.print(f"   Rebuilding from archive [bold]{archive.manifest.archive_id}[/bold] "
                      f"({len(archive.manifest.expected_tags)} resources).")
        if not Confirm.ask("   Proceed?", default=False):
            console.print("[dim]Cancelled — nothing was changed.[/dim]")
            raise SystemExit(0)
        console.print()

    # ── Pre-flight ────────────────────────────────────────────────────────────
    console.print("[bold]Pre-flight[/bold] — clearing any leftovers from a prior run")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            rule_out, _ = conn.run_tolerant(find_rule)
            if SENTINEL_TAG in rule_out:
                conn.run_tolerant(_remove_sentinel_cmd())
                console.print("  [yellow]⚠[/yellow]  Removed leftover sentinel rule")
            else:
                console.print("  ✓ No leftover sentinel rule")

            sched_out, _ = conn.run_tolerant(find_sched)
            if scheduler in sched_out:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("  [yellow]⚠[/yellow]  Removed leftover scheduler job")
            else:
                console.print("  ✓ No leftover scheduler job")

            # The fired-marker is durable by design — a leftover from a prior
            # run would make this run's Phase 5 pass for the wrong reason.
            if _fired_marker_state(conn) is not None:
                conn.run_tolerant(f'/system scheduler remove [find name="{FIRED_MARKER_NAME}"]')
                console.print("  [yellow]⚠[/yellow]  Removed leftover fired-marker")
            else:
                console.print("  ✓ No leftover fired-marker")
    except RouterError as e:
        console.print(f"[bold red]Router error (pre-flight):[/bold red] {e}")
        raise SystemExit(1)

    # ── Phase 1: upload payload, install scheduler, add sentinel ──────────────
    console.print("\n[bold]Phase 1[/bold] — upload payload, install scheduler, add sentinel")
    expected_tags = set()
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            if full:
                master_facts = conn.read_master_wifi_facts()
                payload = revert.build_revert_bundle(
                    archive, cfg.router.lan_interface, master_facts, fasttrack_reenable=True
                )
                import_file = REVERT_BUNDLE_NAME
                expected_tags = set(revert.expected_tags(archive))
            else:
                payload = DRILL_SCRIPT
                import_file = DRILL_SCRIPT_NAME

            payload_size = len(payload.encode("utf-8"))
            conn.upload(payload, import_file)
            if not conn.verify_uploaded_file(import_file, payload_size):
                console.print(f"[bold red]✗ FAIL[/bold red] — {import_file} upload could not "
                              f"be verified; refusing to arm the scheduler against it.")
                raise SystemExit(1)
            console.print(f"  ✓ Uploaded and verified [dim]{import_file}[/dim] "
                          f"({payload_size} bytes)")

            # The production on-event, byte for byte — only the imported
            # filename differs between this drill and a real apply.
            conn.add_failsafe_scheduler(
                name=scheduler,
                revert_cmd=build_failsafe_on_event(import_file),
                seconds=seconds,
            )
            console.print(f"  ✓ Scheduler installed with the production on-event "
                          f"(fires in {seconds}s)")

            conn.run(_add_sentinel_cmd())
            console.print(f"  ✓ Sentinel rule added [dim]({SENTINEL_TAG})[/dim]")

            # ── Phase 2: verify present (within the window, same connection) ──
            console.print("\n[bold]Phase 2[/bold] — verify sentinel is present during failsafe window")
            rule_out, _ = conn.run_tolerant(find_rule)
            if SENTINEL_TAG not in rule_out:
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

    # ── Phase 3: reschedule the scheduler in place ────────────────────────────
    # Exercises RouterConnection.reschedule_scheduler — the same primitive
    # apply.py uses to re-arm the failsafe before the Confirm prompt.
    console.print("\n[bold]Phase 3[/bold] — reschedule the scheduler in place")
    reschedule_fire_at = None
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            before, _ = conn.run_tolerant(f"{find_sched} detail")
            m = re.search(r"start-time=(\S+)", before)
            old_start = m.group(1) if m else None
            if not old_start:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("[bold red]✗ FAIL[/bold red] — could not read initial start-time.")
                raise SystemExit(1)
            console.print(f"  Initial start-time: {old_start}")

            # Let the router clock advance so the rescheduled time is provably different
            time.sleep(3)

            ok = conn.reschedule_scheduler(scheduler, seconds)
            if not ok:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print("[bold red]✗ FAIL[/bold red] — reschedule_scheduler returned False.")
                raise SystemExit(1)
            reschedule_fire_at = time.time() + seconds
            console.print("  ✓ reschedule_scheduler returned True")

            after, _ = conn.run_tolerant(f"{find_sched} detail")
            m = re.search(r"start-time=(\S+)", after)
            new_start = m.group(1) if m else None
            if new_start == old_start:
                conn.cancel_failsafe_scheduler(scheduler)
                console.print(
                    f"[bold red]✗ FAIL[/bold red] — start-time did not change "
                    f"(was {old_start}, still {new_start})."
                )
                raise SystemExit(1)
            console.print(f"  ✓ start-time advanced: {old_start} → {new_start}")

            # Negative: rescheduling an absent scheduler must return False, not raise
            if conn.reschedule_scheduler("lankit-no-such-scheduler-xyzzy", 10):
                conn.cancel_failsafe_scheduler(scheduler)
                console.print(
                    "[bold red]✗ FAIL[/bold red] — reschedule of absent scheduler returned True."
                )
                raise SystemExit(1)
            console.print("  ✓ absent-scheduler path returns False (no exception)")

    except RouterError as e:
        console.print(f"[bold red]Router error (phase 3):[/bold red] {e}")
        raise SystemExit(1)

    # ── Phase 4: wait for the rescheduled scheduler to fire ───────────────────
    buffer = 5
    wait = max(1, int(reschedule_fire_at - time.time()) + buffer)
    console.print(f"\n[bold]Phase 4[/bold] — waiting {wait}s for rescheduled scheduler to fire")
    if full:
        console.print("  [dim]WiFi and DHCP are being rebuilt during this window.[/dim]")
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

    # ── Phase 5: verify the revert happened, and left evidence ────────────────
    console.print("\n[bold]Phase 5[/bold] — verify the import ran and left evidence")
    failures = []
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            # 1. The sentinel must be gone — removed BY the imported file.
            rule_out, _ = conn.run_tolerant(find_rule)
            if SENTINEL_TAG in rule_out:
                failures.append("sentinel still present — the scheduler did not fire, or the "
                                "import failed before reaching it")
            else:
                console.print("  ✓ Sentinel was removed by the imported script")

            # 2. The scheduler must have deleted itself (one-shot property).
            sched_out, _ = conn.run_tolerant(find_sched)
            if scheduler in sched_out:
                failures.append("scheduler still installed — the self-delete in the on-event "
                                "did not run, so it could fire again")
            else:
                console.print("  ✓ Scheduler removed itself (one-shot confirmed)")

            # 3. The durable fired-marker must exist and say 'ok' (§7.2).
            marker = _fired_marker_state(conn)
            if marker is None:
                failures.append("durable fired-marker absent — the on-event did not reach "
                                "either of its completion branches")
            elif marker == "error":
                failures.append("fired-marker records an import FAILURE "
                                "(lankit:failsafe-fired:error)")
            else:
                console.print(f"  ✓ Durable fired-marker present ({marker})")

            # 4. Log markers — corroborating, never decisive: the buffer is
            #    small and a busy router can age them out inside the window.
            fired_logged, finished_logged = _check_log_markers(conn)
            if fired_logged and finished_logged:
                console.print("  ✓ Log markers present (fired + finished)")
            else:
                console.print("  [dim]○ Log markers not fully readable "
                              "(the /log buffer is short-lived — not a failure).[/dim]")

            # 5. --full only: the rebuild half must have run too. A revert
            #    that only removes is a broken revert, and removal alone
            #    would satisfy every check above.
            if full:
                live = conn.read_lankit_tags()
                verdict = revert.verify_revert(live, expected_tags)
                if verdict.ok:
                    console.print(f"  ✓ lankit state rebuilt — {len(expected_tags)} resource(s) "
                                  f"verified against the archive")
                else:
                    failures.append(
                        f"revert removed but did not fully rebuild: "
                        f"{len(verdict.missing)} missing, {len(verdict.unexpected)} unexpected"
                    )

            # Cleanup: leave the router as we found it.
            conn.run_tolerant(_remove_sentinel_cmd())
            conn.cancel_failsafe_scheduler(scheduler)
            conn.run_tolerant(f'/system scheduler remove [find name="{FIRED_MARKER_NAME}"]')

    except RouterError as e:
        console.print(f"[bold red]Router error (phase 5):[/bold red] {e}")
        raise SystemExit(1)

    if failures:
        console.print("\n[bold red]✗ FAIL[/bold red] — the failsafe did not behave as designed:")
        for f in failures:
            console.print(f"    [red]•[/red] {f}")
        console.print("\n  Check [bold]/system scheduler print[/bold] and [bold]/log print[/bold] "
                      "on the router.")
        console.print("  Do not rely on the failsafe until this passes.")
        raise SystemExit(1)

    console.print("\n[green]✓ PASS[/green] — the failsafe fired, imported, and left evidence.")
    if full:
        console.print("  End-to-end certified: the real revert bundle removed and rebuilt "
                      "lankit state on this router.\n")
    else:
        console.print("  Mechanism confirmed with zero churn. For end-to-end certification "
                      "against the real revert bundle, run [bold]--full[/bold] on a lab "
                      "router.\n")
