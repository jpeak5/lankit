import click
from lankit import __version__


_BANNER = """
  ┌─────────────────────────────────────────────┐
  │                                             │
  │   l a n k i t                               │
  │   home network segmentation toolkit         │
  │                                             │
  │              ╔═══════════╗                  │
  │              ║  router   ║                  │
  │              ╚═╤═══════╤═╝                  │
  │                │       │                    │
  │        ┌───────┘       └───────┐            │
  │        │                       │            │
  │   ┌────┴──────┐         ┌──────┴────┐       │
  │   │  trusted  │         │    iot    │       │
  │   └───────────┘         └───────────┘       │
  │                                             │
  └─────────────────────────────────────────────┘
"""


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="lankit")
@click.pass_context
def cli(ctx):
    """lankit — home network segmentation toolkit."""
    if ctx.invoked_subcommand is None:
        from rich.console import Console
        console = Console()
        console.print(_BANNER)
        console.print("  [bold]lankit[/bold] — home network segmentation toolkit\n")
        console.print("  [dim]Start with:[/dim]  lankit discover --new")
        console.print("  [dim]Get help:[/dim]    lankit --help\n")


# Commands are registered here as they are implemented.
# Import order matches the recommended first-run workflow.
from lankit.cli.commands import (  # noqa: E402, F401
    discover,
    provision,
    commit,
    extend,
    rollback,
    restore,
    status,
    rules,
    generate,
    apply,
    diagram,
    test_failsafe,
    rollback_card,
    secrets,
    password_card,
    explain,
    matrix,
    probe,
    snapshots_cmd,
    audit,
    reset_provision,
)


if __name__ == "__main__":
    cli()
