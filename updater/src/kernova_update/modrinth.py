"""Modrinth API v2 client for resolving and downloading mods."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from .models import Mod, ResolvedMod

BASE_URL = "https://api.modrinth.com/v2"
USER_AGENT = "Kernova-Updater (github.com/stromblex/Kernova)"


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )



def resolve_mod(
    mod: Mod,
    mc_version: str,
    loader: str,
) -> ResolvedMod:
    """Resolve a single mod to a downloadable version for the target MC version."""
    project_id = mod.modrinth_id
    if not project_id:
        return ResolvedMod(
            name=mod.name,
            slug=mod.slug,
            modrinth_id=None,
            priority=mod.priority,
            available=False,
            skipped_reason="No Modrinth project ID",
        )

    with _client() as client:
        resp = client.get(
            f"/project/{project_id}/version",
            params={
                "game_versions": f'["{mc_version}"]',
                "loaders": f'["{loader}"]',
            },
        )
        if resp.status_code == 404:
            return ResolvedMod(
                name=mod.name,
                slug=mod.slug,
                modrinth_id=project_id,
                priority=mod.priority,
                available=False,
                skipped_reason="Project not found on Modrinth",
            )
        resp.raise_for_status()
        versions = resp.json()

    if not versions:
        return ResolvedMod(
            name=mod.name,
            slug=mod.slug,
            modrinth_id=project_id,
            priority=mod.priority,
            available=False,
            skipped_reason=f"No version available for {mc_version}/{loader}",
        )

    # Prefer release > beta > alpha
    type_order = {"release": 0, "beta": 1, "alpha": 2}
    versions.sort(key=lambda v: type_order.get(v.get("version_type", "release"), 3))
    chosen = versions[0]

    primary_file = next(
        (f for f in chosen.get("files", []) if f.get("primary")),
        chosen.get("files", [{}])[0] if chosen.get("files") else None,
    )

    if not primary_file:
        return ResolvedMod(
            name=mod.name,
            slug=mod.slug,
            modrinth_id=project_id,
            priority=mod.priority,
            available=False,
            skipped_reason="Version found but no downloadable file",
        )

    hashes = primary_file.get("hashes", {})

    return ResolvedMod(
        name=mod.name,
        slug=mod.slug,
        modrinth_id=project_id,
        priority=mod.priority,
        version_id=chosen.get("id"),
        version_number=chosen.get("version_number"),
        filename=primary_file.get("filename"),
        url=primary_file.get("url"),
        sha512=hashes.get("sha512"),
        sha1=hashes.get("sha1"),
        available=True,
    )


def resolve_dependencies(
    resolved: list[ResolvedMod],
    mc_version: str,
    loader: str,
) -> list[ResolvedMod]:
    """Check resolved mods for required dependencies not already in the list."""
    known_ids = {m.modrinth_id for m in resolved if m.modrinth_id}
    dep_mods: list[ResolvedMod] = []

    with _client() as client:
        for mod in resolved:
            if not mod.available or not mod.version_id:
                continue
            resp = client.get(f"/version/{mod.version_id}")
            if resp.status_code != 200:
                continue
            version_data = resp.json()
            for dep in version_data.get("dependencies", []):
                dep_project_id = dep.get("project_id")
                dep_type = dep.get("dependency_type", "")
                if dep_type != "required" or not dep_project_id:
                    continue
                if dep_project_id in known_ids:
                    continue
                # Resolve the dependency
                dep_mod = Mod(
                    name=f"dependency:{dep_project_id}",
                    modrinth_id=dep_project_id,
                    priority="required",
                )
                dep_resolved = resolve_mod(dep_mod, mc_version, loader)
                dep_resolved.source = "dependency"
                if dep_resolved.available:
                    # Fetch project name
                    proj_resp = client.get(f"/project/{dep_project_id}")
                    if proj_resp.status_code == 200:
                        dep_resolved.name = proj_resp.json().get("title", dep_resolved.name)
                        dep_resolved.slug = proj_resp.json().get("slug")
                    dep_mods.append(dep_resolved)
                    known_ids.add(dep_project_id)

    return dep_mods


def download_mod(mod: ResolvedMod, dest_dir: Path) -> Path:
    """Download a mod jar and verify its hash. Returns the file path."""
    if not mod.url or not mod.filename:
        raise ValueError(f"Cannot download {mod.name}: no URL or filename")

    dest = dest_dir / mod.filename
    with _client() as client:
        with client.stream("GET", mod.url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)

    # Verify hash
    if mod.sha512:
        file_hash = hashlib.sha512(dest.read_bytes()).hexdigest()
        if file_hash != mod.sha512:
            dest.unlink()
            raise ValueError(
                f"Hash mismatch for {mod.filename}: "
                f"expected {mod.sha512[:16]}..., got {file_hash[:16]}..."
            )
    elif mod.sha1:
        file_hash = hashlib.sha1(dest.read_bytes()).hexdigest()
        if file_hash != mod.sha1:
            dest.unlink()
            raise ValueError(
                f"SHA1 mismatch for {mod.filename}: "
                f"expected {mod.sha1}, got {file_hash}"
            )

    return dest
