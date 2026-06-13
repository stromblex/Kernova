from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from typer.testing import CliRunner

from kernova_update import cli
from kernova_update.models import BuildManifest


class CliTests(TestCase):
    def test_yes_uses_default_name_when_name_is_omitted(self) -> None:
        runner = CliRunner()
        manifest = BuildManifest(
            minecraft_version="26.1",
            loader="fabric",
            pack_version="1.0.0-alpha",
            build_folder="Kernova fabric 26.1 v1.0.0-alpha",
            resolved_mods=[],
        )

        with (
            patch.object(cli, "resolve_all", return_value=[]),
            patch.object(cli, "has_changes", return_value=True),
            patch.object(cli, "stability_tag", return_value="alpha"),
            patch.object(cli, "suggest_next_version", return_value="1.0.0"),
            patch.object(cli, "create_build", return_value=manifest) as create_build,
            patch.object(cli, "record_build"),
            patch.object(cli, "generate_changelog", return_value=Path("changelog.md")),
        ):
            result = runner.invoke(cli.app, ["fabric", "--mc", "26.1", "--yes", "--force"])

        self.assertEqual(result.exit_code, 0, result.output)
        create_build.assert_called_once()
        self.assertEqual(create_build.call_args.args[0], "Kernova")

    def test_yes_requires_minecraft_version(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.app, ["fabric", "--yes"])

        self.assertEqual(result.exit_code, 2)
        self.assertIn("Minecraft version is required", result.output)
