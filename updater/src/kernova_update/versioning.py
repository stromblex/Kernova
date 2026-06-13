"""Version management: auto-increment, stability tags, and history tracking."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import ResolvedMod, VersionHistory, VersionHistoryEntry

STATE_DIR = Path(__file__).resolve().parent.parent.parent / "state"
HISTORY_FILE = STATE_DIR / "version_history.json"

# Total mods in modlist.json (used for percentage calculation)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIST_DIR = PROJECT_ROOT / "list"
BUILDS_DIR = PROJECT_ROOT / "builds"
CHANGELOGS_DIR = PROJECT_ROOT / "changelogs"
PUBLISH_DIR = PROJECT_ROOT / "publish"


def _modlist_path(loader: str) -> Path:
    profile_path = LIST_DIR / loader / "mods" / "modlist.json"
    if profile_path.exists():
        return profile_path
    return LIST_DIR / "mods" / "modlist.json"


def _total_mods_in_list(loader: str) -> int:
    """Count total mods defined in the loader-specific modlist."""
    modlist_path = _modlist_path(loader)
    if modlist_path.exists():
        data = json.loads(modlist_path.read_text())
        return len(data.get("mods", []))
    return 25  # fallback


def load_history() -> VersionHistory:
    if HISTORY_FILE.exists():
        data = json.loads(HISTORY_FILE.read_text())
        return VersionHistory(**data)
    return VersionHistory()


def save_history(history: VersionHistory) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(history.model_dump_json(indent=2))


def _artifact_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def entry_has_project_artifact(entry: VersionHistoryEntry) -> bool:
    """Return true when a history entry still has a generated project artifact.

    Version history is useful for increments, but it should not resurrect old
    versions after the project artifacts were intentionally cleaned.
    """
    display_name = f"{entry.build_name} {entry.loader} {entry.minecraft_version} v{entry.pack_version}"
    slug = _artifact_slug(display_name)
    candidates = [
        BUILDS_DIR / display_name / "build_manifest.json",
        CHANGELOGS_DIR / f"{display_name}.md",
        PUBLISH_DIR / "modrinth" / entry.minecraft_version / entry.loader / slug / f"{slug}.mrpack",
        PUBLISH_DIR / "full" / entry.minecraft_version / entry.loader / slug / f"{slug}-full.zip",
        PUBLISH_DIR / "prism" / entry.minecraft_version / entry.loader / slug / f"{slug}-prism.zip",
    ]
    return any(path.exists() for path in candidates)


def get_latest_entry(
    build_name: str,
    mc_version: str,
    loader: str,
    channel: str | None = None,
) -> VersionHistoryEntry | None:
    """Get the latest history entry for a given build name + MC version + loader."""
    history = load_history()
    matching = [
        e for e in history.entries
        if (
            e.minecraft_version == mc_version
            and e.build_name == build_name
            and e.loader == loader
            and (channel is None or pack_version_matches_channel(e.pack_version, channel))
            and entry_has_project_artifact(e)
        )
    ]
    return matching[-1] if matching else None


def get_latest_version(build_name: str, mc_version: str, loader: str, channel: str | None = None) -> str | None:
    entry = get_latest_entry(build_name, mc_version, loader, channel)
    return entry.pack_version if entry else None


def compute_versions_hash(resolved: list[ResolvedMod]) -> str:
    """Compute a hash of all resolved version IDs to detect changes."""
    ids = sorted(
        m.version_id for m in resolved if m.available and m.version_id
    )
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def has_changes(
    build_name: str,
    mc_version: str,
    loader: str,
    resolved: list[ResolvedMod],
) -> bool:
    """Check if anything changed since the last build."""
    entry = get_latest_entry(build_name, mc_version, loader)
    if entry is None:
        return True
    current_hash = compute_versions_hash(resolved)
    return current_hash != entry.mod_versions_hash


def stability_tag(available_count: int, loader: str) -> str:
    """Determine stability tag based on percentage of modlist resolved.

    - alpha: < 60% available
    - beta: 60-89% available
    - release: 90%+ available
    """
    total = _total_mods_in_list(loader)
    if total == 0:
        return "alpha"
    pct = available_count / total * 100
    if pct >= 90:
        return "release"
    elif pct >= 60:
        return "beta"
    else:
        return "alpha"


def suggest_next_version(build_name: str, mc_version: str, loader: str, channel: str = "release") -> str:
    """Suggest the next base version for a release channel."""
    latest = get_latest_version(build_name, mc_version, loader, channel)
    if latest is None:
        return initial_version_for_channel(channel)

    base = latest.split("-")[0]
    return increment_base_version(base, channel)


def initial_version_for_channel(channel: str) -> str:
    if channel == "release":
        return "1.0.0"
    if channel == "beta":
        return "0.1.0"
    return "0.0.1"


def increment_base_version(base: str, channel: str = "release") -> str:
    parts = base.split(".")
    if len(parts) != 3:
        return initial_version_for_channel(channel)

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if patch >= 9:
        minor += 1
        patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def pack_version_channel(pack_version: str) -> str:
    return pack_version.rsplit("-", 1)[-1] if "-" in pack_version else "release"


def pack_version_matches_channel(pack_version: str, channel: str) -> bool:
    if pack_version_channel(pack_version) != channel:
        return False
    base = pack_version.split("-")[0]
    parts = base.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return False
    major, minor, _patch = (int(part) for part in parts)
    if channel == "release":
        return major >= 1
    if channel == "beta":
        return major == 0 and minor >= 1
    if channel == "alpha":
        return major == 0 and minor == 0
    return True


def record_build(
    build_name: str,
    mc_version: str,
    loader: str,
    pack_version: str,
    mod_count: int,
    skipped_count: int,
    resolved: list[ResolvedMod] | None = None,
) -> None:
    """Record a build in version history."""
    history = load_history()
    entry = VersionHistoryEntry(
        build_name=build_name,
        minecraft_version=mc_version,
        loader=loader,
        pack_version=pack_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        mod_count=mod_count,
        skipped_count=skipped_count,
        mod_versions_hash=compute_versions_hash(resolved) if resolved else "",
    )
    for index, existing in enumerate(history.entries):
        if (
            existing.build_name == build_name
            and existing.minecraft_version == mc_version
            and existing.loader == loader
            and existing.pack_version == pack_version
        ):
            history.entries[index] = entry
            break
    else:
        history.entries.append(entry)
    save_history(history)
