"""CLI entry point using Typer + Rich."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .builder import create_build, resolve_all
from .changelog import generate_changelog
from .versioning import has_changes, record_build, stability_tag, suggest_next_version

app = typer.Typer(
    name="kernova-update",
    help="Terminal updater for Kernova modpack.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def build(
    name: Annotated[Optional[str], typer.Option("--name", help="Build name")] = None,
    mc: Annotated[Optional[str], typer.Option("--mc", help="Minecraft version")] = None,
    loader: Annotated[str, typer.Option(help="Mod loader")] = "fabric",
    version: Annotated[Optional[str], typer.Option(help="Pack version override")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force build even if unchanged")] = False,
) -> None:
    """Build a new modpack release."""
    if not name:
        name = typer.prompt("Build name", default="Kernova")
    if not mc:
        mc = typer.prompt("Minecraft version")

    # Resolve mods
    resolved = resolve_all(mc, loader)
    available = [m for m in resolved if m.available]
    skipped = [m for m in resolved if not m.available]

    # Check if update needed
    if not force and not has_changes(name, mc, resolved):
        console.print("[yellow]No changes since last build. Skipping.[/yellow]")
        console.print("[dim]Use --force to rebuild.[/dim]")
        raise typer.Exit()

    # Version
    tag = stability_tag(len(available))
    base_version = version or suggest_next_version(name, mc)
    if not version:
        console.print(f"[dim]Suggested:[/dim] [bold]{base_version}-{tag}[/bold]")
        if not yes:
            base_version = typer.prompt("Pack version", default=base_version)

    pack_version = f"{base_version}-{tag}"

    console.print()
    console.print(f"[bold]{name}[/bold] | MC {mc} | {loader} | v{pack_version}")
    console.print()

    # Preview
    table = Table(title="Mods")
    table.add_column("Mod", style="bold")
    table.add_column("Version")
    table.add_column("Status")

    for m in sorted(available, key=lambda x: x.name):
        table.add_row(m.name, m.version_number or "", "[green]OK[/green]")
    for m in sorted(skipped, key=lambda x: x.name):
        table.add_row(m.name, "", f"[red]{m.skipped_reason}[/red]")

    console.print(table)
    console.print(f"\n[green]{len(available)}[/green] available, [yellow]{len(skipped)}[/yellow] skipped\n")

    if not yes:
        if not typer.confirm("Continue?", default=False):
            raise typer.Abort()

    # Build
    manifest = create_build(name, mc, loader, pack_version, resolved)

    # Record
    record_build(
        build_name=name,
        mc_version=mc,
        pack_version=pack_version,
        mod_count=len(available),
        skipped_count=len(skipped),
        resolved=resolved,
    )

    # Changelog
    cl_path = generate_changelog(manifest)

    console.print()
    console.print(f"[bold green]Done![/bold green]")
    console.print(f"  builds/{manifest.build_folder}/")
    console.print(f"  changelogs/{cl_path.name}")


def main() -> None:
    app()
