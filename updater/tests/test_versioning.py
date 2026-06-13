import json
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from kernova_update import versioning
from kernova_update.models import ResolvedMod


class VersioningTests(TestCase):
    def test_latest_version_is_loader_specific(self) -> None:
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "version_history.json"
            history_file.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "fabric",
                                "pack_version": "1.0.0-release",
                                "timestamp": "2026-06-12T00:00:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "fabric-hash",
                            },
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "neoforge",
                                "pack_version": "1.0.0-beta",
                                "timestamp": "2026-06-12T00:01:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "neoforge-hash",
                            },
                        ]
                    }
                )
            )

            with (
                patch.object(versioning, "HISTORY_FILE", history_file),
                patch.object(versioning, "entry_has_project_artifact", return_value=True),
            ):
                self.assertEqual(
                    versioning.get_latest_version("Kernova", "26.1", "fabric"),
                    "1.0.0-release",
                )
                self.assertEqual(
                    versioning.get_latest_version("Kernova", "26.1", "neoforge"),
                    "1.0.0-beta",
                )

    def test_versions_hash_uses_available_version_ids(self) -> None:
        resolved = [
            ResolvedMod(name="A", available=True, version_id="2"),
            ResolvedMod(name="B", available=False, version_id="1"),
            ResolvedMod(name="C", available=True, version_id="1"),
        ]

        self.assertEqual(
            versioning.compute_versions_hash(resolved),
            versioning.compute_versions_hash(list(reversed(resolved))),
        )

    def test_channel_initial_versions(self) -> None:
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "version_history.json"

            with patch.object(versioning, "HISTORY_FILE", history_file):
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "beta"), "0.1.0")
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "release"), "1.0.0")
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "alpha"), "0.0.1")

    def test_channel_versions_roll_over_at_patch_nine(self) -> None:
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "version_history.json"
            history_file.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "fabric",
                                "pack_version": "0.1.9-beta",
                                "timestamp": "2026-06-12T00:00:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "beta-hash",
                            },
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "fabric",
                                "pack_version": "1.0.9-release",
                                "timestamp": "2026-06-12T00:01:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "release-hash",
                            },
                        ]
                    }
                )
            )

            with (
                patch.object(versioning, "HISTORY_FILE", history_file),
                patch.object(versioning, "entry_has_project_artifact", return_value=True),
            ):
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "beta"), "0.2.0")
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "release"), "1.1.0")

    def test_beta_suggestion_ignores_legacy_one_x_beta_history(self) -> None:
        with TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "version_history.json"
            history_file.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "neoforge",
                                "pack_version": "1.0.1-beta",
                                "timestamp": "2026-06-12T00:00:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "legacy-beta-hash",
                            }
                        ]
                    }
                )
            )

            with patch.object(versioning, "HISTORY_FILE", history_file):
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "neoforge", "beta"), "0.1.0")

    def test_suggestion_ignores_history_without_project_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_file = root / "version_history.json"
            builds_dir = root / "builds"
            changelogs_dir = root / "changelogs"
            publish_dir = root / "publish"
            history_file.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "build_name": "Kernova",
                                "minecraft_version": "26.1",
                                "loader": "fabric",
                                "pack_version": "1.0.2-release",
                                "timestamp": "2026-06-12T00:00:00+00:00",
                                "mod_count": 1,
                                "skipped_count": 0,
                                "mod_versions_hash": "stale-hash",
                            }
                        ]
                    }
                )
            )

            with (
                patch.object(versioning, "HISTORY_FILE", history_file),
                patch.object(versioning, "BUILDS_DIR", builds_dir),
                patch.object(versioning, "CHANGELOGS_DIR", changelogs_dir),
                patch.object(versioning, "PUBLISH_DIR", publish_dir),
            ):
                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "release"), "1.0.0")

                build_dir = builds_dir / "Kernova fabric 26.1 v1.0.2-release"
                build_dir.mkdir(parents=True)
                (build_dir / "build_manifest.json").write_text("{}")

                self.assertEqual(versioning.suggest_next_version("Kernova", "26.1", "fabric", "release"), "1.0.3")

    def test_record_build_updates_existing_pack_version(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "state"
            history_file = state_dir / "version_history.json"

            with (
                patch.object(versioning, "STATE_DIR", state_dir),
                patch.object(versioning, "HISTORY_FILE", history_file),
            ):
                versioning.record_build("Kernova", "26.1", "fabric", "1.0.2-release", 10, 1)
                versioning.record_build("Kernova", "26.1", "fabric", "1.0.2-release", 11, 2)

                history = versioning.load_history()

        self.assertEqual(len(history.entries), 1)
        self.assertEqual(history.entries[0].mod_count, 11)
        self.assertEqual(history.entries[0].skipped_count, 2)
