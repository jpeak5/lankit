import click
from lankit.cli.__main__ import cli


@cli.command(name="diagram")
@click.option("--config", "-c", "config_path", type=click.Path(), default=None,
              metavar="PATH", help="Path to network.yml")
@click.option("--output", "-o", type=click.Path(), default="network.png",
              metavar="FILE", help="Output file (default: network.png)")
@click.option("--format", "fmt", type=click.Choice(["png", "svg", "pdf"]), default="png",
              help="Output format (default: png)")
@click.option("--view", is_flag=True, default=False, help="Open diagram after generating")
def diagram(config_path, output, fmt, view):
    """Generate a network topology diagram.

    Produces a GraphViz diagram showing segments, trust relationships,
    internet access, and DNS routing. Requires graphviz to be installed.

    \b
    Examples:
      lankit diagram
      lankit diagram --output network.svg --format svg
      lankit diagram --view
    """
    from lankit.core.config import load, ConfigError
    from pathlib import Path
    from rich.console import Console

    console = Console()

    try:
        import graphviz
    except ImportError:
        console.print("[bold red]graphviz package not found.[/bold red]")
        console.print("Install it: pip install graphviz")
        console.print("You also need the graphviz system package: apt install graphviz")
        raise SystemExit(1)

    try:
        cfg = load(Path(config_path) if config_path else None)
    except ConfigError as e:
        console.print(f"[bold red]Config error:[/bold red] {e}")
        raise SystemExit(1)

    dot = graphviz.Digraph(
        name=cfg.household_name,
        graph_attr={
            "rankdir": "LR",
            "splines": "ortho",
            "fontname": "Helvetica",
            "bgcolor": "white",
            "pad": "0.4",
        },
        node_attr={"fontname": "Helvetica", "fontsize": "11"},
        edge_attr={"fontname": "Helvetica", "fontsize": "9"},
    )

    # Color scheme by trust level
    _seg_color = {
        "trusted":    ("#d4edda", "#155724"),
        "servers":    ("#cce5ff", "#004085"),
        "media":      ("#fff3cd", "#856404"),
        "iot":        ("#f8d7da", "#721c24"),
        "guest":      ("#e2e3e5", "#383d41"),
        "quarantine": ("#f5c6cb", "#491217"),
        "admin":      ("#d1ecf1", "#0c5460"),
    }
    _internet_color = {
        "full":        "#28a745",
        "egress_only": "#fd7e14",
        "none":        "#dc3545",
    }

    # Internet node
    dot.node("_internet", "Internet", shape="diamond",
             style="filled", fillcolor="#e9ecef", color="#6c757d")

    # Router node
    dot.node("_router", f"Router\\n{cfg.router.ip}", shape="box",
             style="filled", fillcolor="#343a40", fontcolor="white",
             color="#343a40")

    # Segment nodes
    for name, seg in cfg.segments.items():
        fill, font = _seg_color.get(name, ("#ffffff", "#000000"))
        wifi_label = f"\\n📶 {seg.ssid}" if seg.has_wifi else ""
        label = f"{name}\\nVLAN {seg.vlan_id}\\n{seg.subnet}{wifi_label}"
        dot.node(name, label, shape="box", style="filled,rounded",
                 fillcolor=fill, fontcolor=font, color=font)

        # Internet edge
        inet_color = _internet_color.get(seg.internet, "#6c757d")
        if seg.internet == "full":
            dot.edge(name, "_internet", color=inet_color, style="solid", penwidth="1.5")
        elif seg.internet == "egress_only":
            dot.edge(name, "_internet", color=inet_color, style="dashed",
                     label="egress only")
        # none = no edge

    # Permission edges (inter-segment)
    for src_name, perm in cfg.permissions.items():
        for dst_name in perm.can_reach:
            dot.edge(src_name, dst_name, color="#6610f2", style="solid",
                     arrowhead="open", penwidth="1.2")

    # DNS server host
    dns = cfg.hosts.get("dns_server")
    if dns:
        dot.node("_dns", f"Pi-hole\\n{dns.ip}", shape="cylinder",
                 style="filled", fillcolor="#6f42c1", fontcolor="white",
                 color="#6f42c1")
        # Segments with DNS point at Pi-hole
        for name, seg in cfg.segments.items():
            if seg.dns == "filtered":
                dot.edge(name, "_dns", color="#6f42c1", style="dotted",
                         arrowhead="open", label="DNS")

    out_path = Path(output)
    out_stem = str(out_path.with_suffix(""))

    try:
        dot.render(out_stem, format=fmt, cleanup=True, view=view)
    except graphviz.ExecutableNotFound:
        console.print("[bold red]graphviz executable not found.[/bold red]")
        console.print("Install the system package: apt install graphviz  (or brew install graphviz)")
        raise SystemExit(1)

    final_path = out_path.with_suffix(f".{fmt}")
    console.print(f"[green]✓[/green] Diagram written to [bold]{final_path}[/bold]")
