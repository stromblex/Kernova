"""CLI entry point using Typer + Rich."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .builder import create_build, resolve_all
from .changelog import generate_changelog
from .integrations import DEFAULT_PACKPING_UPDATE_URL
from .validation import validate_repository
from .versioning import has_changes, record_build, stability_tag, suggest_next_version

app = typer.Typer(
    name="kernova-update",
    help="Terminal updater for Kernova modpack.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def build(
    loader_profile: Annotated[
        Optional[str],
        typer.Argument(help="Loader profile: fabric or neoforge"),
    ] = None,
    name: Annotated[Optional[str], typer.Option("--name", help="Build name")] = None,
    mc: Annotated[Optional[str], typer.Option("--mc", help="Minecraft version")] = None,
    loader: Annotated[
        Optional[str],
        typer.Option(help="Mod loader/profile override"),
    ] = None,
    update_url: Annotated[
        str,
        typer.Option("--update-url", help="PackPing update JSON URL"),
    ] = DEFAULT_PACKPING_UPDATE_URL,
    version: Annotated[Optional[str], typer.Option(help="Pack version override")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force build even if unchanged")] = False,
) -> None:
    """Build a new modpack release."""
    _run_build(loader_profile, name, mc, loader, update_url, version, yes, force)


@app.command("fabric")
def build_fabric(
    name: Annotated[Optional[str], typer.Option("--name", help="Build name")] = None,
    mc: Annotated[Optional[str], typer.Option("--mc", help="Minecraft version")] = None,
    update_url: Annotated[
        str,
        typer.Option("--update-url", help="PackPing update JSON URL"),
    ] = DEFAULT_PACKPING_UPDATE_URL,
    version: Annotated[Optional[str], typer.Option(help="Pack version override")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force build even if unchanged")] = False,
) -> None:
    """Build the Fabric profile."""
    _run_build("fabric", name, mc, None, update_url, version, yes, force)


@app.command("neoforge")
def build_neoforge(
    name: Annotated[Optional[str], typer.Option("--name", help="Build name")] = None,
    mc: Annotated[Optional[str], typer.Option("--mc", help="Minecraft version")] = None,
    update_url: Annotated[
        str,
        typer.Option("--update-url", help="PackPing update JSON URL"),
    ] = DEFAULT_PACKPING_UPDATE_URL,
    version: Annotated[Optional[str], typer.Option(help="Pack version override")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
    force: Annotated[bool, typer.Option("--force", "-f", help="Force build even if unchanged")] = False,
) -> None:
    """Build the NeoForge profile."""
    _run_build("neoforge", name, mc, None, update_url, version, yes, force)


def _run_build(
    loader_profile: str | None,
    name: str | None,
    mc: str | None,
    loader: str | None,
    update_url: str,
    version: str | None,
    yes: bool,
    force: bool,
) -> None:
    if loader_profile and loader and loader_profile.lower() != loader.lower():
        console.print("[red]Positional loader and --loader must match.[/red]")
        raise typer.Exit(2)

    loader = (loader or loader_profile or "fabric").strip().lower()

    if not name and yes:
        name = "Kernova"
    elif not name:
        name = typer.prompt("Build name", default="Kernova")
    if not mc and yes:
        console.print("[red]Minecraft version is required when --yes is used.[/red]")
        console.print("[dim]Pass --mc, for example: --mc 26.1[/dim]")
        raise typer.Exit(2)
    elif not mc:
        mc = typer.prompt("Minecraft version")

    # Resolve mods
    resolved = resolve_all(mc, loader)
    available = [m for m in resolved if m.available]
    skipped = [m for m in resolved if not m.available]
    listed = [m for m in resolved if m.source == "list"]
    listed_available = [m for m in listed if m.available]
    listed_skipped = [m for m in listed if not m.available]
    dependencies = [m for m in resolved if m.source == "dependency"]
    dependencies_available = [m for m in dependencies if m.available]

    # Check if update needed
    if not force and not has_changes(name, mc, loader, resolved):
        console.print("[yellow]No changes since last build. Skipping.[/yellow]")
        console.print("[dim]Use --force to rebuild.[/dim]")
        raise typer.Exit()

    # Version
    tag = stability_tag(len(listed_available), loader)
    base_version = version or suggest_next_version(name, mc, loader, tag)
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
    table.add_column("Source")
    table.add_column("Status")

    for m in sorted(available, key=lambda x: x.name):
        source = "auto dependency" if m.source == "dependency" else "listed"
        table.add_row(m.name, m.version_number or "", source, "[green]OK[/green]")
    for m in sorted(skipped, key=lambda x: x.name):
        source = "auto dependency" if m.source == "dependency" else "listed"
        table.add_row(m.name, "", source, f"[red]{m.skipped_reason}[/red]")

    console.print(table)
    console.print()
    console.print(
        f"[green]{len(listed_available)}/{len(listed)}[/green] listed mods available"
        f", [cyan]{len(dependencies_available)}[/cyan] auto dependencies"
        f", [green]{len(available)}[/green] total available"
        f", [yellow]{len(listed_skipped)}[/yellow] listed skipped"
    )
    console.print()

    if not yes:
        if not typer.confirm("Continue?", default=False):
            raise typer.Abort()

    # Build
    manifest = create_build(name, mc, loader, pack_version, resolved, update_url)

    # Record
    record_build(
        build_name=name,
        mc_version=mc,
        loader=loader,
        pack_version=pack_version,
        mod_count=len(available),
        skipped_count=len(listed_skipped),
        resolved=resolved,
    )

    # Changelog
    cl_path = generate_changelog(manifest)

    console.print()
    console.print(f"[bold green]Done![/bold green]")
    console.print(f"  builds/{manifest.build_folder}/")
    console.print(f"  changelogs/{cl_path.name}")


@app.command()
def validate(
    strict: Annotated[
        bool,
        typer.Option("--strict", help="Treat warnings as failures."),
    ] = False,
    artifacts: Annotated[
        bool,
        typer.Option("--artifacts", help="Also validate generated builds and packaged artifacts."),
    ] = False,
) -> None:
    """Validate local repository source data."""
    report = validate_repository(include_artifacts=artifacts)

    if report.issues:
        table = Table(title="Repository validation")
        table.add_column("Level", style="bold")
        table.add_column("Path")
        table.add_column("Message")
        for issue in report.issues:
            style = "red" if issue.level == "error" else "yellow"
            table.add_row(f"[{style}]{issue.level}[/{style}]", issue.path, issue.message)
        console.print(table)
    else:
        console.print("[green]Repository validation passed with no issues.[/green]")

    summary = f"{report.error_count} errors, {report.warning_count} warnings"
    if report.error_count:
        console.print(f"[red]{summary}[/red]")
        raise typer.Exit(1)
    if strict and report.warning_count:
        console.print(f"[yellow]{summary}; strict mode failed.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]{summary}[/green]")


def main() -> None:
    app()
