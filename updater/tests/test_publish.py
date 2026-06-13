import importlib.util
import json
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
    def test_curseforge_upload_artifact_defaults_to_full_zip(self) -> None:
        manifest = {
            "minecraft_version": "26.1",
            "loader": "fabric",
            "build_folder": "Kernova fabric 26.1 v1.0.0-release",
            "pack_version": "1.0.0-release",
        }
        config = {"curseforge": {}}

        path = publish.curseforge_upload_artifact(manifest, config)

        self.assertEqual(path.name, "Kernova-fabric-26.1-v1.0.0-release-full.zip")
        self.assertIn("full", path.parts)

    def test_curseforge_dry_run_upload_uses_full_zip_mode(self) -> None:
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
            artifact = Path(tmp) / "full.zip"
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
        self.assertEqual(metadata["gameVersions"], [16021, 11135, 9638, 7499])

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
