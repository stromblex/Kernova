from hashlib import sha256
from pathlib import Path
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RepositoryLayoutTests(TestCase):
    def test_identical_loader_configs_are_kept_in_common(self) -> None:
        fabric_config = PROJECT_ROOT / "list" / "fabric" / "config"
        neoforge_config = PROJECT_ROOT / "list" / "neoforge" / "config"
        common_config = PROJECT_ROOT / "list" / "common" / "config"

        duplicate_paths: list[str] = []
        for fabric_file in sorted(fabric_config.rglob("*")):
            if not fabric_file.is_file():
                continue
            rel = fabric_file.relative_to(fabric_config)
            neoforge_file = neoforge_config / rel
            if not neoforge_file.is_file():
                continue
            if sha256(fabric_file.read_bytes()).digest() == sha256(neoforge_file.read_bytes()).digest():
                duplicate_paths.append(rel.as_posix())

        self.assertEqual(
            duplicate_paths,
            [],
            "Identical Fabric/NeoForge configs should be moved to list/common/config",
        )
        self.assertTrue(common_config.is_dir())
