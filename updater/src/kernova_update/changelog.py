"""Changelog generation — short, Modrinth-ready format."""

from __future__ import annotations

import json
from pathlib import Path

from .models import BuildManifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHANGELOGS_DIR = PROJECT_ROOT / "changelogs"
BUILDS_DIR = PROJECT_ROOT / "builds"


def generate_changelog(manifest: BuildManifest) -> Path:
    """Generate a short changelog for Modrinth."""
    CHANGELOGS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{manifest.build_folder}.md"
    path = CHANGELOGS_DIR / filename

    previous = find_previous_manifest(manifest)
    available = [m for m in manifest.resolved_mods if m.available]
    skipped = [m for m in manifest.resolved_mods if not m.available]
    listed = [m for m in manifest.resolved_mods if m.source == "list"]
    listed_available = [m for m in listed if m.available]
    dependencies = [m for m in manifest.resolved_mods if m.source == "dependency"]
    dependencies_available = [m for m in dependencies if m.available]

    lines: list[str] = [
        f"## {manifest.build_folder}",
        "",
    ]

    if previous:
        lines.extend(["### Changes", "", *change_lines(manifest, previous), ""])

    lines.extend([
        "### Build Details",
        "",
        build_summary_line(manifest),
        "",
    ])

    if previous:
        path.write_text("\n".join(lines).strip() + "\n")
        return path

    lines.extend(["### Included Mods", ""])

    for m in sorted(listed_available, key=lambda x: x.name):
        lines.append(f"- {m.name} {m.version_number or ''}")

    if dependencies_available:
        lines.append("")
        lines.append("### Auto Dependencies")
        lines.append("")
        for m in sorted(dependencies_available, key=lambda x: x.name):
            lines.append(f"- {m.name} {m.version_number or ''}")

    if skipped:
        lines.append("")
        lines.append("### Unavailable")
        lines.append("")
        for m in sorted(skipped, key=lambda x: x.name):
            lines.append(f"- ~~{m.name}~~ — {m.skipped_reason}")

    lines.append("")
    path.write_text("\n".join(lines))
    return path


def build_summary_line(manifest: BuildManifest) -> str:
    available = [m for m in manifest.resolved_mods if m.available]
    listed = [m for m in manifest.resolved_mods if m.source == "list"]
    listed_available = [m for m in listed if m.available]
    dependencies_available = [
        m for m in manifest.resolved_mods
        if m.source == "dependency" and m.available
    ]
    return (
        f"Minecraft {manifest.minecraft_version} | {manifest.loader} | "
        f"{len(listed_available)}/{len(listed)} listed mods + "
        f"{len(dependencies_available)} dependencies = {len(available)} available"
    )


def find_previous_manifest(manifest: BuildManifest) -> BuildManifest | None:
    candidates: list[BuildManifest] = []
    current_channel = pack_version_channel(manifest.pack_version)
    for manifest_path in BUILDS_DIR.glob("*/build_manifest.json"):
        try:
            candidate = BuildManifest(**json.loads(manifest_path.read_text()))
        except Exception:
            continue
        if not same_lineage(manifest, candidate):
            continue
        if candidate.pack_version == manifest.pack_version:
            continue
        if pack_version_channel(candidate.pack_version) != current_channel:
            continue
        if version_sort_key(candidate.pack_version) >= version_sort_key(manifest.pack_version):
            continue
        candidates.append(candidate)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: version_sort_key(item.pack_version))[-1]


def same_lineage(current: BuildManifest, candidate: BuildManifest) -> bool:
    return (
        candidate.minecraft_version == current.minecraft_version
        and candidate.loader == current.loader
        and build_name_base(candidate) == build_name_base(current)
    )


def build_name_base(manifest: BuildManifest) -> str:
    suffix = f" {manifest.loader} {manifest.minecraft_version} v{manifest.pack_version}"
    if manifest.build_folder.endswith(suffix):
        return manifest.build_folder[:-len(suffix)]
    return manifest.build_folder.rsplit(" v", 1)[0]


def change_lines(manifest: BuildManifest, previous: BuildManifest) -> list[str]:
    current_mods = available_mod_index(manifest)
    previous_mods = available_mod_index(previous)
    added = [current_mods[key] for key in sorted(current_mods.keys() - previous_mods.keys())]
    removed = [previous_mods[key] for key in sorted(previous_mods.keys() - current_mods.keys())]
    updated = [
        (previous_mods[key], current_mods[key])
        for key in sorted(current_mods.keys() & previous_mods.keys())
        if mod_signature(previous_mods[key]) != mod_signature(current_mods[key])
    ]

    if not added and not removed and not updated:
        return [
            f"- No mod version changes since `{previous.pack_version}`; this rebuild contains config, metadata, or feed changes only."
        ]

    lines: list[str] = []
    for old, new in updated:
        lines.append(f"- Updated {new.name}: {mod_label(old)} -> {mod_label(new)}")
    for mod in added:
        lines.append(f"- Added {mod.name} {mod_label(mod)}".rstrip())
    for mod in removed:
        lines.append(f"- Removed {mod.name} {mod_label(mod)}".rstrip())
    return lines


def available_mod_index(manifest: BuildManifest) -> dict[str, object]:
    return {mod_identity(mod): mod for mod in manifest.resolved_mods if mod.available}


def mod_identity(mod: object) -> str:
    for attr in ("modrinth_id", "slug", "name", "filename"):
        value = str(getattr(mod, attr) or "").strip().lower()
        if value:
            return value
    return "unknown"


def mod_signature(mod: object) -> tuple[str, str, str]:
    return (
        str(getattr(mod, "version_id") or ""),
        str(getattr(mod, "version_number") or ""),
        str(getattr(mod, "filename") or ""),
    )


def mod_label(mod: object) -> str:
    return str(getattr(mod, "version_number") or getattr(mod, "filename") or "unknown")


def pack_version_channel(pack_version: str) -> str:
    return pack_version.rsplit("-", 1)[-1] if "-" in pack_version else "release"


def version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    pieces = version.replace("+", "-").split("-")
    out: list[tuple[int, int | str]] = []
    for piece in ".".join(pieces).split("."):
        out.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(out)
