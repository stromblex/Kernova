import importlib.util
import json
import zipfile
from tempfile import TemporaryDirectory
from pathlib import Path
from argparse import Namespace
from unittest import TestCase
from unittest.mock import patch


def load_publish_module():
    path = Path(__file__).resolve().parents[2] / "publish" / "publish.py"
    spec = importlib.util.spec_from_file_location("kernova_publish", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load publish module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish = load_publish_module()


class PublishTests(TestCase):
    def test_curseforge_upload_artifact_defaults_to_modpack_zip(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {"curseforge": {}}

        path = publish.curseforge_upload_artifact(manifest, config)

        self.assertEqual(path.name, "Kernova-fabric-26.1-v1.0.0-release-curseforge.zip")
        self.assertIn("curseforge", path.parts)

    def test_create_curseforge_zip_has_manifest_and_overrides(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [
                {
                    "name": "Example",
                    "slug": "example",
                    "filename": "example.jar",
                    "available": True,
                }
            ],
        }
        config = {
            "author": "stromblex",
            "icon": "",
            "curseforge": {},
            "modrinth": {
                "loader_dependencies": {"fabric": {"fabric-loader": "0.19.3"}},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            out_dir = root / "out"
            (build_dir / "config").mkdir(parents=True)
            (build_dir / "config" / "packping.json").write_text("{}")
            (build_dir / "mods").mkdir()
            (build_dir / "mods" / "example.jar").write_text("jar")
            (build_dir / "build_manifest.json").write_text("{}")

            fingerprint = publish.curseforge_file_fingerprint(build_dir / "mods" / "example.jar")
            with patch.object(
                publish,
                "curseforge_fingerprint_matches",
                return_value={
                    fingerprint: {
                        "projectID": 1234,
                        "fileID": 5678,
                        "required": True,
                        "isLocked": False,
                    }
                },
            ):
                artifact = publish.create_curseforge_modpack_zip(
                    build_dir,
                    manifest,
                    config,
                    out_dir,
                    {"curseforge_token": "token"},
                )

            with zipfile.ZipFile(artifact) as archive:
                names = set(archive.namelist())
                cf_manifest = json.loads(archive.read("manifest.json"))

        self.assertIn("manifest.json", names)
        self.assertIn("modlist.html", names)
        self.assertIn("overrides/config/packping.json", names)
        self.assertNotIn("overrides/mods/example.jar", names)
        self.assertNotIn("overrides/build_manifest.json", names)
        self.assertEqual(cf_manifest["manifestType"], "minecraftModpack")
        self.assertEqual(cf_manifest["minecraft"]["modLoaders"][0]["id"], "fabric-0.19.3")
        self.assertEqual(
            cf_manifest["files"],
            [
                {
                    "projectID": 1234,
                    "fileID": 5678,
                    "required": True,
                    "isLocked": False,
                }
            ],
        )

    def test_mrpack_overrides_packping_update_url_for_modrinth_feed(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [],
        }
        config = {
            "icon": "",
            "modrinth": {"loader_dependencies": {"fabric": {"fabric-loader": "0.19.3"}}},
            "packping": {
                "platforms": {
                    "modrinth": {
                        "update_url": "https://example.invalid/update.modrinth.json",
                    }
                }
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            out_dir = root / "out"
            (build_dir / "config").mkdir(parents=True)
            (build_dir / "config" / "packping.json").write_text(
                json.dumps({"updateUrl": "https://example.invalid/update.json", "localVersion": "old"})
            )

            artifact = publish.create_mrpack(build_dir, manifest, config, out_dir)

            with zipfile.ZipFile(artifact) as archive:
                packping_config = json.loads(archive.read("overrides/config/packping.json"))

        self.assertEqual(packping_config["updateUrl"], "https://example.invalid/update.modrinth.json")

    def test_curseforge_zip_overrides_packping_update_url_for_curseforge_feed(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [],
        }
        config = {
            "author": "stromblex",
            "icon": "",
            "curseforge": {},
            "modrinth": {"loader_dependencies": {"fabric": {"fabric-loader": "0.19.3"}}},
            "packping": {
                "platforms": {
                    "curseforge": {
                        "update_url": "https://example.invalid/update.curseforge.json",
                    }
                }
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            out_dir = root / "out"
            (build_dir / "config").mkdir(parents=True)
            (build_dir / "config" / "packping.json").write_text(
                json.dumps({"updateUrl": "https://example.invalid/update.json", "localVersion": "old"})
            )

            artifact = publish.create_curseforge_modpack_zip(build_dir, manifest, config, out_dir)

            with zipfile.ZipFile(artifact) as archive:
                packping_config = json.loads(archive.read("overrides/config/packping.json"))

        self.assertEqual(packping_config["updateUrl"], "https://example.invalid/update.curseforge.json")

    def test_create_curseforge_zip_fails_when_mod_jar_cannot_be_matched(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [
                {
                    "name": "Example",
                    "filename": "example.jar",
                    "available": True,
                }
            ],
        }
        config = {
            "author": "stromblex",
            "icon": "",
            "curseforge": {},
            "modrinth": {
                "loader_dependencies": {"fabric": {"fabric-loader": "0.19.3"}},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            out_dir = root / "out"
            (build_dir / "mods").mkdir(parents=True)
            (build_dir / "mods" / "example.jar").write_text("jar")

            with patch.object(publish, "curseforge_fingerprint_matches", return_value={}):
                with self.assertRaisesRegex(ValueError, "Could not match every mod jar"):
                    publish.create_curseforge_modpack_zip(
                        build_dir,
                        manifest,
                        config,
                        out_dir,
                        {"curseforge_token": "token"},
                    )

    def test_create_curseforge_zip_uses_configured_file_override_without_api(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [
                {
                    "name": "Example",
                    "modrinth_id": "modrinth-example",
                    "filename": "example.jar",
                    "available": True,
                }
            ],
        }
        config = {
            "author": "stromblex",
            "icon": "",
            "curseforge": {
                "file_overrides": {
                    "modrinth-example": {"projectID": 4321, "fileID": 8765},
                },
            },
            "modrinth": {
                "loader_dependencies": {"fabric": {"fabric-loader": "0.19.3"}},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            out_dir = root / "out"
            (build_dir / "mods").mkdir(parents=True)
            (build_dir / "mods" / "example.jar").write_text("jar")

            with patch.object(publish, "curseforge_fingerprint_matches") as fingerprint_matches:
                artifact = publish.create_curseforge_modpack_zip(build_dir, manifest, config, out_dir)

            with zipfile.ZipFile(artifact) as archive:
                names = set(archive.namelist())
                cf_manifest = json.loads(archive.read("manifest.json"))

        fingerprint_matches.assert_not_called()
        self.assertNotIn("overrides/mods/example.jar", names)
        self.assertEqual(cf_manifest["files"][0]["projectID"], 4321)
        self.assertEqual(cf_manifest["files"][0]["fileID"], 8765)

    def test_curseforge_dry_run_upload_uses_modpack_zip_mode(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {
            "author": "stromblex",
            "curseforge": {
                "project_id": "",
                "release_type": "release",
                "game_versions": [],
                "minecraft_versions": {"26.1": 15933},
                "java_versions": [11135],
                "environment": [9638],
                "loaders": {"fabric": [7499]},
            },
        }
        with TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "curseforge.zip"
            artifact.write_text("zip")
            changelog = Path(tmp) / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "curseforge_upload_artifact", return_value=artifact),
                patch.object(publish, "changelog_path", return_value=changelog),
            ):
                ok = publish.upload_curseforge(manifest, config, {}, dry_run=True)

        self.assertTrue(ok)

    def test_packping_minecraft_upgrade_updates_old_loader_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            update_json = Path(tmp) / "update.json"
            update_json.write_text(
                json.dumps(
                    [
                        {
                            "minecraft": "26.1",
                            "loader": "fabric",
                            "version": "0.1.0-beta",
                            "download": "old",
                            "changelog": "old",
                            "settings": {"notifications": {"showToast": False}},
                        }
                    ]
                )
            )
            config = {
                "packping": {
                    "update_json": str(update_json),
                    "toast_on_minecraft_upgrade": True,
                    "minecraft_upgrade_toast": {
                        "title": "Kernova is available for Minecraft %version%",
                        "subtitle": "%pack_version% on %loader%",
                    },
                }
            }
            new_entry = {
                "minecraft": "26.1.1",
                "loader": "fabric",
                "version": "0.1.1-beta",
                "download": "new",
                "changelog": "new changes",
            }

            publish.update_packping_json(new_entry, config)

            entries = json.loads(update_json.read_text())
            old_entry = next(item for item in entries if item["minecraft"] == "26.1")
            self.assertEqual(old_entry["upgradeMinecraft"], "26.1.1")
            self.assertEqual(old_entry["version"], "0.1.1-beta")
            self.assertEqual(old_entry["download"], "new")
            self.assertTrue(old_entry["settings"]["notifications"]["showToast"])
            self.assertEqual(old_entry["toast"]["title"], "Kernova is available for Minecraft 26.1.1")
            self.assertEqual(old_entry["toast"]["subtitle"], "0.1.1-beta on fabric")

    def test_packping_changelog_is_concise(self) -> None:
        manifest = {
            "minecraft_version": "26.1.1",
            "loader": "neoforge",
            "build_folder": "Kernova neoforge 26.1.1 v0.1.0-beta",
            "pack_version": "0.1.0-beta",
            "resolved_mods": [
                {"name": "Sodium", "source": "list", "available": True},
                {"name": "Balm", "source": "dependency", "available": True},
                {
                    "name": "ModernFix",
                    "source": "list",
                    "available": False,
                    "skipped_reason": "No version available",
                },
            ],
        }

        changelog = publish.packping_changelog(manifest, publish.generated_changelog(manifest))

        self.assertIn("Pack version: 0.1.0-beta (beta)", changelog)
        self.assertIn("Mods: 2 available", changelog)
        self.assertIn("Unavailable: ModernFix", changelog)
        self.assertNotIn("### Mods", changelog)
        self.assertLessEqual(len(changelog.splitlines()), 10)

    def test_update_packping_json_uses_platform_specific_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "packping": {
                    "platforms": {
                        "modrinth": {"update_json": str(root / "update.modrinth.json")},
                        "curseforge": {"update_json": str(root / "update.curseforge.json")},
                    }
                }
            }
            entry = {
                "minecraft": "26.1",
                "loader": "fabric",
                "version": "1.0.0-release",
                "download": "https://example.invalid/modrinth",
                "changelog": "short",
            }

            path = publish.update_packping_json(entry, config, "curseforge")

            self.assertEqual(path, root / "update.curseforge.json")
            self.assertFalse((root / "update.modrinth.json").exists())
            self.assertEqual(json.loads(path.read_text())[0]["download"], "https://example.invalid/modrinth")

    def test_packping_download_url_uses_platform_template(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {
            "packping": {
                "platforms": {
                    "modrinth": {
                        "download_url_template": "https://modrinth.example/{version_number_url}",
                    },
                    "curseforge": {
                        "download_url_template": "https://curseforge.example/{minecraft}/{loader}",
                    },
                }
            }
        }

        self.assertEqual(
            publish.packping_download_url(manifest, config, "modrinth", "artifact.mrpack"),
            "https://modrinth.example/1.0.0-release%2Bfabric.mc26.1",
        )
        self.assertEqual(
            publish.packping_download_url(manifest, config, "curseforge", "artifact.zip"),
            "https://curseforge.example/26.1/fabric",
        )

    def test_modrinth_version_number_includes_minecraft_version(self) -> None:
        manifest = {
            "minecraft_version": "26.1.1",
            "loader": "neoforge",
            "pack_version": "0.1.0-beta",
        }

        self.assertEqual(
            publish.modrinth_version_number(manifest),
            "0.1.0-beta+neoforge.mc26.1.1",
        )

    def test_minecraft_upgrade_toast_renders_placeholders(self) -> None:
        toast = publish.minecraft_upgrade_toast(
            {"version": "1.0.0-release", "loader": "neoforge"},
            {
                "packping": {
                    "minecraft_upgrade_toast": {
                        "title": "Kernova %pack_version%",
                        "subtitle": "Minecraft %minecraft% via %loader%",
                    }
                }
            },
            "26.1.2",
        )

        self.assertEqual(toast["title"], "Kernova 1.0.0-release")
        self.assertEqual(toast["subtitle"], "Minecraft 26.1.2 via neoforge")

    def test_curseforge_metadata_resolves_minecraft_loader_java_and_environment_ids(self) -> None:
        manifest = {
            "minecraft_version": "26.1.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1.1 v0.1.0-beta",
            "pack_version": "0.1.0-beta",
        }
        config = {
            "curseforge": {
                "project_id": "1573362",
                "release_type": "beta",
                "game_versions": [],
                "minecraft_versions": {},
                "minecraft_version_type_ids": {"26.1": 83806},
                "java_versions": [11135],
                "environment": [9638],
                "loaders": {"fabric": [7499]},
            }
        }
        with TemporaryDirectory() as tmp:
            changelog = Path(tmp) / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(
                    publish,
                    "fetch_curseforge_game_versions",
                    return_value=[
                        {"id": 16084, "gameVersionTypeID": 1, "name": "26.1.1"},
                        {"id": 16021, "gameVersionTypeID": 83806, "name": "26.1.1"},
                    ],
                ),
                patch.object(
                    publish,
                    "fetch_curseforge_version_types",
                    return_value=[
                        {"id": 1, "name": "Minecraft"},
                        {"id": 83806, "name": "Minecraft 26.1"},
                    ],
                ),
            ):
                metadata, missing = publish.curseforge_upload_metadata(
                    manifest,
                    config,
                    {"curseforge_token": "token"},
                    allow_network=True,
                )

        self.assertEqual(missing, [])
        self.assertEqual(metadata["gameVersions"], [16021, 11135, 7499])
        self.assertEqual(metadata["releaseType"], "beta")

    def test_curseforge_release_type_auto_uses_pack_version_channel(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v0.1.0-beta",
            "pack_version": "0.1.0-beta",
        }
        config = {
            "curseforge": {
                "release_type": "auto",
                "game_versions": [],
                "minecraft_versions": {"26.1": 15933},
                "java_versions": [],
                "environment": [],
                "loaders": {"fabric": [7499]},
            }
        }
        with TemporaryDirectory() as tmp:
            changelog = Path(tmp) / "changelog.md"
            changelog.write_text("changes")
            with patch.object(publish, "changelog_path", return_value=changelog):
                metadata, missing = publish.curseforge_upload_metadata(
                    manifest,
                    config,
                    {},
                    allow_network=False,
                )

        self.assertEqual(missing, [])
        self.assertEqual(metadata["releaseType"], "beta")

    def test_curseforge_release_type_auto_supports_alpha(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v0.0.1-alpha",
            "pack_version": "0.0.1-alpha",
        }
        config = {
            "curseforge": {
                "release_type": "auto",
                "game_versions": [],
                "minecraft_versions": {"26.1": 15933},
                "java_versions": [],
                "environment": [],
                "loaders": {"fabric": [7499]},
            }
        }
        with TemporaryDirectory() as tmp:
            changelog = Path(tmp) / "changelog.md"
            changelog.write_text("changes")
            with patch.object(publish, "changelog_path", return_value=changelog):
                metadata, missing = publish.curseforge_upload_metadata(
                    manifest,
                    config,
                    {},
                    allow_network=False,
                )

        self.assertEqual(missing, [])
        self.assertEqual(metadata["releaseType"], "alpha")

    def test_modrinth_version_type_auto_uses_pack_version_channel(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v0.1.0-beta",
            "pack_version": "0.1.0-beta",
        }
        config = {
            "modrinth": {
                "project_id": "project",
                "version_type": "auto",
                "loaders": {"fabric": ["fabric"]},
                "featured": True,
            }
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / f"{publish.artifact_slug(manifest)}.mrpack"
            artifact.write_text("pack")
            changelog = root / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "platform_dir", return_value=root),
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(publish, "modrinth_version_exists", return_value=False),
                patch.object(publish, "post_multipart", return_value=(200, "ok")) as post_multipart,
            ):
                ok = publish.upload_modrinth(
                    manifest,
                    config,
                    {"modrinth_token": "token"},
                    dry_run=False,
                )

        self.assertTrue(ok)
        data = json.loads(post_multipart.call_args.args[2]["data"])
        self.assertEqual(data["version_type"], "beta")
        self.assertEqual(data["version_number"], "0.1.0-beta+fabric.mc26.1")

    def test_modrinth_version_type_auto_supports_alpha(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v0.0.1-alpha",
            "pack_version": "0.0.1-alpha",
        }
        config = {
            "modrinth": {
                "project_id": "project",
                "version_type": "auto",
                "loaders": {"fabric": ["fabric"]},
                "featured": True,
            }
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / f"{publish.artifact_slug(manifest)}.mrpack"
            artifact.write_text("pack")
            changelog = root / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "platform_dir", return_value=root),
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(publish, "modrinth_version_exists", return_value=False),
                patch.object(publish, "post_multipart", return_value=(200, "ok")) as post_multipart,
            ):
                ok = publish.upload_modrinth(
                    manifest,
                    config,
                    {"modrinth_token": "token"},
                    dry_run=False,
                )

        self.assertTrue(ok)
        data = json.loads(post_multipart.call_args.args[2]["data"])
        self.assertEqual(data["version_type"], "alpha")

    def test_modrinth_upload_syncs_project_side_metadata(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {
            "modrinth": {
                "project_id": "project",
                "version_type": "auto",
                "client_side": "required",
                "server_side": "unsupported",
                "loaders": {"fabric": ["fabric"]},
                "featured": True,
            }
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / f"{publish.artifact_slug(manifest)}.mrpack"
            artifact.write_text("pack")
            changelog = root / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "platform_dir", return_value=root),
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(publish, "fetch_modrinth_project_metadata", return_value=None),
                patch.object(publish, "modrinth_version_exists", return_value=False),
                patch.object(publish, "patch_json", return_value=(204, "")) as patch_json,
                patch.object(publish, "post_multipart", return_value=(200, "ok")),
            ):
                ok = publish.upload_modrinth(
                    manifest,
                    config,
                    {"modrinth_token": "token"},
                    dry_run=False,
                )

        self.assertTrue(ok)
        self.assertEqual(
            patch_json.call_args.args[2],
            {"client_side": "required", "server_side": "unsupported"},
        )

    def test_modrinth_upload_skips_project_side_metadata_when_already_set(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {
            "modrinth": {
                "project_id": "project",
                "version_type": "auto",
                "client_side": "required",
                "server_side": "unsupported",
                "loaders": {"fabric": ["fabric"]},
                "featured": True,
            }
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / f"{publish.artifact_slug(manifest)}.mrpack"
            artifact.write_text("pack")
            changelog = root / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "platform_dir", return_value=root),
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(
                    publish,
                    "fetch_modrinth_project_metadata",
                    return_value={"client_side": "required", "server_side": "unsupported"},
                ),
                patch.object(publish, "modrinth_version_exists", return_value=False),
                patch.object(publish, "patch_json") as patch_json,
                patch.object(publish, "post_multipart", return_value=(200, "ok")) as post_multipart,
            ):
                ok = publish.upload_modrinth(
                    manifest,
                    config,
                    {"modrinth_token": "token"},
                    dry_run=False,
                )

        self.assertTrue(ok)
        patch_json.assert_not_called()
        post_multipart.assert_called_once()

    def test_modrinth_upload_skips_existing_version(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {
            "modrinth": {
                "project_id": "project",
                "version_type": "auto",
                "client_side": "required",
                "server_side": "unsupported",
                "loaders": {"fabric": ["fabric"]},
                "featured": True,
            }
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / f"{publish.artifact_slug(manifest)}.mrpack"
            artifact.write_text("pack")
            changelog = root / "changelog.md"
            changelog.write_text("changes")
            with (
                patch.object(publish, "platform_dir", return_value=root),
                patch.object(publish, "changelog_path", return_value=changelog),
                patch.object(publish, "sync_modrinth_project_metadata", return_value=True),
                patch.object(publish, "modrinth_version_exists", return_value=True),
                patch.object(publish, "post_multipart") as post_multipart,
            ):
                ok = publish.upload_modrinth(
                    manifest,
                    config,
                    {"modrinth_token": "token"},
                    dry_run=False,
                )

        self.assertTrue(ok)
        post_multipart.assert_not_called()

    def test_invalid_publish_release_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be one of"):
            publish.publish_release_type({"pack_version": "1.0.0-release"}, {"release_type": "stable"}, "release_type")

    def test_manual_notes_file_takes_precedence_over_existing_changelog(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.1-release",
            "pack_version": "1.0.1-release",
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            script_dir = root / "publish"
            changelogs_dir = root / "changelogs"
            notes_dir = script_dir / "notes"
            notes_dir.mkdir(parents=True)
            changelogs_dir.mkdir()
            (notes_dir / "26.1-fabric.md").write_text("fresh notes\n\n### Build Details")
            (changelogs_dir / "Kernova fabric 26.1 v1.0.1-release.md").write_text(
                "## Kernova fabric 26.1 v1.0.1-release\n\n"
                "### Release Notes\n\n"
                "old generated notes\n\n"
                "Minecraft 26.1 | fabric | summary\n"
            )

            with (
                patch.object(publish, "SCRIPT_DIR", script_dir),
                patch.object(publish, "CHANGELOGS_DIR", changelogs_dir),
            ):
                notes = publish.read_manual_notes(manifest, None)

        self.assertEqual(notes, "fresh notes")

    def test_generated_changelog_summarizes_rebuild_against_previous_manifest(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.1-release",
            "pack_version": "1.0.1-release",
            "resolved_mods": [
                {
                    "name": "Sodium",
                    "source": "list",
                    "available": True,
                    "version_number": "1.0.0",
                    "version_id": "same",
                }
            ],
        }
        previous = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
            "resolved_mods": [
                {
                    "name": "Sodium",
                    "source": "list",
                    "available": True,
                    "version_number": "1.0.0",
                    "version_id": "same",
                }
            ],
        }

        with patch.object(publish, "find_previous_manifest", return_value=previous):
            changelog = publish.generated_changelog(manifest)

        self.assertIn("No mod version changes since `1.0.0-release`", changelog)
        self.assertIn("### Build Details", changelog)
        self.assertNotIn("### Included Mods", changelog)

    def test_neoforge_dependency_prefix_matches_exact_minecraft_patch(self) -> None:
        self.assertEqual(publish.neoforge_maven_prefix("1.21"), "21.0.")
        self.assertEqual(publish.neoforge_maven_prefix("1.21.1"), "21.1.")
        self.assertEqual(publish.neoforge_maven_prefix("26.1"), "26.1.0.")
        self.assertEqual(publish.neoforge_maven_prefix("26.1.1"), "26.1.1.")
        self.assertEqual(publish.neoforge_maven_prefix("26.1.2"), "26.1.2.")

    def test_latest_neoforge_version_does_not_cross_patch_branches(self) -> None:
        with patch.object(
            publish,
            "maven_versions",
            return_value=[
                "26.1.0.18-beta",
                "26.1.0.19-beta",
                "26.1.1.15-beta",
                "26.1.2.76",
            ],
        ):
            self.assertEqual(publish.latest_neoforge_version("26.1"), "26.1.0.19-beta")
            self.assertEqual(publish.latest_neoforge_version("26.1.1"), "26.1.1.15-beta")
            self.assertEqual(publish.latest_neoforge_version("26.1.2"), "26.1.2.76")

    def test_release_stops_before_real_upload_when_dry_run_fails(self) -> None:
        args = Namespace(
            build=None,
            latest=True,
            loader="both",
            mc="26.1",
            notes_file=None,
            platform="both",
            remote_repo=None,
            remote_file=None,
            message=None,
            init_repo=False,
        )
        with (
            patch.object(publish, "build_dirs_from_args", return_value=[Path("build")]),
            patch.object(publish, "package_one_build"),
            patch.object(publish, "upload_build", return_value=1) as upload_build,
            patch.object(publish, "sync_update_json") as sync_update_json,
        ):
            code = publish.release_build(args)

        self.assertEqual(code, 1)
        upload_build.assert_called_once()
        sync_update_json.assert_not_called()

    def test_release_stops_before_curseforge_when_real_modrinth_fails(self) -> None:
        args = Namespace(
            build=None,
            latest=True,
            loader="both",
            mc="26.1",
            notes_file=None,
            platform="both",
            remote_repo=None,
            remote_file=None,
            message=None,
            init_repo=False,
        )
        with (
            patch.object(publish, "build_dirs_from_args", return_value=[Path("build")]),
            patch.object(publish, "package_one_build"),
            patch.object(publish, "upload_build", side_effect=[0, 1]) as upload_build,
            patch.object(publish, "sync_update_json") as sync_update_json,
        ):
            code = publish.release_build(args)

        self.assertEqual(code, 1)
        self.assertEqual(upload_build.call_count, 2)
        self.assertTrue(upload_build.call_args_list[0].args[0].dry_run)
        self.assertEqual(upload_build.call_args_list[1].args[0].platform, "modrinth")
        sync_update_json.assert_not_called()

    def test_sync_update_json_copies_split_feeds_and_docs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local"
            remote = root / "remote"
            local.mkdir()
            remote.mkdir()
            (remote / "README.md").write_text(
                "# Kernova Update Feed\n\n"
                "Public PackPing update feed for the Kernova Minecraft modpack.\n\n"
                "The feed is generated by the Kernova release automation and served from GitHub Pages as `update.json`."
            )
            (local / "update.modrinth.json").write_text("")
            (local / "update.curseforge.json").write_text("")
            config = {
                "packping": {
                    "remote_repo": str(remote),
                    "platforms": {
                        "modrinth": {
                            "update_json": str(local / "update.modrinth.json"),
                            "remote_file": "update.modrinth.json",
                        },
                        "curseforge": {
                            "update_json": str(local / "update.curseforge.json"),
                            "remote_file": "update.curseforge.json",
                        },
                    },
                }
            }
            args = Namespace(
                remote_repo=None,
                remote_file=None,
                commit=False,
                push=False,
                message=None,
                init_repo=False,
            )

            code = publish.sync_update_json(args, config)

            self.assertEqual(code, 0)
            self.assertTrue((remote / "update.modrinth.json").exists())
            self.assertTrue((remote / "update.curseforge.json").exists())
            self.assertTrue((remote / "README.md").exists())
            self.assertTrue((remote / "LICENSE").exists())
            self.assertEqual(json.loads((remote / "update.modrinth.json").read_text()), [])
            self.assertEqual(json.loads((local / "update.modrinth.json").read_text()), [])
            self.assertIn("update.modrinth.json", (remote / "README.md").read_text())
