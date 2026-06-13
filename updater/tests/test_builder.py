from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import json
import subprocess

from kernova_update import builder
from kernova_update import integrations
from kernova_update.models import ResolvedMod


class BuilderTests(TestCase):
    def test_copy_configs_layers_common_then_loader_specific(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            list_dir = root / "list"
            build_dir = root / "build"
            common_config = list_dir / "common" / "config"
            fabric_config = list_dir / "fabric" / "config"
            common_config.mkdir(parents=True)
            fabric_config.mkdir(parents=True)

            (common_config / "shared.txt").write_text("common\n")
            (common_config / "overridden.txt").write_text("common\n")
            (fabric_config / "overridden.txt").write_text("fabric\n")
            (fabric_config / "fabric-only.txt").write_text("fabric\n")

            with patch.object(builder, "LIST_DIR", list_dir):
                copied = builder.copy_configs(build_dir, "fabric")

            self.assertTrue(copied)
            self.assertEqual((build_dir / "config" / "shared.txt").read_text(), "common\n")
            self.assertEqual((build_dir / "config" / "overridden.txt").read_text(), "fabric\n")
            self.assertEqual((build_dir / "config" / "fabric-only.txt").read_text(), "fabric\n")

    def test_copy_options_remains_loader_specific(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            list_dir = root / "list"
            build_dir = root / "build"
            fabric_dir = list_dir / "fabric"
            fabric_dir.mkdir(parents=True)
            (fabric_dir / "options.txt").write_text("options\n")

            with patch.object(builder, "LIST_DIR", list_dir):
                copied = builder.copy_options(build_dir, "fabric")

            self.assertTrue(copied)
            self.assertEqual((build_dir / "options.txt").read_text(), "options\n")

    def test_vartapack_integrity_tracks_support_configs_without_mod_config_noise(self) -> None:
        with TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            config_dir = build_dir / "config"
            (config_dir / "vartapack").mkdir(parents=True)
            (config_dir / "vartapack" / "profile.json").write_text("{}")
            (config_dir / "vartapack" / "rules.json").write_text("{}")
            (config_dir / "vartapack" / "vartapack.json").write_text("{}")
            (config_dir / "packping.json").write_text("{}")
            (config_dir / "sodium-options.json").write_text("{}")

            integrations._write_integrity_manifest(build_dir, [])

            data = json.loads((config_dir / "vartapack" / "integrity.json").read_text())

        entries = {item["path"]: item for item in data["files"]}
        self.assertEqual(entries["config/vartapack/profile.json"]["severityIfMissing"], "ERROR")
        self.assertEqual(entries["config/packping.json"]["severityIfMissing"], "WARNING")
        self.assertEqual(entries["config/packping.json"]["severityIfChanged"], "INFO")
        self.assertNotIn("config/sodium-options.json", entries)

    def test_vartapack_doctor_retries_after_old_java(self) -> None:
        with TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            mods_dir = build_dir / "mods"
            mods_dir.mkdir()
            (mods_dir / "vartapack.jar").write_text("")
            resolved = [ResolvedMod(name="VartaPack", slug="vartapack", filename="vartapack.jar", available=True)]
            old_java = subprocess.CompletedProcess(
                args=["/old/java"],
                returncode=1,
                stdout="",
                stderr="UnsupportedClassVersionError",
            )
            newer_java = subprocess.CompletedProcess(
                args=["/newer/java"],
                returncode=0,
                stdout='{"status":"OK","issues":[]}',
                stderr="",
            )

            with (
                patch.object(integrations, "_java_candidates", return_value=[Path("/old/java"), Path("/newer/java")]),
                patch.object(integrations.subprocess, "run", side_effect=[old_java, newer_java]) as run,
            ):
                doctor = integrations.run_vartapack_doctor(build_dir, resolved)

        self.assertEqual(doctor.status, "ok")
        self.assertEqual(run.call_args_list[0].args[0][0], "/old/java")
        self.assertEqual(run.call_args_list[1].args[0][0], "/newer/java")
