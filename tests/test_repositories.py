import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vidxp.app_paths import default_repository_directory
from vidxp.repositories import (
    DEFAULT_REPOSITORY_NAME,
    RepositoryConfigError,
    RepositoryRegistry,
    resolve_repository,
)


class RepositoryRegistryTests(unittest.TestCase):
    def test_missing_registry_exposes_non_persistent_default(self):
        with TemporaryDirectory() as directory:
            registry = RepositoryRegistry(Path(directory) / "repos.json")

            repositories = registry.list()
            selected = registry.resolve()

        self.assertEqual(repositories[0].name, DEFAULT_REPOSITORY_NAME)
        self.assertFalse(repositories[0].configured)
        self.assertEqual(
            selected.index_directory,
            default_repository_directory(),
        )
        self.assertTrue(selected.index_directory.is_absolute())

    def test_data_directory_controls_the_implicit_default_repository(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry_path = root / "repos.json"
            data_directory = root / "application-data"
            unrelated_working_directory = root / "working-directory"
            unrelated_working_directory.mkdir()
            original_working_directory = Path.cwd()
            try:
                os.chdir(unrelated_working_directory)
                with patch.dict(
                    os.environ,
                    {"VIDXP_DATA_DIR": str(data_directory)},
                    clear=False,
                ):
                    _, selected = resolve_repository(
                        registry_path=registry_path,
                    )
            finally:
                os.chdir(original_working_directory)

        self.assertEqual(
            selected.index_directory,
            data_directory / "repositories" / "default",
        )

    def test_add_use_replace_and_remove_round_trip(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "repos.json"
            registry = RepositoryRegistry(path)
            repository = registry.add(
                "team",
                "indexes/team",
                device="cuda",
            )
            registry.use("team")

            selected = registry.resolve()
            payload = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(selected, repository)
            self.assertEqual(payload["active_repository"], "team")
            with self.assertRaisesRegex(
                RepositoryConfigError,
                "already exists",
            ):
                registry.add("team", "other")

            replacement = registry.add(
                "team",
                "indexes/replacement",
                replace=True,
            )
            removed = registry.remove("team")

        self.assertEqual(
            replacement.index_directory,
            Path("indexes/replacement").resolve(),
        )
        self.assertEqual(removed, replacement)

    def test_remove_never_deletes_the_repository_index(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            index = root / "index"
            index.mkdir()
            marker = index / "keep"
            marker.write_text("data", encoding="utf-8")
            registry = RepositoryRegistry(root / "repos.json")
            registry.add("team", index)

            registry.remove("team")

            self.assertTrue(marker.is_file())

    def test_invalid_names_and_unknown_repositories_are_rejected(self):
        with TemporaryDirectory() as directory:
            registry = RepositoryRegistry(Path(directory) / "repos.json")
            with self.assertRaises(RepositoryConfigError):
                registry.add("../escape", "index")
            with self.assertRaisesRegex(
                RepositoryConfigError,
                "not configured",
            ):
                registry.resolve("missing")

    def test_explicit_and_environment_overrides_have_stable_precedence(self):
        with TemporaryDirectory() as directory:
            registry_path = Path(directory) / "repos.json"
            RepositoryRegistry(registry_path).add(
                "team",
                "registered",
                device="cpu",
            )
            environment = {
                "VIDXP_REPOSITORY": "team",
                "VIDXP_INDEX_DIR": "environment-index",
                "VIDXP_DEVICE": "mps",
            }
            with patch.dict(os.environ, environment, clear=False):
                _, selected = resolve_repository(
                    registry_path=registry_path,
                    index_directory="explicit-index",
                    device="cuda",
                )

        self.assertEqual(selected.name, "team")
        self.assertEqual(selected.index_directory, Path("explicit-index"))
        self.assertEqual(selected.device, "cuda")

    def test_explicit_data_directory_precedes_environment_default(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment_root = root / "environment-data"
            explicit_root = root / "explicit-data"
            with patch.dict(
                os.environ,
                {"VIDXP_DATA_DIR": str(environment_root)},
                clear=False,
            ):
                _, selected = resolve_repository(
                    registry_path=root / "repos.json",
                    data_directory=explicit_root,
                )

        self.assertEqual(
            selected.index_directory,
            explicit_root / "repositories" / "default",
        )


if __name__ == "__main__":
    unittest.main()
