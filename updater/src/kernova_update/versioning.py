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
MODLIST_PATH = PROJECT_ROOT / "list" / "mods" / "modlist.json"


def _total_mods_in_list() -> int:
    """Count total mods defined in modlist.json."""
    if MODLIST_PATH.exists():
        data = json.loads(MODLIST_PATH.read_text())
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


def get_latest_entry(build_name: str, mc_version: str) -> VersionHistoryEntry | None:
    """Get the latest history entry for a given build name + MC version."""
    history = load_history()
    matching = [
        e for e in history.entries
        if e.minecraft_version == mc_version and e.build_name == build_name
    ]
    return matching[-1] if matching else None


def get_latest_version(build_name: str, mc_version: str) -> str | None:
    entry = get_latest_entry(build_name, mc_version)
    return entry.pack_version if entry else None


def compute_versions_hash(resolved: list[ResolvedMod]) -> str:
    """Compute a hash of all resolved version IDs to detect changes."""
    ids = sorted(
        m.version_id for m in resolved if m.available and m.version_id
    )
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]


def has_changes(build_name: str, mc_version: str, resolved: list[ResolvedMod]) -> bool:
    """Check if anything changed since the last build."""
    entry = get_latest_entry(build_name, mc_version)
    if entry is None:
        return True
    current_hash = compute_versions_hash(resolved)
    return current_hash != entry.mod_versions_hash


def stability_tag(available_count: int) -> str:
    """Determine stability tag based on percentage of modlist resolved.

    - alpha: < 60% available
    - beta: 60-89% available
    - release: 90%+ available
    """
    total = _total_mods_in_list()
    if total == 0:
        return "alpha"
    pct = available_count / total * 100
    if pct >= 90:
        return "release"
    elif pct >= 60:
        return "beta"
    else:
        return "alpha"


def suggest_next_version(build_name: str, mc_version: str) -> str:
    """Suggest the next version: 1.0.0 first, then patch bumps."""
    latest = get_latest_version(build_name, mc_version)
    if latest is None:
        return "1.0.0"

    base = latest.split("-")[0]
    parts = base.split(".")
    if len(parts) != 3:
        return "1.0.0"

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    return f"{major}.{minor}.{patch + 1}"


def record_build(
    build_name: str,
    mc_version: str,
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
        pack_version=pack_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        mod_count=mod_count,
        skipped_count=skipped_count,
        mod_versions_hash=compute_versions_hash(resolved) if resolved else "",
    )
    history.entries.append(entry)
    save_history(history)
