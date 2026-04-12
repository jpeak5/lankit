import click
from lankit.cli.__main__ import cli


@cli.command(name="generate")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml (default: ./network.yml)")
@click.option("--out", "-o", type=click.Path(), default=None,
              metavar="DIR", help="Output directory (default: ansible/generated/)")
@click.option("--template", "-t", type=str, default=None,
              metavar="NAME", help="Render only this template (e.g. 03-firewall.rsc.j2)")
def generate(config_path, out, template):
    """Render RouterOS .rsc scripts from network.yml.

    Reads network.yml, validates it, and writes scripts to ansible/generated/.
    No router connection required — safe to run offline.

    \b
    Examples:
      lankit generate
      lankit generate --template 03-firewall.rsc.j2
      lankit generate --out /tmp/preview/
    """
    from lankit.core.config import load, ConfigError
    from lankit.core.generator import generate_all, generate_one
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    out_dir = Path(out) if out else None

    try:
        if template:
            path = generate_one(cfg, template, out_dir)
            console.print(f"[green]✓[/green] {path}")
        else:
            outputs = generate_all(cfg, out_dir)
            console.print(f"[green]✓[/green] Generated {len(outputs)} scripts:")
            for p in outputs:
                console.print(f"  [dim]{p}[/dim]")
    except Exception as e:
        console.print(f"[bold red]Generate error:[/bold red] {e}")
        raise SystemExit(1)
