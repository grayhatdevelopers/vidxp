import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from vidxp import frontend
from vidxp.application_models import (
    ApplicationError,
    DependencyCheckResult,
    CreateActorOverlayCommand,
    ErrorCategory,
    IndexStatus,
    JobState,
    SearchHit,
    SearchResult,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
from vidxp.settings import LocalExecutionSettings, VidXPSettings


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"


class UploadedVideo:
    name = "video.mp4"

    def __init__(self, content: bytes):
        self.content = content

    def getvalue(self):
        return self.content


class FrontendTests(unittest.TestCase):
    def test_durable_job_ids_restore_from_query_parameters(self):
        session_state = {}
        query_params = {
            frontend.INDEX_JOB_QUERY_PARAM: "index-job",
            frontend.SEARCH_JOB_QUERY_PARAM: "search-job",
            frontend.SEARCH_TYPE_QUERY_PARAM: "scene",
        }
        with (
            patch.object(frontend.st, "session_state", session_state),
            patch.object(frontend.st, "query_params", query_params),
        ):
            frontend._restore_durable_jobs()

        self.assertEqual(
            session_state[frontend.INDEX_JOB_ID_KEY],
            "index-job",
        )
        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "scene",
                "query": "",
                "job_id": "search-job",
            },
        )

    def test_query_modalities_derive_from_capability_operations(self):
        service = Mock()
        service.list_capabilities.return_value = (
            SimpleNamespace(
                name="visual-plugin",
                operations=(SimpleNamespace(name="search"),),
            ),
            SimpleNamespace(
                name="people-plugin",
                operations=(
                    SimpleNamespace(name="clusters"),
                    SimpleNamespace(name="detections"),
                ),
            ),
            SimpleNamespace(name="index-only", operations=()),
        )
        with patch.object(
            frontend,
            "_configured_service",
            return_value=service,
        ):
            available = frontend._available_query_modalities(
                ("visual-plugin", "people-plugin", "index-only"),
            )

        self.assertEqual(
            available,
            ("visual-plugin", "people-plugin"),
        )

    def tearDown(self):
        frontend._configured_service.cache_clear()
        frontend._configured_jobs.cache_clear()

    def service(self, root: Path) -> Mock:
        service = Mock()
        service.layout.media = root / "media"
        service.layout.artifacts = root / "artifacts"
        service.layout.root = root
        service.registry = create_capability_registry()
        service.list_capabilities.return_value = CapabilityService(
            service.registry
        ).list()
        return service

    def test_service_is_composed_lazily_and_cached(self):
        application = Mock()
        with patch.object(
            frontend,
            "create_application",
            return_value=application,
        ) as create:
            expected = VidXPSettings(
                repository_root=Path("repository"),
                runtime_backend="cpu",
                model_cache=Path("model-cache"),
                allow_model_downloads=False,
                max_loaded_models=5,
                max_concurrent_indexing=2,
                max_concurrent_inference=3,
                cpu_thread_budget=6,
                minimum_available_memory_mb=2048,
                external_capabilities=True,
                capability_allowlist=("acme:ocr",),
            )
            settings = frontend._settings_from_arguments(
                (
                    "--vidxp-settings-json",
                    LocalExecutionSettings.from_settings(
                        expected
                    ).model_dump_json(),
                )
            )
            first = frontend._configured_service(settings)
            second = frontend._configured_service(settings)

        self.assertIs(first, application)
        self.assertIs(second, application)
        create.assert_called_once()
        self.assertEqual(settings, expected)

    def test_media_identity_controls_search_readiness(self):
        with TemporaryDirectory() as directory:
            service = self.service(Path(directory))
            with patch.object(
                frontend,
                "_configured_service",
                return_value=service,
            ):
                matching = {
                    "state": "ready",
                    "summary": {"media_ids": [MEDIA_ID]},
                }
                stale = {
                    "state": "ready",
                    "summary": {"media_ids": []},
                }
                self.assertTrue(frontend._is_search_ready(matching, MEDIA_ID))
                self.assertFalse(frontend._is_search_ready(stale, MEDIA_ID))

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
        self.assertTrue(
            all(
                not call.args[0].include_runtime_checks
                for call in service.check_dependencies.call_args_list
            )
        )

    def test_search_queues_shared_command_without_loading_models(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.service(root)
            service.index_status.return_value = IndexStatus(
                schema_version=1,
                state="ready",
                stage="complete",
                message="ready",
            )
            jobs = Mock()
            jobs.submit_search.return_value = Mock(job_id="job-1")
            with (
                patch.object(
                    frontend,
                    "_configured_service",
                    return_value=service,
                ),
                patch.object(
                    frontend,
                    "_configured_jobs",
                    return_value=jobs,
                ),
            ):
                result = frontend._run_search("scene", "taxi")

        self.assertEqual(result["job_id"], "job-1")
        command = jobs.submit_search.call_args.args[0]
        self.assertEqual(command.modality, "scene")
        self.assertEqual(command.query, "taxi")
        service.search.assert_not_called()

    def test_actor_search_queues_without_blocking_ui(self):
        with TemporaryDirectory() as directory:
            service = self.service(Path(directory))
            service.index_status.return_value = IndexStatus(
                schema_version=1,
                state="ready",
                stage="complete",
                message="ready",
            )
            jobs = Mock()
            jobs.submit_actor_overlay.return_value = Mock(job_id="job-1")
            with (
                patch.object(
                    frontend,
                    "_configured_service",
                    return_value=service,
                ),
                patch.object(
                    frontend,
                    "_configured_jobs",
                    return_value=jobs,
                ),
            ):
                result = frontend._run_search("actor", "actor-1")

        self.assertEqual(
            result,
            {
                "type": "actor",
                "query": "actor-1",
                "job_id": "job-1",
            },
        )
        jobs.submit_actor_overlay.assert_called_once_with(
            CreateActorOverlayCommand(cluster_id="actor-1")
        )
        jobs.wait.assert_not_called()

    @staticmethod
    def _render_job(job, result=None):
        jobs = Mock()
        jobs.get.return_value = job
        session_state = {}
        query_params = {
            frontend.SEARCH_JOB_QUERY_PARAM: "job-1",
            frontend.SEARCH_TYPE_QUERY_PARAM: "actor",
        }

        def fragment(*, run_every):
            assert run_every == "1s"
            return lambda function: function

        with (
            patch.object(
                frontend,
                "_configured_jobs",
                return_value=jobs,
            ),
            patch.object(frontend.st, "fragment", side_effect=fragment),
            patch.object(frontend.st, "session_state", session_state),
            patch.object(frontend.st, "query_params", query_params),
            patch.object(frontend.st, "rerun") as rerun,
        ):
            frontend._render_search_result(
                result or {
                    "type": "actor",
                    "query": "actor-1",
                    "job_id": "job-1",
                }
            )
        return session_state, rerun

    def test_terminal_index_failure_survives_browser_refresh(self):
        session_state = {
            frontend.INDEX_JOB_ID_KEY: "job-1",
        }
        query_params = {
            frontend.INDEX_JOB_QUERY_PARAM: "job-1",
        }
        error = Mock(message="Indexing failed.")
        job = Mock(state=JobState.failed, error=error)

        with (
            patch.object(frontend.st, "session_state", session_state),
            patch.object(frontend.st, "query_params", query_params),
        ):
            frontend._finish_index_job(job)

        self.assertNotIn(frontend.INDEX_JOB_ID_KEY, session_state)
        self.assertNotIn(frontend.INDEX_JOB_QUERY_PARAM, query_params)
        self.assertEqual(
            session_state[frontend.INDEX_ERROR_KEY],
            "Indexing failed.",
        )

    def test_actor_poll_persists_success_before_stopping(self):
        job = Mock(state=JobState.succeeded, error=None)
        job.result.result.artifact_id = "artifact-1"

        session_state, rerun = self._render_job(job)

        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "actor",
                "query": "actor-1",
                "artifact_id": "artifact-1",
            },
        )
        rerun.assert_called_once_with()

    def test_actor_poll_persists_unavailable_job_error(self):
        session_state, rerun = self._render_job(None)

        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "actor",
                "query": "actor-1",
                "error": "The actor overlay job is unavailable.",
            },
        )
        rerun.assert_called_once_with()

    def test_search_poll_keeps_durable_job_on_retryable_backend_failure(self):
        jobs = Mock()
        jobs.get.side_effect = ApplicationError(
            "job_backend_unavailable",
            ErrorCategory.unavailable,
            "The durable job backend is unavailable.",
            retryable=True,
        )
        result = {
            "type": "scene",
            "query": "taxi",
            "job_id": "job-1",
        }
        session_state = {frontend.SEARCH_RESULT_KEY: result}

        def fragment(*, run_every):
            self.assertEqual(run_every, "1s")
            return lambda function: function

        with (
            patch.object(
                frontend,
                "_configured_jobs",
                return_value=jobs,
            ),
            patch.object(frontend.st, "fragment", side_effect=fragment),
            patch.object(frontend.st, "session_state", session_state),
            patch.object(frontend.st, "warning") as warning,
            patch.object(frontend.st, "rerun") as rerun,
        ):
            frontend._render_search_result(result)

        self.assertEqual(session_state[frontend.SEARCH_RESULT_KEY], result)
        warning.assert_called_once_with(
            "The scene search status is temporarily unavailable. Retrying."
        )
        rerun.assert_not_called()

    def test_job_lookup_only_suppresses_not_found(self):
        jobs = Mock()
        jobs.get.side_effect = ApplicationError(
            "resource_not_found",
            ErrorCategory.not_found,
            "The requested job was not found.",
        )
        self.assertIsNone(frontend._get_job(jobs, "job-1"))

        outage = ApplicationError(
            "job_backend_unavailable",
            ErrorCategory.unavailable,
            "The durable job backend is unavailable.",
            retryable=True,
        )
        jobs.get.side_effect = outage
        with self.assertRaises(ApplicationError) as raised:
            frontend._get_job(jobs, "job-1")
        self.assertIs(raised.exception, outage)

    def test_actor_poll_persists_failed_job_error(self):
        error = Mock(message="Overlay rendering failed.")
        job = Mock(state=JobState.failed, error=error, result=None)

        session_state, rerun = self._render_job(job)

        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "actor",
                "query": "actor-1",
                "error": "Overlay rendering failed.",
            },
        )
        rerun.assert_called_once_with()

    def test_actor_poll_persists_cancelled_job_error(self):
        job = Mock(state=JobState.cancelled, error=None, result=None)

        session_state, rerun = self._render_job(job)

        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "actor",
                "query": "actor-1",
                "error": "The actor overlay was cancelled.",
            },
        )
        rerun.assert_called_once_with()

    def test_search_poll_persists_the_best_typed_hit(self):
        completed = SearchResult(
            query_id="scene:taxi",
            query="taxi",
            modality="scene",
            hits=(
                SearchHit(
                    rank=1,
                    media_id=MEDIA_ID,
                    video_id=MEDIA_ID,
                    generation_id=GENERATION_ID,
                    start=12.5,
                    end=13.0,
                    score=-0.1,
                    raw_distance=0.1,
                    modality="scene",
                    source_id="scene:1",
                ),
            ),
        )
        job = Mock(state=JobState.succeeded, error=None)
        job.result.result = completed

        session_state, rerun = self._render_job(
            job,
            {
                "type": "scene",
                "query": "taxi",
                "job_id": "job-1",
            },
        )

        persisted = session_state[frontend.SEARCH_RESULT_KEY]
        self.assertEqual(persisted["media_id"], MEDIA_ID)
        self.assertEqual(persisted["timestamp"], 12.5)
        rerun.assert_called_once_with()

    def test_restored_search_uses_completed_query_when_no_hit_exists(self):
        completed = SearchResult(
            query_id="scene:taxi",
            query="yellow taxi",
            modality="scene",
        )
        job = Mock(state=JobState.succeeded, error=None)
        job.result.result = completed

        session_state, rerun = self._render_job(
            job,
            {
                "type": "scene",
                "query": "",
                "job_id": "job-1",
            },
        )

        self.assertEqual(
            session_state[frontend.SEARCH_RESULT_KEY],
            {
                "type": "scene",
                "query": "yellow taxi",
                "error": "No scene match was found.",
            },
        )
        rerun.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
