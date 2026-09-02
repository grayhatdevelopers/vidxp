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
    ComponentReadiness,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    DependencyKind,
    DependencyCheckCommand,
    DependencyCheckResult,
    DependencyUnavailableError,
    EvidenceBoardResult,
    EvidenceDeliveryMode,
    FusedSearchResult,
    IndexResult,
    IndexSnapshotReference,
    ModelUnavailableError,
    ModelDownloadError,
    PrepareModelsCommand,
    QueryAnswerMode,
    QueryVideoCommand,
    RemoveIndexCommand,
    SearchCommand,
    SearchHit,
    InitialEvidenceDeliveryPolicy,
)
from vidxp.core.media import MediaUnavailableError
from vidxp.core.contracts import IndexConfig, IndexSchemaError
from vidxp.infrastructure.local_index import LocalIndexBackend
from vidxp.model_contracts import (
    ModelArtifactDownloadError,
    ModelArtifactUnavailableError,
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
from vidxp.capabilities.actor.schemas import (
    ActorClusterInput,
    ActorClusterSummary,
    ActorClustersInput,
    ActorClustersOutput,
    ActorDetection,
    ActorDetectionsInput,
    ActorDetectionsOutput,
)
from vidxp.control_plane import ControlPlaneApplication
from vidxp.composition import create_local_application
from vidxp.repository_layout import RepositoryLayout
from vidxp.repositories import RepositoryConfigError
from vidxp.runtime import ModelRuntime
from vidxp.settings import VidXPSettings
from vidxp.ports import IndexStore
from vidxp.execution import ExecutionContext


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
SNAPSHOT_SHA256 = "a" * 64


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
        media_service = Mock()
        artifact_service = Mock()
        return (
            VidXPApplication(
                settings=settings,
                layout=RepositoryLayout(root=Path(root)),
                registry=registry or create_capability_registry(),
                runtime=runtime,
                index_backend=active_backend,
                media=media_service,
                artifacts=artifact_service,
                index_status=lambda: active_backend.status(Path(root)),
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
        backend.active_config.return_value = IndexConfig.local(
            enabled_modalities=("indexed",),
            collection_names={"indexed": "indexed"},
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

    def test_local_composition_opens_and_closes_services_lazily(self):
        application = Mock()
        jobs = Mock()
        with (
            TemporaryDirectory() as directory,
            patch(
                "vidxp.composition.create_application",
                return_value=application,
            ) as create_application,
            patch(
                "vidxp.composition.create_job_service",
                return_value=jobs,
            ) as create_jobs,
        ):
            local = create_local_application(index_directory=directory)
            self.assertEqual(local.repository.index_directory, Path(directory))
            create_application.assert_not_called()
            create_jobs.assert_not_called()

            self.assertIs(local.application, application)
            self.assertIs(local.application, application)
            create_application.assert_called_once()
            create_jobs.assert_not_called()
            local.close()
            jobs.close.assert_not_called()

            self.assertIs(local.jobs, jobs)
            create_jobs.assert_called_once()
            local.close()
            jobs.close.assert_called_once()

    def test_missing_index_has_shared_status_model(self):
        application, backend = self.application("missing")
        backend.status.return_value = None

        status = application.index_status()

        self.assertEqual(status.state, "missing")
        self.assertEqual(status.schema_version, 1)
        self.assertIsInstance(application, ControlPlaneApplication)

    def test_actor_cluster_is_a_typed_projection_over_execute(self):
        application, _ = self.application("repository")
        expected = ActorClusterSummary(
            cluster_id="actor-1",
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
            detection_count=2,
            first_timestamp=1,
            last_timestamp=2,
        )
        with patch.object(
            application,
            "execute",
            return_value=expected,
        ) as execute:
            cluster = application.actor_cluster("actor-1")

        self.assertEqual(cluster, expected)
        execute.assert_called_once_with(
            "actor",
            "cluster",
            {"cluster_id": "actor-1"},
        )

    def test_pinned_search_reopens_the_requested_snapshot(self):
        contexts = []

        def handler(context, request):
            contexts.append(context)
            return SearchResult(
                query_id="indexed:query",
                query=request.query,
                modality="indexed",
            )

        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application = self.indexed_application(handler, manager)
        backend = application.index_backend
        pinned = IndexConfig.local(
            enabled_modalities=("indexed",),
            collection_names={"indexed": "indexed"},
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256=SNAPSHOT_SHA256,
        )
        backend.config_for_snapshot.return_value = pinned

        result = application.search(
            SearchCommand(modalities=("indexed",), query="query"),
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
        )

        self.assertEqual(result.query, "query")
        backend.active_config.assert_not_called()
        backend.config_for_snapshot.assert_called_once_with(
            application.index_directory,
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256=SNAPSHOT_SHA256,
            device="cpu",
        )
        backend.open_store.assert_called_once_with(pinned)
        self.assertIs(contexts[0].storage, manager.__enter__.return_value)

    def test_query_reuses_one_pinned_store_and_preserves_media_scope(self):
        requests = []

        def handler(_context, request):
            requests.append(request)
            return SearchResult(
                query_id="indexed:query",
                query=request.query,
                modality="indexed",
            )

        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application = self.indexed_application(handler, manager)
        pinned = IndexConfig.local(
            enabled_modalities=("indexed",),
            collection_names={"indexed": "indexed"},
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256=SNAPSHOT_SHA256,
        )
        application.index_backend.config_for_snapshot.return_value = pinned

        result = application.query_video(
            QueryVideoCommand(
                question="What happens?",
                media_id=MEDIA_ID,
                modalities=("indexed",),
            ),
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
        )

        self.assertEqual(result.mode, QueryAnswerMode.no_evidence)
        self.assertEqual(requests[0].media_id, MEDIA_ID)
        application.index_backend.open_store.assert_called_once_with(pinned)

    def test_actor_render_reuses_one_pinned_store_and_context(self):
        contexts = []
        calls = []

        def cluster_handler(context, request):
            contexts.append(context)
            calls.append(("cluster", request.cluster_id))
            return ActorClusterSummary(
                cluster_id=request.cluster_id,
                media_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                detection_count=1,
                first_timestamp=1,
                last_timestamp=1,
            )

        def detections_handler(context, request):
            contexts.append(context)
            calls.append(("detections", request.cursor))
            return ActorDetectionsOutput(
                cluster_id=request.cluster_id,
                detections=(
                    ActorDetection(
                        detection_id="detection-1",
                        cluster_id=request.cluster_id,
                        frame_index=1,
                        timestamp=1,
                        bbox=(0, 0, 10, 10),
                        dataset="local",
                        split="local",
                        run_id="default",
                        media_id=MEDIA_ID,
                        generation_id=GENERATION_ID,
                        modality="actor",
                        source_id="frame-1",
                    ),
                ),
            )

        definition = CapabilityDefinition(
            name="actor",
            description="Actor provider.",
            extra="actor",
            collection_name="actor",
            index_stage="actor",
            execution_group="actor",
            operations={
                "cluster": OperationDefinition(
                    input_model=ActorClusterInput,
                    output_model=ActorClusterSummary,
                ),
                "detections": OperationDefinition(
                    input_model=ActorDetectionsInput,
                    output_model=ActorDetectionsOutput,
                ),
            },
        )
        registry = CapabilityRegistry(
            (
                CapabilityPlugin(
                    definition=definition,
                    executor_factory=lambda: CapabilityExecutor(
                        indexer=Mock(),
                        operations={
                            "cluster": cluster_handler,
                            "detections": detections_handler,
                        },
                    ),
                ),
            )
        )
        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application, backend = self.application(
            "repository",
            registry=registry,
        )
        pinned = IndexConfig.local(
            enabled_modalities=("actor",),
            collection_names={"actor": "actor"},
            snapshot_id=SNAPSHOT_ID,
            snapshot_sha256=SNAPSHOT_SHA256,
        )
        backend.config_for_snapshot.return_value = pinned
        backend.open_store.return_value = manager
        application.artifacts.create_actor_overlay.return_value = Mock()

        application.render_actor(
            CreateActorOverlayCommand(cluster_id="actor-1"),
            snapshot=IndexSnapshotReference(
                snapshot_id=SNAPSHOT_ID,
                snapshot_sha256=SNAPSHOT_SHA256,
            ),
            media_id=MEDIA_ID,
            generation_id=GENERATION_ID,
        )

        backend.active_config.assert_not_called()
        backend.open_store.assert_called_once_with(pinned)
        self.assertEqual(
            calls,
            [("cluster", "actor-1"), ("detections", None)],
        )
        self.assertEqual(len({id(context) for context in contexts}), 1)
        application.artifacts.create_actor_overlay.assert_called_once()
        artifact_call = (
            application.artifacts.create_actor_overlay.call_args.kwargs
        )
        self.assertEqual(artifact_call["media_id"], MEDIA_ID)
        self.assertEqual(artifact_call["generation_id"], GENERATION_ID)

    def test_create_index_builds_one_central_config(self):
        with TemporaryDirectory() as directory:
            application, backend = self.application(directory)
            application.media.require_record.return_value = Mock(
                original_filename="video.mp4",
                sha256="1" * 64,
            )
            application.media.content.return_value = Mock(
                path=Path("managed.mp4")
            )
            backend.create.return_value = {
                "media_id": MEDIA_ID,
                "generation_id": GENERATION_ID,
                "snapshot_id": SNAPSHOT_ID,
                "active_media_count": 1,
                "record_counts": {"scene": 1},
            }

            result = application.create_index(
                CreateIndexCommand(
                    media_id=MEDIA_ID,
                    modalities=("scene",),
                    frame_stride=5,
                    scene_sample_fps=2.0,
                )
            )

        self.assertEqual(
            result,
            IndexResult(
                media_id=MEDIA_ID,
                generation_id=GENERATION_ID,
                snapshot_id=SNAPSHOT_ID,
                active_media_count=1,
                record_counts={"scene": 1},
            ),
        )
        config = backend.create.call_args.kwargs["config"]
        self.assertEqual(config.enabled_modalities, ("scene",))
        self.assertEqual(config.frame_stride, 5)
        self.assertEqual(config.options_for("scene")["sample_fps"], 2.0)
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

    def test_search_reuses_the_registered_capability_operation(self):
        calls = []

        def handler(context, request):
            calls.append((context, request))
            return SearchResult(
                query_id="indexed:1",
                query=request.query,
                modality="indexed",
            )

        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application = self.indexed_application(handler, manager)

        result = application.search(
            SearchCommand(
                modalities=("indexed",),
                query="yellow taxi",
                top_k=7,
            )
        )

        self.assertIsInstance(result, FusedSearchResult)
        self.assertEqual(result.modalities, ("indexed",))
        self.assertEqual(calls[0][1].query, "yellow taxi")
        self.assertEqual(calls[0][1].top_k, 7)
        self.assertIs(
            calls[0][0].storage,
            manager.__enter__.return_value,
        )

    def test_initial_evidence_attaches_board_to_completed_search(self):
        def handler(_context, request):
            return SearchResult(
                query_id="indexed:1",
                query=request.query,
                modality="indexed",
                hits=(
                    SearchHit(
                        rank=1,
                        media_id=MEDIA_ID,
                        video_id=MEDIA_ID,
                        generation_id=GENERATION_ID,
                        start=1.0,
                        end=2.0,
                        score=0.9,
                        raw_distance=0.1,
                        modality="indexed",
                        source_id="indexed:1",
                    ),
                ),
            )

        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application = self.indexed_application(handler, manager)
        board = EvidenceBoardResult(
            source_job_id=SNAPSHOT_ID,
            source_fingerprint="b" * 64,
            requested_count=1,
            rendered_count=1,
            failed_count=0,
            pages=(),
            tiles=(),
        )
        application.evidence_boards = Mock()
        application.evidence_boards.create.return_value = board

        result = application.search(
            SearchCommand(
                modalities=("indexed",),
                query="yellow taxi",
                evidence_delivery=InitialEvidenceDeliveryPolicy(
                    mode=EvidenceDeliveryMode.none,
                ),
            ),
            execution=ExecutionContext(job_id=SNAPSHOT_ID),
        )

        self.assertEqual(result.evidence_delivery.board, board)
        request = application.evidence_boards.create.call_args.args[0]
        self.assertEqual(request.source_job_id, SNAPSHOT_ID)
        self.assertEqual(len(request.candidates), 1)

    def test_default_search_filters_indexed_non_search_capabilities(self):
        searched: list[str] = []

        def search_plugin(name: str) -> CapabilityPlugin:
            definition = CapabilityDefinition(
                name=name,
                description=f"{name} search.",
                extra=name,
                collection_name=name,
                index_stage=name,
                execution_group=name,
                operations={
                    "search": OperationDefinition(
                        input_model=SearchInput,
                        output_model=SearchResult,
                    )
                },
            )

            def handler(_context, request):
                searched.append(name)
                return SearchResult(
                    query_id=f"{name}:query",
                    query=request.query,
                    modality=name,
                )

            return CapabilityPlugin(
                definition=definition,
                executor_factory=lambda: CapabilityExecutor(
                    indexer=Mock(),
                    operations={"search": handler},
                ),
            )

        actor = CapabilityPlugin(
            definition=CapabilityDefinition(
                name="actor",
                description="Actor clusters.",
                extra="actor",
                collection_name="actor",
                index_stage="actor",
                execution_group="actor",
                operations={
                    "clusters": OperationDefinition(
                        input_model=ActorClustersInput,
                        output_model=ActorClustersOutput,
                    )
                },
            ),
            executor_factory=lambda: CapabilityExecutor(
                indexer=Mock(),
                operations={
                    "clusters": Mock(
                        side_effect=AssertionError(
                            "Search must not execute actor clusters."
                        )
                    )
                },
            ),
        )
        registry = CapabilityRegistry(
            (
                search_plugin("scene"),
                search_plugin("speech"),
                actor,
            )
        )
        manager = MagicMock()
        manager.__enter__.return_value = Mock(spec=IndexStore)
        application, backend = self.application(
            "repository",
            registry=registry,
        )
        backend.active_config.return_value = IndexConfig.local(
            enabled_modalities=("scene", "speech", "actor"),
            collection_names={
                "scene": "scene",
                "speech": "speech",
                "actor": "actor",
            },
        )
        backend.open_store.return_value = manager

        result = application.search(SearchCommand(query="taxi"))

        self.assertEqual(searched, ["scene", "speech"])
        self.assertEqual(result.modalities, ("scene", "speech"))

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
        application.media.require_record.side_effect = (
            MediaUnavailableError("secret")
        )

        with self.assertRaises(ApplicationError) as raised:
            application.create_index(
                CreateIndexCommand(
                    media_id=MEDIA_ID,
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
            application.media.require_record.return_value = Mock(
                original_filename="video.mp4",
                sha256="1" * 64,
            )
            application.media.content.return_value = Mock(path=path)
            backend.create.side_effect = FileNotFoundError("ffmpeg")

            with self.assertRaises(DependencyUnavailableError) as raised:
                application.create_index(
                    CreateIndexCommand(
                        media_id=MEDIA_ID,
                        modalities=("scene",),
                    )
                )

        self.assertEqual(raised.exception.code, "dependency_unavailable")
        self.assertNotEqual(raised.exception.category, "not_found")

    def test_open_store_dependency_failure_is_stable(self):
        application, backend = self.application("unused")
        backend.active_config.return_value = IndexConfig.local(
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
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

    def test_missing_model_is_not_misclassified_as_a_package_dependency(self):
        application, backend = self.application("unused")
        application.media.require_record.return_value = Mock(
            original_filename="video.mp4",
            sha256="1" * 64,
        )
        application.media.content.return_value = Mock(
            path=Path("video.mp4")
        )
        backend.create.side_effect = ModelArtifactUnavailableError("scene")

        with self.assertRaises(ModelUnavailableError) as raised:
            application.create_index(
                CreateIndexCommand(
                    media_id=MEDIA_ID,
                    modalities=("scene",),
                )
            )

        self.assertEqual(raised.exception.code, "model_unavailable")
        self.assertNotIn(
            "pip install",
            json.dumps(raised.exception.to_dict()),
        )

    def test_model_download_failure_preserves_retry_details(self):
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
        failure = ModelArtifactDownloadError(
            "prepare-only.embedding",
            "publisher/model",
            attempts=3,
            reason="ConnectionError",
            resumable=True,
            retryable=True,
        )
        plugin = CapabilityPlugin(
            definition=definition,
            executor_factory=lambda: CapabilityExecutor(
                operations={"noop": Mock()},
                prepare=Mock(side_effect=failure),
            ),
        )
        registry = CapabilityRegistry((plugin,))
        registry.dependency_checks = Mock(return_value=())
        application, _ = self.application("unused", registry=registry)

        with self.assertRaises(ModelDownloadError) as raised:
            application.prepare_models(
                PrepareModelsCommand(modalities=("prepare-only",))
            )

        payload = raised.exception.to_dict()
        self.assertEqual(payload["code"], "model_download_failed")
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["details"]["attempts"], 3)
        self.assertTrue(payload["details"]["partial_files_preserved"])
        self.assertEqual(
            payload["details"]["remediation"],
            "vidxp prepare --modalities prepare-only",
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

    @patch("vidxp.application.which", return_value=None)
    def test_dependency_check_reports_missing_media_executables(self, _which):
        registry = create_capability_registry()
        registry.dependency_checks = Mock(return_value=())
        application, _ = self.application("unused", registry=registry)

        result = application.check_dependencies(
            DependencyCheckCommand(modalities=("scene",))
        )

        self.assertFalse(result.ok)
        self.assertEqual(
            [(check.capability, check.name) for check in result.checks],
            [("media", "ffmpeg"), ("media", "ffprobe")],
        )
        self.assertTrue(
            all(
                "does not install OS packages" in (check.error or "")
                for check in result.checks
            )
        )

    def test_runtime_readiness_includes_dependency_failures(self):
        application, _ = self.application("unused")
        application.control_plane_readiness = Mock(
            return_value=(
                ComponentReadiness(
                    name="catalog",
                    ready=True,
                    message="available",
                ),
            )
        )
        application._index_storage_readiness = Mock(
            return_value=ComponentReadiness(
                name="index_storage",
                ready=True,
                message="available",
            )
        )
        application.check_dependencies = Mock(
            return_value=DependencyCheckResult(
                ok=False,
                modalities=("scene",),
                checks=(),
            )
        )

        readiness = application.runtime_readiness()

        self.assertFalse(readiness.ready)
        self.assertFalse(readiness.dependencies.ok)

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

    def test_remove_delegates_to_index_backend(self):
        application, backend = self.application("repository")
        backend.remove.return_value = True

        self.assertTrue(
            application.remove_from_index(
                RemoveIndexCommand(media_id=MEDIA_ID)
            )
        )

        config, media_id = backend.remove.call_args.args
        self.assertEqual(
            Path(config.storage_directory),
            Path("repository") / "indexes",
        )
        self.assertEqual(media_id, MEDIA_ID)

    def test_generation_storage_validation_checks_identity_and_count(self):
        config = IndexConfig.local(
            video_id="episode-1",
            generation_id="generation-1",
            enabled_modalities=("scene",),
            collection_names={"scene": "scene"},
        )
        storage = Mock()
        storage.records.return_value = [
            {
                "generation_id": "generation-1",
                "video_id": "episode-1",
                "modality": "scene",
            }
        ]
        LocalIndexBackend._validate_generation_records(
            storage,
            config,
            {"scene": 1},
        )

        with self.assertRaisesRegex(
            IndexSchemaError,
            "record count",
        ):
            LocalIndexBackend._validate_generation_records(
                storage,
                config,
                {"scene": 2},
            )

    def test_local_backend_injects_storage_for_generation_build(self):
        with TemporaryDirectory() as directory:
            settings = VidXPSettings(
                repository_root=directory,
                runtime_backend="cpu",
            )
            registry = create_capability_registry()
            backend = LocalIndexBackend(
                registry,
                ModelRuntime(settings),
                settings.layout,
            )
            cleanup_storage = MagicMock()
            storage = MagicMock()
            storage.__enter__.return_value = storage
            config = IndexConfig.local(
                video_id=MEDIA_ID,
                enabled_modalities=("scene",),
                collection_names={"scene": "scene"},
                storage_directory=settings.layout.indexes,
            )
            snapshot = Mock(
                snapshot_id="a" * 32,
                generations={"media": Mock()},
            )
            with (
                patch(
                    "vidxp.infrastructure.local_index.IndexStorage",
                    side_effect=(cleanup_storage, storage),
                ),
                patch(
                    "vidxp.infrastructure.local_index.index_video",
                    return_value={"scene_frames": 1},
                ) as index_video,
                patch(
                    "vidxp.infrastructure.local_index.LocalSnapshotRepository."
                    "generation_reference",
                    return_value=Mock(),
                ),
                patch(
                    "vidxp.infrastructure.local_index.LocalSnapshotRepository."
                    "publish_generation",
                    return_value=snapshot,
                ),
                patch.object(
                    LocalIndexBackend,
                    "_validate_generation_records",
                    return_value={"scene": 1},
                ),
                patch(
                    "vidxp.infrastructure.local_index.ManifestStore."
                    "record_storage_counts",
                ),
            ):
                result = backend.create(
                    Path("video.mp4"),
                    config=config,
                    progress=None,
                    cancellation=None,
                    source_name=None,
                    source_checksum="1" * 64,
                )

            self.assertEqual(result["record_counts"], {"scene": 1})
            self.assertEqual(result["media_id"], MEDIA_ID)
            self.assertEqual(result["snapshot_id"], "a" * 32)
            self.assertIs(index_video.call_args.kwargs["storage"], storage)
            self.assertIs(
                index_video.call_args.kwargs["manifest_store"].runtime,
                backend.runtime,
            )
            self.assertIsNotNone(
                index_video.call_args.kwargs["config"].generation_id
            )
            cleanup_storage.__exit__.assert_called_once()
            storage.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
