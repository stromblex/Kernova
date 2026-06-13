"""Generated configs for support/update helper mods bundled with Kernova."""

from __future__ import annotations

import json
import hashlib
import io
import os
import shutil
import subprocess
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .models import ResolvedMod

DEFAULT_PACKPING_UPDATE_URL = "https://stromblex.github.io/kernova-update/update.json"


@dataclass(frozen=True)
class JarModInfo:
    id: str
    name: str
    version: str


@dataclass(frozen=True)
class JarInspection:
    primary: JarModInfo | None
    bundled: tuple[JarModInfo, ...] = ()


@dataclass(frozen=True)
class DoctorRun:
    status: str
    message: str


def generate_build_integrations(
    build_dir: Path,
    build_name: str,
    mc_version: str,
    loader: str,
    pack_version: str,
    resolved: list[ResolvedMod],
    update_url: str = DEFAULT_PACKPING_UPDATE_URL,
) -> None:
    """Generate configs for VartaPack and PackPing for this exact build."""
    mods_dir = build_dir / "mods"
    inspections = _collect_jar_inspections(mods_dir, resolved)
    mod_infos = {key: value.primary for key, value in inspections.items() if value.primary}

    _write_vartapack_config(build_dir, build_name, mc_version, loader, pack_version, resolved, mod_infos, inspections)
    _write_packping_config(build_dir, pack_version, update_url)
    _write_integrity_manifest(build_dir, resolved)


def run_vartapack_doctor(build_dir: Path, resolved: list[ResolvedMod]) -> DoctorRun:
    """Run VartaPack Doctor against a completed build, if the local Java can run it."""
    vartapack = next(
        (
            mod
            for mod in resolved
            if mod.available and mod.filename and (mod.slug == "vartapack" or mod.modrinth_id == "C1UZdEDT")
        ),
        None,
    )
    if not vartapack or not vartapack.filename:
        return DoctorRun("skipped", "VartaPack jar was not downloaded.")

    jar_path = build_dir / "mods" / vartapack.filename
    if not jar_path.exists():
        return DoctorRun("skipped", f"VartaPack jar not found: {jar_path.name}")

    old_java_seen = False
    java_seen = False
    for java_bin in _java_candidates():
        java_seen = True
        command = [
            str(java_bin),
            "-cp",
            str(jar_path),
            "com.stromblex.vartapack.doctor.DoctorCli",
            "--instance",
            str(build_dir),
            "--json",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return DoctorRun("warning", "VartaPack Doctor timed out after 30 seconds.")

        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode == 0:
            return DoctorRun("ok", _doctor_summary(output, "Doctor passed."))
        if "UnsupportedClassVersionError" in error:
            old_java_seen = True
            continue
        if result.returncode == 1:
            return DoctorRun("warning", _doctor_summary(output, "Doctor found warnings."))
        return DoctorRun("error", _doctor_summary(output, error or f"Doctor exited with {result.returncode}."))

    if old_java_seen:
        return DoctorRun(
            "skipped",
            "No new enough Java runtime was found for the downloaded VartaPack jar.",
        )
    if java_seen:
        return DoctorRun("skipped", "Configured Java runtimes were not found.")
    return DoctorRun("skipped", "Java runtime was not found on PATH or JAVA_HOME.")


def _java_candidates() -> list[Path]:
    candidates: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidates.append(Path(java_home) / "bin" / "java")
    for version in ("25", "24", "23", "22", "21"):
        candidates.append(Path(f"/usr/lib/jvm/java-{version}-openjdk-amd64/bin/java"))
        candidates.append(Path(f"/usr/lib/jvm/java-{version}-openjdk/bin/java"))
    java_on_path = shutil.which("java")
    if java_on_path:
        candidates.append(Path(java_on_path))
    candidates.append(Path("java"))

    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _collect_jar_inspections(mods_dir: Path, resolved: list[ResolvedMod]) -> dict[str, JarInspection]:
    out: dict[str, JarInspection] = {}
    for mod in resolved:
        if not mod.available or not mod.filename:
            continue
        jar_path = mods_dir / mod.filename
        if not jar_path.exists():
            continue
        out[_resolved_key(mod)] = _inspect_mod_jar(jar_path)
    return out


def _inspect_mod_jar(jar_path: Path) -> JarInspection:
    try:
        with zipfile.ZipFile(jar_path) as jar:
            return _inspect_open_jar(jar)
    except (OSError, zipfile.BadZipFile):
        return JarInspection(None)


def _inspect_open_jar(jar: zipfile.ZipFile) -> JarInspection:
    fabric = _read_fabric_mod_json(jar)
    if fabric:
        return JarInspection(fabric, tuple(_dedupe_mod_infos(_read_nested_fabric_mods(jar))))

    neoforge = _read_neoforge_mods_toml(jar)
    if neoforge:
        return JarInspection(neoforge[0], tuple(_dedupe_mod_infos(neoforge[1:])))

    return JarInspection(None)


def _read_fabric_mod_json(jar: zipfile.ZipFile) -> JarModInfo | None:
    try:
        raw = jar.read("fabric.mod.json")
    except KeyError:
        return None

    data = json.loads(raw.decode("utf-8"))
    mod_id = data.get("id", "")
    if not mod_id:
        return None
    return JarModInfo(
        id=mod_id,
        name=data.get("name") or mod_id,
        version=str(data.get("version") or ""),
    )


def _read_nested_fabric_mods(jar: zipfile.ZipFile) -> list[JarModInfo]:
    try:
        raw = jar.read("fabric.mod.json")
    except KeyError:
        return []

    data = json.loads(raw.decode("utf-8"))
    out: list[JarModInfo] = []
    for item in data.get("jars", []):
        nested_path = item.get("file") if isinstance(item, dict) else None
        if not nested_path:
            continue
        try:
            nested_raw = jar.read(nested_path)
        except KeyError:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(nested_raw)) as nested_jar:
                inspected = _inspect_open_jar(nested_jar)
        except (OSError, zipfile.BadZipFile):
            continue
        if inspected.primary:
            out.append(inspected.primary)
        out.extend(inspected.bundled)
    return out


def _read_neoforge_mods_toml(jar: zipfile.ZipFile) -> list[JarModInfo]:
    for path in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml"):
        try:
            raw = jar.read(path)
        except KeyError:
            continue

        data = tomllib.loads(raw.decode("utf-8"))
        mods = data.get("mods", [])
        if not mods:
            continue
        out: list[JarModInfo] = []
        for mod in mods:
            mod_id = mod.get("modId", "")
            if not mod_id:
                continue
            out.append(
                JarModInfo(
                    id=mod_id,
                    name=mod.get("displayName") or mod_id,
                    version=str(mod.get("version") or ""),
                )
            )
        return out
    return []


def _dedupe_mod_infos(infos: list[JarModInfo]) -> list[JarModInfo]:
    seen: set[str] = set()
    out: list[JarModInfo] = []
    for info in infos:
        key = info.id.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out


def _write_vartapack_config(
    build_dir: Path,
    build_name: str,
    mc_version: str,
    loader: str,
    pack_version: str,
    resolved: list[ResolvedMod],
    mod_infos: dict[str, JarModInfo],
    inspections: dict[str, JarInspection],
) -> None:
    config_dir = build_dir / "config" / "vartapack"
    config_dir.mkdir(parents=True, exist_ok=True)

    required = []
    recommended = []
    allowed_optional: list[str] = []

    for mod in sorted(resolved, key=lambda item: item.name.lower()):
        if not mod.available:
            continue
        info = mod_infos.get(_resolved_key(mod))
        if info is None:
            continue

        rule = {
            "id": info.id,
            "name": info.name or mod.name,
            "requiredVersion": f"=={info.version}" if info.version else "",
            "reason": _profile_reason(mod),
        }

        if mod.source == "dependency" or mod.priority == "required":
            required.append(rule)
        elif mod.priority == "recommended":
            recommended.append(rule)
        else:
            allowed_optional.append(info.id)

        inspection = inspections.get(_resolved_key(mod))
        if inspection:
            allowed_optional.extend(info.id for info in inspection.bundled)

    profile = {
        "schema": 1,
        "packId": "kernova",
        "packName": build_name,
        "profileVersion": pack_version,
        "supportUrl": "https://github.com/stromblex/Kernova/issues",
        "homepageUrl": "https://github.com/stromblex/Kernova",
        "expectedMinecraftVersions": [mc_version],
        "expectedLoaders": [loader],
        "minimumJavaMajor": 21,
        "minimumRamMb": 2048,
        "recommendedRamMb": 2048,
        "requiredMods": required,
        "recommendedMods": recommended,
        "blockedMods": _profile_blocked_mods(),
        "allowedExtraMods": sorted(set(allowed_optional + _known_bundled_extra_mods(loader))),
    }

    rules = {
        "schema": 1,
        "supportPolicyText": "Modified Kernova instances may receive limited support.",
        "rules": _advanced_rules(),
        "conflicts": _conflict_rules(),
    }

    vartapack = {
        "schema": 1,
        "enabled": True,
        "showToastOnStartup": False,
        "showScreenOnCriticalIssues": True,
        "allowContinueAnyway": True,
        "includeInstalledModsInReport": True,
        "includeExtraModsInReport": True,
        "redactUserHomePath": True,
        "redactUsername": True,
        "strictMode": False,
        "extraModsSeverity": "INFO",
        "requiredModsSeverity": "ERROR",
        "blockedModsSeverity": "ERROR",
        "recommendedModsSeverity": "WARNING",
        "fixedGuiScale": True,
        "targetGuiScale": 2,
    }

    _write_json(config_dir / "profile.json", profile)
    _write_json(config_dir / "rules.json", rules)
    _write_json(config_dir / "vartapack.json", vartapack)


def _write_integrity_manifest(build_dir: Path, resolved: list[ResolvedMod]) -> None:
    files: list[dict[str, object]] = []

    for mod in sorted(resolved, key=lambda item: item.name.lower()):
        if not mod.available or not mod.filename:
            continue
        rel = f"mods/{mod.filename}"
        path = build_dir / rel
        if not path.exists():
            continue
        files.append(
            {
                "path": rel,
                "type": "MOD_JAR",
                "sha256": _sha256(path),
                "required": mod.priority == "required" or mod.source == "dependency",
                "severityIfMissing": "ERROR" if mod.priority == "required" or mod.source == "dependency" else "WARNING",
                "severityIfChanged": "WARNING",
                "displayName": mod.name,
                "reason": "Kernova ships a tested mod jar for this build.",
                "fix": "Restore the original jar from the Kernova build archive.",
            }
        )

    tracked_configs = {
        "config/vartapack/profile.json": ("ERROR", True),
        "config/vartapack/rules.json": ("ERROR", True),
        "config/vartapack/vartapack.json": ("ERROR", True),
        "config/packping.json": ("WARNING", False),
    }

    for rel, (missing_severity, required) in tracked_configs.items():
        path = build_dir / rel
        if not path.exists():
            continue
        files.append(
            {
                "path": rel,
                "type": "CONFIG",
                "sha256": _sha256(path),
                "required": required,
                "severityIfMissing": missing_severity,
                "severityIfChanged": "INFO",
                "displayName": rel,
                "reason": "Kernova ships this support configuration for this build.",
                "fix": "Restore this file from the original Kernova build archive.",
            }
        )

    _write_json(build_dir / "config" / "vartapack" / "integrity.json", {"schema": 1, "files": files})


def _write_packping_config(build_dir: Path, pack_version: str, update_url: str) -> None:
    config = {
        "updateUrl": update_url or DEFAULT_PACKPING_UPDATE_URL,
        "localVersion": pack_version,
        "delay": 3000,
        "showChat": False,
        "showToast": False,
    }
    _write_json(build_dir / "config" / "packping.json", config)


def _profile_blocked_mods() -> list[dict[str, str]]:
    return [
        {
            "id": item["modId"],
            "name": item["displayName"],
            "requiredVersion": "",
            "reason": item["reason"],
        }
        for item in _hard_blocked_mods()
    ]


def _advanced_rules() -> list[dict[str, object]]:
    rules: list[dict[str, object]] = []

    for item in _hard_blocked_mods():
        rules.append(
            {
                "id": f"block-{item['modId']}",
                "type": "BLOCKED_MOD",
                "modId": item["modId"],
                "displayName": item["displayName"],
                "severity": item.get("severity", "ERROR"),
                "category": item.get("category", "performance"),
                "reason": item["reason"],
                "fix": item["fix"],
                "versionRange": "",
                "blockContinue": item.get("blockContinue", True),
            }
        )

    for item in _soft_blocked_mods():
        rules.append(
            {
                "id": f"softblock-{item['modId']}",
                "type": "SOFT_BLOCKED_MOD",
                "modId": item["modId"],
                "displayName": item["displayName"],
                "severity": "WARNING",
                "category": item.get("category", "performance"),
                "reason": item["reason"],
                "fix": item["fix"],
                "versionRange": "",
                "blockContinue": False,
            }
        )

    for item in _suspicious_mods():
        rules.append(
            {
                "id": f"suspicious-{item['modId']}",
                "type": "SUSPICIOUS_MOD",
                "modId": item["modId"],
                "displayName": item["displayName"],
                "severity": "INFO",
                "category": item.get("category", "performance"),
                "reason": item["reason"],
                "fix": item["fix"],
                "versionRange": "",
                "blockContinue": False,
            }
        )

    return rules


def _conflict_rules() -> list[dict[str, str]]:
    conflicts = []
    for mod_id, display_name in [
        ("optifine", "OptiFine"),
        ("optifabric", "OptiFabric"),
        ("rubidium", "Rubidium"),
        ("oculus", "Oculus"),
        ("embeddium", "Embeddium"),
        ("chloride", "Chloride"),
        ("vulkanmod", "VulkanMod"),
    ]:
        conflicts.append(
            {
                "id": f"sodium-{mod_id}",
                "modA": "sodium",
                "modB": mod_id,
                "severity": "ERROR",
                "reason": f"{display_name} changes the same rendering layer as Sodium.",
                "fix": f"Remove {display_name} and keep Kernova's tested Sodium stack.",
                "versionRangeA": "",
                "versionRangeB": "",
            }
        )

    for mod_id, display_name in [
        ("oculus", "Oculus"),
        ("optifine", "OptiFine"),
        ("optifabric", "OptiFabric"),
    ]:
        conflicts.append(
            {
                "id": f"iris-{mod_id}",
                "modA": "iris",
                "modB": mod_id,
                "severity": "ERROR",
                "reason": f"{display_name} overlaps with Iris shader support.",
                "fix": f"Remove {display_name} and keep Iris.",
                "versionRangeA": "",
                "versionRangeB": "",
            }
        )

    for mod_id, display_name in [
        ("starlight", "Starlight"),
        ("phosphor", "Phosphor"),
    ]:
        conflicts.append(
            {
                "id": f"scalablelux-{mod_id}",
                "modA": "scalablelux",
                "modB": mod_id,
                "severity": "ERROR",
                "reason": f"{display_name} is another lighting engine and is not part of Kernova's tested ScalableLux setup.",
                "fix": f"Remove {display_name}; keep ScalableLux.",
                "versionRangeA": "",
                "versionRangeB": "",
            }
        )

    for mod_id, display_name in [
        ("entitycullingunofficial", "Entity Culling Unofficial"),
        ("entity-culling", "Entity Culling alternative"),
    ]:
        conflicts.append(
            {
                "id": f"entityculling-{mod_id}",
                "modA": "entityculling",
                "modB": mod_id,
                "severity": "ERROR",
                "reason": f"{display_name} duplicates Kernova's official Entity Culling mod.",
                "fix": f"Remove {display_name}; keep the bundled Entity Culling.",
                "versionRangeA": "",
                "versionRangeB": "",
            }
        )

    return conflicts


def _hard_blocked_mods() -> list[dict[str, object]]:
    return [
        {
            "modId": "optifine",
            "displayName": "OptiFine",
            "severity": "CRITICAL",
            "category": "rendering",
            "reason": "OptiFine conflicts with Sodium, Iris, and the tested Kernova rendering stack.",
            "fix": "Remove OptiFine from the mods folder.",
        },
        {
            "modId": "optifabric",
            "displayName": "OptiFabric",
            "severity": "CRITICAL",
            "category": "rendering",
            "reason": "OptiFabric loads OptiFine into Fabric and is not supported by Kernova.",
            "fix": "Remove OptiFabric and OptiFine from the mods folder.",
        },
        {
            "modId": "rubidium",
            "displayName": "Rubidium",
            "category": "rendering",
            "reason": "Rubidium is an alternative Sodium port and duplicates Kernova's Sodium renderer.",
            "fix": "Remove Rubidium; Kernova already includes Sodium.",
        },
        {
            "modId": "oculus",
            "displayName": "Oculus",
            "category": "rendering",
            "reason": "Oculus is an Iris alternative for Forge-family stacks and overlaps with Kernova's Iris setup.",
            "fix": "Remove Oculus; Kernova already includes Iris.",
        },
        {
            "modId": "embeddium",
            "displayName": "Embeddium",
            "category": "rendering",
            "reason": "Embeddium is an alternative Sodium-derived renderer and is not part of Kernova's tested stack.",
            "fix": "Remove Embeddium; Kernova already includes Sodium.",
        },
        {
            "modId": "chloride",
            "displayName": "Chloride",
            "category": "rendering",
            "reason": "Chloride modifies the same rendering stack as Sodium/Embeddium and is not tested with Kernova.",
            "fix": "Remove Chloride.",
        },
        {
            "modId": "starlight",
            "displayName": "Starlight",
            "category": "lighting",
            "reason": "Starlight is another lighting engine and duplicates ScalableLux's role.",
            "fix": "Remove Starlight; Kernova uses ScalableLux.",
        },
        {
            "modId": "phosphor",
            "displayName": "Phosphor",
            "category": "lighting",
            "reason": "Phosphor is another lighting optimization layer and is not tested with ScalableLux.",
            "fix": "Remove Phosphor; Kernova uses ScalableLux.",
        },
        {
            "modId": "entitycullingunofficial",
            "displayName": "Entity Culling Unofficial",
            "category": "rendering",
            "reason": "This duplicates the official Entity Culling mod already bundled with Kernova.",
            "fix": "Remove Entity Culling Unofficial.",
        },
        {
            "modId": "entity-culling",
            "displayName": "Entity Culling Alternative",
            "category": "rendering",
            "reason": "This duplicates the official Entity Culling mod already bundled with Kernova.",
            "fix": "Remove the duplicate Entity Culling mod.",
        },
        {
            "modId": "oculus-flywheel-compat",
            "displayName": "Iris/Oculus Flywheel Compat",
            "category": "rendering",
            "reason": "Kernova does not ship Oculus, so Oculus compatibility layers are unsupported.",
            "fix": "Remove Oculus compatibility layers from the mods folder.",
        },
        {
            "modId": "sodiumplus",
            "displayName": "Sodium Plus",
            "category": "rendering",
            "reason": "Kernova already ships a curated Sodium stack and does not support bundled renderer replacements.",
            "fix": "Remove Sodium Plus-style renderer bundles.",
        },
        {
            "modId": "betterfpsdist",
            "displayName": "Better FPS Distance",
            "category": "rendering",
            "reason": "Render distance and culling behavior is already tuned by Kernova's Sodium/Entity Culling stack.",
            "fix": "Remove Better FPS Distance for supported Kernova testing.",
        },
    ]


def _soft_blocked_mods() -> list[dict[str, str]]:
    return [
        {
            "modId": "canary",
            "displayName": "Canary",
            "category": "performance",
            "reason": "Canary overlaps with Lithium-style game logic optimizations and is outside Kernova's tested stack.",
            "fix": "Remove Canary unless you are debugging a custom instance.",
        },
        {
            "modId": "radium",
            "displayName": "Radium",
            "category": "performance",
            "reason": "Radium overlaps with Lithium-style optimizations and may change game logic behavior.",
            "fix": "Remove Radium unless you are intentionally testing it.",
        },
        {
            "modId": "saturn",
            "displayName": "Saturn",
            "category": "memory",
            "reason": "Saturn changes memory behavior and can complicate support reports.",
            "fix": "Remove Saturn when reporting Kernova issues.",
        },
        {
            "modId": "vulkanmod",
            "displayName": "VulkanMod",
            "category": "rendering",
            "reason": "VulkanMod replaces the rendering backend and is not part of Kernova's tested Sodium/Iris stack.",
            "fix": "Remove VulkanMod for supported Kernova testing.",
        },
        {
            "modId": "nvidium",
            "displayName": "Nvidium",
            "category": "rendering",
            "reason": "Nvidium heavily changes Sodium rendering behavior and can affect shader compatibility.",
            "fix": "Remove Nvidium when reporting renderer or shader issues.",
        },
        {
            "modId": "distanthorizons",
            "displayName": "Distant Horizons",
            "category": "rendering",
            "reason": "Distant Horizons is a major LOD renderer and can affect performance, memory, and visual debugging.",
            "fix": "Disable Distant Horizons when reporting performance or rendering issues.",
        },
        {
            "modId": "exordium",
            "displayName": "Exordium",
            "category": "rendering",
            "reason": "Exordium modifies GUI rendering and can overlap with ImmediatelyFast behavior.",
            "fix": "Remove Exordium when reporting UI rendering issues.",
        },
        {
            "modId": "dashloader",
            "displayName": "DashLoader",
            "category": "loading",
            "reason": "DashLoader changes cache/loading behavior and can hide or introduce startup issues.",
            "fix": "Remove DashLoader when reporting startup or resource loading issues.",
        },
        {
            "modId": "smoothboot",
            "displayName": "Smooth Boot Reloaded",
            "category": "loading",
            "reason": "Alternative boot/thread tuning can overlap with Kernova's loader-specific startup tuning.",
            "fix": "Remove extra Smooth Boot variants when reporting startup issues.",
        },
    ]


def _suspicious_mods() -> list[dict[str, str]]:
    return [
        {
            "modId": "performant",
            "displayName": "Performant",
            "category": "performance",
            "reason": "Performant is known to alter broad game behavior and can make support reports unreliable.",
            "fix": "Remove Performant before reporting Kernova issues.",
        },
        {
            "modId": "betterfps",
            "displayName": "BetterFPS",
            "category": "performance",
            "reason": "BetterFPS-style tweaks overlap with Kernova's tested optimization stack.",
            "fix": "Remove it when comparing performance or reporting issues.",
        },
        {
            "modId": "cull-less-leaves",
            "displayName": "Cull Less Leaves",
            "category": "rendering",
            "reason": "Kernova already includes More Culling for leaf and block culling.",
            "fix": "Remove duplicate culling mods if visual artifacts appear.",
        },
        {
            "modId": "cull-less-leaves-reforged",
            "displayName": "Cull Less Leaves Reforged",
            "category": "rendering",
            "reason": "Kernova already includes More Culling for leaf and block culling.",
            "fix": "Remove duplicate culling mods if visual artifacts appear.",
        },
        {
            "modId": "rubidium_extra",
            "displayName": "Rubidium Extra",
            "category": "rendering",
            "reason": "Kernova uses Sodium Extra, not Rubidium/Embeddium Extra.",
            "fix": "Remove Rubidium Extra and use the bundled Sodium Extra.",
        },
        {
            "modId": "embeddiumplus",
            "displayName": "Embeddium Plus",
            "category": "rendering",
            "reason": "Kernova uses Sodium Extra and the tested Sodium stack.",
            "fix": "Remove Embeddium Plus.",
        },
        {
            "modId": "textrues_rubidium_options",
            "displayName": "TexTrue's Rubidium Options",
            "category": "rendering",
            "reason": "Kernova uses Reese's Sodium Options, not Rubidium options screens.",
            "fix": "Remove Rubidium option-screen addons.",
        },
    ]


def _profile_reason(mod: ResolvedMod) -> str:
    if mod.source == "dependency":
        return "Required dependency resolved automatically for this Kernova build."
    return f"{mod.priority.capitalize()} mod in the tested Kernova profile."


def _known_bundled_extra_mods(loader: str) -> list[str]:
    common = [
        "conditional-mixin",
        "conditional_mixin",
        "dynamic_fps_common",
        "mixinsquared",
        "cloth-basic-math",
        "net_lostluma_battery",
        "net_lenni0451_reflect",
        "transition",
        "trender",
        "org_jctools_jctools-core",
        "org_reactivestreams_reactive-streams",
        "io_reactivex_rxjava3_rxjava",
        "com_ibm_async_asyncutil",
        "com_velocitypowered_velocity-native",
        "com_github_ben-manes_caffeine_caffeine",
        "com_electronwill_night-config_core",
        "com_electronwill_night-config_toml",
        "org_anarres_jcpp",
        "org_antlr_antlr4-runtime",
        "io_github_douira_glsl-transformer",
        "net_objecthunter_exp4j",
    ]

    c2me_fabric = [
        "c2me-base",
        "c2me-client-uncapvd",
        "c2me-fixes-chunkio-threading-issues",
        "c2me-fixes-general-threading-issues",
        "c2me-fixes-worldgen-threading-issues",
        "c2me-fixes-worldgen-vanilla-bugs",
        "c2me-notickvd",
        "c2me-opts-allocs",
        "c2me-opts-chunkio",
        "c2me-opts-dfc",
        "c2me-opts-math",
        "c2me-opts-natives-math",
        "c2me-opts-scheduling",
        "c2me-opts-worldgen-general",
        "c2me-opts-worldgen-vanilla",
        "c2me-rewrites-chunk-serializer",
        "c2me-rewrites-chunk-system",
        "c2me-rewrites-chunkio",
        "c2me-server-utils",
        "c2me-threading-lighting",
    ]
    c2me_neoforge = [item.replace("-", "_") for item in c2me_fabric]

    if loader == "neoforge":
        return common + c2me_fabric + c2me_neoforge + ["kuma_api"]
    return common + c2me_fabric + c2me_neoforge


def _resolved_key(mod: ResolvedMod) -> str:
    return mod.version_id or mod.modrinth_id or mod.slug or mod.name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _doctor_summary(output: str, fallback: str) -> str:
    if not output:
        return fallback
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return fallback
    status = data.get("status", "UNKNOWN")
    issue_count = len(data.get("issues", []))
    return f"Doctor status {status}, issues={issue_count}."


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
