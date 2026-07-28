import sys
import types
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from packaging.requirements import Requirement

from vidxp.capabilities.contracts import CapabilityDefinition
from vidxp.capabilities.dialogue import models as dialogue_models
from vidxp.capabilities.registry import create_capability_registry
from vidxp.core.contracts import VideoSource
from vidxp.dependencies import inspect_requirement
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


class ModelTests(unittest.TestCase):
    def runtime(self, root: str) -> ModelRuntime:
        return ModelRuntime(
            VidXPSettings(
                repository_root=root,
                model_cache=Path(root) / "models",
                runtime_backend="cpu",
            )
        )

    def test_model_runtime_reuses_one_provider_instance(self):
        with TemporaryDirectory() as directory:
            runtime = self.runtime(directory)
            constructor = Mock(return_value=object())
            fake_module = types.SimpleNamespace(
                SentenceTransformer=constructor
            )
            with patch.dict(
                sys.modules,
                {"sentence_transformers": fake_module},
            ):
                first = dialogue_models.get_embedder(
                    runtime,
                    "model-id",
                    "revision",
                )
                second = dialogue_models.get_embedder(
                    runtime,
                    "model-id",
                    "revision",
                )

        self.assertIs(first, second)
        constructor.assert_called_once_with(
            "model-id",
            device="cpu",
            cache_folder=str(Path(directory) / "models"),
            revision="revision",
            local_files_only=False,
        )

    def test_scene_dependency_check_is_capability_scoped(self):
        registry = create_capability_registry()
        inspected = []

        def installed(name):
            inspected.append(name)
            return {
                "chromadb": "1.5.9",
                "numpy": "2.5.1",
                "opencv-python-headless": "5.0.0.93",
                "Pillow": "12.3.0",
                "torch": "2.13.0",
                "transformers": "5.14.1",
            }[name]

        with patch("vidxp.dependencies.version", side_effect=installed):
            checks = registry.dependency_checks(("scene",))

        self.assertTrue(all(check["ok"] for check in checks))
        self.assertNotIn("faster-whisper", inspected)
        self.assertNotIn("sentence-transformers", inspected)
        self.assertNotIn("pooch", inspected)

    def test_transcript_only_excludes_transcription_provider(self):
        registry = create_capability_registry()
        source = VideoSource(
            transcript=({"text": "hello", "start": 0, "end": 1},)
        )

        distributions = {
            requirement.name
            for requirement in registry.requirements_for(
                ("dialogue",),
                source=source,
            )
        }

        self.assertIn("sentence-transformers", distributions)
        self.assertNotIn("faster-whisper", distributions)

    def test_runtime_distributions_come_from_registry(self):
        registry = create_capability_registry()
        with patch(
            "vidxp.capabilities.registry.installed_base_requirements",
            return_value=(),
        ):
            distributions = registry.runtime_distributions()

        self.assertIn("transformers", distributions)
        self.assertIn("faster-whisper", distributions)
        self.assertIn("opencv-python-headless", distributions)
        self.assertNotIn("clip-anytorch", distributions)
        self.assertNotIn("face-recognition", distributions)
        self.assertEqual(len(distributions), len(set(distributions)))

    def test_requirement_files_are_dependency_contract(self):
        self.assertNotIn("dependencies", CapabilityDefinition.model_fields)
        registry = create_capability_registry()
        self.assertEqual(
            registry.runtime_checks_for(("dialogue",)),
            (),
        )

    def test_requirement_check_uses_distribution_metadata(self):
        requirement = Requirement("example>=2,<3")
        with patch("vidxp.dependencies.version", return_value="2.5"):
            self.assertTrue(inspect_requirement(requirement)["ok"])
        with patch("vidxp.dependencies.version", return_value="1.0"):
            self.assertFalse(inspect_requirement(requirement)["ok"])
        with patch(
            "vidxp.dependencies.version",
            side_effect=PackageNotFoundError("example"),
        ):
            self.assertFalse(inspect_requirement(requirement)["ok"])


if __name__ == "__main__":
    unittest.main()
