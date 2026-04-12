import click
from lankit.cli.__main__ import cli


@cli.command(name="secrets")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
def secrets(config_path):
    """Configure secrets (SSH key and WiFi passwords) for provisioning.

    Runs through two sections in order:

    \b
      1. SSH key — checks whether your key exists; generates it if not.
         You choose the key type and parameters.

      2. WiFi passwords — guided by wifi_password_source in network.yml:
           vault  → Encrypted wifi-vault.yml. Each password is saved
                    immediately after entry, so a cancelled run keeps
                    whatever was completed.
           env    → Prints the exact variable names to set, with OS
                    keychain instructions.
           prompt → Nothing to configure; passwords are asked at apply time.

    \b
    Examples:
      lankit secrets
    """
    import subprocess
    from lankit.core.config import load, ConfigError
    from lankit.core.passwords import (
        save_to_vault, ensure_vault_password_file, vault_password_file,
        read_vault, VAULT_FILE,
    )
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    # ── 1. SSH key ────────────────────────────────────────────────────────────
    console.print("\n[bold]SSH key[/bold]\n")

    ssh_key_path = Path(cfg.ssh_key).expanduser()
    if ssh_key_path.exists():
        console.print(f"  [green]✓[/green] Found: [dim]{ssh_key_path}[/dim]")
    else:
        console.print(f"  [yellow]Not found:[/yellow] {ssh_key_path}\n")

        key_type = click.prompt(
            "  Key type",
            type=click.Choice(["ed25519", "rsa", "ecdsa"], case_sensitive=False),
            default="ed25519",
            show_default=True,
        )
        keygen_args = ["-t", key_type, "-f", str(ssh_key_path)]

        if key_type == "rsa":
            bits = click.prompt("  Bits", type=click.Choice(["2048", "3072", "4096"]), default="4096")
            keygen_args += ["-b", bits]
        elif key_type == "ecdsa":
            bits = click.prompt("  Bits", type=click.Choice(["256", "384", "521"]), default="521")
            keygen_args += ["-b", bits]

        comment = click.prompt("  Comment", default="lankit")
        keygen_args += ["-C", comment, "-N", ""]

        console.print()
        result = subprocess.run(["ssh-keygen"] + keygen_args, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"  [bold red]ssh-keygen failed:[/bold red] {result.stderr.strip()}")
            raise SystemExit(1)

        console.print(f"  [green]✓[/green] Key generated: [dim]{ssh_key_path}[/dim]")
        console.print(f"  [green]✓[/green] Public key:   [dim]{ssh_key_path}.pub[/dim]")
        console.print(f"\n  [dim]Copy the public key to new hosts with:[/dim]")
        console.print(f"  [dim]  ssh-copy-id -i {ssh_key_path}.pub <user>@<host>[/dim]")

    # ── 2. WiFi passwords ─────────────────────────────────────────────────────
    console.print("\n[bold]WiFi passwords[/bold]\n")

    wifi_segments = {name: seg for name, seg in cfg.segments.items() if seg.has_wifi}
    if not wifi_segments:
        console.print("  [dim]No WiFi segments defined.[/dim]")
        return

    source = cfg.wifi_password_source

    if source == "prompt":
        console.print(f"  [dim]wifi_password_source: prompt[/dim]")
        console.print("  Nothing to configure — [bold]lankit apply[/bold] will ask at run time.")

    elif source == "env":
        console.print(f"  [dim]wifi_password_source: env[/dim]\n")
        console.print("  Set these environment variables before running [bold]lankit apply[/bold]:\n")
        for name, seg in wifi_segments.items():
            var = f"LANKIT_WIFI_{name.upper()}"
            console.print(f"    [cyan]{var}[/cyan]  ({seg.ssid or name})")
        console.print()
        console.print("  [dim]Persist in your OS keychain:[/dim]")
        console.print("  [dim]  macOS:  security add-generic-password -a lankit -s LANKIT_WIFI_<NAME> -w '<pw>'[/dim]")
        console.print("  [dim]  Linux:  secret-tool store --label='lankit' service lankit username LANKIT_WIFI_<NAME>[/dim]")

    elif source == "vault":
        console.print(f"  [dim]wifi_password_source: vault[/dim]\n")
        console.print("  Each password is saved immediately after entry.")
        console.print("  Ctrl+C at any time — already-saved entries are kept.\n")

        ensure_vault_password_file()
        existing = read_vault()

        saved_any = False
        for name, seg in wifi_segments.items():
            label = f"{name} ({seg.ssid})" if seg.ssid else name

            if name in existing:
                if not click.confirm(f"  {label} [dim](already set)[/dim] — update?", default=False):
                    console.print(f"    [dim]kept[/dim]")
                    continue

            pw = click.prompt(f"  {label}", hide_input=True, confirmation_prompt=True)
            existing[name] = pw

            try:
                save_to_vault(existing)
                console.print(f"    [green]✓[/green] saved")
                saved_any = True
            except ConfigError as e:
                console.print(f"    [bold red]Error saving:[/bold red] {e}")
                console.print(f"    [dim]This entry was not persisted. Try again.[/dim]")
                del existing[name]

        if saved_any or VAULT_FILE.exists():
            vp = vault_password_file()
            console.print(f"\n  Vault: [dim]{VAULT_FILE}[/dim]")
            console.print(f"  Key:   [dim]{vp}[/dim]")
            console.print(f"\n  [dim]wifi-vault.yml is safe to commit; it is encrypted.[/dim]")
            console.print(f"  [dim]Back up ~/.lankit/vault-password — without it the vault cannot be opened.[/dim]")

    else:
        console.print(f"[bold red]Unknown wifi_password_source:[/bold red] {source!r}")
        raise SystemExit(1)
