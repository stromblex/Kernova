"""Changelog generation — short, Modrinth-ready format."""

from __future__ import annotations

from pathlib import Path

from .models import BuildManifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CHANGELOGS_DIR = PROJECT_ROOT / "changelogs"


def generate_changelog(manifest: BuildManifest) -> Path:
    """Generate a short changelog for Modrinth."""
    CHANGELOGS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{manifest.build_folder}.md"
    path = CHANGELOGS_DIR / filename

    available = [m for m in manifest.resolved_mods if m.available]
    skipped = [m for m in manifest.resolved_mods if not m.available]
    listed = [m for m in manifest.resolved_mods if m.source == "list"]
    listed_available = [m for m in listed if m.available]
    dependencies = [m for m in manifest.resolved_mods if m.source == "dependency"]
    dependencies_available = [m for m in dependencies if m.available]

    lines: list[str] = [
        f"## {manifest.build_folder}",
        "",
        (
            f"Minecraft {manifest.minecraft_version} | {manifest.loader} | "
            f"{len(listed_available)}/{len(listed)} listed mods + "
            f"{len(dependencies_available)} dependencies = {len(available)} available"
        ),
        "",
        "### Mods",
        "",
    ]

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
