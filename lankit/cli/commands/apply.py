import re
import sys

import click
from lankit.core.generator import generated_dir as _generated_dir
from lankit.cli.__main__ import cli

# Shared with rollback/restore (G1.4) — classify_import_error and
# check_failsafe_fired_marker were extracted from this module mechanically
# in G1.4; the aliases keep this module's names (and its tests) unchanged.
from lankit.cli.commands._revert_shared import (
    FAILSAFE_NAME as _FAILSAFE_NAME,
    FIRED_MARKER_NAME as _FIRED_MARKER_NAME,
    build_failsafe_on_event as _build_failsafe_on_event,
    check_failsafe_fired_marker as _check_failsafe_fired_marker,
    classify_import_error as _classify_import_error,
    import_failure as _import_failure,
    preflight_router as _preflight_router,
    current_archive_id as _current_archive_id,
    print_verdict_evidence as _print_verdict_evidence,
)


def _archive_update_plan(segment, script, prior_archive, all_segment_names):
    """Decide (segments_applied, globals_applied) for the known-good
    archive after a kept apply, or None if this apply shouldn't touch the
    archive at all. Pure — no I/O — so it's unit-testable on its own.

    - --script mode never updates the archive: one script's output isn't
      a coherent, self-contained state on its own (design doesn't cover
      this case explicitly; treating it conservatively).
    - A full apply (no --segment) always covers every segment and always
      includes globals — it supersedes any prior partial coverage.
    - A --segment apply extends the prior archive's segment coverage
      (union, design §3's "segments_applied := previous ∪ {X}"), keeping
      whatever globals_applied state the prior archive had — a segment
      apply never touches globals itself.
    """
    if script:
        return None
    if segment is None:
        return sorted(all_segment_names), True
    prior_segments = set(prior_archive.manifest.segments_applied) if prior_archive else set()
    prior_globals = prior_archive.manifest.globals_applied if prior_archive else False
    return sorted(prior_segments | {segment}), prior_globals


# ── Import residue and the apply log (G1.7, issue #32) ───────────────────────
#
# G1.3 made *classified* import errors fatal. What was left over is the
# residue: stderr lines the classifier doesn't recognise as fatal, which were
# printed once as a yellow warning mid-scroll and then forgotten — the user
# reached "Keep? y" with no reminder that anything had complained at all.
# These collect that residue for a consolidated summary, and persist every
# script's raw stdout/stderr so a diagnosis isn't limited to what fit on
# screen.


def _format_apply_log(router_ip, mode_label, entries, timestamp):
    """Render one apply run as an appendable plain-text log section.

    entries: [(script_name, stdout, stderr), ...] in import order.

    Deliberately plain and greppable rather than structured — its reader is
    a person diagnosing a half-applied router, often over SSH. Pure — no
    I/O — so it's unit-testable on its own.
    """
    lines = [
        "═" * 72,
        f"lankit apply — {timestamp}",
        f"router: {router_ip}   mode: {mode_label}   scripts: {len(entries)}",
        "═" * 72,
    ]
    for name, out, err in entries:
        lines.append(f"\n─── {name} ───")
        lines.append("--- stdout ---")
        lines.append((out or "").rstrip() or "(empty)")
        lines.append("--- stderr ---")
        lines.append((err or "").rstrip() or "(empty)")
    return "\n".join(lines) + "\n"


def _write_apply_log(generated_dir, router_ip, mode_label, entries):
    """Append this run's import output to ansible/generated/apply.log.

    Appends rather than overwrites: an incremental segment-by-segment
    build-out is several applies, and the useful diagnosis is usually the
    sequence, not the last run alone.

    Returns the Path written, or None if writing failed — logging must
    never be able to break an apply that otherwise succeeded.
    """
    from datetime import datetime, timezone

    try:
        generated_dir.mkdir(parents=True, exist_ok=True)
        path = generated_dir / "apply.log"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with path.open("a", encoding="utf-8") as f:
            f.write(_format_apply_log(router_ip, mode_label, entries, stamp))
        return path
    except OSError:
        return None


def _print_residue_summary(residue, log_path, console):
    """Consolidated recap of every non-fatal stderr line, printed directly
    before the keep-prompt so it is the last thing the user reads before
    deciding (issue #32's core complaint: the warnings scrolled past)."""
    console.print()
    console.print(f"[yellow]⚠  {len(residue)} script(s) wrote unclassified messages to stderr:[/yellow]")
    for name, msg in residue:
        console.print(f"  [cyan]{name}[/cyan]")
        for line in msg.splitlines():
            if line.strip():
                console.print(f"    [yellow]{line.strip()}[/yellow]")
    console.print("  [dim]These were not recognised as fatal, so the apply continued — but the "
                  "router may be only partially configured.[/dim]")
    if log_path:
        console.print(f"  [dim]Full output: {log_path}[/dim]")
    console.print("  [dim]The prompt below defaults to 'no' because of this.[/dim]")


# ── Global-rule pre-flight for --segment mode (G3.3, issue #20) ──────────────

_GLOBAL_MARKER = "lankit:fw:all:default-deny"


def _globals_present(conn):
    """Is the global firewall policy live on this router? True / False /
    None when it couldn't be determined (the print itself errored).
    """
    out, err = conn.run_tolerant("/ip firewall filter print detail without-paging")
    if err:
        return None
    return _GLOBAL_MARKER in out


def _segment_next_steps(cfg_segment_names, segments_applied):
    """The 'what's left' line after a --segment apply: which segments have
    no coverage in the known-good archive yet. Pure — no I/O."""
    return [n for n in cfg_segment_names if n not in set(segments_applied)]


# ── Post-apply probe (G1.6, issue #1) ────────────────────────────────────────
#
# The failsafe window used to be the only verification window: the user
# answered "Keep? y" on nothing but the absence of an import error, and the
# safety net came down at that instant. These run the cheap reachability
# checks *while the failsafe is still armed*, so a broken apply can still be
# declined rather than kept and rolled back afterwards.

_DNS_PROBE_NAME = "mikrotik.com"
_RESOLVED_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _probe_plan(cfg, segment):
    """What a post-apply health check should test: (gateways, dns_ip).

    A --segment apply only claims to have brought up that one segment, so
    only its gateway is a fair test — the others may legitimately not exist
    yet during an incremental build-out (admin → trusted → servers → ...).
    A full or --script apply is answerable for every segment.

    dns_ip is returned only when a lankit-managed resolver is configured
    (hosts.dns_server). When cfg.dns_ip is a public resolver instead, this
    apply cannot have broken it and checking it would only add noise.

    Pure — no I/O — so it's unit-testable on its own.
    """
    if segment is not None:
        gateways = [(segment, cfg.segments[segment].gateway)]
    else:
        gateways = [(name, seg.gateway) for name, seg in cfg.segments.items()]
    dns_host = cfg.hosts.get("dns_server")
    return gateways, (dns_host.ip if dns_host else None)


def _probe_verdict(gateway_results, dns_result):
    """Turn raw probe observations into (ok, summary, failures).

    gateway_results: [(name, gateway, ok, detail), ...]
    dns_result:      (ok, dns_ip, detail) or None when DNS wasn't checked.

    Pure — no I/O — so it's unit-testable on its own.
    """
    total = len(gateway_results)
    reachable = sum(1 for _, _, ok, _ in gateway_results if ok)
    failures = [
        f"gateway {name} ({gw}) unreachable — {detail}"
        for name, gw, ok, detail in gateway_results if not ok
    ]
    parts = [f"{reachable}/{total} gateways reachable"] if total else []
    if dns_result is not None:
        dns_ok, dns_ip, detail = dns_result
        parts.append(f"DNS {dns_ip} {'answered' if dns_ok else 'did not answer'}")
        if not dns_ok:
            failures.append(f"DNS server {dns_ip} did not resolve {_DNS_PROBE_NAME} — {detail}")
    return (not failures), ", ".join(parts), failures


def _resolve_check(conn, dns_ip):
    """Ask the configured resolver to resolve a name, from the router.

    Pinging the resolver's IP is deliberately *not* what this does. The DNS
    outage this project actually hit (2026-07-20) was a hung Pi-hole FTL
    process: the host answered ICMP the whole time while every lookup
    timed out. Only a real lookup distinguishes the two.

    Returns (ok, detail). A failure here can also mean WAN is down rather
    than DNS being misconfigured — the caller's wording says so.
    """
    out, err = conn.run_tolerant(f":put [:resolve {_DNS_PROBE_NAME} server={dns_ip}]", timeout=15)
    if _RESOLVED_IP_RE.search(out or ""):
        return True, out.strip().splitlines()[-1].strip()
    first_err = (err or "").strip().splitlines()
    return False, (first_err[0] if first_err else "no answer")


def _run_post_apply_probe(conn, cfg, segment, console):
    """Ping each applied segment's gateway from the router, then (when a
    lankit-managed resolver is configured) ask that resolver to resolve a
    name. Prints per-check results and a summary line; returns True when
    everything answered."""
    from lankit.cli.commands.probe import _ping_gateway

    gateways, dns_ip = _probe_plan(cfg, segment)
    console.print("\n[dim]Post-apply health check (failsafe still armed)...[/dim]")

    gateway_results = []
    for name, gw in gateways:
        ok, detail = _ping_gateway(conn, gw)
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {mark} gateway {name:<14} {gw:<16} [dim]{detail}[/dim]")
        gateway_results.append((name, gw, ok, detail))

    dns_result = None
    if dns_ip:
        ok, detail = _resolve_check(conn, dns_ip)
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {mark} DNS     {dns_ip:<14} {_DNS_PROBE_NAME:<16} [dim]{detail}[/dim]")
        dns_result = (ok, dns_ip, detail)

    ok, summary, failures = _probe_verdict(gateway_results, dns_result)
    if ok:
        console.print(f"  [green]✓[/green] {summary}")
        return True

    console.print(f"  [yellow]⚠[/yellow] {summary}")
    for line in failures:
        console.print(f"    [red]•[/red] {line}")
    if any(not r[2] for r in gateway_results):
        console.print("    [dim]Unreachable gateways usually mean a VLAN interface didn't come "
                      "up — check 01-vlans.rsc imported cleanly.[/dim]")
    if dns_result and not dns_result[0]:
        console.print("    [dim]A DNS failure can also mean the resolver host is down or WAN is "
                      "out, not that this apply broke anything.[/dim]")
    return False


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
@click.option("--skip-probe", is_flag=True, default=False,
              help="Skip the post-apply health check and disarm the failsafe immediately")
@click.option("--strict", is_flag=True, default=False,
              help="Treat ANY import stderr as fatal — revert immediately instead of warning")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip pre-flight confirmations. Does NOT skip the keep/revert prompt.")
def apply(config_path, no_generate, dry_run, script, segment, skip_probe, strict, yes):
    """Apply generated RouterOS scripts to the router.

    \b
    RECOMMENDED ORDER (first-time build-out):
      1. lankit apply --segment admin      ← keep your own access working first
      2. lankit apply --segment trusted
      3. lankit apply --segment servers
      4. lankit apply --segment iot        ← ...and any remaining segments
      5. lankit apply                      ← full apply adds the global rules
    Segments are NOT isolated from each other until step 5 lands NAT,
    default-deny and VLAN filtering. apply warns if you are still there.

    \b
    Workflow:
      1. Optionally regenerate scripts from network.yml
      2. Save a pre-apply config snapshot (for human inspection/diffing)
      3. Build and upload a revert bundle — the failsafe's target if
         anything goes wrong (see 'lankit rollback' / docs/design/
         tag-scoped-revert.md)
      4. Upload and import each .rsc script on the router, logging all
         import output to ansible/generated/apply.log
      5. Recap anything the imports wrote to stderr, then prompt to keep
         or revert changes (the prompt defaults to 'no' if there was any)
      6. Health-check the result (gateway pings, DNS lookup) while the
         failsafe is still armed, and re-prompt if anything failed
         (--skip-probe skips this)

    Errors RouterOS reports in a recognised form abort the apply and revert
    immediately. Unrecognised stderr is reported but not fatal — use
    --strict to treat any stderr at all as fatal instead.

    If you disconnect or decline, the router reverts to the last known-good
    state (or, on a first-ever apply, to a bare/flat state) automatically.
    Use 'lankit rollback' to undo a kept apply after the fact.

    With --segment NAME, only that segment's VLAN, DHCP, firewall rules, WiFi,
    DNS entry, and bandwidth queue are generated and applied. Global rules
    (NAT, default-deny, VLAN filtering, hide-master-wifi, etc.) are omitted.
    The failsafe still runs — a revert converges to the last known-good
    archive, so it won't undo segments applied in earlier runs.
    Recommended order: admin → trusted → servers → iot → others → lankit apply.

    \b
    Examples:
      lankit apply
      lankit apply --script 03-firewall.rsc
      lankit apply --dry-run
      lankit apply --segment iot
      lankit apply --strict
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.config import _resolve_path as _resolve_config_path
    from lankit.core.generator import generate_all, render_texts
    from lankit.core.passwords import load_wifi_passwords
    from lankit.core.router import RouterConnection, RouterError
    from lankit.core import snapshots, revert
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm
    import hashlib
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

    generated_dir = _generated_dir(cfg)

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

    # Config-file hash for the archive manifest — informational only, doesn't
    # gate anything in this unit. Uses config.py's own path resolution so it
    # hashes exactly the file load() just read (respects LANKIT_CONFIG too).
    network_yml_hash = hashlib.sha256(
        _resolve_config_path(Path(config_path) if config_path else None).read_bytes()
    ).hexdigest()[:16]

    failsafe_secs = cfg.failsafe_seconds
    scheduler_installed = False
    failsafe_fire_at = None

    # ── Step 2: Connect ──────────────────────────────────────────────────────
    mode_label = f"segment [bold]{segment}[/bold]" if segment else "all segments"
    mode_plain = f"segment {segment}" if segment else "all segments"   # for apply.log
    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            identity = conn.identity()
            version = conn.version()
            console.print(f"  Connected: [bold]{identity}[/bold]  RouterOS {version}")

            _check_failsafe_fired_marker(conn, console)
            _preflight_router(conn, console, version)
            _check_untagged(conn, [s.read_text() for s in scripts], console)
            if segment is None and any(s.name == "01-vlans.rsc" for s in scripts):
                _check_host_ports(conn, cfg, console)

            # ── Global-rule pre-flight (G3.3) ──────────────────────────────────
            # A --segment apply brings up that segment's VLAN, DHCP and WiFi
            # but emits no global rules, so until a full apply lands the
            # segment is reachable and *not* isolated. That is the normal,
            # documented state during an incremental build-out — this says so
            # rather than treating it as a mistake. Runs before anything is
            # written, so declining costs nothing.
            if segment:
                globals_live = _globals_present(conn)
                if globals_live is False:
                    console.print()
                    console.print("[yellow]⚠  Global rules are not on this router yet[/yellow] "
                                  "(no default-deny found).")
                    console.print(f"   [bold]{segment}[/bold] will come up with a working VLAN, "
                                  "DHCP and WiFi, but segments are [bold]not isolated from each "
                                  "other[/bold] and internet access may not work until the global "
                                  "rules land.")
                    console.print("   That is expected mid-build-out: finish the segments, then "
                                  "run a full [bold]lankit apply[/bold] to add NAT, default-deny "
                                  "and VLAN filtering.")
                    if not yes and not Confirm.ask("   Continue with this segment?", default=True):
                        console.print("[dim]Cancelled — nothing was changed.[/dim]")
                        raise SystemExit(0)
                elif globals_live is None:
                    console.print("  [dim]Could not read the firewall table to check for global "
                                  "rules — continuing.[/dim]")

            console.print("  Saving pre-apply snapshot...")
            pre_config = conn.export_config()
            snap_label = f"pre-apply-{segment}" if segment else "pre-apply"
            # Pair the pre-apply snapshot with the archive current *before*
            # this operation (design §8) — None on a first apply / an
            # un-bootstrapped router.
            # Rotation warning *before* the save (G1.8, issue #30): every
            # apply writes two snapshots, so history quietly ran out after
            # about five applies. Warning first means the snapshot named is
            # still there to be pinned.
            rot = snapshots.rotation_status(cfg.router.ip)
            if rot.next_to_rotate:
                console.print(f"  [yellow]↻[/yellow] Snapshot history is full "
                              f"({rot.unpinned_count}/{rot.max_unpinned}) — this apply will "
                              f"rotate out [dim]{rot.next_to_rotate.path.name}[/dim].")
                console.print("    [dim]To keep it, cancel and run: "
                              "lankit snapshots --pin 1[/dim]")

            snap_path = snapshots.save(cfg.router.ip, pre_config, label=snap_label,
                                       archive_id=_current_archive_id(cfg.router.ip))
            console.print(f"  Snapshot: [dim]{snap_path}[/dim]")

            # ── Step 3: Build and upload the revert bundle ─────────────────────
            # Everything the failsafe needs must be on the router BEFORE the
            # scheduler installs and before any apply script imports — design
            # §6. The bundle targets the current known-good archive (or, on a
            # genuine first apply / a not-yet-bootstrapped existing router,
            # a remove-only revert to a bare/flat state).
            archive = revert.load_known_good(cfg.router.ip)
            master_facts = conn.read_master_wifi_facts()
            if master_facts is None:
                console.print("  [dim]Master WiFi state not captured — a revert would leave "
                              "WiFi masters hidden (wired recovery unaffected).[/dim]")

            if archive is None:
                archive = _migration_bootstrap(conn, cfg, wifi_passphrases, segment, network_yml_hash, console)
                # The pre-apply snapshot above was saved before the bootstrap
                # existed, so it paired to None. The bootstrap archive
                # describes exactly the state that snapshot captured — pair
                # them now (design §8), or rollback would offer the honest
                # refusal path for a snapshot that does have a usable
                # archive. Fills a missing pairing only; nothing else in
                # apply's flow changes. (G1.5 secondary item.)
                if archive is not None:
                    snapshots.repair_pairing(cfg.router.ip, snap_path, archive.manifest.archive_id)

            # fasttrack_reenable: 03-firewall.rsc disables the defconf
            # fasttrack rule in place (untagged, design §5.3); the revert
            # must re-enable it.
            bundle = revert.build_revert_bundle(
                archive, cfg.router.lan_interface, master_facts, fasttrack_reenable=True,
                host_ports=revert.host_ports_to_restore(cfg, archive),
            )
            bundle_size = len(bundle.encode("utf-8"))
            console.print("  Preparing revert bundle...")
            conn.upload(bundle, "lankit-revert.rsc")
            if not conn.verify_uploaded_file("lankit-revert.rsc", bundle_size):
                console.print("[bold red]Revert bundle upload could not be verified.[/bold red]")
                console.print("  Refusing to arm the failsafe against an unconfirmed file — aborting "
                              "before any changes were made.")
                raise SystemExit(1)

            # ── Install failsafe, upload and import scripts ────────────────────
            # RouterOS SFTP root is the flash filesystem — no /tmp, no subdirs.
            # Upload bare filenames; /import finds them in the flash root.
            # The on-event script itself lives in _revert_shared so
            # `lankit test-failsafe` rehearses this exact shape (G1.9).
            revert_cmd = _build_failsafe_on_event()
            console.print(f"  Installing failsafe scheduler ({failsafe_secs}s)...")
            conn.add_failsafe_scheduler(_FAILSAFE_NAME, revert_cmd, failsafe_secs)
            scheduler_installed = True
            failsafe_fire_at = time.time() + failsafe_secs

            console.print(f"\nApplying {len(scripts)} script(s) ({mode_label}):")
            import_error = None
            strict_tripped = False
            import_log = []   # every script attempted: (name, stdout, stderr)
            residue = []      # non-fatal stderr, recapped before the prompt (G1.7)
            for rsc in scripts:
                console.print(f"  → [cyan]{rsc.name}[/cyan]")
                content = rsc.read_text()
                conn.upload(content, rsc.name)
                out, err = conn.run_tolerant(f"/import file={rsc.name}")
                import_log.append((rsc.name, out, err))
                failure = _import_failure(out, err)
                if failure:
                    # 7.x reports /import halts on stdout (#66): never treat
                    # a "Script Error:" there as success.
                    import_error = (rsc.name, failure)
                    break
                if err:
                    if strict:
                        # --strict: the classifier's benefit of the doubt is
                        # exactly what issue #32 distrusts, so opt out of it.
                        strict_tripped = True
                        import_error = (rsc.name, err.strip())
                        break
                    else:
                        residue.append((rsc.name, err.strip()))
                        console.print(f"    [yellow]Warning:[/yellow] {err.strip()}")

            log_path = _write_apply_log(generated_dir, cfg.router.ip, mode_plain, import_log)
            if log_path:
                console.print(f"  [dim]Import log: {log_path}[/dim]")

            expected = revert.expected_tags(archive)

            if import_error:
                script_name, err_msg = import_error
                if strict_tripped:
                    console.print(f"\n[bold red]--strict: {script_name} wrote to stderr:[/bold red] {err_msg}")
                else:
                    console.print(f"\n[bold red]Import error in {script_name}:[/bold red] {err_msg}")
                console.print("[red]Cancelling failsafe and reverting to last known-good state...[/red]")
                conn.cancel_failsafe_scheduler(_FAILSAFE_NAME)
                _revert_and_verify(conn, expected, console)
                raise SystemExit(1)

            # ── Step 4: Confirm ─────────────────────────────────────────────────
            # Reset the failsafe so the user gets the full window to decide,
            # not the remainder after uploads ate part of it.
            rescheduled = conn.reschedule_scheduler(_FAILSAFE_NAME, failsafe_secs)
            if rescheduled:
                failsafe_fire_at = time.time() + failsafe_secs
            remaining = max(0, int(failsafe_fire_at - time.time()))
            console.print()
            if rescheduled:
                console.print(f"[yellow]⏱  Failsafe re-armed — {remaining}s to decide. "
                              f"Router auto-reverts if this session ends.[/yellow]")
            else:
                console.print(f"[yellow]⏱  Failsafe active — ~{remaining}s remain. "
                              f"(Could not re-arm; deciding under remainder of original window.)[/yellow]")
            def _ask_keep(question, **kwargs):
                """Confirm.ask with the shared Ctrl-C handling. Interrupting
                at either keep-prompt means the same thing — the user walked
                away mid-decision — so both must try to disarm rather than
                leave a live failsafe behind unannounced."""
                try:
                    return Confirm.ask(question, **kwargs)
                except KeyboardInterrupt:
                    remaining = max(0, int(failsafe_fire_at - time.time()))
                    console.print("\n[yellow]Apply interrupted.[/yellow]")
                    console.print("  Attempting to cancel failsafe scheduler...")
                    cancelled = conn.cancel_failsafe_scheduler(_FAILSAFE_NAME)
                    if cancelled:
                        console.print("  [green]✓[/green] Failsafe disarmed. Router retains applied changes.")
                        console.print("  Run [bold]lankit rollback[/bold] to undo if needed.")
                    else:
                        console.print(f"  [yellow]⚠[/yellow] Failsafe may still be active — "
                                      f"router will auto-revert in ≤{remaining}s.")
                        console.print(f"  Verify: ssh {cfg.router.ssh_user}@{cfg.router.ip} "
                                      f"'/system scheduler print'")
                    raise SystemExit(0)

            # Residue recap immediately before the prompt (G1.7): whatever
            # complained during the import is the last thing read before the
            # decision, and the decision defaults to reverting.
            if residue:
                _print_residue_summary(residue, log_path, console)

            keep = _ask_keep(
                f"[bold]Keep these changes?[/bold] "
                f"(router will retain them; 'no' reverts to last known-good state)",
                **({"default": False} if residue else {}),
            )

            # ── Post-apply probe (G1.6) ────────────────────────────────────────
            # Deliberately between the answer and the disarm: the whole point
            # is that a failed check can still be declined into the revert
            # path, with the failsafe never having come down.
            if keep and not skip_probe:
                if not _run_post_apply_probe(conn, cfg, segment, console):
                    # Probing spent part of the window — give a full one back
                    # before asking the user to re-decide.
                    if conn.reschedule_scheduler(_FAILSAFE_NAME, failsafe_secs):
                        failsafe_fire_at = time.time() + failsafe_secs
                    remaining = max(0, int(failsafe_fire_at - time.time()))
                    console.print(f"\n[yellow]⏱  Failsafe still armed — ~{remaining}s to decide.[/yellow]")
                    keep = _ask_keep(
                        "[bold]Some checks failed. Keep anyway?[/bold] "
                        "('no' reverts to last known-good state)",
                        default=False,
                    )

            if keep:
                cancelled = conn.cancel_failsafe_scheduler(_FAILSAFE_NAME)
                conn.run_tolerant(f'/system scheduler remove [find name="{_FIRED_MARKER_NAME}"]')
                post_config = conn.export_config()
                post_label = f"post-apply-{segment}" if segment else "post-apply"

                new_archive_id = None
                plan = _archive_update_plan(segment, script, archive, cfg.segments.keys())
                if plan is not None:
                    segs_applied, globals_applied = plan
                    try:
                        if segment is None and script is None:
                            archive_texts = {p.name: p.read_text() for p in scripts}
                        else:
                            archive_texts = render_texts(
                                cfg, wifi_passphrases,
                                segment_filter=set(segs_applied), include_globals=globals_applied,
                            )
                        new_archive_id = revert.save_known_good(
                            cfg.router.ip, archive_texts,
                            segments_applied=segs_applied, globals_applied=globals_applied,
                            network_yml_hash=network_yml_hash, lan_interface=cfg.router.lan_interface,
                            host_ports=list(cfg.host_ports()),
                        )
                    except Exception as e:
                        console.print(f"  [yellow]⚠[/yellow] Could not save known-good archive: {e}")
                        console.print("  [yellow]Applied changes are still live — only the safety net "
                                      "for the *next* apply is affected.[/yellow]")
                elif script:
                    console.print("  [dim]--script mode: known-good archive not updated "
                                  "(a single script isn't a self-contained state).[/dim]")

                post_snap = snapshots.save(cfg.router.ip, post_config, label=post_label, archive_id=new_archive_id)
                if cancelled:
                    console.print(f"\n[green]✓[/green] Changes applied. Failsafe disarmed.")
                else:
                    console.print(f"\n[green]✓[/green] Changes applied.")
                    console.print(f"  [yellow]⚠[/yellow] Failsafe scheduler may still be active.")
                    console.print(f"  Verify: ssh {cfg.router.ssh_user}@{cfg.router.ip} "
                                  f"'/system scheduler print'")
                console.print(f"  Post-apply snapshot: [dim]{post_snap}[/dim]")

                # Next steps after an incremental segment apply (G3.3): where
                # the build-out stands and what still has to happen for the
                # segmentation to actually be enforced.
                if segment:
                    covered = plan[0] if plan is not None else [segment]
                    remaining = _segment_next_steps(cfg.segments.keys(), covered)
                    console.print()
                    if remaining:
                        console.print(f"  [bold]Next:[/bold] {len(remaining)} segment(s) not yet "
                                      f"applied — [cyan]{', '.join(remaining)}[/cyan]")
                        console.print(f"    [dim]lankit apply --segment {remaining[0]}[/dim]")
                    if not globals_live:
                        console.print("  [bold]Then:[/bold] run a full [bold]lankit apply[/bold] "
                                      "to add the global rules (NAT, default-deny, VLAN "
                                      "filtering) — until then segments are not isolated.")
                    elif not remaining:
                        console.print("  [dim]All segments applied and global rules are live.[/dim]")

                console.print("  Run [bold]lankit rollback[/bold] to undo if needed.")
            else:
                cancelled = conn.cancel_failsafe_scheduler(_FAILSAFE_NAME)
                if not cancelled:
                    remaining = max(0, int(failsafe_fire_at - time.time()))
                    console.print(f"  [yellow]⚠[/yellow] Failsafe may still be active — "
                                  f"will auto-revert in ≤{remaining}s regardless.")
                console.print("\nReverting to last known-good state...")
                _revert_and_verify(conn, expected, console)

    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        if scheduler_installed:
            console.print(f"  [dim]Failsafe scheduler is active — router will import "
                          f"the last known-good state in ≤{failsafe_secs}s.[/dim]")
        else:
            console.print("  [dim]Failsafe was not installed — use "
                          "[bold]lankit rollback[/bold] to restore manually.[/dim]")
        raise SystemExit(1)


def _migration_bootstrap(conn, cfg, wifi_passphrases, segment, network_yml_hash, console):
    """No known-good archive exists yet for this router. Distinguishes a
    genuine first-ever apply (nothing live, nothing to bootstrap — return
    None, proceed with a remove-only failsafe as normal) from an existing
    lankit-provisioned router that predates this archive mechanism
    (design §9's migration case).

    For an existing full-apply-only router: compare live tags against a
    full render of the current network.yml. If they match, offer to
    record the current state as known-good. If they don't, refuse to
    proceed silently — the user must either accept an explicit remove-only
    failsafe for this one apply, or investigate first with 'lankit probe'.

    For --segment mode on an unarchived existing router, the "matches a
    full render" comparison doesn't apply (a segment-scoped apply never
    matches a full render) — warn, then require the same interactive
    confirmation as every other remove-only path.
    """
    from lankit.core import revert
    from lankit.core.generator import render_texts
    from rich.prompt import Confirm

    live_tags = conn.read_lankit_tags()
    if not live_tags:
        return None  # genuine first apply — nothing to bootstrap

    if segment is not None:
        console.print(
            "[yellow]Note:[/yellow] existing lankit configuration found but no known-good "
            "archive is on record for this router yet (it predates lankit's revert-safety "
            "upgrade). If the failsafe fires during this segment apply, it will revert to "
            "a bare/flat state, not preserve other already-applied segments."
        )
        _confirm_remove_only_failsafe(console)
        return None

    expected_now = revert.expected_tags_for_config(cfg, wifi_passphrases)
    verdict = revert.verify_revert(live_tags, expected_now)

    if verdict.ok:
        console.print(
            "[yellow]Existing lankit configuration found with no known-good archive on "
            "record[/yellow] (this router predates lankit's revert-safety upgrade). "
            "Current router state matches network.yml."
        )
        if Confirm.ask("Record current state as known-good before proceeding?", default=True):
            texts = render_texts(cfg, wifi_passphrases, segment_filter=None, include_globals=True)
            aid = revert.save_known_good(
                cfg.router.ip, texts,
                segments_applied=sorted(cfg.segments.keys()), globals_applied=True,
                network_yml_hash=network_yml_hash, lan_interface=cfg.router.lan_interface,
                host_ports=list(cfg.host_ports()),
            )
            console.print(f"  [green]✓[/green] Recorded current state as known-good ({aid})")
            return revert.load_known_good(cfg.router.ip, aid)
        # Declining used to return None silently, and apply then armed a
        # remove-only failsafe with no warning at all. Same hazard as the
        # mismatch path below, so it gets the same gate.
        console.print(
            "  Not recorded. A failsafe firing during this apply could only revert to a "
            "bare remove-only state (flat network)."
        )
        _confirm_remove_only_failsafe(console)
        return None

    console.print(
        "[bold red]Existing lankit configuration found that does not match network.yml, "
        "and no known-good archive is on record.[/bold red]"
    )
    console.print(
        "  A failsafe firing right now could only revert to a bare remove-only state "
        "(flat network) — not this router's current configuration."
    )
    if verdict.missing:
        console.print(f"  Missing ({len(verdict.missing)}): network.yml expects these, router doesn't have them.")
    if verdict.unexpected:
        console.print(f"  Unexpected ({len(verdict.unexpected)}): live but not expected — possibly drift.")
    console.print("  Run [bold]lankit probe[/bold] first to see exactly what differs, or:")
    _confirm_remove_only_failsafe(console)
    return None


def _check_host_ports(conn, cfg, console) -> None:
    """Refuse if a host port isn't a port of the bridge.

    01-vlans names each host port in `untagged=`; a name RouterOS doesn't
    know aborts the import there, after the VLAN rows were removed and
    before filtering is back on, leaving the router to the failsafe. A real
    interface that isn't a bridge port does nothing at all, so the host
    silently stays where it was. Both are caught before anything changes.
    """
    import re

    wanted = sorted(cfg.host_ports())
    if not wanted:
        return
    out, err = conn.run_tolerant("/interface bridge port print terse where bridge=bridge")
    if err.strip() or not out.strip():
        console.print("  [dim]Could not read the bridge ports to check the host ports — "
                      "continuing.[/dim]")
        return
    on_bridge = set(re.findall(r"(?:^|\s)interface=(\S+)", out))
    missing = [p for p in wanted if p not in on_bridge]
    if missing:
        console.print()
        console.print("[bold red]Refusing to apply:[/bold red] these host ports are not ports "
                      "of the router's bridge: " + ", ".join(f"[bold]{p}[/bold]" for p in missing))
        console.print(f"  Bridge ports: {', '.join(sorted(on_bridge)) or 'none'}")
        console.print("  Fix hosts.<name>.port (or router.lan_interface for the DNS server) in "
                      "network.yml. Nothing has been changed.")
        raise SystemExit(1)


def _check_untagged(conn, script_texts, console) -> None:
    """Report config lankit doesn't own; refuse if any of it would collide
    with an add in this apply (#70).

    An untagged object survives every remove-pass, so an add with the same
    identity fails mid-import ("already have such ..."), and the failsafe's
    revert then runs against a half-applied router. Caught here, before the
    snapshot, bundle or failsafe, refusing changes nothing.
    """
    from lankit.core import untagged

    found = untagged.find_untagged(conn.read_tag_tables())
    if not found:
        return
    collisions = untagged.find_collisions(found, script_texts)
    if collisions:
        console.print()
        console.print("[bold red]Refusing to apply:[/bold red] the router has config lankit "
                      "didn't make that clashes with what this apply adds:")
        for u in collisions:
            console.print(f"  [bold]{u.path}[/bold]  {u.label}"
                          + (f"  [dim];;; {u.comment}[/dim]" if u.comment else ""))
        console.print("  lankit only removes objects tagged [bold]lankit:[/bold], so these would "
                      "make the import fail partway.")
        console.print("  Remove or rename them on the router, then run apply again. "
                      "Nothing has been changed.")
        raise SystemExit(1)
    console.print(f"  [yellow]![/yellow] {len(found)} object(s) on the router aren't managed "
                  "by lankit; no clash with this apply. [dim]lankit audit lists them.[/dim]")


def _confirm_remove_only_failsafe(console) -> None:
    """Gate arming a failsafe whose revert would flatten the network.

    Answerable only by a person at a terminal. Piped or redirected stdin is
    refused outright, and --yes does not answer it: on 2026-11-14 a piped
    `y` accepted this prompt against a production router, and only an
    unrelated upload-verification failure stopped the apply from arming a
    revert that would have dropped every VLAN. Raises SystemExit(1) unless
    the operator explicitly agrees."""
    from rich.prompt import Confirm

    if not sys.stdin.isatty():
        console.print(
            "[bold red]Refusing: a remove-only failsafe needs an interactive "
            "confirmation.[/bold red]\n"
            "  stdin is not a terminal, so this prompt was not shown. --yes does not "
            "answer it.\n  Run apply from a terminal, or record a known-good state first."
        )
        raise SystemExit(1)
    if not Confirm.ask("Continue with a remove-only failsafe for this one apply?", default=False):
        raise SystemExit(1)


def _revert_and_verify(conn, expected_tags, console) -> bool:
    """Import the already-uploaded lankit-revert.rsc, verify live tags
    converged to expected_tags, and print success/failure accordingly.

    Design §7.1's global invariant: a green mark may be printed only when
    import stderr was clean AND verification passed. This replaces the old
    _restore_snapshot, which printed a bare yellow warning on stderr and
    then declared success unconditionally — the exact swallow the whole
    revert mechanism exists to close (readiness review finding S1)."""
    from lankit.core import revert

    out, err = conn.run_tolerant("/import file=lankit-revert.rsc")
    failure = _import_failure(out, err)
    import_clean = failure is None
    if failure:
        console.print(f"  [red]Import error:[/red] {failure}")
    elif err:
        console.print(f"  [yellow]Warning:[/yellow] {err.strip()}")

    live_tags = conn.read_lankit_tags()
    verdict = revert.verify_revert(live_tags, set(expected_tags))

    if import_clean and verdict.ok:
        console.print(f"[green]✓[/green] Reverted — router matches last known-good state "
                      f"({len(expected_tags)} lankit resource(s) verified).")
        return True

    console.print("[bold red]✗ REVERT INCOMPLETE[/bold red] — router may be in a mixed state.")
    _print_verdict_evidence(verdict, console)
    console.print("\n  Next steps:")
    console.print("    1. Re-run the revert by hand over SSH:")
    console.print("       /import file=lankit-revert.rsc")
    console.print("    2. Then check: [bold]lankit probe[/bold]")
    console.print("    3. If still wrong: [bold]lankit rollback[/bold], then the printed rollback card.")
    return False
