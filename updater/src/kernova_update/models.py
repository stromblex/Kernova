"""Pydantic models for mod list and build state."""

from __future__ import annotations

from pydantic import BaseModel


class Mod(BaseModel):
    name: str
    slug: str | None = None
    modrinth_id: str | None = None
    category: str = ""
    source_packs: list[str] = []
    reason: str = ""
    priority: str = "required"
    confidence: int = 0
    risk: str = "low"
    dependencies: list[str] = []
    conflicts: list[str] = []
    config_files: list[str] = []
    notes: str = ""


class ModList(BaseModel):
    meta: dict
    mods: list[Mod]


class ResolvedMod(BaseModel):
    """A mod resolved against Modrinth for a specific MC version."""

    name: str
    slug: str | None = None
    modrinth_id: str | None = None
    priority: str = "required"
    version_id: str | None = None
    version_number: str | None = None
    filename: str | None = None
    url: str | None = None
    sha512: str | None = None
    sha1: str | None = None
    available: bool = False
    source: str = "list"  # list / dependency
    skipped_reason: str = ""


class BuildManifest(BaseModel):
    minecraft_version: str
    loader: str
    pack_version: str
    build_folder: str
    resolved_mods: list[ResolvedMod] = []
    config_copied: bool = False
    options_copied: bool = False


class VersionHistoryEntry(BaseModel):
    build_name: str = ""
    minecraft_version: str
    pack_version: str
    timestamp: str
    mod_count: int
    skipped_count: int
    mod_versions_hash: str = ""


class VersionHistory(BaseModel):
    entries: list[VersionHistoryEntry] = []
