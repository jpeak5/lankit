import click
from lankit.cli.__main__ import cli


@cli.command(name="reset-provision")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--dry-run", is_flag=True, default=False,
              help="Generate and print the omnibus script without uploading or resetting")
@click.option("--output", "-o", type=click.Path(), default=None, metavar="PATH",
              help="Write omnibus script to file (implies --dry-run)")
def reset_provision(config_path, dry_run, output):
    """Factory-reset the router and self-provision from a single script.

    Builds an omnibus .rsc file (SSH key + all 7 config scripts), uploads it
    to the router flash, then triggers a factory reset with run-after-reset.
    The router reboots and applies the full config unattended — no wired
    connection required, no WiFi-kills-SSH problem.

    Safe to run from the router's current WiFi. The connection drop on reset
    is expected and handled.

    \b
    Workflow:
      1. Generates all config scripts from network.yml
      2. Prepends SSH key installation to the omnibus script
      3. Uploads lankit-provision.rsc to router flash
      4. Triggers: /system reset-configuration run-after-reset=lankit-provision.rsc
      5. Waits for router to reboot (~60s)
      6. Verifies SSH access with the installed key

    \b
    Examples:
      lankit reset-provision --dry-run     ← review what will run
      lankit reset-provision               ← upload and trigger reset
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.generator import generate_all
    from lankit.core.passwords import load_wifi_passwords
    from lankit.core.router import RouterConnection, RouterError
    from pathlib import Path
    from rich.console import Console
    from rich.prompt import Confirm
    from rich.syntax import Syntax
    import datetime
    import time

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    try:
        wifi_passphrases = load_wifi_passwords(cfg)
    except ConfigError as e:
        console.print(f"[bold red]Password error:[/bold red] {e}")
        raise SystemExit(1)

    # ── Read SSH public key ───────────────────────────────────────────────────
    # Follow symlinks — the .pub lives next to the real key file, not the symlink.
    ssh_key_path = Path(cfg.ssh_key).expanduser().resolve()
    pub_key_path = Path(str(ssh_key_path) + ".pub")
    if not pub_key_path.exists():
        console.print(f"[bold red]SSH public key not found:[/bold red] {pub_key_path}")
        console.print(f"  (resolved from {cfg.ssh_key})")
        console.print("  Generate with: ssh-keygen -t ed25519 -f ~/.ssh/lankit -C lankit")
        raise SystemExit(1)
    pub_key = pub_key_path.read_text().strip()

    # ── Generate scripts ──────────────────────────────────────────────────────
    generated_dir = Path("ansible/generated")
    console.print("[dim]Generating scripts from network.yml...[/dim]")
    try:
        script_paths = generate_all(cfg, generated_dir, wifi_passphrases)
    except Exception as e:
        console.print(f"[bold red]Generate failed:[/bold red] {e}")
        raise SystemExit(1)

    # ── Build omnibus script ──────────────────────────────────────────────────
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    parts = [
        f"# lankit-provision.rsc — generated {now}",
        "# Created by: lankit reset-provision",
        "# DO NOT import manually — run via reset-configuration run-after-reset",
        "",
        "# ── Bootstrap: install SSH authorized key ───────────────────────────",
        f'/user ssh-keys add key="{pub_key}" user=admin comment="lankit"',
        "",
    ]

    for path in script_paths:
        parts.append(f"# {'─' * 60}")
        parts.append(f"# {path.name}")
        parts.append(f"# {'─' * 60}")
        parts.append(path.read_text().rstrip())
        parts.append("")

    omnibus = "\n".join(parts) + "\n"

    # ── Dry-run / output modes ────────────────────────────────────────────────
    if output:
        Path(output).write_text(omnibus)
        console.print(f"[green]✓[/green] Omnibus script written to [dim]{output}[/dim]")
        return

    if dry_run:
        console.print(f"\n[bold]Omnibus script[/bold] ({len(omnibus.splitlines())} lines):\n")
        console.print(Syntax(omnibus, "bash", theme="monokai", line_numbers=True))
        return

    # ── Connect, upload, reset ────────────────────────────────────────────────
    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    console.print()
    console.print("[bold yellow]⚠  This will factory-reset the router.[/bold yellow]")
    console.print("   All existing config will be wiped and rebuilt from network.yml.")
    console.print("   The connection will drop — that is expected.")
    console.print()

    if not Confirm.ask("[bold]Proceed with factory reset?[/bold]"):
        console.print("Aborted.")
        return

    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            console.print(f"  Connected: [bold]{conn.identity()}[/bold]  RouterOS {conn.version()}")

            console.print("  Uploading lankit-provision.rsc...")
            conn.upload(omnibus, "lankit-provision.rsc")
            console.print("  [green]✓[/green] Uploaded.")

            console.print("\n  Triggering factory reset — connection will drop now...")
            try:
                conn.run(
                    "/system reset-configuration "
                    "run-after-reset=lankit-provision.rsc "
                    "skip-backup=yes"
                )
            except Exception:
                pass  # Expected — router drops the connection on reset

    except RouterError as e:
        # Connection error before or during upload is unexpected
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)

    # ── Wait and verify ───────────────────────────────────────────────────────
    console.print()
    console.print("[dim]Router is rebooting and self-provisioning...[/dim]")

    with console.status("[dim]Waiting for router to come back up...[/dim]"):
        time.sleep(90)

    console.print("  Verifying SSH access...")
    for attempt in range(6):
        try:
            with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
                identity = conn.identity()
                version = conn.version()
                trusted_ssid = cfg.segments.get("trusted") and cfg.segments["trusted"].ssid
                wifi_hint = f"Connect to [bold]{trusted_ssid}[/bold] WiFi" if trusted_ssid else ""
                console.print(f"\n[green]✓[/green] Router is up: [bold]{identity}[/bold]  RouterOS {version}")
                if wifi_hint:
                    console.print(f"  {wifi_hint}")
                console.print("  Run [bold]lankit audit[/bold] to verify provisioning.")
                return
        except Exception:
            time.sleep(15)

    console.print("[yellow]⚠  Could not verify SSH access after 90s + 6 retries.[/yellow]")
    console.print("  The router may still be provisioning, or the script hit an error.")
    console.print(f"  Try: ssh {cfg.router.ssh_user}@{cfg.router.ip}")
    console.print("  Or connect to your trusted WiFi and run: lankit audit")
