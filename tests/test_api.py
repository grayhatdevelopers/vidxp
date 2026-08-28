import asyncio
import gc
import unittest
import warnings
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from vidxp.api import create_app
from vidxp.api_routes.dependencies import scoped_job_id
from vidxp.application_models import (
    ApplicationError,
    Artifact,
    ErrorCategory,
    ErrorDetail,
    EvidenceArtifact,
    EvidenceDeliveryItem,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryResult,
    EvidenceDeliveryState,
    EvidenceFrameMatch,
    EvidenceKeyframe,
    FusedSearchResult,
    FusionProvenance,
    ComponentReadiness,
    Job,
    JobKind,
    JobQueue,
    JobState,
    JobWaitResult,
    IndexStatus,
    MediaAsset,
    MediaPage,
    Principal,
    SearchCommand,
    SearchJobResult,
    QueryVideoCommand,
    UploadIntent,
    WorkspaceOverview,
)
from vidxp.composition import (
    HttpApplicationContext,
    create_http_application,
)
from vidxp.control_plane import ControlPlaneApplication
from vidxp.authentication import create_authenticator
from vidxp.authorization import AuthorizationPolicy
from vidxp.core.media import MediaState, MediaStream
from vidxp.core.artifacts import ArtifactKind, ArtifactState
from vidxp.core.uploads import UploadState
from vidxp.job_service import JobService
from vidxp.ports import LocalFileResource
from vidxp.readiness_service import ReadinessService
from vidxp.settings import HttpAuthMode, VidXPSettings
from vidxp.upload_service import RemoteUploadService


MEDIA_ID = "123456781234423481234567890abcde"
JOB_ID = "223456781234423481234567890abcde"
IDEMPOTENCY_KEY = "323456781234423481234567890abcde"
ARTIFACT_ID = "423456781234423481234567890abcde"
TOKEN = "a" * 32


def media_asset() -> MediaAsset:
    return MediaAsset(
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        original_filename="video.mp4",
        sha256="1" * 64,
        byte_size=10,
        detected_mime_type="video/mp4",
        container="mp4",
        duration_seconds=2,
        streams=(
            MediaStream(
                index=0,
                kind="video",
                codec="h264",
                width=1,
                height=1,
            ),
        ),
        state=MediaState.ready,
        created_at=datetime.now(timezone.utc),
    )


def queued_job() -> Job:
    return Job(
        job_id=JOB_ID,
        kind=JobKind.index,
        state=JobState.queued,
        queue=JobQueue.cpu,
    )


def evidence_job() -> Job:
    artifact = Artifact(
        artifact_id=ARTIFACT_ID,
        media_id=MEDIA_ID,
        generation_id="523456781234423481234567890abcde",
        job_id=JOB_ID,
        kind=ArtifactKind.evidence_frame,
        profile="png",
        mime_type="image/png",
        byte_size=8,
        sha256="1" * 64,
        state=ArtifactState.ready,
        created_at=datetime.now(timezone.utc),
    )
    return Job(
        job_id=JOB_ID,
        kind=JobKind.search,
        state=JobState.succeeded,
        queue=JobQueue.cpu,
        result=SearchJobResult(
            result=FusedSearchResult(
                query_id="http-evidence",
                query="frame",
                modalities=("scene",),
                fusion=FusionProvenance(
                    requested_modalities=("scene",),
                    searched_modalities=("scene",),
                ),
                evidence_delivery=EvidenceDeliveryResult(
                    policy=EvidenceDeliveryPolicy(
                        mode=EvidenceDeliveryMode.keyframes,
                        max_items=1,
                    ),
                    items=(
                        EvidenceDeliveryItem(
                            evidence_id="e" * 64,
                            rank=1,
                            media_id=MEDIA_ID,
                            generation_id=artifact.generation_id or "",
                            modalities=("scene",),
                            state=EvidenceDeliveryState.ready,
                            keyframe=EvidenceKeyframe(
                                match=EvidenceFrameMatch.representative,
                                timestamp_seconds=1,
                                width=1,
                                height=1,
                                artifact=EvidenceArtifact(artifact=artifact),
                            ),
                        ),
                    ),
                ),
            )
        ),
    )


class ApiTests(unittest.TestCase):
    def context(
        self,
        root: Path,
        *,
        auth: HttpAuthMode = HttpAuthMode.none,
        upload_limit: int = 256 * 1024 * 1024,
        json_limit: int = 4 * 1024 * 1024,
        mcp_limit: int = 4 * 1024 * 1024,
        allowed_origins: tuple[str, ...] = (),
        remote_uploads: bool = False,
    ) -> HttpApplicationContext:
        settings = VidXPSettings(
            repository_root=root,
            runtime_backend="cpu",
            http_auth_mode=auth,
            http_static_bearer_token=TOKEN if auth == HttpAuthMode.static else None,
            http_oidc_issuer=(
                "https://issuer.example"
                if auth == HttpAuthMode.oidc
                else None
            ),
            http_oidc_audience=(
                "https://api.example"
                if auth == HttpAuthMode.oidc
                else None
            ),
            http_oidc_jwks_url=(
                "https://issuer.example/jwks"
                if auth == HttpAuthMode.oidc
                else None
            ),
            http_required_scopes=(
                ("vidxp.read",)
                if auth == HttpAuthMode.oidc
                else ()
            ),
            mcp_public_url=(
                "https://api.example/mcp"
                if auth == HttpAuthMode.oidc
                else None
            ),
            http_max_small_upload_bytes=upload_limit,
            http_max_json_body_bytes=json_limit,
            mcp_max_request_body_bytes=mcp_limit,
            http_allowed_origins=allowed_origins,
            upload_public_endpoint=(
                "http://localhost:8080/uploads/"
                if remote_uploads
                else None
            ),
            upload_internal_endpoint=(
                "http://localhost:8080/uploads/"
                if remote_uploads
                else None
            ),
            upload_cleanup_token="c" * 32 if remote_uploads else None,
        )
        application = Mock()
        jobs = Mock(spec=JobService)
        readiness = Mock()
        readiness.ready.return_value = True
        uploads = Mock(spec=RemoteUploadService) if remote_uploads else None
        return HttpApplicationContext(
            application=application,
            jobs=jobs,
            readiness=readiness,
            authenticator=create_authenticator(settings),
            authorization=AuthorizationPolicy(),
            settings=settings,
            uploads=uploads,
        )

    @staticmethod
    def auth() -> dict[str, str]:
        return {"Authorization": f"Bearer {TOKEN}"}

    def test_local_http_context_stops_worker_when_closed(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))

            context.close()

        context.jobs.stop_worker.assert_called_once_with()
        context.jobs.close.assert_called_once_with()

    def test_health_and_minimal_readiness_are_public(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
            )
            with TestClient(create_app(context=context)) as client:
                health = client.get("/health")
                favicon = client.get("/favicon.ico")
                ready = client.get("/ready")

        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})
        self.assertEqual(favicon.status_code, 204)
        self.assertEqual(favicon.content, b"")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json(), {"ready": True, "status": "ready"})

    def test_resumable_upload_api_uses_typed_intents_and_private_urls(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
                remote_uploads=True,
            )
            assert context.uploads is not None
            now = datetime.now(timezone.utc)
            intent = UploadIntent(
                intent_id="123456781234423481234567890abcde",
                original_filename="video.mp4",
                byte_size=20,
                declared_mime_type="video/mp4",
                state=UploadState.pending,
                created_at=now,
                expires_at=now.replace(year=now.year + 1),
            )
            context.uploads.create_intent.return_value = intent
            context.uploads.upload_url.return_value = None
            with TestClient(create_app(context=context)) as client:
                created = client.post(
                    "/api/v1/media/uploads",
                    headers={
                        **self.auth(),
                        "Idempotency-Key": IDEMPOTENCY_KEY,
                    },
                    json={
                        "original_filename": "video.mp4",
                        "byte_size": 20,
                        "declared_mime_type": "video/mp4",
                    },
                )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.headers["cache-control"], "private, no-store")
        self.assertEqual(created.headers["referrer-policy"], "no-referrer")
        self.assertEqual(
            created.headers["location"],
            f"/api/v1/media/uploads/{intent.intent_id}",
        )
        payload = created.json()
        self.assertEqual(payload["intent"]["state"], "pending")
        self.assertEqual(
            payload["creation_url"],
            "http://localhost:8080/uploads/",
        )
        self.assertIsNone(payload["resume_url"])
        self.assertTrue(payload["upload_metadata"].startswith("intent_id "))

    def test_readiness_failure_returns_503_without_details(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.readiness.ready.return_value = False
            with TestClient(create_app(context=context)) as client:
                response = client.get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"ready": False, "status": "not_ready"},
        )

    def test_static_bearer_is_enforced_before_dispatch(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
            )
            context.application.list_capabilities.return_value = ()
            with TestClient(create_app(context=context)) as client:
                missing = client.get("/api/v1/capabilities")
                accepted = client.get(
                    "/api/v1/capabilities",
                    headers=self.auth(),
                )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(
            missing.json()["error"]["code"],
            "authentication_required",
        )
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), {"items": []})
        context.application.list_capabilities.assert_called_once_with()

    def test_workspace_endpoint_returns_actionable_repository_state(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.workspace.return_value = WorkspaceOverview(
                media_total=0,
                index=IndexStatus(
                    schema_version=2,
                    state="missing",
                    stage="status",
                    message="No index.",
                ),
                next_actions=("register_media",),
            )
            with TestClient(create_app(context=context)) as client:
                response = client.get("/api/v1/workspace?page_size=25")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["media_total"], 0)
        self.assertEqual(response.json()["next_actions"], ["register_media"])
        command = context.application.workspace.call_args.args[0]
        self.assertEqual(command.page_size, 25)

    def test_list_media_passes_filters_to_application(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.list_media.return_value = MediaPage(
                items=(),
                total=0,
            )
            with TestClient(create_app(context=context)) as client:
                response = client.get(
                    "/api/v1/media",
                    params={
                        "page_size": 10,
                        "filename": "clip.mp4",
                        "state": "ready",
                    },
                )

        self.assertEqual(response.status_code, 200)
        command = context.application.list_media.call_args.args[0]
        self.assertEqual(command.page_size, 10)
        self.assertEqual(command.filename, "clip.mp4")
        self.assertEqual(command.state, MediaState.ready)

    def test_workspace_passes_filters_to_application(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.workspace.return_value = WorkspaceOverview(
                media_total=0,
                index=IndexStatus(
                    schema_version=2,
                    state="missing",
                    stage="status",
                    message="No index.",
                ),
                next_actions=("register_media",),
            )
            with TestClient(create_app(context=context)) as client:
                response = client.get(
                    "/api/v1/workspace",
                    params={
                        "filename": "batch",
                        "state": "pending",
                    },
                )

        self.assertEqual(response.status_code, 200)
        command = context.application.workspace.call_args.args[0]
        self.assertEqual(command.filename, "batch")
        self.assertEqual(command.state, MediaState.pending)

    def test_repository_scopes_are_enforced_per_operation(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
            )
            authenticator = Mock()
            authenticator.authenticate.return_value = Principal(
                subject="reader",
                scopes=frozenset({"vidxp.read"}),
            )
            authenticator.readiness.return_value = ComponentReadiness(
                name="authentication",
                ready=True,
                message="Authentication is ready.",
            )
            context = replace(context, authenticator=authenticator)
            context.application.list_capabilities.return_value = ()
            with TestClient(create_app(context=context)) as client:
                readable = client.get(
                    "/api/v1/capabilities",
                    headers=self.auth(),
                )
                forbidden = client.post(
                    "/api/v1/jobs/index",
                    headers={
                        **self.auth(),
                        "Idempotency-Key": IDEMPOTENCY_KEY,
                    },
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                        "frame_stride": 1,
                        "capability_options": {},
                    },
                )

        self.assertEqual(readable.status_code, 200)
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(
            forbidden.json()["error"]["code"],
            "insufficient_scope",
        )
        context.jobs.submit_index.assert_not_called()

    def test_validation_errors_use_safe_shared_envelope(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            with TestClient(create_app(context=context)) as client:
                response = client.get("/api/v1/media/not-an-id")

        self.assertEqual(response.status_code, 422)
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid_request")
        self.assertEqual(
            error["details"]["errors"][0]["location"],
            ["path", "media_id"],
        )
        self.assertTrue(error["correlation_id"])

    def test_unexpected_errors_are_logged_and_masked(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.get_media.side_effect = RuntimeError(
                "secret-path"
            )
            with TestClient(
                create_app(context=context),
                raise_server_exceptions=False,
            ) as client:
                response = client.get(f"/api/v1/media/{MEDIA_ID}")

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "internal_error")
        self.assertNotIn("secret-path", response.text)

    def test_job_submission_is_thin_idempotent_delegation(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_index.return_value = queued_job()
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                        "frame_stride": 1,
                        "scene_sample_fps": 0.5,
                        "capability_options": {},
                    },
                )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers["location"], f"/api/v1/jobs/{JOB_ID}")
        command = context.jobs.submit_index.call_args.args[0]
        self.assertEqual(command.media_id, MEDIA_ID)
        self.assertEqual(command.modalities, ("scene",))
        self.assertEqual(command.scene_sample_fps, 0.5)
        expected_job_id = scoped_job_id(
            context,
            context.authenticator.authenticate(None),
            operation="index",
            idempotency_key=IDEMPOTENCY_KEY,
        )
        self.assertEqual(
            context.jobs.submit_index.call_args.kwargs["job_id"],
            expected_job_id,
        )
        self.assertNotEqual(expected_job_id, IDEMPOTENCY_KEY)
        self.assertNotEqual(
            expected_job_id,
            scoped_job_id(
                context,
                Principal(subject="other", scopes=frozenset({"*"})),
                operation="index",
                idempotency_key=IDEMPOTENCY_KEY,
            ),
        )
        self.assertNotEqual(
            expected_job_id,
            scoped_job_id(
                context,
                context.authenticator.authenticate(None),
                operation="snippet",
                idempotency_key=IDEMPOTENCY_KEY,
            ),
        )

    def test_http_index_retry_routes_through_upload_relink_boundary(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory), remote_uploads=True)
            assert context.uploads is not None
            context.uploads.start_indexing.return_value = queued_job()
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                    },
                )

        self.assertEqual(response.status_code, 202)
        context.uploads.start_indexing.assert_called_once()
        context.jobs.submit_index.assert_not_called()

    def test_failed_model_preparation_job_is_structured_over_http(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.get.return_value = Job(
                job_id=JOB_ID,
                kind=JobKind.prepare_models,
                state=JobState.failed,
                queue=JobQueue.cpu,
                error=ErrorDetail(
                    code="model_download_failed",
                    category=ErrorCategory.unavailable,
                    message="The model download failed after three attempts.",
                    details={
                        "model": "publisher/model",
                        "partial_files_preserved": True,
                        "remediation": "vidxp prepare --modalities dialogue",
                    },
                    retryable=True,
                ),
            )
            with TestClient(create_app(context=context)) as client:
                response = client.get(f"/api/v1/jobs/{JOB_ID}")

        self.assertEqual(response.status_code, 200)
        error = response.json()["error"]
        self.assertEqual(error["code"], "model_download_failed")
        self.assertTrue(error["retryable"])
        self.assertTrue(error["details"]["partial_files_preserved"])

    def test_http_job_summary_and_bounded_wait_do_not_project_results(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            compact = JobService._summary(queued_job())
            context.jobs.summary.return_value = compact
            context.jobs.wait_for_change.return_value = JobWaitResult(
                job=compact,
                changed=False,
                timed_out=True,
            )
            with TestClient(create_app(context=context)) as client:
                summary = client.get(
                    f"/api/v1/jobs/{JOB_ID}/status",
                )
                waited = client.get(
                    f"/api/v1/jobs/{JOB_ID}/wait",
                    params={
                        "after_observation_token": compact.observation_token,
                        "timeout_seconds": 5,
                    },
                )

        self.assertEqual(summary.status_code, 200)
        self.assertNotIn("result", summary.json())
        self.assertNotIn("poll_after_seconds", summary.json())
        self.assertEqual(summary.json()["observation_token"], compact.observation_token)
        self.assertEqual(waited.status_code, 200)
        self.assertTrue(waited.json()["timed_out"])
        self.assertNotIn("poll_after_seconds", waited.json()["job"])
        context.jobs.summary.assert_called_once_with(JOB_ID)
        context.jobs.wait_for_change.assert_called_once_with(
            JOB_ID,
            after=compact.observation_token,
            timeout_seconds=5,
        )
        context.jobs.get.assert_not_called()

    def test_http_job_projects_durable_evidence_to_protected_artifact_route(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            durable = evidence_job()
            evidence = durable.result.result.evidence_delivery.items[0]
            self.assertIsNone(evidence.keyframe.artifact.resource_uri)
            context.jobs.get.return_value = durable
            context.jobs.result.return_value = durable.result
            with TestClient(create_app(context=context)) as client:
                response = client.get(f"/api/v1/jobs/{JOB_ID}")
                result_response = client.get(f"/api/v1/jobs/{JOB_ID}/result")

        expected = f"/api/v1/artifacts/{ARTIFACT_ID}/content"
        self.assertEqual(response.status_code, 200)
        projected = response.json()["result"]["result"]["evidence_delivery"]
        self.assertEqual(
            projected["items"][0]["keyframe"]["artifact"]["resource_uri"],
            expected,
        )
        result_projected = result_response.json()["result"]["evidence_delivery"]
        self.assertEqual(
            result_projected["items"][0]["keyframe"]["artifact"]["resource_uri"],
            expected,
        )
        self.assertNotIn("vidxp://", response.text)
        self.assertNotIn(str(Path(directory)), response.text)

    def test_missing_models_fail_before_job_submission(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_index.side_effect = ApplicationError(
                "model_unavailable",
                ErrorCategory.unavailable,
                "Run vidxp prepare --modalities scene.",
                details={
                    "capability": "scene",
                    "remediation": "vidxp prepare --modalities scene",
                },
            )
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                    },
                )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "model_unavailable")
        self.assertEqual(
            response.json()["error"]["details"]["remediation"],
            "vidxp prepare --modalities scene",
        )
        context.jobs.submit_index.assert_called_once()

    def test_job_submission_requires_an_idempotency_key(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                        "frame_stride": 1,
                        "capability_options": {},
                    },
                )

        self.assertEqual(response.status_code, 422)
        context.jobs.submit_index.assert_not_called()

    def test_search_submission_uses_the_durable_query_boundary(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_search.return_value = queued_job().model_copy(
                update={"kind": JobKind.search}
            )
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/search",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "modalities": ["scene"],
                        "query": "yellow taxi",
                        "top_k": 3,
                    },
                )

        self.assertEqual(response.status_code, 202)
        context.jobs.submit_search.assert_called_once()
        command = context.jobs.submit_search.call_args.args[0]
        self.assertEqual(
            command,
            SearchCommand(
                modalities=("scene",),
                query="yellow taxi",
                top_k=3,
            ),
        )

    def test_grounded_query_submission_uses_the_durable_boundary(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_query.return_value = queued_job().model_copy(
                update={"kind": JobKind.query}
            )
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/query",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "question": "What happens after the taxi arrives?",
                        "media_id": MEDIA_ID,
                        "modalities": ["scene", "dialogue"],
                        "top_k": 5,
                    },
                )

        self.assertEqual(response.status_code, 202)
        context.jobs.submit_query.assert_called_once()
        command = context.jobs.submit_query.call_args.args[0]
        self.assertEqual(
            command,
            QueryVideoCommand(
                question="What happens after the taxi arrives?",
                media_id=MEDIA_ID,
                modalities=("scene", "dialogue"),
                top_k=5,
            ),
        )

    def test_idempotency_payload_mismatch_returns_422(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.submit_index.side_effect = ApplicationError(
                "idempotency_key_reused",
                ErrorCategory.validation,
                "The idempotency key was used for another request.",
            )
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                        "frame_stride": 1,
                        "capability_options": {},
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"],
            "idempotency_key_reused",
        )

    def test_small_upload_stages_then_calls_application_ingestion(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory), upload_limit=32)
            captured = {}

            def ingest(**kwargs):
                staged = kwargs["staged_path"]
                captured["path"] = staged
                captured["bytes"] = staged.read_bytes()
                captured["filename"] = kwargs["original_filename"]
                captured["mime"] = kwargs["declared_mime_type"]
                captured["request_key"] = kwargs["request_key"]
                return media_asset()

            context.application.import_uploaded_media.side_effect = ingest
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/media",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    files={"upload": ("video.mp4", b"0123456789", "video/mp4")},
                )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["media_id"], MEDIA_ID)
        self.assertEqual(captured["bytes"], b"0123456789")
        self.assertEqual(captured["filename"], "video.mp4")
        self.assertEqual(captured["mime"], "video/mp4")
        self.assertEqual(len(captured["request_key"]), 64)
        self.assertFalse(captured["path"].exists())

    def test_small_upload_enforces_streamed_file_limit(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory), upload_limit=4)
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/media",
                    headers={"Idempotency-Key": IDEMPOTENCY_KEY},
                    files={"upload": ("video.mp4", b"12345", "video/mp4")},
                )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "media_too_large")
        context.application.import_uploaded_media.assert_not_called()

    def test_json_body_limit_rejects_before_job_dispatch(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory), json_limit=32)
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/api/v1/jobs/index",
                    json={
                        "media_id": MEDIA_ID,
                        "modalities": ["scene"],
                        "frame_stride": 1,
                        "capability_options": {},
                    },
                )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json()["error"]["code"],
            "request_body_too_large",
        )
        context.jobs.submit_index.assert_not_called()

    def test_streamed_body_limit_does_not_require_content_length(self):
        async def request() -> httpx.Response:
            with TemporaryDirectory() as directory:
                context = self.context(Path(directory), upload_limit=4)
                app = create_app(context=context)

                async def body():
                    yield (
                        b"--boundary\r\n"
                        b'Content-Disposition: form-data; name="upload"; '
                        b'filename="video.mp4"\r\n'
                        b"Content-Type: video/mp4\r\n\r\n"
                    )
                    yield b"x" * (1024 * 1024 + 32)
                    yield b"\r\n--boundary--\r\n"

                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    return await client.post(
                        "/api/v1/media",
                        headers={
                            "Content-Type": (
                                "multipart/form-data; boundary=boundary"
                            ),
                            "Idempotency-Key": IDEMPOTENCY_KEY,
                        },
                        content=body(),
                    )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            response = asyncio.run(request())
            gc.collect()

        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            response.json()["error"]["code"],
            "request_body_too_large",
        )
        self.assertFalse(
            [
                warning
                for warning in caught
                if issubclass(warning.category, ResourceWarning)
                and "Unclosed file" in str(warning.message)
            ]
        )

    def test_file_delivery_uses_starlette_range_and_strong_etag(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = root / "video.mp4"
            content.write_bytes(b"0123456789")
            context = self.context(root)
            context.application.open_media_content.return_value = (
                LocalFileResource(
                    path=content,
                    filename="video.mp4",
                    mime_type="video/mp4",
                    byte_size=10,
                    etag="1" * 64,
                )
            )
            context.application.open_artifact_content.return_value = (
                LocalFileResource(
                    path=content,
                    filename="snippet.mp4",
                    mime_type="video/mp4",
                    byte_size=10,
                    etag="1" * 64,
                )
            )
            with TestClient(create_app(context=context)) as client:
                ranged = client.get(
                    f"/api/v1/media/{MEDIA_ID}/content",
                    headers={"Range": "bytes=2-5"},
                )
                cached = client.get(
                    f"/api/v1/media/{MEDIA_ID}/content",
                    headers={"If-None-Match": f'"{"1" * 64}"'},
                )
                artifact = client.get(
                    f"/api/v1/artifacts/{ARTIFACT_ID}/content",
                    headers={"Range": "bytes=6-9"},
                )

        self.assertEqual(ranged.status_code, 206)
        self.assertEqual(ranged.content, b"2345")
        self.assertEqual(ranged.headers["content-range"], "bytes 2-5/10")
        self.assertEqual(ranged.headers["etag"], f'"{"1" * 64}"')
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.content, b"")
        self.assertEqual(artifact.status_code, 206)
        self.assertEqual(artifact.content, b"6789")
        self.assertIn(
            "snippet.mp4",
            artifact.headers["content-disposition"],
        )

    def test_openapi_is_curated_and_has_unique_operation_ids(self):
        with TemporaryDirectory() as directory:
            app = create_app(context=self.context(Path(directory)))
            schema = app.openapi()

        operations = [
            operation
            for path in schema["paths"].values()
            for method, operation in path.items()
            if method.lower() in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "head",
            }
        ]
        operation_ids = [operation["operationId"] for operation in operations]
        self.assertEqual(len(operation_ids), len(set(operation_ids)))
        self.assertNotIn("/api/v1/execute", schema["paths"])
        self.assertNotIn("/mcp", schema["paths"])
        upload_schema = schema["paths"]["/api/v1/media"]["post"]
        self.assertIn("multipart/form-data", upload_schema["requestBody"]["content"])
        self.assertNotIn('"format": "path"', str(upload_schema))
        clip_schema = schema["paths"]["/api/v1/jobs/snippet"]["post"]
        self.assertIn("downloadable", clip_schema["summary"].lower())
        artifact_schema = schema["paths"][
            "/api/v1/artifacts/{artifact_id}/content"
        ]["get"]
        self.assertIn("download", artifact_schema["summary"].lower())

    def test_openapi_declares_bearer_security_without_securing_health(self):
        with TemporaryDirectory() as directory:
            app = create_app(
                context=self.context(
                    Path(directory),
                    auth=HttpAuthMode.static,
                )
            )
            schema = app.openapi()

        bearer = schema["components"]["securitySchemes"]["BearerAuth"]
        self.assertEqual(bearer["type"], "http")
        self.assertEqual(bearer["scheme"], "bearer")
        self.assertEqual(
            schema["paths"]["/api/v1/capabilities"]["get"]["security"],
            [{"BearerAuth": []}],
        )
        self.assertNotIn("security", schema["paths"]["/health"]["get"])

    def test_cors_is_scoped_to_the_rest_namespace(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                allowed_origins=("https://client.example",),
            )
            headers = {
                "Origin": "https://client.example",
                "Access-Control-Request-Method": "GET",
            }
            with TestClient(create_app(context=context)) as client:
                api = client.options("/api/v1/media", headers=headers)
                mcp = client.options("/mcp", headers=headers)

        self.assertEqual(api.status_code, 200)
        self.assertEqual(
            api.headers["access-control-allow-origin"],
            "https://client.example",
        )
        self.assertNotIn("access-control-allow-origin", mcp.headers)

    def test_security_middleware_rejections_use_typed_envelopes(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                allowed_origins=("https://client.example",),
            )
            with TestClient(create_app(context=context)) as client:
                host = client.get(
                    "/health",
                    headers={"Host": "untrusted.example"},
                )
                ipv6_loopback = client.get(
                    "/health",
                    headers={"Host": "[::1]:8000"},
                )
                origin = client.options(
                    "/api/v1/media",
                    headers={
                        "Origin": "https://untrusted.example",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                method = client.options(
                    "/api/v1/media",
                    headers={
                        "Origin": "https://client.example",
                        "Access-Control-Request-Method": "TRACE",
                    },
                )

        self.assertEqual(host.status_code, 400)
        self.assertEqual(host.json()["error"]["code"], "host_not_allowed")
        self.assertEqual(ipv6_loopback.status_code, 200)
        self.assertEqual(origin.status_code, 403)
        self.assertEqual(
            origin.json()["error"]["code"],
            "cors_origin_forbidden",
        )
        self.assertEqual(method.status_code, 400)
        self.assertEqual(
            method.json()["error"]["code"],
            "cors_preflight_invalid",
        )

    def test_authenticated_profiles_protect_schema_and_hide_interactive_docs(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
            )
            with TestClient(create_app(context=context)) as client:
                unauthenticated = client.get("/docs")
                authenticated = client.get(
                    "/docs",
                    headers=self.auth(),
                )
                schema_without_token = client.get("/openapi.json")
                schema = client.get(
                    "/openapi.json",
                    headers=self.auth(),
                )

        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(authenticated.status_code, 404)
        self.assertEqual(schema_without_token.status_code, 401)
        self.assertEqual(schema.status_code, 200)
        self.assertIn("/api/v1/capabilities", schema.json()["paths"])

    def test_readiness_masks_component_probe_failures(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.control_plane_readiness.side_effect = OSError(
                "private-catalog-detail"
            )
            context.application.runtime_readiness.side_effect = OSError(
                "private-runtime-detail"
            )
            context.jobs.readiness.return_value = ComponentReadiness(
                name="workflow",
                ready=True,
                message="Workflow is ready.",
            )
            readiness = ReadinessService(
                application=context.application,
                jobs=context.jobs,
                authenticator=context.authenticator,
            )
            context = replace(context, readiness=readiness)
            with TestClient(create_app(context=context)) as client:
                minimal = client.get("/ready")
                details = client.get("/api/v1/runtime/readiness")

        self.assertEqual(minimal.status_code, 503)
        self.assertEqual(
            minimal.json(),
            {"ready": False, "status": "not_ready"},
        )
        self.assertEqual(details.status_code, 200)
        self.assertFalse(details.json()["ready"])
        self.assertNotIn("private-", details.text)

    def test_owned_context_is_closed_when_lifespan_exits(self):
        with TemporaryDirectory() as directory:
            owned = self.context(Path(directory))
            with patch(
                "vidxp.api.create_http_application",
                return_value=owned,
            ):
                with TestClient(create_app()) as client:
                    self.assertEqual(client.get("/health").status_code, 200)

        owned.jobs.close.assert_called_once_with()

    def test_local_worker_startup_failure_fails_api_startup(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.jobs.start.side_effect = RuntimeError(
                "transient worker failure"
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "transient worker failure",
            ):
                with TestClient(create_app(context=context)):
                    pass

        context.jobs.start.assert_called_once_with()

    def test_http_composition_does_not_construct_model_runtime(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            settings = VidXPSettings(
                data_dir=Path(directory) / "data",
                repository_root=Path(directory),
                runtime_backend="cpu",
            )
            with patch(
                "vidxp.composition.ModelRuntime",
                side_effect=AssertionError("model runtime constructed"),
            ) as runtime:
                context = create_http_application(settings)
            try:
                self.assertIsInstance(
                    context.application,
                    ControlPlaneApplication,
                )
                self.assertEqual(
                    context.settings.artifact_download_public_url,
                    "http://127.0.0.1:32191/artifact-download",
                )
                self.assertIsNotNone(context.settings.artifact_download_secret)
                runtime.assert_not_called()
            finally:
                context.close()

    def test_mcp_namespace_uses_static_auth_and_sdk_body_policy(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.static,
                mcp_limit=32,
            )
            with TestClient(create_app(context=context)) as client:
                unauthorized = client.post(
                    "/mcp",
                    headers={"Content-Type": "application/json"},
                    content=b"{}",
                )
                oversized = client.post(
                    "/mcp",
                    headers={
                        **self.auth(),
                        "Content-Type": "application/json",
                    },
                    content=b"x" * 64,
                )
                metadata = client.get(
                    "/.well-known/oauth-protected-resource/mcp"
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(
            unauthorized.json()["error"]["code"],
            "authentication_required",
        )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(metadata.status_code, 401)
        self.assertEqual(
            metadata.json()["error"]["code"],
            "authentication_required",
        )

    def test_oidc_mcp_metadata_is_public_and_sdk_auth_challenges(self):
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.oidc,
            )
            with TestClient(create_app(context=context)) as client:
                metadata = client.get(
                    "/.well-known/oauth-protected-resource/mcp"
                )
                challenged = client.post(
                    "/mcp",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2026-07-28",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "test",
                                "version": "1",
                            },
                        },
                    },
                )

        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(
            metadata.json()["resource"],
            "https://api.example/mcp",
        )
        self.assertEqual(
            metadata.json()["authorization_servers"],
            ["https://issuer.example"],
        )
        self.assertEqual(challenged.status_code, 401)
        self.assertIn(
            "/.well-known/oauth-protected-resource/mcp",
            challenged.headers["www-authenticate"],
        )

    def test_oidc_mcp_valid_token_with_missing_scope_returns_403(self):
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        public_jwk = jwt.PyJWK.from_dict(
            {
                **jwt.algorithms.RSAAlgorithm.to_jwk(
                    private_key.public_key(),
                    as_dict=True,
                ),
                "kid": "mcp-key",
                "alg": "RS256",
                "use": "sig",
            }
        )
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": "user-1",
                "iss": "https://issuer.example",
                "aud": "https://api.example/mcp",
                "scope": "other",
                "iat": now,
                "exp": now.replace(year=now.year + 1),
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "mcp-key"},
        )
        with TemporaryDirectory() as directory:
            context = self.context(
                Path(directory),
                auth=HttpAuthMode.oidc,
            )
            context.authenticator._jwks = Mock()
            context.authenticator._jwks.get_signing_key_from_jwt.return_value = (
                public_jwk
            )
            with TestClient(create_app(context=context)) as client:
                response = client.post(
                    "/mcp",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2026-07-28",
                            "capabilities": {},
                            "clientInfo": {
                                "name": "test",
                                "version": "1",
                            },
                        },
                    },
                )

        self.assertEqual(response.status_code, 403)

    def test_typed_application_error_status_mapping(self):
        with TemporaryDirectory() as directory:
            context = self.context(Path(directory))
            context.application.get_media.side_effect = ApplicationError(
                "media_conflict",
                ErrorCategory.conflict,
                "The media is in conflict.",
            )
            with TestClient(create_app(context=context)) as client:
                response = client.get(f"/api/v1/media/{MEDIA_ID}")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "media_conflict")


if __name__ == "__main__":
    unittest.main()
