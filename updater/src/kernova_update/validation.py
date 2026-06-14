"""Repository validation checks for Kernova source data."""

from __future__ import annotations

import json
import hashlib
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .builder import COMMON_PROFILE, LIST_DIR, list_profile_dir
from .models import BuildManifest, Mod, ModList

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PUBLISH_DIR = PROJECT_ROOT / "publish"
BUILDS_DIR = PROJECT_ROOT / "builds"
MODRINTH_DIR = PUBLISH_DIR / "modrinth"
CURSEFORGE_DIR = PUBLISH_DIR / "curseforge"
PACKPING_UPDATE = PUBLISH_DIR / "packping" / "update.json"
PACKPING_PLATFORM_UPDATES = ("update.modrinth.json", "update.curseforge.json")
LOADERS = ("fabric", "neoforge")
GENERATED_CONFIG_FILES = {
    "packping.json",
    "vartapack/profile.json",
    "vartapack/rules.json",
    "vartapack/vartapack.json",
}


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.level == "warning")


def validate_repository(include_artifacts: bool = False) -> ValidationReport:
    issues: list[ValidationIssue] = []
    issues.extend(validate_structured_files())
    issues.extend(validate_loader_profiles())
    issues.extend(validate_no_duplicate_loader_configs())
    issues.extend(validate_publish_config())
    if include_artifacts:
        issues.extend(validate_artifacts())
    return ValidationReport(tuple(issues))


def validate_artifacts() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(validate_build_artifacts())
    issues.extend(validate_modrinth_artifacts())
    issues.extend(validate_curseforge_artifacts())
    issues.extend(validate_packping_update())
    return issues


def validate_structured_files() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in _walk_source_files(("*.json", "*.toml")):
        if path.as_posix() == "publish/secrets.json":
            continue
        try:
            if path.suffix == ".json":
                json.loads(path.read_text())
            elif path.suffix == ".toml":
                tomllib.loads(path.read_text())
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            issues.append(_error(path, f"Cannot parse {path.suffix[1:].upper()}: {error}"))
    return issues


def validate_loader_profiles(loaders: Iterable[str] = LOADERS) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for loader in loaders:
        profile = list_profile_dir(loader)
        modlist_path = profile / "mods" / "modlist.json"
        if not modlist_path.exists():
            issues.append(_error(modlist_path, "Missing loader modlist."))
            continue

        try:
            mod_list = ModList(**json.loads(modlist_path.read_text()))
        except Exception as error:
            issues.append(_error(modlist_path, f"Invalid modlist schema: {error}"))
            continue

        meta_loader = mod_list.meta.get("loader")
        if meta_loader and meta_loader != loader:
            issues.append(_error(modlist_path, f"meta.loader is {meta_loader!r}, expected {loader!r}."))

        issues.extend(_duplicate_field_issues(modlist_path, mod_list.mods, "name"))
        issues.extend(_duplicate_field_issues(modlist_path, mod_list.mods, "slug"))
        issues.extend(_duplicate_field_issues(modlist_path, mod_list.mods, "modrinth_id"))
        issues.extend(_missing_config_issues(loader, modlist_path, mod_list.mods))
    return issues


def validate_no_duplicate_loader_configs() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    fabric_config = LIST_DIR / "fabric" / "config"
    neoforge_config = LIST_DIR / "neoforge" / "config"
    common_config = LIST_DIR / COMMON_PROFILE / "config"

    if not common_config.exists():
        issues.append(_warning(common_config, "Shared config directory is missing."))

    for fabric_file in sorted(fabric_config.rglob("*")):
        if not fabric_file.is_file():
            continue
        rel = fabric_file.relative_to(fabric_config)
        neoforge_file = neoforge_config / rel
        if not neoforge_file.is_file():
            continue
        if _digest(fabric_file) == _digest(neoforge_file):
            issues.append(
                _warning(
                    fabric_file,
                    f"Identical loader-specific config also exists at {neoforge_file}; move it to list/common/config/{rel}.",
                )
            )
    return issues


def validate_publish_config() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    config_path = PUBLISH_DIR / "config.json"
    if not config_path.exists():
        return issues

    try:
        config = json.loads(config_path.read_text())
    except json.JSONDecodeError as error:
        return [_error(config_path, f"Cannot parse publish config: {error}")]

    icon = str(config.get("icon", ""))
    if icon and Path(icon).is_absolute():
        issues.append(_warning(config_path, "icon uses an absolute local path; this is not portable."))

    remote_repo = str(config.get("packping", {}).get("remote_repo", ""))
    if remote_repo and Path(remote_repo).is_absolute():
        issues.append(_warning(config_path, "packping.remote_repo uses an absolute local path."))

    curseforge = config.get("curseforge", {})
    if isinstance(curseforge, dict) and curseforge.get("environment"):
        issues.append(
            _error(
                config_path,
                "curseforge.environment must stay empty; the CurseForge upload endpoint rejects Client/Server environment IDs as invalid dependencies.",
            )
        )

    return issues


def validate_build_artifacts() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    manifest_paths = sorted(BUILDS_DIR.glob("*/build_manifest.json"))
    if not manifest_paths:
        return [_warning(BUILDS_DIR, "No generated builds found.")]

    for manifest_path in manifest_paths:
        try:
            manifest = BuildManifest(**json.loads(manifest_path.read_text()))
        except Exception as error:
            issues.append(_error(manifest_path, f"Invalid build manifest: {error}"))
            continue

        build_dir = manifest_path.parent
        if manifest.build_folder != build_dir.name:
            issues.append(_error(manifest_path, "build_folder does not match the actual directory name."))

        seen_filenames: set[str] = set()
        for mod in manifest.resolved_mods:
            if not mod.available:
                continue
            if not mod.filename:
                issues.append(_error(manifest_path, f"{mod.name} is available but has no filename."))
                continue
            if mod.filename in seen_filenames:
                issues.append(_error(manifest_path, f"Duplicate resolved filename: {mod.filename}."))
            seen_filenames.add(mod.filename)

            jar_path = build_dir / "mods" / mod.filename
            if not jar_path.exists():
                issues.append(_error(jar_path, f"Missing downloaded jar for {mod.name}."))
                continue
            if mod.sha512 and _file_hash(jar_path, "sha512") != mod.sha512:
                issues.append(_error(jar_path, f"SHA512 mismatch for {mod.name}."))
            if mod.sha1 and _file_hash(jar_path, "sha1") != mod.sha1:
                issues.append(_error(jar_path, f"SHA1 mismatch for {mod.name}."))

        if manifest.config_copied and not (build_dir / "config").exists():
            issues.append(_error(build_dir / "config", "Manifest says configs were copied, but config/ is missing."))
        if manifest.options_copied and not (build_dir / "options.txt").exists():
            issues.append(_error(build_dir / "options.txt", "Manifest says options were copied, but options.txt is missing."))
    return issues


def validate_modrinth_artifacts() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    artifacts = sorted(MODRINTH_DIR.glob("**/*.mrpack"))
    if not artifacts:
        return [_warning(MODRINTH_DIR, "No Modrinth .mrpack artifacts found.")]

    for artifact in artifacts:
        try:
            with zipfile.ZipFile(artifact) as archive:
                index = json.loads(archive.read("modrinth.index.json"))
        except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
            issues.append(_error(artifact, f"Invalid .mrpack: {error}"))
            continue

        dependencies = index.get("dependencies", {})
        minecraft = dependencies.get("minecraft")
        if not minecraft:
            issues.append(_error(artifact, "Missing minecraft dependency."))

        loader = _loader_from_artifact_path(artifact)
        if loader == "fabric" and not dependencies.get("fabric-loader"):
            issues.append(_error(artifact, "Missing fabric-loader dependency."))
        elif loader == "neoforge" and not dependencies.get("neoforge"):
            issues.append(_error(artifact, "Missing neoforge dependency."))
        elif loader == "neoforge" and minecraft:
            neoforge = str(dependencies.get("neoforge", ""))
            expected_prefix = _expected_neoforge_prefix(str(minecraft))
            if expected_prefix and not neoforge.startswith(expected_prefix):
                issues.append(
                    _error(
                        artifact,
                        f"NeoForge dependency {neoforge} does not match Minecraft {minecraft}; "
                        f"expected {expected_prefix}*.",
                    )
                )

        files = index.get("files", [])
        if not files:
            issues.append(_error(artifact, "Modrinth index has no files."))
        for item in files:
            path = item.get("path", "")
            downloads = item.get("downloads", [])
            hashes = item.get("hashes", {})
            file_size = item.get("fileSize", 0)
            if not path or not downloads or not hashes.get("sha1") or not file_size:
                issues.append(_error(artifact, f"Incomplete Modrinth file entry: {path or '<missing path>'}."))
    return issues


def validate_curseforge_artifacts() -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    artifacts = sorted(CURSEFORGE_DIR.glob("**/*-curseforge.zip"))
    if not artifacts:
        return [_warning(CURSEFORGE_DIR, "No CurseForge modpack zip artifacts found.")]

    for artifact in artifacts:
        try:
            with zipfile.ZipFile(artifact) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("manifest.json"))
        except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
            issues.append(_error(artifact, f"Invalid CurseForge modpack zip: {error}"))
            continue

        if manifest.get("manifestType") != "minecraftModpack":
            issues.append(_error(artifact, "CurseForge manifestType must be minecraftModpack."))
        if manifest.get("manifestVersion") != 1:
            issues.append(_error(artifact, "CurseForge manifestVersion must be 1."))
        minecraft = manifest.get("minecraft", {})
        if not isinstance(minecraft, dict) or not minecraft.get("version"):
            issues.append(_error(artifact, "CurseForge manifest is missing minecraft.version."))
        modloaders = minecraft.get("modLoaders") if isinstance(minecraft, dict) else None
        if not isinstance(modloaders, list) or not modloaders:
            issues.append(_error(artifact, "CurseForge manifest is missing minecraft.modLoaders."))
        if "overrides" not in manifest:
            issues.append(_error(artifact, "CurseForge manifest is missing overrides field."))
            overrides = ""
        else:
            overrides = str(manifest["overrides"]).rstrip("/")
            if not any(name.startswith(f"{overrides}/") for name in names):
                issues.append(_error(artifact, f"CurseForge overrides folder '{manifest['overrides']}' is missing."))

        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            issues.append(_error(artifact, "CurseForge manifest files list must reference mod project/file ids."))
        else:
            for item in files:
                if not isinstance(item, dict) or not item.get("projectID") or not item.get("fileID"):
                    issues.append(_error(artifact, "CurseForge manifest contains an incomplete file entry."))

        if overrides:
            embedded_mod_jars = sorted(
                name for name in names
                if name.startswith(f"{overrides}/mods/") and name.lower().endswith(".jar")
            )
            if embedded_mod_jars:
                issues.append(
                    _error(
                        artifact,
                        "CurseForge modpack zip embeds mod jars in overrides/mods instead of manifest files.",
                    )
                )
    return issues


def validate_packping_update() -> list[ValidationIssue]:
    paths = packping_update_paths()
    if not paths:
        return [_warning(PACKPING_UPDATE.parent, "PackPing update JSON does not exist.")]

    issues: list[ValidationIssue] = []
    for path in paths:
        issues.extend(validate_packping_update_file(path))
    return issues


def packping_update_paths() -> list[Path]:
    split_paths = [PACKPING_UPDATE.parent / name for name in PACKPING_PLATFORM_UPDATES]
    existing_split = [path for path in split_paths if path.exists()]
    if existing_split:
        return existing_split
    return [PACKPING_UPDATE] if PACKPING_UPDATE.exists() else []


def validate_packping_update_file(path: Path) -> list[ValidationIssue]:
    if not path.exists():
        return [_warning(path, "PackPing update JSON does not exist.")]

    try:
        text = path.read_text()
        entries = [] if not text.strip() else json.loads(text)
    except json.JSONDecodeError as error:
        return [_error(path, f"Cannot parse PackPing update JSON: {error}")]

    if not isinstance(entries, list):
        return [_error(path, "PackPing update JSON must be a list.")]

    issues: list[ValidationIssue] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        minecraft = str(entry.get("minecraft", ""))
        loader = str(entry.get("loader", ""))
        key = (minecraft, loader)
        if key in seen:
            issues.append(_error(path, f"Duplicate PackPing entry for Minecraft {minecraft} / {loader}."))
        seen.add(key)

        for field in ("minecraft", "loader", "version", "download", "changelog"):
            if not entry.get(field):
                issues.append(_error(path, f"PackPing entry {key} is missing {field}."))
        if loader not in LOADERS:
            issues.append(_warning(path, f"PackPing entry uses unknown loader {loader!r}."))
    return issues


def _duplicate_field_issues(path: Path, mods: list[Mod], field: str) -> list[ValidationIssue]:
    seen: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for mod in mods:
        value = getattr(mod, field)
        if not value:
            continue
        key = str(value).lower()
        if key in seen:
            issues.append(_error(path, f"Duplicate {field} {value!r}: {seen[key]} and {mod.name}."))
        else:
            seen[key] = mod.name
    return issues


def _missing_config_issues(loader: str, path: Path, mods: list[Mod]) -> list[ValidationIssue]:
    existing = _merged_config_paths(loader)
    issues: list[ValidationIssue] = []
    for mod in mods:
        for config_file in mod.config_files:
            normalized = config_file.replace("\\", "/")
            if normalized in GENERATED_CONFIG_FILES:
                continue
            if normalized not in existing:
                issues.append(
                    _warning(
                        path,
                        f"{mod.name} references missing config file {normalized!r} for {loader}.",
                    )
                )
    return issues


def _merged_config_paths(loader: str) -> set[str]:
    out: set[str] = set()
    for base in (LIST_DIR / COMMON_PROFILE / "config", list_profile_dir(loader) / "config"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                out.add(path.relative_to(base).as_posix())
    return out


def _walk_source_files(patterns: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    skip_parts = {".git", ".venv", "__pycache__"}
    for pattern in patterns:
        for path in PROJECT_ROOT.rglob(pattern):
            rel = path.relative_to(PROJECT_ROOT)
            if any(part in skip_parts for part in rel.parts):
                continue
            if rel.parts[:1] == ("builds",):
                continue
            if rel.parts[:2] == ("publish", "packping"):
                continue
            if rel.parts[:2] == ("publish", "curseforge"):
                continue
            out.append(path)
    return sorted(set(out))


def _digest(path: Path) -> str:
    return _file_hash(path, "sha256")


def _file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _loader_from_artifact_path(path: Path) -> str:
    parts = path.parts
    if "fabric" in parts:
        return "fabric"
    if "neoforge" in parts:
        return "neoforge"
    return ""


def _expected_neoforge_prefix(minecraft: str) -> str:
    parts = minecraft.split(".")
    if len(parts) < 2:
        return ""
    if parts[0] == "1":
        minor = parts[1]
        patch = parts[2] if len(parts) > 2 else "0"
        return f"{minor}.{patch}."
    major = parts[0]
    minor = parts[1]
    patch = parts[2] if len(parts) > 2 else "0"
    return f"{major}.{minor}.{patch}."


def _error(path: Path, message: str) -> ValidationIssue:
    return ValidationIssue("error", _display_path(path), message)


def _warning(path: Path, message: str) -> ValidationIssue:
    return ValidationIssue("warning", _display_path(path), message)


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()
