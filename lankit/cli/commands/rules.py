import click
from lankit.cli.__main__ import cli


@cli.command(name="rules")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--segment", "-s", type=str, default=None, metavar="NAME",
              help="Filter by segment name")
@click.option("--unit", "-u", type=str, default=None, metavar="UNIT",
              help="Filter by comment unit (e.g. fw, bw, dns, vlan)")
def rules(config_path, segment, unit):
    """Show generated firewall and routing rules.

    Parses the generated .rsc scripts and displays rules tagged with the
    lankit comment convention (lankit:<unit>:<scope>:<resource>).
    Run 'lankit generate' first to produce the scripts.

    \b
    Examples:
      lankit rules
      lankit rules --segment iot
      lankit rules --unit fw
    """
    import re
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()

    # Show provenance: config → generated scripts → not yet applied
    try:
        from lankit.core.config import load, ConfigError as CE
        cfg = load(Path(config_path) if config_path else None)
        console.print(
            f"\n[bold]{cfg.household_name}[/bold] — "
            f"{len(cfg.segments)} segments — "
            f"generated from [dim]network.yml[/dim]"
        )
    except Exception:
        pass

    console.print("[dim]These rules exist in ansible/generated/ — run [bold]lankit apply[/bold] to push them to the router.[/dim]\n")

    # Find generated scripts
    generated_dir = Path("ansible/generated")
    if not generated_dir.exists():
        console.print("[yellow]No generated scripts found.[/yellow]")
        console.print("Run [bold]lankit generate[/bold] first.")
        raise SystemExit(1)

    rsc_files = sorted(generated_dir.glob("*.rsc"))
    if not rsc_files:
        console.print("[yellow]No .rsc files in ansible/generated/[/yellow]")
        console.print("Run [bold]lankit generate[/bold] first.")
        raise SystemExit(1)

    # Parse rules with lankit comments
    # Comment convention: lankit:<unit>:<scope>:<resource>
    comment_re = re.compile(r'comment="(lankit:[^"]+)"')

    table = Table(box=box.SIMPLE_HEAD, show_lines=False)
    table.add_column("File", style="dim", no_wrap=True)
    table.add_column("Unit", style="bold cyan", no_wrap=True)
    table.add_column("Scope", style="yellow", no_wrap=True)
    table.add_column("Resource")
    table.add_column("Rule (truncated)", overflow="fold")

    found = 0
    for rsc_file in rsc_files:
        text = rsc_file.read_text()
        # Find all "add ..." blocks with lanlankit: comments
        # Each rule is a multi-line block ending with a comment
        block_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("add ") or stripped.startswith("/"):
                block_lines = [stripped]
            elif stripped.startswith("\\") or (block_lines and not stripped.startswith("#")):
                block_lines.append(stripped)

            m = comment_re.search(line)
            if m and block_lines:
                tag = m.group(1)  # e.g. lankit:fw:iot:deny
                parts = tag.split(":")
                tag_unit = parts[1] if len(parts) > 1 else ""
                tag_scope = parts[2] if len(parts) > 2 else ""
                tag_resource = ":".join(parts[3:]) if len(parts) > 3 else ""

                # Apply filters
                if segment and tag_scope != segment:
                    block_lines = []
                    continue
                if unit and tag_unit != unit:
                    block_lines = []
                    continue

                rule_text = " ".join(block_lines).replace("\\ ", " ")
                rule_text = re.sub(r'\s+', ' ', rule_text).strip()
                if len(rule_text) > 80:
                    rule_text = rule_text[:77] + "..."

                table.add_row(
                    rsc_file.name,
                    tag_unit,
                    tag_scope,
                    tag_resource,
                    rule_text,
                )
                found += 1
                block_lines = []

    if found == 0:
        filters = []
        if segment:
            filters.append(f"segment={segment}")
        if unit:
            filters.append(f"unit={unit}")
        filter_str = f" (filters: {', '.join(filters)})" if filters else ""
        console.print(f"[yellow]No kit-tagged rules found{filter_str}.[/yellow]")
        return

    console.print(table)
    console.print(f"[dim]{found} rule(s)[/dim]")
