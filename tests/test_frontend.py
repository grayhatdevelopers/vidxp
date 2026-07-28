import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from vidxp import frontend
from vidxp.application_models import DependencyCheckResult, IndexStatus
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capabilities.schemas import SearchHit, SearchResult


class UploadedVideo:
    name = "video.mp4"

    def __init__(self, content: bytes):
        self.content = content

    def getvalue(self):
        return self.content


class FrontendTests(unittest.TestCase):
    def tearDown(self):
        frontend._configured_service.cache_clear()

    def service(self, root: Path) -> Mock:
        service = Mock()
        service.layout.media = root / "media"
        service.layout.artifacts = root / "artifacts"
        service.layout.root = root
        service.registry = create_capability_registry()
        return service

    def test_service_is_composed_lazily_and_cached(self):
        repository = SimpleNamespace(
            index_directory=Path("repository"),
            device="cpu",
        )
        application = Mock()
        with (
            patch.object(
                frontend,
                "resolve_repository",
                return_value=(Mock(), repository),
            ),
            patch.object(
                frontend,
                "create_application",
                return_value=application,
            ) as create,
        ):
            first = frontend._configured_service()
            second = frontend._configured_service()

        self.assertIs(first, application)
        self.assertIs(second, application)
        create.assert_called_once()

    def test_uploaded_video_identity_controls_search_readiness(self):
        with TemporaryDirectory() as directory:
            service = self.service(Path(directory))
            with patch.object(
                frontend,
                "_configured_service",
                return_value=service,
            ):
                uploaded = UploadedVideo(b"video")
                matching = {
                    "state": "ready",
                    "video": {"sha256": frontend._video_hash(uploaded)},
                }
                stale = {
                    "state": "ready",
                    "video": {"sha256": "different"},
                }
                self.assertTrue(frontend._is_search_ready(matching, uploaded))
                self.assertFalse(frontend._is_search_ready(stale, uploaded))

    def test_available_modalities_use_application_dependency_commands(self):
        with TemporaryDirectory() as directory:
            service = self.service(Path(directory))

            def check(command):
                return DependencyCheckResult(
                    ok=command.modalities != ("actor",),
                    modalities=command.modalities,
                    checks=(),
                )

            service.check_dependencies.side_effect = check
            with patch.object(
                frontend,
                "_configured_service",
                return_value=service,
            ):
                available = frontend._available_index_modalities()

        self.assertEqual(available, ("dialogue", "scene"))

    def test_search_uses_shared_command_and_returns_timestamp(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.service(root)
            service.index_status.return_value = IndexStatus(
                schema_version=1,
                state="ready",
                stage="complete",
                message="ready",
                repository_root=root,
                index_directory=root / "indexes/current",
            )
            service.search.return_value = SearchResult(
                query_id="scene:1",
                query="taxi",
                modality="scene",
                hits=(
                    SearchHit(
                        rank=1,
                        video_id="video-1",
                        start=12.5,
                        end=13.0,
                        score=-0.1,
                        raw_distance=0.1,
                        modality="scene",
                        source_id="scene:1",
                    ),
                ),
            )
            with patch.object(
                frontend,
                "_configured_service",
                return_value=service,
            ):
                result = frontend._run_search("scene", "taxi")

        self.assertEqual(result["timestamp"], 12.5)
        command = service.search.call_args.args[0]
        self.assertEqual(command.modality, "scene")
        self.assertEqual(command.query, "taxi")


if __name__ == "__main__":
    unittest.main()
