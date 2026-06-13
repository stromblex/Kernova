import json
import zipfile
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from kernova_update import builder, validation


class ValidationTests(TestCase):
    def test_loader_validation_reports_duplicate_ids_and_missing_configs(self) -> None:
        with TemporaryDirectory() as tmp:
            list_dir = Path(tmp) / "list"
            modlist_dir = list_dir / "fabric" / "mods"
            modlist_dir.mkdir(parents=True)
            (list_dir / "common" / "config").mkdir(parents=True)
            (list_dir / "common" / "config" / "present.json").write_text("{}")
            modlist = {
                "meta": {"loader": "fabric"},
                "mods": [
                    {
                        "name": "One",
                        "slug": "one",
                        "modrinth_id": "same",
                        "config_files": ["present.json"],
                    },
                    {
                        "name": "Two",
                        "slug": "two",
                        "modrinth_id": "same",
                        "config_files": ["missing.json"],
                    },
                ],
            }
            (modlist_dir / "modlist.json").write_text(json.dumps(modlist))

            with (
                patch.object(builder, "LIST_DIR", list_dir),
                patch.object(validation, "LIST_DIR", list_dir),
            ):
                issues = validation.validate_loader_profiles(("fabric",))

        messages = [issue.message for issue in issues]
        self.assertTrue(any("Duplicate modrinth_id" in message for message in messages))
        self.assertTrue(any("missing config file 'missing.json'" in message for message in messages))

    def test_duplicate_loader_configs_are_warnings(self) -> None:
        with TemporaryDirectory() as tmp:
            list_dir = Path(tmp) / "list"
            fabric_config = list_dir / "fabric" / "config"
            neoforge_config = list_dir / "neoforge" / "config"
            common_config = list_dir / "common" / "config"
            fabric_config.mkdir(parents=True)
            neoforge_config.mkdir(parents=True)
            common_config.mkdir(parents=True)
            (fabric_config / "same.json").write_text("{}")
            (neoforge_config / "same.json").write_text("{}")

            with patch.object(validation, "LIST_DIR", list_dir):
                issues = validation.validate_no_duplicate_loader_configs()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].level, "warning")
        self.assertIn("Identical loader-specific config", issues[0].message)

    def test_build_artifact_validation_reports_missing_jars(self) -> None:
        with TemporaryDirectory() as tmp:
            builds_dir = Path(tmp) / "builds"
            build_dir = builds_dir / "Kernova fabric 26.1 v1.0.0-release"
            build_dir.mkdir(parents=True)
            manifest = {
                "minecraft_version": "26.1",
                "loader": "fabric",
                "pack_version": "1.0.0-release",
                "build_folder": build_dir.name,
                "resolved_mods": [
                    {
                        "name": "Missing Jar",
                        "filename": "missing.jar",
                        "available": True,
                    }
                ],
            }
            (build_dir / "build_manifest.json").write_text(json.dumps(manifest))

            with patch.object(validation, "BUILDS_DIR", builds_dir):
                issues = validation.validate_build_artifacts()

        self.assertTrue(any("Missing downloaded jar" in issue.message for issue in issues))

    def test_packping_validation_reports_duplicate_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            update_json = Path(tmp) / "update.json"
            update_json.write_text(
                json.dumps(
                    [
                        {
                            "minecraft": "26.1",
                            "loader": "fabric",
                            "version": "1.0.0",
                            "download": "https://example.invalid",
                            "changelog": "ok",
                        },
                        {
                            "minecraft": "26.1",
                            "loader": "fabric",
                            "version": "1.0.1",
                            "download": "https://example.invalid",
                            "changelog": "ok",
                        },
                    ]
                )
            )

            with patch.object(validation, "PACKPING_UPDATE", update_json):
                issues = validation.validate_packping_update()

        self.assertTrue(any("Duplicate PackPing entry" in issue.message for issue in issues))

    def test_packping_validation_rejects_empty_feed(self) -> None:
        with TemporaryDirectory() as tmp:
            update_json = Path(tmp) / "update.json"
            update_json.write_text("[]")

            with patch.object(validation, "PACKPING_UPDATE", update_json):
                issues = validation.validate_packping_update()

        self.assertTrue(any("at least one entry" in issue.message for issue in issues))

    def test_modrinth_artifact_validation_requires_loader_dependency(self) -> None:
        with TemporaryDirectory() as tmp:
            modrinth_dir = Path(tmp) / "modrinth"
            artifact = modrinth_dir / "26.1" / "fabric" / "test" / "test.mrpack"
            artifact.parent.mkdir(parents=True)
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr(
                    "modrinth.index.json",
                    json.dumps(
                        {
                            "dependencies": {"minecraft": "26.1"},
                            "files": [
                                {
                                    "path": "mods/a.jar",
                                    "downloads": ["https://example.invalid/a.jar"],
                                    "hashes": {"sha1": "abc"},
                                    "fileSize": 1,
                                }
                            ],
                        }
                    ),
                )

            with patch.object(validation, "MODRINTH_DIR", modrinth_dir):
                issues = validation.validate_modrinth_artifacts()

        self.assertTrue(any("Missing fabric-loader dependency" in issue.message for issue in issues))
