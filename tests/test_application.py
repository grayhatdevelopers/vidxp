import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    CreateIndexCommand,
    DependencyCheckCommand,
    IndexResult,
    PrepareModelsCommand,
    SearchCommand,
)
from vidxp.capabilities.contracts import (
    CapabilityDefinition,
    CapabilityExecutor,
    CapabilityPlugin,
    OperationDefinition,
)
from vidxp.capabilities.registry import (
    CapabilityRegistry,
    create_capability_registry,
)
from vidxp.capabilities.schemas import SearchInput, SearchResult
from vidxp.repository_layout import RepositoryLayout
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings


class ApplicationTests(unittest.TestCase):
    def application(
        self,
        root: str | Path,
        *,
        registry: CapabilityRegistry | None = None,
        backend: Mock | None = None,
    ) -> tuple[VidXPApplication, Mock]:
        settings = VidXPSettings(
            repository_root=Path(root),
            runtime_backend="cpu",
        )
        runtime = ModelRuntime(settings)
        active_backend = backend or Mock()
        return (
            VidXPApplication(
                layout=RepositoryLayout(root=Path(root)),
                registry=registry or create_capability_registry(),
                runtime=runtime,
                index_backend=active_backend,
            ),
            active_backend,
        )

    def test_missing_index_has_shared_status_model(self):
        application, backend = self.application("missing")
        backend.status.return_value = None

        status = application.index_status()

        self.assertEqual(status.state, "missing")
        self.assertEqual(status.schema_version, 1)
        self.assertEqual(status.index_directory, Path("missing/indexes/current"))

    def test_create_index_builds_one_central_config(self):
        with TemporaryDirectory() as directory:
            application, backend = self.application(directory)
            backend.create.return_value = {"scene_frames": 1}

            result = application.create_index(
                CreateIndexCommand(
                    path=Path("video.mp4"),
                    modalities=("scene",),
                    frame_stride=5,
                )
            )

        self.assertEqual(result, IndexResult(summary={"scene_frames": 1}))
        config = backend.create.call_args.kwargs["config"]
        self.assertEqual(config.enabled_modalities, ("scene",))
        self.assertEqual(config.frame_stride, 5)
        self.assertEqual(config.device, "cpu")

    def test_operation_definition_is_metadata_and_executor_owns_handler(self):
        definition = CapabilityDefinition(
            name="export",
            description="Export results.",
            extra="export",
            operations={
                "run": OperationDefinition(
                    input_model=SearchInput,
                    output_model=SearchResult,
                    requires_index=False,
                )
            },
        )
        plugin = CapabilityPlugin(
            definition=definition,
            executor_factory=lambda: CapabilityExecutor(
                operations={
                    "run": lambda _context, request: {
                        "query_id": "export:1",
                        "query": request.query,
                        "modality": "export",
                        "hits": (),
                    }
                }
            ),
        )
        application, _ = self.application(
            "unused",
            registry=CapabilityRegistry((plugin,)),
        )

        result = application.execute(
            "export",
            "run",
            {"query": "bundle"},
        )

        self.assertEqual(result.query, "bundle")

    def test_search_is_only_a_typed_projection_over_execute(self):
        application, _ = self.application("unused")
        expected = SearchResult(
            query_id="scene:1",
            query="yellow taxi",
            modality="scene",
        )
        application.execute = Mock(return_value=expected)

        result = application.search(
            SearchCommand(
                modality="scene",
                query="yellow taxi",
                top_k=7,
            )
        )

        self.assertIs(result, expected)
        application.execute.assert_called_once_with(
            "scene",
            "search",
            {"query": "yellow taxi", "top_k": 7},
        )

    def test_dependency_check_returns_shared_result_model(self):
        registry = create_capability_registry()
        registry.dependency_checks = Mock(
            return_value=(
                {"name": "transformers", "ok": False, "error": "missing"},
            )
        )
        application, _ = self.application("unused", registry=registry)

        result = application.check_dependencies(
            DependencyCheckCommand(modalities=("scene",))
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.modalities, ("scene",))

    def test_prepare_uses_injected_runtime_and_executor(self):
        definition = CapabilityDefinition(
            name="prepare-only",
            description="Prepare a provider.",
            extra="prepare-only",
            operations={
                "noop": OperationDefinition(
                    input_model=SearchInput,
                    output_model=SearchResult,
                    requires_index=False,
                )
            },
            prepares_models=True,
        )
        prepare = Mock(return_value=("model",))
        plugin = CapabilityPlugin(
            definition=definition,
            executor_factory=lambda: CapabilityExecutor(
                operations={
                    "noop": lambda _context, request: {
                        "query_id": "noop:1",
                        "query": request.query,
                        "modality": "noop",
                        "hits": (),
                    }
                },
                prepare=prepare,
            ),
        )
        registry = CapabilityRegistry((plugin,))
        registry.dependency_checks = Mock(return_value=())
        application, _ = self.application("unused", registry=registry)

        result = application.prepare_models(
            PrepareModelsCommand(modalities=("prepare-only",))
        )

        self.assertEqual(result.prepared, ("model",))
        self.assertIs(
            prepare.call_args.args[0].runtime,
            application.runtime,
        )

    def test_clear_delegates_storage_and_removes_known_state_only(self):
        with TemporaryDirectory() as directory:
            application, backend = self.application(directory)
            index = application.index_directory
            index.mkdir(parents=True)
            (index / "manifest.json").write_text("{}", encoding="utf-8")
            unrelated = index / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            backend.indexing_in_progress.return_value = False
            backend.status.return_value = None

            self.assertTrue(application.clear_index())

            backend.clear.assert_called_once()
            self.assertFalse((index / "manifest.json").exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
