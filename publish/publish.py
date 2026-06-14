#!/usr/bin/env python3
"""Package and publish Kernova builds to Modrinth, CurseForge, and PackPing."""

from __future__ import annotations

import argparse
import copy
import html
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BUILDS_DIR = PROJECT_ROOT / "builds"
CHANGELOGS_DIR = PROJECT_ROOT / "changelogs"
CONFIG_PATH = SCRIPT_DIR / "config.json"
SECRETS_PATH = SCRIPT_DIR / "secrets.json"

MODRINTH_DIR = SCRIPT_DIR / "modrinth"
CURSEFORGE_DIR = SCRIPT_DIR / "curseforge"
FULL_DIR = SCRIPT_DIR / "full"
PRISM_DIR = SCRIPT_DIR / "prism"
PACKPING_DIR = SCRIPT_DIR / "packping"
CURSEFORGE_FINGERPRINTS_URL = "https://api.curseforge.com/v1/fingerprints"
CURSEFORGE_FINGERPRINT_WHITESPACE = {9, 10, 13, 32}


def load_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def project_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return value.strip("-") or "kernova"


def build_dir_from_arg(build: str | None, latest: bool, loader: str | None, mc: str | None) -> Path:
    if build:
        path = Path(build)
        if not path.is_absolute():
            path = BUILDS_DIR / build
        if not path.exists():
            raise FileNotFoundError(f"Build folder not found: {path}")
        return path

    if not latest:
        raise ValueError("Use --build <folder> or --latest.")

    candidates: list[Path] = []
    for manifest_path in BUILDS_DIR.glob("*/build_manifest.json"):
        manifest = load_json(manifest_path)
        if loader and manifest.get("loader") != loader:
            continue
        if mc and manifest.get("minecraft_version") != mc:
            continue
        candidates.append(manifest_path.parent)
    if not candidates:
        raise FileNotFoundError("No matching build_manifest.json found in builds/.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_manifest(build_dir: Path) -> dict[str, Any]:
    return load_json(build_dir / "build_manifest.json")


def build_display_name(manifest: dict[str, Any]) -> str:
    return manifest.get("build_folder") or (
        f"Kernova {manifest['loader']} {manifest['minecraft_version']} v{manifest['pack_version']}"
    )


def artifact_slug(manifest: dict[str, Any]) -> str:
    return slugify(build_display_name(manifest))


def platform_dir(base: Path, manifest: dict[str, Any]) -> Path:
    return base / manifest["minecraft_version"] / manifest["loader"] / artifact_slug(manifest)


def changelog_path(manifest: dict[str, Any]) -> Path:
    return CHANGELOGS_DIR / f"{build_display_name(manifest)}.md"


def available_mods(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [mod for mod in manifest.get("resolved_mods", []) if mod.get("available")]


def skipped_mods(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [mod for mod in manifest.get("resolved_mods", []) if not mod.get("available")]


def sha1(path: Path) -> str:
    return _hash(path, "sha1")


def sha512(path: Path) -> str:
    return _hash(path, "sha512")


def _hash(path: Path, algo: str) -> str:
    digest = hashlib.new(algo)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path: Path) -> int:
    return path.stat().st_size


def icon_path(config: dict[str, Any]) -> Path | None:
    value = config.get("icon", "")
    if not value:
        return None
    path = project_path(str(value))
    return path if path.exists() and path.is_file() else None


def write_icon(zf: zipfile.ZipFile, config: dict[str, Any], target: str) -> None:
    path = icon_path(config)
    if path:
        zf.writestr(target, path.read_bytes())


def iter_override_files(build_dir: Path) -> list[Path]:
    out: list[Path] = []
    for path in build_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(build_dir)
        if rel.parts[0] == "mods":
            continue
        if rel.name == "build_manifest.json":
            continue
        out.append(path)
    return sorted(out)


def iter_curseforge_override_files(build_dir: Path, excluded_paths: set[Path] | None = None) -> list[Path]:
    excluded = {path.resolve() for path in (excluded_paths or set())}
    out: list[Path] = []
    for path in build_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name == "build_manifest.json":
            continue
        if path.resolve() in excluded:
            continue
        out.append(path)
    return sorted(out)


def generated_changelog(manifest: dict[str, Any]) -> str:
    listed = [m for m in manifest["resolved_mods"] if m.get("source") == "list"]
    listed_available = [m for m in listed if m.get("available")]
    dependencies = [m for m in manifest["resolved_mods"] if m.get("source") == "dependency" and m.get("available")]
    skipped = skipped_mods(manifest)

    lines = [
        f"## {build_display_name(manifest)}",
        "",
        (
            f"Minecraft {manifest['minecraft_version']} | {manifest['loader']} | "
            f"{len(listed_available)}/{len(listed)} listed mods + "
            f"{len(dependencies)} dependencies = {len(available_mods(manifest))} available"
        ),
        "",
        "### Mods",
        "",
    ]
    for mod in sorted(listed_available, key=lambda item: item["name"].lower()):
        lines.append(f"- {mod['name']} {mod.get('version_number') or ''}".rstrip())

    if dependencies:
        lines.extend(["", "### Auto Dependencies", ""])
        for mod in sorted(dependencies, key=lambda item: item["name"].lower()):
            lines.append(f"- {mod['name']} {mod.get('version_number') or ''}".rstrip())

    if skipped:
        lines.extend(["", "### Unavailable", ""])
        for mod in sorted(skipped, key=lambda item: item["name"].lower()):
            lines.append(f"- ~~{mod['name']}~~ - {mod.get('skipped_reason') or 'Unavailable'}")

    return "\n".join(lines).strip() + "\n"


def compose_changelog(manifest: dict[str, Any], notes_file: str | None = None) -> str:
    generated = generated_changelog(manifest).strip()
    manual_notes = read_manual_notes(manifest, notes_file)
    if not manual_notes:
        return generated + "\n"

    generated_body = strip_heading(generated)
    return "\n".join(
        [
            f"## {build_display_name(manifest)}",
            "",
            "### Release Notes",
            "",
            manual_notes.strip(),
            "",
            generated_body.strip(),
            "",
        ]
    )


def read_manual_notes(manifest: dict[str, Any], notes_file: str | None) -> str:
    candidates: list[Path] = []
    if notes_file:
        candidates.append(project_path(notes_file))
    candidates.append(SCRIPT_DIR / "notes" / f"{manifest['minecraft_version']}-{manifest['loader']}.md")
    candidates.append(SCRIPT_DIR / "notes" / f"{manifest['minecraft_version']}.md")

    build_changelog = CHANGELOGS_DIR / f"{build_display_name(manifest)}.md"
    if build_changelog.exists():
        extracted = extract_release_notes(build_changelog.read_text())
        if extracted:
            return extracted

    for path in candidates:
        if path.exists():
            return strip_heading(path.read_text()).strip()
    return ""


def extract_release_notes(markdown: str) -> str:
    body = strip_heading(markdown)
    sections = split_markdown_sections(body)
    for title in ("release notes", "changes", "notes"):
        if title in sections:
            return sections[title].strip()
    return ""


def split_markdown_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        match = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def strip_heading(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    if lines and lines[0].startswith("#"):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def create_mrpack(
    build_dir: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
    out_dir: Path,
) -> Path:
    name = build_display_name(manifest)
    artifact = out_dir / f"{artifact_slug(manifest)}.mrpack"
    out_dir.mkdir(parents=True, exist_ok=True)
    dependencies = {"minecraft": manifest["minecraft_version"]}
    dependencies.update(resolve_loader_dependencies(manifest, config))
    dependencies = {key: value for key, value in dependencies.items() if value}

    files = []
    for mod in available_mods(manifest):
        filename = mod.get("filename")
        url = mod.get("url")
        if not filename or not url:
            continue
        jar_path = build_dir / "mods" / filename
        if not jar_path.exists():
            continue
        files.append(
            {
                "path": f"mods/{filename}",
                "hashes": {
                    "sha1": mod.get("sha1") or sha1(jar_path),
                    "sha512": mod.get("sha512") or sha512(jar_path),
                },
                "env": {"client": "required", "server": "unsupported"},
                "downloads": [url],
                "fileSize": file_size(jar_path),
            }
        )

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": manifest["pack_version"],
        "name": name,
        "summary": "Kernova performance-focused Minecraft modpack.",
        "files": files,
        "dependencies": dependencies,
    }

    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("modrinth.index.json", json.dumps(index, indent=2) + "\n")
        write_icon(zf, config, "overrides/icon.png")
        for path in iter_override_files(build_dir):
            rel = path.relative_to(build_dir)
            zf.write(path, f"overrides/{rel.as_posix()}")

    return artifact


def resolve_loader_dependencies(manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    loader = manifest["loader"]
    mc = manifest["minecraft_version"]
    configured = config["modrinth"].get("loader_dependencies", {}).get(loader, {})
    resolved: dict[str, str] = {}

    for dependency, value in configured.items():
        version = resolve_loader_dependency_value(dependency, value, mc)
        if version:
            resolved[dependency] = version
        else:
            print(
                f"[WARN] No Modrinth loader dependency for {dependency} on Minecraft {mc}. "
                "Fill publish/config.json if the .mrpack importer needs it."
            )
    return resolved


def resolve_loader_dependency_value(dependency: str, value: Any, mc: str) -> str:
    if isinstance(value, dict):
        value = value.get(mc) or value.get("default") or ""
    if value == "auto":
        return auto_loader_dependency(dependency, mc)
    return str(value or "")


def auto_loader_dependency(dependency: str, mc: str) -> str:
    try:
        if dependency == "fabric-loader":
            return latest_maven_version(
                "https://maven.fabricmc.net/net/fabricmc/fabric-loader/maven-metadata.xml"
            )
        if dependency == "neoforge":
            return latest_neoforge_version(mc)
    except Exception as error:
        print(f"[WARN] Could not auto-resolve {dependency}: {error}")
    return ""


def latest_neoforge_version(mc: str) -> str:
    prefix = neoforge_maven_prefix(mc)
    if not prefix:
        return ""
    versions = maven_versions(
        "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
    )
    matches = [version for version in versions if version.startswith(prefix)]
    return sorted(matches, key=version_sort_key)[-1] if matches else ""


def neoforge_maven_prefix(mc: str) -> str:
    parts = mc.split(".")
    if len(parts) < 2:
        return ""
    if parts[0] == "1":
        minecraft_minor = parts[1]
        minecraft_patch = parts[2] if len(parts) > 2 else "0"
        return f"{minecraft_minor}.{minecraft_patch}."
    else:
        minecraft_major = parts[0]
        minecraft_minor = parts[1]
        minecraft_patch = parts[2] if len(parts) > 2 else "0"
        return f"{minecraft_major}.{minecraft_minor}.{minecraft_patch}."


def latest_maven_version(url: str) -> str:
    versions = maven_versions(url)
    return sorted(versions, key=version_sort_key)[-1] if versions else ""


def maven_versions(url: str) -> list[str]:
    with urllib.request.urlopen(url, timeout=20) as response:
        root = ET.fromstring(response.read())
    versions = root.findall("./versioning/versions/version")
    return [node.text for node in versions if node.text]


def version_sort_key(version: str) -> tuple[tuple[int, int | str], ...]:
    pieces = re.split(r"[.+-]", version)
    return tuple((0, int(piece)) if piece.isdigit() else (1, piece) for piece in pieces)


def dedupe_ints(values: list[Any]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            item = int(value)
        except (TypeError, ValueError):
            continue
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def curseforge_upload_artifact(manifest: dict[str, Any], config: dict[str, Any]) -> Path:
    out_dir = platform_dir(CURSEFORGE_DIR, manifest)
    return out_dir / f"{artifact_slug(manifest)}-curseforge.zip"


def curseforge_upload_metadata(
    manifest: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any],
    allow_network: bool,
) -> tuple[dict[str, Any], list[str]]:
    cf = config["curseforge"]
    missing: list[str] = []
    minecraft_id = curseforge_minecraft_version_id(
        manifest["minecraft_version"],
        cf,
        secrets.get("curseforge_token", ""),
        allow_network=allow_network,
    )
    if not minecraft_id:
        missing.append(f"CurseForge Minecraft game version id for {manifest['minecraft_version']}")

    game_versions = dedupe_ints(
        ([minecraft_id] if minecraft_id else [])
        + list(cf.get("game_versions", []))
        + list(cf.get("java_versions", []))
        + list(cf.get("environment", []))
        + list(cf.get("loaders", {}).get(manifest["loader"], []))
    )
    metadata = {
        "changelog": changelog_path(manifest).read_text().replace("\n", "\r\n"),
        "changelogType": "markdown",
        "displayName": build_display_name(manifest),
        "gameVersions": game_versions,
        "releaseType": cf["release_type"],
    }
    if not game_versions:
        missing.append("CurseForge gameVersions")
    return metadata, missing


def curseforge_minecraft_version_id(
    minecraft: str,
    curseforge_config: dict[str, Any],
    token: str,
    allow_network: bool,
) -> int | None:
    if allow_network and token:
        try:
            versions = fetch_curseforge_game_versions(token)
            version_types = fetch_curseforge_version_types(token)
            resolved = select_curseforge_minecraft_version_id(minecraft, curseforge_config, versions, version_types)
            if resolved:
                return resolved
        except Exception as error:
            print(f"[WARN] Could not resolve CurseForge Minecraft id for {minecraft}: {error}")

    cached = curseforge_config.get("minecraft_versions", {})
    value = cached.get(minecraft) if isinstance(cached, dict) else None
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def select_curseforge_minecraft_version_id(
    minecraft: str,
    curseforge_config: dict[str, Any],
    versions: list[dict[str, Any]],
    version_types: list[dict[str, Any]],
) -> int | None:
    candidates = [item for item in versions if item.get("name") == minecraft]
    if not candidates:
        return None

    preferred_type = configured_curseforge_minecraft_type_id(minecraft, curseforge_config)
    if preferred_type:
        for item in candidates:
            if item.get("gameVersionTypeID") == preferred_type:
                return int(item["id"])

    type_names = {item.get("id"): item.get("name", "") for item in version_types}
    series = minecraft_series(minecraft)
    for item in candidates:
        if type_names.get(item.get("gameVersionTypeID")) == f"Minecraft {series}":
            return int(item["id"])
    for item in candidates:
        if str(type_names.get(item.get("gameVersionTypeID"), "")).startswith("Minecraft "):
            return int(item["id"])
    for item in candidates:
        if type_names.get(item.get("gameVersionTypeID")) != "Addons":
            return int(item["id"])
    return int(candidates[0]["id"])


def configured_curseforge_minecraft_type_id(minecraft: str, curseforge_config: dict[str, Any]) -> int | None:
    configured = curseforge_config.get("minecraft_version_type_ids", {})
    if not isinstance(configured, dict):
        return None
    value = configured.get(minecraft) or configured.get(minecraft_series(minecraft))
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def minecraft_series(minecraft: str) -> str:
    parts = minecraft.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else minecraft


def fetch_curseforge_game_versions(token: str) -> list[dict[str, Any]]:
    return fetch_curseforge_api_json("https://minecraft.curseforge.com/api/game/versions", token)


def fetch_curseforge_version_types(token: str) -> list[dict[str, Any]]:
    return fetch_curseforge_api_json("https://minecraft.curseforge.com/api/game/version-types", token)


def fetch_curseforge_api_json(url: str, token: str) -> list[dict[str, Any]]:
    req = urllib.request.Request(url, headers={"X-Api-Token": token})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def curseforge_fingerprint_api_key(secrets: dict[str, Any] | None) -> str:
    values = secrets or {}
    return str(
        values.get("curseforge_core_api_key")
        or values.get("curseforge_api_key")
        or values.get("curseforge_token")
        or ""
    ).strip()


def curseforge_file_fingerprint(path: Path) -> int:
    data = bytes(byte for byte in path.read_bytes() if byte not in CURSEFORGE_FINGERPRINT_WHITESPACE)
    return murmur2_unsigned(data)


def murmur2_unsigned(data: bytes, seed: int = 1) -> int:
    """CurseForge Core API uses the Minecraft MurmurHash2 file fingerprint."""
    m = 0x5BD1E995
    r = 24
    length = len(data)
    h = (seed ^ length) & 0xFFFFFFFF

    rounded_end = length & ~3
    for offset in range(0, rounded_end, 4):
        k = int.from_bytes(data[offset:offset + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k ^= (k & 0xFFFFFFFF) >> r
        k = (k * m) & 0xFFFFFFFF

        h = (h * m) & 0xFFFFFFFF
        h ^= k

    tail = data[rounded_end:]
    if len(tail) == 3:
        h ^= tail[2] << 16
    if len(tail) >= 2:
        h ^= tail[1] << 8
    if len(tail) >= 1:
        h ^= tail[0]
        h = (h * m) & 0xFFFFFFFF

    h ^= (h & 0xFFFFFFFF) >> 13
    h = (h * m) & 0xFFFFFFFF
    h ^= (h & 0xFFFFFFFF) >> 15
    return h & 0xFFFFFFFF


def curseforge_fingerprint_matches(fingerprints: list[int], token: str) -> dict[int, dict[str, Any]]:
    if not fingerprints:
        return {}
    if not token:
        raise ValueError(curseforge_fingerprint_auth_message())

    body = json.dumps({"fingerprints": fingerprints}).encode("utf-8")
    request = urllib.request.Request(
        CURSEFORGE_FINGERPRINTS_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-api-key": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ValueError(curseforge_fingerprint_auth_message()) from error
        raise

    data = payload.get("data", payload)
    exact_matches = data.get("exactMatches", []) if isinstance(data, dict) else []
    matches: dict[int, dict[str, Any]] = {}
    for match in exact_matches:
        parsed = parse_curseforge_fingerprint_match(match)
        if parsed:
            fingerprint, entry = parsed
            matches[fingerprint] = entry
    return matches


def curseforge_fingerprint_auth_message() -> str:
    return (
        "CurseForge Core API key is required to build a publishable modpack manifest. "
        "The legacy upload token is not always accepted by the fingerprint API. "
        "Add curseforge_core_api_key to publish/secrets.json, or provide explicit "
        "CurseForge project/file ids before packaging."
    )


def parse_curseforge_fingerprint_match(match: Any) -> tuple[int, dict[str, Any]] | None:
    if not isinstance(match, dict):
        return None
    file_data = match.get("file") if isinstance(match.get("file"), dict) else {}
    fingerprint = first_int(
        file_data.get("fileFingerprint"),
        file_data.get("packageFingerprint"),
        match.get("fingerprint"),
        match.get("fileFingerprint"),
    )
    project_id = first_int(
        file_data.get("modId"),
        file_data.get("modID"),
        file_data.get("projectId"),
        file_data.get("projectID"),
        match.get("modId"),
        match.get("modID"),
        match.get("projectId"),
        match.get("projectID"),
        match.get("id"),
    )
    file_id = first_int(
        file_data.get("id"),
        file_data.get("fileId"),
        file_data.get("fileID"),
        match.get("fileId"),
        match.get("fileID"),
    )
    if fingerprint is None or project_id is None or file_id is None:
        return None
    entry = {
        "projectID": project_id,
        "fileID": file_id,
        "required": True,
        "isLocked": False,
    }
    filename = file_data.get("fileName") or file_data.get("displayName")
    if filename:
        entry["_filename"] = str(filename)
    return fingerprint, entry


def first_int(*values: Any) -> int | None:
    for value in values:
        try:
            if value is not None and value != "":
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def configured_curseforge_file_entry(mod: dict[str, Any], config: dict[str, Any]) -> dict[str, Any] | None:
    overrides = config.get("curseforge", {}).get("file_overrides", {})
    if not isinstance(overrides, dict):
        return None

    keys = [
        mod.get("modrinth_id"),
        mod.get("slug"),
        mod.get("filename"),
        mod.get("name"),
    ]
    for key in keys:
        if not key:
            continue
        value = overrides.get(str(key))
        if isinstance(value, dict):
            return clean_curseforge_file_entry(value)
    return None


def clean_curseforge_file_entry(entry: dict[str, Any]) -> dict[str, Any]:
    project_id = first_int(
        entry.get("projectID"),
        entry.get("projectId"),
        entry.get("project_id"),
        entry.get("modId"),
        entry.get("modID"),
    )
    file_id = first_int(entry.get("fileID"), entry.get("fileId"), entry.get("file_id"), entry.get("id"))
    if project_id is None or file_id is None:
        raise ValueError("CurseForge file override must include projectID and fileID.")
    return {
        "projectID": project_id,
        "fileID": file_id,
        "required": bool(entry.get("required", True)),
        "isLocked": bool(entry.get("isLocked", False)),
    }


def curseforge_modpack_file_entries(
    build_dir: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], set[Path]]:
    mod_paths: list[tuple[dict[str, Any], Path, int]] = []
    entries: list[dict[str, Any]] = []
    excluded_paths: set[Path] = set()
    seen_entries: set[tuple[int, int]] = set()
    for mod in available_mods(manifest):
        filename = mod.get("filename")
        if not filename:
            continue
        jar_path = build_dir / "mods" / filename
        if not jar_path.exists():
            continue
        configured_entry = configured_curseforge_file_entry(mod, config)
        if configured_entry:
            key = (configured_entry["projectID"], configured_entry["fileID"])
            if key not in seen_entries:
                seen_entries.add(key)
                entries.append(configured_entry)
            excluded_paths.add(jar_path)
            continue
        mod_paths.append((mod, jar_path, curseforge_file_fingerprint(jar_path)))

    if not mod_paths:
        return sorted(entries, key=lambda item: (item["projectID"], item["fileID"])), excluded_paths

    fingerprints = [fingerprint for _, _, fingerprint in mod_paths]
    matches = curseforge_fingerprint_matches(fingerprints, curseforge_fingerprint_api_key(secrets))
    allow_unmatched = bool(config.get("curseforge", {}).get("allow_unmatched_override_mods", False))

    unmatched: list[str] = []
    for mod, jar_path, fingerprint in mod_paths:
        entry = matches.get(fingerprint)
        if not entry:
            unmatched.append(f"{mod.get('name') or jar_path.name} ({jar_path.name})")
            continue

        cleaned = clean_curseforge_file_entry(entry)
        key = (cleaned["projectID"], cleaned["fileID"])
        if key not in seen_entries:
            seen_entries.add(key)
            entries.append(cleaned)
        excluded_paths.add(jar_path)

    if unmatched and not allow_unmatched:
        listed = "\n".join(f"  - {item}" for item in unmatched)
        raise ValueError(
            "Could not match every mod jar to a CurseForge project/file. "
            "CurseForge-hosted mods must be referenced in manifest.json, not embedded in overrides/mods.\n"
            f"{listed}\n"
            "If a listed mod is an approved Non-CurseForge override mod, set "
            "curseforge.allow_unmatched_override_mods=true in publish/config.json."
        )

    return sorted(entries, key=lambda item: (item["projectID"], item["fileID"])), excluded_paths


def create_full_zip(build_dir: Path, manifest: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"{artifact_slug(manifest)}-full.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file() and path.name != "build_manifest.json":
                zf.write(path, path.relative_to(build_dir).as_posix())
    return artifact


def create_curseforge_modpack_zip(
    build_dir: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
    out_dir: Path,
    secrets: dict[str, Any] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"{artifact_slug(manifest)}-curseforge.zip"
    loader_dependencies = resolve_loader_dependencies(manifest, config)
    files, excluded_mod_paths = curseforge_modpack_file_entries(build_dir, manifest, config, secrets)
    cf_manifest = {
        "minecraft": {
            "version": manifest["minecraft_version"],
            "modLoaders": [
                {
                    "id": curseforge_modloader_id(manifest, loader_dependencies),
                    "primary": True,
                }
            ],
        },
        "manifestType": "minecraftModpack",
        "manifestVersion": 1,
        "name": build_display_name(manifest),
        "version": manifest["pack_version"],
        "author": config.get("author", ""),
        "files": files,
        "overrides": "overrides",
    }

    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(cf_manifest, indent=2) + "\n")
        zf.writestr("modlist.html", curseforge_modlist_html(manifest))
        write_icon(zf, config, "overrides/icon.png")
        for path in iter_curseforge_override_files(build_dir, excluded_mod_paths):
            rel = path.relative_to(build_dir)
            zf.write(path, f"overrides/{rel.as_posix()}")

    return artifact


def curseforge_modlist_html(manifest: dict[str, Any]) -> str:
    lines = ["<ul>"]
    for mod in sorted(available_mods(manifest), key=lambda item: str(item.get("name", "")).lower()):
        name = html.escape(str(mod.get("name") or mod.get("filename") or "Unknown mod"))
        slug = mod.get("slug")
        if slug:
            url = "https://www.curseforge.com/minecraft/search?" + urllib.parse.urlencode(
                {"search": str(slug)}
            )
            lines.append(f'<li><a href="{html.escape(url)}">{name}</a></li>')
        else:
            lines.append(f"<li>{name}</li>")
    lines.append("</ul>")
    return "\n".join(lines) + "\n"


def curseforge_modloader_id(manifest: dict[str, Any], loader_dependencies: dict[str, str]) -> str:
    loader = manifest["loader"]
    if loader == "fabric":
        return f"fabric-{loader_dependencies.get('fabric-loader', '')}".rstrip("-")
    if loader == "neoforge":
        return f"neoforge-{loader_dependencies.get('neoforge', '')}".rstrip("-")
    return loader


def create_prism_zip(
    build_dir: Path,
    manifest: dict[str, Any],
    config: dict[str, Any],
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = out_dir / f"{artifact_slug(manifest)}-prism.zip"
    loader_dependencies = resolve_loader_dependencies(manifest, config)
    mmc_pack = {
        "components": prism_components(manifest, loader_dependencies),
        "formatVersion": 1,
    }

    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mmc-pack.json", json.dumps(mmc_pack, indent=4) + "\n")
        zf.writestr("instance.cfg", prism_instance_cfg(manifest))
        write_icon(zf, config, "minecraft/icon.png")
        for path in sorted(build_dir.rglob("*")):
            if path.is_file() and path.name != "build_manifest.json":
                rel = path.relative_to(build_dir)
                zf.write(path, f"minecraft/{rel.as_posix()}")
    return artifact


def prism_components(manifest: dict[str, Any], loader_dependencies: dict[str, str]) -> list[dict[str, Any]]:
    mc = manifest["minecraft_version"]
    components: list[dict[str, Any]] = [
        {
            "cachedName": "LWJGL 3",
            "cachedVersion": "3.4.1",
            "cachedVolatile": True,
            "dependencyOnly": True,
            "uid": "org.lwjgl3",
            "version": "3.4.1",
        },
        {
            "cachedName": "Minecraft",
            "cachedRequires": [{"suggests": "3.4.1", "uid": "org.lwjgl3"}],
            "cachedVersion": mc,
            "important": True,
            "uid": "net.minecraft",
            "version": mc,
        },
    ]

    if manifest["loader"] == "fabric":
        loader_version = loader_dependencies.get("fabric-loader", "")
        components.extend(
            [
                {
                    "cachedName": "Intermediary Mappings",
                    "cachedRequires": [{"equals": mc, "uid": "net.minecraft"}],
                    "cachedVersion": mc,
                    "cachedVolatile": True,
                    "dependencyOnly": True,
                    "uid": "net.fabricmc.intermediary",
                    "version": mc,
                },
                {
                    "cachedName": "Fabric Loader",
                    "cachedRequires": [{"uid": "net.fabricmc.intermediary"}],
                    "cachedVersion": loader_version,
                    "uid": "net.fabricmc.fabric-loader",
                    "version": loader_version,
                },
            ]
        )
    elif manifest["loader"] == "neoforge":
        loader_version = loader_dependencies.get("neoforge", "")
        components.append(
            {
                "cachedName": "NeoForge",
                "cachedRequires": [{"equals": mc, "uid": "net.minecraft"}],
                "cachedVersion": loader_version,
                "uid": "net.neoforged",
                "version": loader_version,
            }
        )

    return [component for component in components if component.get("version")]


def prism_instance_cfg(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "[General]",
            "AutoCloseConsole=false",
            "AutomaticJava=true",
            "CloseAfterLaunch=false",
            "ConfigVersion=1.3",
            "EnableFeralGamemode=false",
            "EnableMangoHud=false",
            "ExportAuthor=stromblex",
            f"ExportName={build_display_name(manifest)}",
            "ExportOptionalFiles=true",
            "ExportSummary=Kernova performance-focused Minecraft modpack.",
            f"ExportVersion={manifest['pack_version']}",
            "IgnoreJavaCompatibility=false",
            "InstanceType=OneSix",
            "JoinServerOnLaunch=false",
            "JvmArgs=",
            "LaunchMaximized=false",
            "LogPrePostOutput=true",
            "LowMemWarning=true",
            "ManagedPack=false",
            "MaxMemAlloc=4096",
            "MinMemAlloc=2048",
            "MinecraftWinHeight=480",
            "MinecraftWinWidth=854",
            "OnlineFixes=false",
            "OverrideCommands=false",
            "OverrideConsole=false",
            "OverrideEnv=false",
            "OverrideGameTime=false",
            "OverrideJavaArgs=false",
            "OverrideJavaLocation=false",
            "OverrideMemory=true",
            "OverrideMiscellaneous=false",
            "OverrideNativeWorkarounds=false",
            "OverridePerformance=false",
            "OverrideWindow=false",
            "PermGen=128",
            "QuitAfterGameStop=false",
            "RecordGameTime=true",
            "ShowConsole=false",
            "ShowConsoleOnError=true",
            "ShowGameTime=true",
            "UseDiscreteGpu=true",
            "UseNativeGLFW=false",
            "UseNativeOpenAL=false",
            "UseZink=false",
            "iconKey=default",
            f"name={build_display_name(manifest)}",
            "notes=",
            "",
        ]
    )


def create_packping_entry(
    manifest: dict[str, Any],
    config: dict[str, Any],
    out_dir: Path,
    artifact_name: str,
    changelog: str,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    packping = config.get("packping", {})
    template = packping.get("download_url_template", "")
    base = packping.get("download_base_url", "").rstrip("/")
    download = ""
    if template:
        download = template.format(
            artifact=artifact_name,
            loader=manifest["loader"],
            minecraft=manifest["minecraft_version"],
            version=manifest["pack_version"],
            build=artifact_slug(manifest),
        )
    elif base:
        download = f"{base}/{artifact_name}"

    entry = {
        "minecraft": manifest["minecraft_version"],
        "loader": manifest["loader"],
        "version": manifest["pack_version"],
        "download": download,
        "changelog": changelog.strip(),
        "settings": copy.deepcopy(packping.get("remote_settings", {
            "notifications": {
                "checkOnStartup": True,
                "showFullscreen": True,
                "showChat": False,
                "showToast": False,
            }
        })),
    }
    if should_include_minecraft_upgrade_toast(entry, config):
        entry["toast"] = minecraft_upgrade_toast(entry, config, entry["minecraft"])
        entry.setdefault("settings", {}).setdefault("notifications", {})["showToast"] = True
    path = out_dir / f"{artifact_slug(manifest)}.packping-entry.json"
    write_json(path, entry)
    update_packping_json(entry, config)
    return path


def should_include_minecraft_upgrade_toast(entry: dict[str, Any], config: dict[str, Any]) -> bool:
    packping = config.get("packping", {})
    if not packping.get("toast_on_minecraft_upgrade", False):
        return False

    path = project_path(packping.get("update_json", "update.json"))
    existing = load_json(path, default=[])
    if not isinstance(existing, list):
        return False

    loader = entry.get("loader", "")
    minecraft = str(entry.get("minecraft", ""))
    previous_minecraft_versions = [
        str(item.get("minecraft", ""))
        for item in existing
        if item.get("loader", "") == loader and item.get("minecraft") != minecraft
    ]
    if not previous_minecraft_versions:
        return False

    latest_previous = sorted(previous_minecraft_versions, key=version_sort_key)[-1]
    return version_sort_key(minecraft) > version_sort_key(latest_previous)


def update_packping_json(entry: dict[str, Any], config: dict[str, Any]) -> Path:
    path = project_path(config.get("packping", {}).get("update_json", "update.json"))
    entries = load_json(path, default=[])
    if not isinstance(entries, list):
        raise ValueError(f"PackPing update JSON must be a list: {path}")

    is_minecraft_upgrade = should_include_minecraft_upgrade_toast(entry, config)
    kept = [
        item for item in entries
        if not (
            item.get("minecraft") == entry.get("minecraft")
            and item.get("loader", "") == entry.get("loader", "")
        )
    ]
    if is_minecraft_upgrade:
        apply_minecraft_upgrade_notices(kept, entry, config)
    kept.append(entry)
    kept.sort(key=lambda item: (version_sort_key(item.get("minecraft", "0")), item.get("loader", "")))
    write_json(path, kept)
    return path


def apply_minecraft_upgrade_notices(
    entries: list[dict[str, Any]],
    newest_entry: dict[str, Any],
    config: dict[str, Any],
) -> None:
    newest_minecraft = str(newest_entry.get("minecraft", ""))
    newest_loader = str(newest_entry.get("loader", ""))
    for entry in entries:
        if str(entry.get("loader", "")) != newest_loader:
            continue
        current_minecraft = str(entry.get("minecraft", ""))
        if not current_minecraft or version_sort_key(current_minecraft) >= version_sort_key(newest_minecraft):
            continue
        entry["upgradeMinecraft"] = newest_minecraft
        entry["version"] = newest_entry.get("version", "")
        entry["download"] = newest_entry.get("download", "")
        entry["changelog"] = minecraft_upgrade_changelog(newest_entry)
        entry["toast"] = minecraft_upgrade_toast(newest_entry, config, newest_minecraft)
        settings = copy.deepcopy(entry.get("settings") or config.get("packping", {}).get("remote_settings", {}))
        settings.setdefault("notifications", {})["showToast"] = True
        entry["settings"] = settings


def minecraft_upgrade_changelog(newest_entry: dict[str, Any]) -> str:
    minecraft = newest_entry.get("minecraft", "")
    changelog = str(newest_entry.get("changelog", "")).strip()
    header = f"Kernova is available for Minecraft {minecraft}."
    return f"{header}\n\n{changelog}" if changelog else header


def minecraft_upgrade_toast(entry: dict[str, Any], config: dict[str, Any], target_minecraft: str) -> dict[str, str]:
    template = config.get("packping", {}).get(
        "minecraft_upgrade_toast",
        {
            "title": "§eKernova is available for Minecraft %version%",
            "subtitle": "§7Download the new Kernova build",
        },
    )
    variables = {
        "version": target_minecraft,
        "minecraft": target_minecraft,
        "pack_version": str(entry.get("version", "")),
        "loader": str(entry.get("loader", "")),
    }
    return {
        str(key): render_placeholders(str(value), variables)
        for key, value in template.items()
    }


def render_placeholders(value: str, variables: dict[str, str]) -> str:
    for key, replacement in variables.items():
        value = value.replace(f"%{key}%", replacement)
    return value


def build_dirs_from_args(args: argparse.Namespace) -> list[Path]:
    if getattr(args, "loader", None) == "both":
        if args.build:
            raise ValueError("--loader both can only be used with --latest, not --build.")
        return [
            build_dir_from_arg(None, args.latest, loader, args.mc)
            for loader in ("fabric", "neoforge")
        ]
    return [build_dir_from_arg(args.build, args.latest, args.loader, args.mc)]


def package_one_build(args: argparse.Namespace, config: dict[str, Any], build_dir: Path) -> None:
    secrets = load_json(SECRETS_PATH, default={})
    manifest = load_manifest(build_dir)

    modrinth_dir = platform_dir(MODRINTH_DIR, manifest)
    full_dir = platform_dir(FULL_DIR, manifest)
    prism_dir = platform_dir(PRISM_DIR, manifest)
    packping_dir = platform_dir(PACKPING_DIR, manifest)
    changelog_file = changelog_path(manifest)
    changelog_file.parent.mkdir(parents=True, exist_ok=True)

    changelog = compose_changelog(manifest, args.notes_file)
    changelog_file.write_text(changelog)

    mrpack = create_mrpack(build_dir, manifest, config, modrinth_dir)
    curseforge_zip = create_curseforge_modpack_zip(
        build_dir,
        manifest,
        config,
        platform_dir(CURSEFORGE_DIR, manifest),
        secrets,
    )
    full_zip = create_full_zip(build_dir, manifest, full_dir)
    prism_zip = create_prism_zip(build_dir, manifest, config, prism_dir)
    packping_entry = create_packping_entry(manifest, config, packping_dir, mrpack.name, changelog)

    print(f"[OK] Modrinth pack: {mrpack}")
    print(f"[OK] CurseForge modpack zip: {curseforge_zip}")
    print(f"[OK] Full test zip: {full_zip}")
    print(f"[OK] Prism import zip: {prism_zip}")
    print(f"[OK] Changelog: {changelog_file}")
    print(f"[OK] PackPing entry: {packping_entry}")
    

def package_build(args: argparse.Namespace) -> int:
    config = load_json(CONFIG_PATH)
    try:
        build_dirs = build_dirs_from_args(args)
        for build_dir in build_dirs:
            package_one_build(args, config, build_dir)
    except ValueError as error:
        print(f"[ERROR] {error}")
        return 1
    if args.sync_update:
        return sync_update_json(args, config)
    return 0


def upload_build(args: argparse.Namespace) -> int:
    config = load_json(CONFIG_PATH)
    secrets = load_json(SECRETS_PATH, default={})
    platforms = ["modrinth", "curseforge"] if args.platform == "both" else [args.platform]
    ok = True
    for build_dir in build_dirs_from_args(args):
        manifest = load_manifest(build_dir)
        needs_package = not platform_dir(MODRINTH_DIR, manifest).exists() or not changelog_path(manifest).exists()
        if "curseforge" in platforms and not curseforge_upload_artifact(manifest, config).exists():
            needs_package = True
        if needs_package:
            package_one_build(args, config, build_dir)

        for platform in platforms:
            if platform == "modrinth":
                ok = upload_modrinth(manifest, config, secrets, args.dry_run) and ok
            elif platform == "curseforge":
                ok = upload_curseforge(manifest, config, secrets, args.dry_run) and ok
    return 0 if ok else 1


def release_build(args: argparse.Namespace) -> int:
    config = load_json(CONFIG_PATH)
    for build_dir in build_dirs_from_args(args):
        package_one_build(args, config, build_dir)

    dry_run_args = copy.copy(args)
    dry_run_args.dry_run = True
    if upload_build(dry_run_args) != 0:
        print("[ERROR] Release stopped: upload dry-run failed.")
        return 1

    if args.platform == "both":
        real_modrinth_args = copy.copy(args)
        real_modrinth_args.dry_run = False
        real_modrinth_args.platform = "modrinth"
        if upload_build(real_modrinth_args) != 0:
            print("[ERROR] Release stopped: real Modrinth upload failed.")
            return 1

        real_curseforge_args = copy.copy(args)
        real_curseforge_args.dry_run = False
        real_curseforge_args.platform = "curseforge"
        if upload_build(real_curseforge_args) != 0:
            print("[ERROR] Release stopped: real CurseForge upload failed.")
            return 1
    else:
        real_upload_args = copy.copy(args)
        real_upload_args.dry_run = False
        if upload_build(real_upload_args) != 0:
            print("[ERROR] Release stopped: real upload failed.")
            return 1

    sync_args = argparse.Namespace(
        commit=True,
        push=True,
        remote_repo=args.remote_repo,
        remote_file=args.remote_file,
        message=args.message,
        init_repo=args.init_repo,
    )
    return sync_update_json(sync_args, config)


def upload_modrinth(
    manifest: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any],
    dry_run: bool,
) -> bool:
    project_id = config["modrinth"].get("project_id", "")
    token = secrets.get("modrinth_token", "")
    out_dir = platform_dir(MODRINTH_DIR, manifest)
    artifact = out_dir / f"{artifact_slug(manifest)}.mrpack"
    changelog = changelog_path(manifest).read_text()
    if not artifact.exists():
        print(f"[ERROR] Missing artifact: {artifact}")
        return False

    data = {
        "name": build_display_name(manifest),
        "version_number": f"{manifest['pack_version']}+{manifest['loader']}",
        "changelog": changelog,
        "dependencies": [],
        "game_versions": [manifest["minecraft_version"]],
        "version_type": config["modrinth"]["version_type"],
        "loaders": config["modrinth"]["loaders"][manifest["loader"]],
        "featured": config["modrinth"]["featured"],
        "project_id": project_id,
        "file_parts": ["file"],
        "primary_file": "file",
    }
    if dry_run:
        print(f"[DRY] Modrinth upload {artifact.name}: {data['version_number']}")
        if not project_id or not token:
            print("      Missing real Modrinth project_id/token; this is OK for dry-run.")
        return True
    if not project_id or not token:
        print("[ERROR] Modrinth project_id/token is not configured.")
        return False

    status, body = post_multipart(
        "https://api.modrinth.com/v2/version",
        {"Authorization": token},
        {"data": json.dumps(data)},
        {"file": artifact},
    )
    if status in (200, 201):
        print(f"[OK] Modrinth uploaded: {artifact.name}")
        return True
    print(f"[ERROR] Modrinth {status}: {body}")
    return False


def upload_curseforge(
    manifest: dict[str, Any],
    config: dict[str, Any],
    secrets: dict[str, Any],
    dry_run: bool,
) -> bool:
    project_id = config["curseforge"].get("project_id", "")
    token = secrets.get("curseforge_token", "")
    artifact = curseforge_upload_artifact(manifest, config)
    if not artifact.exists():
        print(f"[ERROR] Missing CurseForge artifact: {artifact}")
        print("        Rerun package to create the configured CurseForge upload artifact.")
        return False

    metadata, missing = curseforge_upload_metadata(manifest, config, secrets, allow_network=not dry_run)
    if dry_run:
        print(f"[DRY] CurseForge upload {artifact.name}: {metadata['displayName']}")
        if metadata["gameVersions"]:
            print(f"      Game versions: {metadata['gameVersions']}")
        if missing:
            print(f"      Missing {', '.join(missing)}; this must be resolved before real upload.")
        if not project_id or not token:
            print("      Missing real CurseForge project_id/token; this is OK for dry-run.")
        return True
    if not project_id or not token:
        print("[ERROR] CurseForge project_id/token is not configured.")
        return False
    if missing:
        print(f"[ERROR] Missing {', '.join(missing)}.")
        return False

    status, body = post_multipart(
        f"https://minecraft.curseforge.com/api/projects/{project_id}/upload-file",
        {"X-Api-Token": token},
        {"metadata": json.dumps(metadata)},
        {"file": artifact},
    )
    if status == 200:
        print(f"[OK] CurseForge uploaded: {artifact.name}")
        return True
    print(f"[ERROR] CurseForge {status}: {body}")
    return False


def sync_update_json(args: argparse.Namespace, config: dict[str, Any] | None = None) -> int:
    config = config or load_json(CONFIG_PATH)
    packping = config.get("packping", {})
    local_update = project_path(packping.get("update_json", "update.json"))
    remote_repo = Path(args.remote_repo or packping.get("remote_repo", ""))
    remote_file = args.remote_file or packping.get("remote_file", "update.json")
    if not remote_repo:
        print("[ERROR] packping.remote_repo is not configured.")
        return 1
    if not local_update.exists():
        print(f"[ERROR] Local update JSON does not exist: {local_update}")
        return 1
    if not validate_packping_feed_file(local_update):
        return 1

    remote_update = remote_repo / remote_file
    remote_update.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(local_update, remote_update)
    print(f"[OK] Synced PackPing update JSON: {remote_update}")
    if not validate_packping_feed_file(remote_update):
        return 1

    if not args.commit:
        return 0
    ensure_git_repo(remote_repo, packping, getattr(args, "init_repo", False))

    run_git(remote_repo, ["add", remote_file])
    if not git_has_staged_changes(remote_repo):
        print("[OK] Remote update JSON has no changes to commit.")
        return 0

    message = args.message or packping.get("commit_message", "chore: update Kernova PackPing feed")
    run_git(remote_repo, ["commit", "-m", message])
    print(f"[OK] Committed remote update JSON: {message}")
    if args.push:
        branch = packping.get("remote_branch", "main")
        run_git(remote_repo, ["push", "origin", branch])
        print("[OK] Pushed remote update repo.")
    return 0


def ensure_git_repo(repo: Path, packping: dict[str, Any], init_repo: bool) -> None:
    has_git = (repo / ".git").exists()
    if not has_git and not init_repo:
        raise SystemExit(
            "[ERROR] Remote update folder is not a git repo. "
            "Run sync-update with --init-repo once, or initialize it manually."
        )

    if not has_git:
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo, check=True)

    branch = packping.get("remote_branch", "main")
    if init_repo:
        subprocess.run(git_command(repo, ["branch", "-M", branch]), cwd=repo, check=True)

    remote_url = packping.get("remote_url", "")
    if init_repo and remote_url:
        existing = subprocess.run(git_command(repo, ["remote"]), cwd=repo, text=True, capture_output=True, check=True)
        if "origin" not in existing.stdout.split():
            subprocess.run(git_command(repo, ["remote", "add", "origin", remote_url]), cwd=repo, check=True)


def run_git(repo: Path, args: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        git_command(repo, args),
        cwd=repo,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode != 0:
        if capture:
            sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    return result


def git_has_staged_changes(repo: Path) -> bool:
    result = subprocess.run(
        git_command(repo, ["diff", "--cached", "--quiet"]),
        cwd=repo,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise SystemExit(result.returncode)


def git_command(repo: Path, args: list[str]) -> list[str]:
    return ["git", "-c", f"safe.directory={repo}", *args]


def validate_packping_feed_file(path: Path) -> bool:
    try:
        entries = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"[ERROR] Invalid PackPing update JSON {path}: {error}")
        return False
    errors = packping_feed_errors(entries)
    for error in errors:
        print(f"[ERROR] PackPing feed {path}: {error}")
    return not errors


def packping_feed_errors(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return ["root value must be a list"]
    if not entries:
        return ["feed must contain at least one entry"]
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry #{index} must be an object")
            continue
        minecraft = str(entry.get("minecraft", ""))
        loader = str(entry.get("loader", ""))
        key = (minecraft, loader)
        if key in seen:
            errors.append(f"duplicate entry for Minecraft {minecraft} / {loader}")
        seen.add(key)
        for field in ("minecraft", "loader", "version", "download", "changelog"):
            if not entry.get(field):
                errors.append(f"entry {key} is missing {field}")
    return errors


def post_multipart(
    url: str,
    headers: dict[str, str],
    fields: dict[str, str],
    files: dict[str, Path],
) -> tuple[int, str]:
    boundary = f"----Kernova{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    for name, path in files.items():
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode())
        body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
        body.extend(path.read_bytes())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Package and publish Kernova builds.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_build_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--build", help="Build folder name or path")
        p.add_argument("--latest", action="store_true", help="Use latest matching build")
        p.add_argument("--loader", choices=["fabric", "neoforge", "both"], help="Filter latest by loader")
        p.add_argument("--mc", help="Filter latest by Minecraft version")
        p.add_argument("--notes-file", help="Manual release notes to merge into platform changelogs")

    package_parser = sub.add_parser("package", help="Create mrpack, CurseForge zip, full zip, changelogs, PackPing entry")
    add_build_args(package_parser)
    package_parser.add_argument("--sync-update", action="store_true", help="Copy update.json to remote PackPing feed repo")
    package_parser.add_argument("--commit", action="store_true", help="Commit remote update JSON when used with --sync-update")
    package_parser.add_argument("--push", action="store_true", help="Push remote update repo when used with --sync-update --commit")
    package_parser.add_argument("--remote-repo", help="Override PackPing remote update repo path")
    package_parser.add_argument("--remote-file", help="Override remote update JSON path inside repo")
    package_parser.add_argument("--message", help="Commit message for remote update JSON")
    package_parser.add_argument("--init-repo", action="store_true", help="Initialize remote update repo if it is missing .git")

    upload_parser = sub.add_parser("upload", help="Upload packaged artifacts")
    add_build_args(upload_parser)
    upload_parser.add_argument("--platform", choices=["modrinth", "curseforge", "both"], default="both")
    upload_parser.add_argument("--dry-run", action="store_true")

    all_parser = sub.add_parser("all", help="Package and upload")
    add_build_args(all_parser)
    all_parser.add_argument("--platform", choices=["modrinth", "curseforge", "both"], default="both")
    all_parser.add_argument("--dry-run", action="store_true")
    all_parser.add_argument("--sync-update", action="store_true")
    all_parser.add_argument("--commit", action="store_true")
    all_parser.add_argument("--push", action="store_true")
    all_parser.add_argument("--remote-repo", help="Override PackPing remote update repo path")
    all_parser.add_argument("--remote-file", help="Override remote update JSON path inside repo")
    all_parser.add_argument("--message", help="Commit message for remote update JSON")
    all_parser.add_argument("--init-repo", action="store_true", help="Initialize remote update repo if it is missing .git")

    release_parser = sub.add_parser("release", help="Package, dry-run, upload, commit and push PackPing feed")
    add_build_args(release_parser)
    release_parser.add_argument("--platform", choices=["modrinth", "curseforge", "both"], default="both")
    release_parser.add_argument("--remote-repo", help="Override PackPing remote update repo path")
    release_parser.add_argument("--remote-file", help="Override remote update JSON path inside repo")
    release_parser.add_argument("--message", help="Commit message for remote update JSON")
    release_parser.add_argument("--init-repo", action="store_true", help="Initialize remote update repo if it is missing .git")

    sync_parser = sub.add_parser("sync-update", help="Copy local update.json to the kernova-update repo")
    sync_parser.add_argument("--commit", action="store_true", help="Commit update.json in the remote repo")
    sync_parser.add_argument("--push", action="store_true", help="Push the remote repo after committing")
    sync_parser.add_argument("--remote-repo", help="Override PackPing remote update repo path")
    sync_parser.add_argument("--remote-file", help="Override remote update JSON path inside repo")
    sync_parser.add_argument("--message", help="Commit message for remote update JSON")
    sync_parser.add_argument("--init-repo", action="store_true", help="Initialize remote update repo if it is missing .git")

    args = parser.parse_args()
    try:
        if args.command == "package":
            return package_build(args)
        if args.command == "upload":
            return upload_build(args)
        if args.command == "all":
            code = package_build(args)
            return code if code else upload_build(args)
        if args.command == "release":
            return release_build(args)
        if args.command == "sync-update":
            return sync_update_json(args)
    except ValueError as error:
        print(f"[ERROR] {error}")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
