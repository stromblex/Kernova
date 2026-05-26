"""Build logic: assembles a complete modpack build folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .models import BuildManifest, Mod, ModList, ResolvedMod
from .modrinth import download_mod, resolve_dependencies, resolve_mod

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIST_DIR = PROJECT_ROOT / "list"
BUILDS_DIR = PROJECT_ROOT / "builds"


def load_mods() -> list[Mod]:
    path = LIST_DIR / "mods" / "modlist.json"
    data = json.loads(path.read_text())
    mod_list = ModList(**data)
    return mod_list.mods


def build_folder_name(name: str, mc_version: str, pack_version: str) -> str:
    return f"{name} {mc_version} v{pack_version}"


def resolve_all(mc_version: str, loader: str) -> list[ResolvedMod]:
    """Resolve all mods from modlist + auto-dependencies."""
    mods = load_mods()
    resolved: list[ResolvedMod] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Resolving mods...", total=len(mods))
        for mod in mods:
            r = resolve_mod(mod, mc_version, loader)
            r.source = "list"
            resolved.append(r)
            progress.advance(task)

    console.print("[dim]Checking dependencies...[/dim]")
    dep_mods = resolve_dependencies(resolved, mc_version, loader)
    resolved.extend(dep_mods)

    return resolved


def copy_configs(build_dir: Path) -> None:
    src_config = LIST_DIR / "config"
    dst_config = build_dir / "config"
    if src_config.exists():
        shutil.copytree(src_config, dst_config, dirs_exist_ok=True)


def copy_options(build_dir: Path) -> None:
    src = LIST_DIR / "options.txt"
    if src.exists():
        shutil.copy2(src, build_dir / "options.txt")


def download_all(resolved: list[ResolvedMod], mods_dir: Path) -> tuple[int, int]:
    available = [m for m in resolved if m.available]
    downloaded = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading mods...", total=len(available))
        for mod in available:
            try:
                download_mod(mod, mods_dir)
                downloaded += 1
            except Exception as e:
                console.print(f"[red]Failed: {mod.name}: {e}[/red]")
                skipped += 1
            progress.advance(task)

    return downloaded, skipped


def create_build(
    name: str,
    mc_version: str,
    loader: str,
    pack_version: str,
    resolved: list[ResolvedMod],
) -> BuildManifest:
    """Create a full build folder with mods, configs, and options."""
    folder_name = build_folder_name(name, mc_version, pack_version)
    build_dir = BUILDS_DIR / folder_name
    mods_dir = build_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    copy_configs(build_dir)
    copy_options(build_dir)

    downloaded, skipped = download_all(resolved, mods_dir)
    console.print(
        f"[green]{downloaded} downloaded[/green]"
        + (f", [yellow]{skipped} failed[/yellow]" if skipped else "")
    )

    manifest = BuildManifest(
        minecraft_version=mc_version,
        loader=loader,
        pack_version=pack_version,
        build_folder=folder_name,
        resolved_mods=resolved,
        config_copied=True,
        options_copied=True,
    )
    manifest_path = build_dir / "build_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))

    return manifest
