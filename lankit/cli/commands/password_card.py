import click
from lankit.cli.__main__ import cli


@cli.command(name="password-card")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--output", "-o", type=click.Path(), default="password-card.txt",
              metavar="FILE", help="Output file (default: password-card.txt)")
def password_card(config_path, output):
    """Generate a printable WiFi password reference card.

    Lists all WiFi network names and their passwords. Useful to post near
    the router or share with household members.

    Reads passwords from the configured wifi_password_source (vault, env,
    or prompts interactively).

    \b
    Examples:
      lankit password-card
      lankit password-card --output ~/Desktop/wifi-passwords.txt
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.passwords import load_wifi_passwords
    from pathlib import Path
    from datetime import datetime, timezone
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    try:
        passwords = load_wifi_passwords(cfg)
    except ConfigError as e:
        console.print(f"[bold red]Password error:[/bold red] {e}")
        raise SystemExit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "=" * 52,
        f"  {cfg.household_name} — WiFi Passwords",
        f"  Generated {now}",
        "=" * 52,
        "",
    ]

    for name, seg in cfg.segments.items():
        if not seg.has_wifi:
            continue
        pw = passwords.get(name, "(not set)")
        lines += [
            f"  {seg.ssid}",
            f"  Password: {pw}",
            f"  {seg.comment}",
            "",
        ]

    lines += [
        "=" * 52,
        "",
    ]

    card_text = "\n".join(lines)
    Path(output).write_text(card_text)
    console.print(f"[green]✓[/green] Password card written to [bold]{output}[/bold]")
