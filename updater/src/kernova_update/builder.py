"""Build logic: assembles a complete modpack build folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .models import BuildManifest, Mod, ModList, ResolvedMod
from .integrations import generate_build_integrations, run_vartapack_doctor
from .modrinth import download_mod, resolve_dependencies, resolve_mod

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIST_DIR = PROJECT_ROOT / "list"
BUILDS_DIR = PROJECT_ROOT / "builds"
COMMON_PROFILE = "common"


def list_profile_dir(loader: str) -> Path:
    """Return the source list directory for a loader profile."""
    profile_dir = LIST_DIR / loader
    if profile_dir.exists():
        return profile_dir

    # Backward compatibility for older checkouts that still use list/mods.
    if (LIST_DIR / "mods" / "modlist.json").exists():
        return LIST_DIR

    available = sorted(p.name for p in LIST_DIR.iterdir() if p.is_dir())
    available_text = ", ".join(available) if available else "none"
    raise FileNotFoundError(
        f"No list profile for loader '{loader}'. Available profiles: {available_text}"
    )


def common_profile_dir() -> Path:
    return LIST_DIR / COMMON_PROFILE


def load_mods(loader: str) -> list[Mod]:
    path = list_profile_dir(loader) / "mods" / "modlist.json"
    data = json.loads(path.read_text())
    mod_list = ModList(**data)
    return mod_list.mods


def build_folder_name(name: str, mc_version: str, loader: str, pack_version: str) -> str:
    return f"{name} {loader} {mc_version} v{pack_version}"


def resolve_all(mc_version: str, loader: str) -> list[ResolvedMod]:
    """Resolve all mods from modlist + auto-dependencies."""
    mods = load_mods(loader)
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


def copy_configs(build_dir: Path, loader: str) -> bool:
    dst_config = build_dir / "config"
    copied = False

    for src_config in (common_profile_dir() / "config", list_profile_dir(loader) / "config"):
        if src_config.exists():
            shutil.copytree(src_config, dst_config, dirs_exist_ok=True)
            copied = True

    return copied


def copy_options(build_dir: Path, loader: str) -> bool:
    src = list_profile_dir(loader) / "options.txt"
    if src.exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, build_dir / "options.txt")
        return True
    return False


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
    update_url: str = "",
) -> BuildManifest:
    """Create a full build folder with mods, configs, and options."""
    folder_name = build_folder_name(name, mc_version, loader, pack_version)
    build_dir = BUILDS_DIR / folder_name
    mods_dir = build_dir / "mods"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    mods_dir.mkdir(parents=True, exist_ok=True)

    config_copied = copy_configs(build_dir, loader)
    options_copied = copy_options(build_dir, loader)

    downloaded, skipped = download_all(resolved, mods_dir)
    console.print(
        f"[green]{downloaded} downloaded[/green]"
        + (f", [yellow]{skipped} failed[/yellow]" if skipped else "")
    )
    generate_build_integrations(build_dir, name, mc_version, loader, pack_version, resolved, update_url)
    doctor = run_vartapack_doctor(build_dir, resolved)
    if doctor.status == "ok":
        console.print(f"[green]VartaPack Doctor:[/green] {doctor.message}")
    elif doctor.status == "skipped":
        console.print(f"[yellow]VartaPack Doctor skipped:[/yellow] {doctor.message}")
    else:
        console.print(f"[yellow]VartaPack Doctor {doctor.status}:[/yellow] {doctor.message}")
        if doctor.status == "error":
            raise RuntimeError(f"VartaPack Doctor failed: {doctor.message}")

    manifest = BuildManifest(
        minecraft_version=mc_version,
        loader=loader,
        pack_version=pack_version,
        build_folder=folder_name,
        resolved_mods=resolved,
        config_copied=config_copied,
        options_copied=options_copied,
    )
    manifest_path = build_dir / "build_manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2))

    return manifest
