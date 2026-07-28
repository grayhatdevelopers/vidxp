import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

from pydantic import ValidationError

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ApplicationError,
    CapabilityDependencyCheck,
    CreateIndexCommand,
    DependencyKind,
    DependencyCheckCommand,
    DependencyUnavailableError,
    IndexResult,
    PrepareModelsCommand,
    SearchCommand,
)
from vidxp.core.contracts import IndexConfig
from vidxp.infrastructure.local_index import LocalIndexBackend
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
from vidxp.composition import create_local_application
from vidxp.repository_layout import RepositoryLayout
from vidxp.repositories import RepositoryConfigError
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings
from vidxp.ports import IndexStore


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
            minimum_available_memory_mb=0,
        )
        runtime = ModelRuntime(settings)
        active_backend = backend or Mock()
        return (
            VidXPApplication(
                settings=settings,
                layout=RepositoryLayout(root=Path(root)),
                registry=registry or create_capability_registry(),
                runtime=runtime,
                index_backend=active_backend,
            ),
            active_backend,
        )

    def indexed_application(
        self,
        handler,
        manager,
    ) -> VidXPApplication:
        definition = CapabilityDefinition(
            name="indexed",
            description="Indexed provider.",
            extra="indexed",
            collection_name="indexed",
            index_stage="indexed",
            execution_group="indexed",
            operations={
                "search": OperationDefinition(
                    input_model=SearchInput,
                    output_model=SearchResult,
                )
            },
        )

        registry = CapabilityRegistry(
            (
                CapabilityPlugin(
                    definition=definition,
                    executor_factory=lambda: CapabilityExecutor(
                        indexer=Mock(),
                        operations={"search": handler},
                    ),
                ),
            )
        )
        application, backend = self.application(
            "unused",
            registry=registry,
        )
        backend.active_config.return_value = (
            IndexConfig.local(
                enabled_modalities=("indexed",),
                collection_names={"indexed": "indexed"},
            ),
            {},
        )
        backend.open_store.return_value = manager
        return application

    def test_local_composition_maps_typed_repository_errors_safely(self):
        with (
            patch(
                "vidxp.composition.resolve_repository",
                side_effect=RepositoryConfigError("secret-path"),
            ),
            self.assertRaises(ApplicationError) as caught,
        ):
            create_local_application()

        self.assertEqual(caught.exception.code, "configuration_invalid")
        self.assertNotIn(
            "secret-path",
            json.dumps(caught.exception.to_dict()),
        )

    def test_local_composition_does_not_reclassify_unexpected_value_errors(self):
        with (
            patch(
                "vidxp.composition.resolve_repository",
                side_effect=ValueError("programming error"),
            ),
            self.assertRaisesRegex(ValueError, "programming error"),
        ):
            create_local_application()

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

    def test_application_boundary_returns_stable_validation_error(self):
        application, _ = self.application("unused")

        with self.assertRaises(ApplicationError) as raised:
            application.execute("scene", "unknown", {})

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(
            raised.exception.to_dict()["category"],
            "validation",
        )

    def test_validation_error_does_not_expose_input(self):
        definition = CapabilityDefinition(
            name="validate",
            description="Validate input.",
            extra="validate",
            operations={
                "run": OperationDefinition(
                    input_model=SearchInput,
                    output_model=SearchResult,
                    requires_index=False,
                )
            },
        )
        application, _ = self.application(
            "unused",
            registry=CapabilityRegistry(
                (
                    CapabilityPlugin(
                        definition=definition,
                        executor_factory=lambda: CapabilityExecutor(
                            operations={
                                "run": lambda _context, request: {
                                    "query_id": "validate:1",
                                    "query": request.query,
                                    "modality": "validate",
                                }
                            }
                        ),
                    ),
                )
            ),
        )

        with self.assertRaises(ApplicationError) as raised:
            application.execute(
                "validate",
                "run",
                {
                    "query": "hello",
                    "secret": "do-not-leak",
                },
            )

        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertNotIn(
            "do-not-leak",
            json.dumps(raised.exception.to_dict()),
        )

    def test_missing_media_error_does_not_expose_path(self):
        application, _ = self.application("unused")
        secret_path = Path("private/customer/video.mp4")

        with self.assertRaises(ApplicationError) as raised:
            application.create_index(
                CreateIndexCommand(
                    path=secret_path,
                    modalities=("scene",),
                )
            )

        self.assertEqual(raised.exception.code, "resource_not_found")
        self.assertNotIn(
            str(secret_path),
            json.dumps(raised.exception.to_dict()),
        )

    def test_downstream_missing_file_is_not_misclassified_as_media(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"video")
            application, backend = self.application(directory)
            backend.create.side_effect = FileNotFoundError("ffmpeg")

            with self.assertRaises(DependencyUnavailableError) as raised:
                application.create_index(
                    CreateIndexCommand(
                        path=path,
                        modalities=("scene",),
                    )
                )

        self.assertEqual(raised.exception.code, "dependency_unavailable")
        self.assertNotEqual(raised.exception.category, "not_found")

    def test_open_store_dependency_failure_is_stable(self):
        application, backend = self.application("unused")
        backend.active_config.return_value = (
            IndexConfig.local(
                enabled_modalities=("scene",),
                collection_names={"scene": "scene"},
            ),
            {},
        )
        backend.open_store.side_effect = ModuleNotFoundError("chromadb")

        with self.assertRaises(DependencyUnavailableError) as raised:
            application.execute(
                "scene",
                "search",
                {"query": "hello"},
            )

        payload = json.dumps(raised.exception.to_dict())
        self.assertNotIn("chromadb", payload)
        self.assertIsInstance(
            raised.exception.__cause__,
            ModuleNotFoundError,
        )

    def test_prepare_dependency_failure_is_stable(self):
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
        plugin = CapabilityPlugin(
            definition=definition,
            executor_factory=lambda: CapabilityExecutor(
                operations={
                    "noop": lambda _context, request: {
                        "query_id": "noop:1",
                        "query": request.query,
                        "modality": "noop",
                    }
                },
                prepare=Mock(
                    side_effect=ModuleNotFoundError("provider.internal")
                ),
            ),
        )
        registry = CapabilityRegistry((plugin,))
        registry.dependency_checks = Mock(return_value=())
        application, _ = self.application("unused", registry=registry)

        with self.assertRaises(DependencyUnavailableError) as raised:
            application.prepare_models(
                PrepareModelsCommand(modalities=("prepare-only",))
            )

        self.assertNotIn(
            "provider.internal",
            json.dumps(raised.exception.to_dict()),
        )

    def test_unexpected_handler_error_is_not_misclassified(self):
        failure = RuntimeError("implementation bug")
        definition = CapabilityDefinition(
            name="broken",
            description="Broken provider.",
            extra="broken",
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
                    "run": Mock(side_effect=failure),
                }
            ),
        )
        application, _ = self.application(
            "unused",
            registry=CapabilityRegistry((plugin,)),
        )

        with self.assertRaises(RuntimeError) as raised:
            application.execute("broken", "run", {"query": "hello"})

        self.assertIs(raised.exception, failure)

    def test_indexed_operation_uses_and_closes_exact_injected_store(self):
        store = Mock(spec=IndexStore)
        manager = MagicMock()
        manager.__enter__.return_value = store
        seen = []

        def handle(context, request):
            seen.append(context.require_storage())
            return {
                "query_id": "indexed:1",
                "query": request.query,
                "modality": "indexed",
            }

        application = self.indexed_application(handle, manager)

        result = application.execute(
            "indexed",
            "search",
            {"query": "hello"},
        )

        self.assertEqual(result.query_id, "indexed:1")
        self.assertEqual(seen, [store])
        manager.__enter__.assert_called_once_with()
        manager.__exit__.assert_called_once_with(None, None, None)

    def test_store_closes_when_output_validation_fails(self):
        store = Mock(spec=IndexStore)
        manager = MagicMock()
        manager.__enter__.return_value = store
        application = self.indexed_application(
            lambda _context, _request: {"query": "missing fields"},
            manager,
        )

        with self.assertRaises(ApplicationError) as raised:
            application.execute(
                "indexed",
                "search",
                {"query": "hello"},
            )

        self.assertEqual(raised.exception.code, "invalid_request")
        exit_args = manager.__exit__.call_args.args
        self.assertIs(exit_args[0], ValidationError)

    def test_store_closes_when_handler_fails(self):
        store = Mock(spec=IndexStore)
        manager = MagicMock()
        manager.__enter__.return_value = store
        failure = RuntimeError("implementation bug")
        application = self.indexed_application(
            Mock(side_effect=failure),
            manager,
        )

        with self.assertRaises(RuntimeError) as raised:
            application.execute(
                "indexed",
                "search",
                {"query": "hello"},
            )

        self.assertIs(raised.exception, failure)
        exit_args = manager.__exit__.call_args.args
        self.assertIs(exit_args[0], RuntimeError)
        self.assertIs(exit_args[1], failure)

    def test_dependency_check_returns_shared_result_model(self):
        registry = create_capability_registry()
        registry.dependency_checks = Mock(
            return_value=(
                CapabilityDependencyCheck(
                    capability="scene",
                    kind=DependencyKind.distribution,
                    name="transformers",
                    requirement="transformers>=5,<6",
                    ok=False,
                    error="distribution is not installed",
                ),
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

    def test_clear_delegates_all_persistence_cleanup_to_backend(self):
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
            self.assertTrue((index / "manifest.json").exists())
            self.assertTrue(unrelated.exists())

    def test_local_backend_owns_vector_and_metadata_cleanup(self):
        with TemporaryDirectory() as directory:
            index = Path(directory) / "index"
            index.mkdir()
            (index / "manifest.json").write_text("{}", encoding="utf-8")
            unrelated = index / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            settings = VidXPSettings(
                repository_root=directory,
                runtime_backend="cpu",
            )
            registry = create_capability_registry()
            backend = LocalIndexBackend(registry, ModelRuntime(settings))
            storage = MagicMock()
            storage.__enter__.return_value = storage
            config = IndexConfig.local(storage_directory=index)

            with patch(
                "vidxp.infrastructure.local_index.IndexStorage",
                return_value=storage,
            ):
                backend.clear(config)

            storage.clear.assert_called_once_with()
            self.assertFalse((index / "manifest.json").exists())
            self.assertTrue(unrelated.exists())

    def test_local_backend_injects_and_closes_storage_for_indexing(self):
        settings = VidXPSettings(
            repository_root="unused",
            runtime_backend="cpu",
        )
        registry = create_capability_registry()
        backend = LocalIndexBackend(registry, ModelRuntime(settings))
        storage = MagicMock()
        storage.__enter__.return_value = storage
        config = IndexConfig.local(
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
        )

        with (
            patch(
                "vidxp.infrastructure.local_index.IndexStorage",
                return_value=storage,
            ),
            patch(
                "vidxp.infrastructure.local_index.index_video",
                return_value={"scene_frames": 1},
            ) as index_video,
        ):
            result = backend.create(
                Path("video.mp4"),
                config=config,
                progress=None,
                cancellation=None,
                source_name=None,
            )

        self.assertEqual(result, {"scene_frames": 1})
        self.assertIs(index_video.call_args.kwargs["storage"], storage)
        self.assertIs(
            index_video.call_args.kwargs["manifest_store"].runtime,
            backend.runtime,
        )
        storage.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
