from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    CreateUploadFileCommand,
    CreateUploadIntentCommand,
    ErrorCategory,
    ErrorDetail,
    JobState,
    Principal,
    ResourceNotFoundError,
)
from vidxp.core.media import utc_now
from vidxp.composition import ControlPlaneContext, UploadHookContext
from vidxp.core.uploads import UploadIntentRecord, UploadSessionState, UploadState
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.sql_tables import media as media_table
from vidxp.infrastructure.sql_tables import upload_intents, upload_quota
from vidxp.ingestion_coordinator import IngestionCoordinator, derived_ingestion_job_id
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService, TusUploadProbe
from vidxp import upload_service as upload_service_module


class _Media:
    pass


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.states: dict[str, JobState] = {}

    def enqueue_media_import_in_transaction(
        self,
        upload_id: str,
        *,
        connection,
        job_id: str,
    ) -> str:
        del connection
        self.calls.append((upload_id, job_id))
        return job_id

    def get(self, job_id: str):
        return SimpleNamespace(
            job_id=job_id,
            state=self.states[job_id],
            result=None,
            error=None,
        )


class _RecoveringNativeJobs:
    def __init__(self) -> None:
        self.available = False
        self.submitted = False
        self.submissions: list[tuple[str, str]] = []

    def submit_completed_media_import(self, upload_id: str, *, job_id: str):
        self.submissions.append((upload_id, job_id))
        if not self.available:
            raise ApplicationError(
                "job_backend_unavailable",
                ErrorCategory.unavailable,
                "The durable job backend is unavailable.",
            )
        self.submitted = True
        return SimpleNamespace(
            job_id=job_id,
            state=JobState.queued,
            result=None,
            error=None,
        )

    def get(self, job_id: str):
        if not self.submitted:
            raise ResourceNotFoundError("job")
        return SimpleNamespace(
            job_id=job_id,
            state=JobState.queued,
            result=None,
            error=None,
        )


class _RestartingCoordinatorJobs:
    def __init__(self) -> None:
        self.jobs: dict[str, SimpleNamespace] = {}
        self.index_crashes = True
        self.index_failure: ApplicationError | None = None
        self.import_submissions: list[str] = []
        self.index_submissions: list[str] = []
        self.index_commands: list[CreateIndexCommand] = []
        self.import_submitted = Event()
        self.index_submitted = Event()
        self.start_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop_worker(self) -> bool:
        return True

    def close(self) -> None:
        self.close_calls += 1

    def submit_local_media_import(self, _command, *, job_id: str):
        self.import_submissions.append(job_id)
        self.import_submitted.set()
        return self.jobs.setdefault(
            job_id,
            SimpleNamespace(job_id=job_id, state=JobState.queued, result=None),
        )

    def submit_completed_media_import(self, _upload_id: str, *, job_id: str):
        return self.submit_local_media_import(None, job_id=job_id)

    def submit_index(self, command, *, job_id: str):
        if self.index_crashes:
            raise RuntimeError("simulated process interruption")
        if self.index_failure is not None:
            raise self.index_failure
        self.index_submissions.append(job_id)
        self.index_commands.append(command)
        self.index_submitted.set()
        return self.jobs.setdefault(
            job_id,
            SimpleNamespace(job_id=job_id, state=JobState.queued, result=None),
        )

    def get(self, job_id: str):
        try:
            return self.jobs[job_id]
        except KeyError as exc:
            raise ResourceNotFoundError("job") from exc


def test_upload_hook_context_starts_jobs_without_background_coordinator() -> None:
    jobs = Mock()
    catalog = Mock()
    coordinator = Mock()
    context = UploadHookContext(
        jobs=jobs,
        authenticator=Mock(),
        authorization=Mock(),
        settings=Mock(),
        catalog=catalog,
        uploads=SimpleNamespace(coordinator=coordinator),
    )

    context.start()
    context.start()
    context.stop()
    context.stop()

    assert jobs.start.call_count == 2
    coordinator.start.assert_not_called()
    coordinator.stop.assert_not_called()

    context.close()
    context.close()
    jobs.close.assert_called_once_with()
    catalog.close.assert_called_once_with()
    with pytest.raises(RuntimeError, match="closed upload-hook"):
        context.start()


def test_coordinator_blocked_stop_cannot_overlap_restart(tmp_path: Path) -> None:
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'blocked.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    coordinator = IngestionCoordinator(
        catalog=catalog,
        jobs=None,
        interval_seconds=0.01,
        shutdown_timeout_seconds=0.05,
    )
    entered = Event()
    release = Event()
    restarted = Event()

    def blocked_sweep() -> None:
        entered.set()
        release.wait()

    coordinator.start(blocked_sweep)
    assert entered.wait(1)
    original_thread = coordinator._thread
    jobs = Mock()
    context = ControlPlaneContext(
        application=Mock(),
        jobs=jobs,
        authorization=Mock(),
        settings=VidXPSettings(repository_root=tmp_path),
        catalog=catalog,
        uploads=SimpleNamespace(coordinator=coordinator),
    )

    with patch.object(catalog, "close") as close_catalog:
        with pytest.raises(RuntimeError, match="did not stop"):
            context.close()
        jobs.stop_worker.assert_not_called()
        jobs.close.assert_not_called()
        close_catalog.assert_not_called()
    assert coordinator._thread is original_thread
    assert original_thread is not None and original_thread.is_alive()

    coordinator.start(restarted.set)
    assert not restarted.wait(0.1)
    assert coordinator._thread is original_thread

    release.set()
    coordinator.stop()
    coordinator.start(restarted.set)
    assert restarted.wait(1)
    coordinator.stop()
    with patch.object(catalog, "close") as close_catalog:
        context.close()
        jobs.stop_worker.assert_called_once_with()
        jobs.close.assert_called_once_with()
        close_catalog.assert_called_once_with()
    catalog.close()


def test_native_multipart_import_submission_recovers_after_linking(
    tmp_path: Path,
) -> None:
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'native.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    jobs = _RecoveringNativeJobs()
    settings = VidXPSettings(
        repository_root=tmp_path,
        upload_handoff_public_url="http://127.0.0.1:8765/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_max_bytes=100,
        upload_quota_bytes=100,
        upload_session_max_files=3,
        upload_session_max_bytes=100,
        max_local_import_bytes=100,
        http_max_small_upload_bytes=100,
    )
    service = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )
    link, browser = _open_session(service)
    authorization = service.authorize_session_file(
        link.status.session_id,
        _file("native-01", size=20),
        session_token=browser.session_token,
    )
    settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    staged = settings.quarantine_root / "vidxp-upload-recovery.mp4"
    staged.write_bytes(b"x" * 20)

    accepted = service.complete_multipart_file(
        link.status.session_id,
        authorization.status.intent_id,
        staged_path=staged,
        original_filename="sample.mp4",
        declared_mime_type="video/mp4",
        byte_size=20,
        session_token=browser.session_token,
    )
    assert accepted.items[0].phase == "uploaded"
    service.coordinator.run_once()
    stored = catalog.get_upload_intent(authorization.status.intent_id)
    assert stored is not None
    assert stored.state == UploadState.processing
    assert stored.job_id is not None
    assert jobs.submissions == [(stored.upload_id, stored.job_id)]
    assert (settings.quarantine_root / (stored.upload_id or "")).is_file()

    imported_media_id = uuid4().hex
    service.media = SimpleNamespace(
        import_quarantined=Mock(
            return_value=SimpleNamespace(media_id=imported_media_id)
        )
    )
    imported = service.import_completed(stored.upload_id or "")
    assert imported.media_id == imported_media_id
    lifecycle_owned = catalog.get_upload_intent(authorization.status.intent_id)
    assert lifecycle_owned is not None
    assert lifecycle_owned.state == UploadState.processing
    assert lifecycle_owned.media_id is None
    assert (settings.quarantine_root / (stored.upload_id or "")).is_file()

    jobs.available = True
    service.coordinator.run_once()
    recovered = service.get_status(
        link.status.session_id,
        principal=Principal(subject="owner", client_id="mcp-client"),
    )

    assert recovered.items[0].phase == "importing"
    assert jobs.submissions == [
        (stored.upload_id, stored.job_id),
        (stored.upload_id, stored.job_id),
    ]
    assert jobs.submitted is True
    catalog.close()


def test_concurrent_multipart_attempt_cannot_replace_winning_bytes(
    tmp_path: Path,
) -> None:
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'race.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    settings = VidXPSettings(
        repository_root=tmp_path,
        upload_handoff_public_url="http://127.0.0.1:8765/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_max_bytes=100,
        upload_quota_bytes=100,
        upload_session_max_files=2,
        upload_session_max_bytes=100,
        max_local_import_bytes=100,
        http_max_small_upload_bytes=100,
    )
    jobs = _RecoveringNativeJobs()
    service = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )
    link, browser = _open_session(service)
    authorization = service.authorize_session_file(
        link.status.session_id,
        _file("race-01", size=20),
        session_token=browser.session_token,
    )
    settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    winner = settings.quarantine_root / "vidxp-upload-winner.mp4"
    loser = settings.quarantine_root / "vidxp-upload-loser.mp4"
    winner.write_bytes(b"a" * 20)
    loser.write_bytes(b"b" * 20)
    both_hashed = Barrier(2)
    winner_committed = Event()
    real_sha256 = upload_service_module._file_sha256

    def ordered_sha256(path: Path) -> str:
        digest = real_sha256(path)
        both_hashed.wait(timeout=5)
        if path.name == loser.name:
            assert winner_committed.wait(timeout=5)
        return digest

    def submit(path: Path):
        result = service.complete_multipart_file(
            link.status.session_id,
            authorization.status.intent_id,
            staged_path=path,
            original_filename="sample.mp4",
            declared_mime_type="video/mp4",
            byte_size=20,
            session_token=browser.session_token,
        )
        if path.name == winner.name:
            winner_committed.set()
        return result

    with patch.object(upload_service_module, "_file_sha256", ordered_sha256):
        with ThreadPoolExecutor(max_workers=2) as executor:
            winner_result = executor.submit(submit, winner)
            loser_result = executor.submit(submit, loser)
            assert winner_result.result(timeout=10).items[0].phase == "uploaded"
            assert loser_result.result(timeout=10).items[0].phase == "uploaded"

    stored = catalog.get_upload_intent(authorization.status.intent_id)
    assert stored is not None
    assert stored.upload_id == winner.name
    assert stored.content_sha256 == hashlib.sha256(b"a" * 20).hexdigest()
    assert winner.read_bytes() == b"a" * 20
    assert not loser.exists()
    service.coordinator.run_once()
    assert jobs.submissions[0][0] == winner.name
    catalog.close()


def _service(
    root: Path,
    *,
    quota: int = 100,
    maximum_file_bytes: int = 100,
    maximum_files: int = 3,
    maximum_session_bytes: int = 200,
    authenticated: bool = False,
) -> tuple[RemoteUploadService, SQLCatalog, _Jobs]:
    catalog = SQLCatalog(
        f"sqlite:///{(root / 'server.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    jobs = _Jobs()
    settings = VidXPSettings(
        repository_root=root,
        upload_public_endpoint="http://localhost:8080/uploads/",
        upload_internal_endpoint="http://localhost:8080/uploads/",
        upload_cleanup_token="x" * 32,
        upload_handoff_public_url="https://upload.example/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://upload\.example)$",
        upload_max_bytes=maximum_file_bytes,
        upload_quota_bytes=quota,
        upload_session_max_files=maximum_files,
        upload_session_max_bytes=maximum_session_bytes,
        http_auth_mode="static" if authenticated else "none",
        http_static_bearer_token="s" * 32 if authenticated else None,
    )
    return (
        RemoteUploadService(
            settings=settings,
            catalog=catalog,
            media=_Media(),
            jobs=jobs,
            tusd_upload_probe=lambda upload_id: (
                TusUploadProbe(upload_id=upload_id, length=60, offset=0)
                if (settings.quarantine_root / f"{upload_id}.info").exists()
                else None
            ),
        ),
        catalog,
        jobs,
    )


def _command(size: int = 60) -> CreateUploadIntentCommand:
    return CreateUploadIntentCommand(
        original_filename="sample.mp4",
        byte_size=size,
        declared_mime_type="video/mp4",
    )


def test_hook_completion_queues_until_control_plane_recovers_and_indexes(
    tmp_path: Path,
) -> None:
    hook_uploads, catalog, hook_jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = hook_uploads.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = hook_uploads.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None

    import_job_id = hook_uploads.complete_tus_transfer(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    queued = catalog.get_upload_intent(intent.intent_id)
    assert queued is not None
    assert queued.state == UploadState.processing
    assert queued.job_id == import_job_id == accepted.upload_id
    assert hook_jobs.calls == [(accepted.upload_id, import_job_id)]

    control_jobs = _RestartingCoordinatorJobs()
    control_jobs.index_crashes = False
    control_uploads = RemoteUploadService(
        settings=hook_uploads.settings,
        catalog=catalog,
        media=_Media(),
        jobs=control_jobs,
    )
    context = ControlPlaneContext(
        application=Mock(),
        jobs=control_jobs,
        authorization=Mock(),
        settings=hook_uploads.settings,
        catalog=catalog,
        uploads=control_uploads,
    )

    context.start()
    assert control_jobs.import_submitted.wait(2)
    context.stop()
    assert control_jobs.import_submissions == [import_job_id]

    media_id = uuid4().hex
    with catalog.engine.begin() as connection:
        connection.execute(
            insert(media_table).values(
                media_id=media_id,
                sha256="8" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
    control_jobs.jobs[import_job_id] = SimpleNamespace(
        job_id=import_job_id,
        state=JobState.succeeded,
        result=SimpleNamespace(result=SimpleNamespace(media_id=media_id)),
    )
    control_jobs.index_submitted.clear()

    context.start()
    assert control_jobs.index_submitted.wait(2)
    context.stop()
    recovered = catalog.get_upload_intent(intent.intent_id)
    assert recovered is not None
    assert recovered.state == UploadState.ready
    assert recovered.media_id == media_id
    assert recovered.index_job_id == derived_ingestion_job_id(intent.intent_id, "index")
    assert control_jobs.index_submissions == [recovered.index_job_id]

    context.close()
    assert control_jobs.close_calls == 1


def test_upload_intent_is_idempotent_and_repository_shared(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner")
    created = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )

    assert (
        service.create_intent(
            _command(),
            principal=owner,
            request_key="a" * 64,
        )
        == created
    )
    assert (
        service.get_intent(
            created.intent_id,
            principal=Principal(subject="other"),
        )
        == created
    )
    catalog.close()


def test_upload_session_is_idempotent_without_intent_or_quota(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner", client_id="mcp-client")

    first = service.create_upload_session(
        principal=owner,
        request_key="a" * 64,
    )
    replay = service.create_upload_session(
        principal=owner,
        request_key="a" * 64,
    )

    assert replay == first
    assert first.status.session_state == UploadSessionState.open
    assert first.status.aggregate_state == "empty"
    assert first.status.items == ()
    stored = catalog.get_upload_session(first.status.session_id)
    assert stored is not None
    assert stored.initiating_subject == owner.subject
    assert stored.initiating_client_id == owner.client_id
    assert stored.browser_session_digest is None
    assert first.capability not in stored.model_dump_json()
    claims = jwt.decode(first.capability, options={"verify_signature": False})
    assert claims["sub"] == first.status.session_id
    assert claims["aud"] == "vidxp-upload-session"
    assert claims["purpose"] == "media-upload"
    with catalog.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(upload_intents)) == 0
        assert connection.scalar(select(func.count()).select_from(upload_quota)) == 0
    catalog.close()


def test_upload_session_idempotency_validates_shared_request_contract(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner", client_id="client-a")
    source = tmp_path / "source.mp4"
    other = tmp_path / "other.mp4"
    source.write_bytes(b"source")
    other.write_bytes(b"other")
    local = RemoteUploadService(
        settings=service.settings.model_copy(update={"max_local_import_bytes": 100}),
        catalog=catalog,
        media=SimpleNamespace(resolve_local_source=lambda path: path.resolve()),
        jobs=jobs,
    )
    service.create_upload_session(
        principal=owner,
        request_key="a" * 64,
        index_modalities=("scene",),
    )
    local.create_local_ingestion(
        (str(source),),
        principal=owner,
        request_key="b" * 64,
        index_after_import=False,
    )

    conflicting_calls = (
        lambda: service.create_upload_session(
            principal=owner,
            request_key="a" * 64,
            index_modalities=("speech",),
        ),
        lambda: service.create_upload_session(
            principal=Principal(subject="other", client_id="client-a"),
            request_key="a" * 64,
            index_modalities=("scene",),
        ),
        lambda: local.create_local_ingestion(
            (str(source),),
            principal=owner,
            request_key="a" * 64,
            index_after_import=False,
        ),
        lambda: local.create_local_ingestion(
            (str(other),),
            principal=owner,
            request_key="b" * 64,
            index_after_import=False,
        ),
        lambda: local.create_local_ingestion(
            (str(source),),
            principal=owner,
            request_key="b" * 64,
            index_modalities=("scene",),
        ),
    )
    for call in conflicting_calls:
        with pytest.raises(ApplicationError) as conflict:
            call()
        assert conflict.value.detail.code == "idempotency_key_reused"

    other_root = tmp_path / "other-repository"
    other_root.mkdir()
    other_repository = RemoteUploadService(
        settings=service.settings.model_copy(update={"repository_root": other_root}),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )
    with pytest.raises(ApplicationError) as repository_conflict:
        other_repository.create_upload_session(
            principal=owner,
            request_key="a" * 64,
            index_modalities=("scene",),
        )
    assert repository_conflict.value.detail.code == "idempotency_key_reused"
    catalog.close()


def test_upload_session_concurrent_insert_winners_use_shared_validation(
    tmp_path: Path,
) -> None:
    service, catalog, _jobs = _service(tmp_path)
    original = catalog.create_upload_session

    def committed_winner(record) -> None:
        original(record)
        raise IntegrityError("insert session", {}, RuntimeError("duplicate"))

    with patch.object(catalog, "create_upload_session", side_effect=committed_winner):
        replay = service.create_upload_session(
            principal=Principal(subject="owner", client_id="client-a"),
            request_key="c" * 64,
            index_modalities=("scene",),
        )

    stored = catalog.get_upload_session_by_request("c" * 64)
    assert stored is not None
    assert replay.status.session_id == stored.session_id
    catalog.close()


def test_local_ingestion_concurrent_insert_winner_uses_shared_validation(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    local = RemoteUploadService(
        settings=service.settings.model_copy(update={"max_local_import_bytes": 100}),
        catalog=catalog,
        media=SimpleNamespace(resolve_local_source=lambda path: path.resolve()),
        jobs=jobs,
    )
    original = catalog.with_upload_transaction

    def committed_winner(operation):
        original(operation)
        raise IntegrityError("insert session", {}, RuntimeError("duplicate"))

    with patch.object(
        catalog,
        "with_upload_transaction",
        side_effect=committed_winner,
    ):
        replay = local.create_local_ingestion(
            (str(source),),
            principal=Principal(subject="owner", client_id="client-a"),
            request_key="d" * 64,
            index_after_import=False,
        )

    stored = catalog.get_upload_session_by_request("d" * 64)
    assert stored is not None
    assert replay.session_id == stored.session_id
    assert replay.file_count == 1
    catalog.close()


def _file(
    key: str,
    *,
    filename: str = "sample.mp4",
    size: int = 20,
    mime: str | None = "video/mp4",
) -> CreateUploadFileCommand:
    return CreateUploadFileCommand(
        client_file_key=key,
        original_filename=filename,
        byte_size=size,
        declared_mime_type=mime,
    )


def _open_session(service: RemoteUploadService, request_key: str = "a" * 64):
    link = service.create_upload_session(
        principal=Principal(subject="owner", client_id="mcp-client"),
        request_key=request_key,
    )
    browser = service.exchange_upload_session(
        link.status.session_id,
        capability=link.capability,
    )
    return link, browser


def test_local_ingestion_batch_rolls_back_and_replays_after_database_failure(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'local-batch.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    settings = VidXPSettings(
        repository_root=tmp_path,
        trusted_local_import_roots=(tmp_path,),
        max_local_import_bytes=100,
        upload_max_bytes=100,
        upload_session_max_bytes=200,
    )
    service = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=SimpleNamespace(resolve_local_source=lambda path: path.resolve()),
        jobs=_RecoveringNativeJobs(),
    )
    request_key = "e" * 64
    original = catalog.create_upload_session_file
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IntegrityError("injected", {}, RuntimeError("database failure"))
        return original(*args, **kwargs)

    with patch.object(
        catalog,
        "create_upload_session_file",
        side_effect=fail_second,
    ):
        with pytest.raises(IntegrityError):
            service.create_local_ingestion(
                (str(first), str(second)),
                principal=Principal(subject="local", client_id="stdio"),
                request_key=request_key,
                index_after_import=False,
            )

    assert catalog.get_upload_session_by_request(request_key) is None
    with catalog.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(upload_intents)) == 0
        assert connection.scalar(select(func.count()).select_from(upload_quota)) == 0

    replay = service.create_local_ingestion(
        (str(first), str(second)),
        principal=Principal(subject="local", client_id="stdio"),
        request_key=request_key,
        index_after_import=False,
    )
    assert replay.file_count == 2
    assert [item.original_filename for item in replay.items] == [
        first.name,
        second.name,
    ]
    assert catalog.get_upload_session_by_request(request_key) is not None
    catalog.close()


def test_open_session_stops_polling_for_current_work_and_resumes_for_new_file(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    link, browser = _open_session(service)
    assert link.status.session_state == UploadSessionState.open
    assert link.status.terminal is False
    assert link.status.poll_after_seconds == 2

    first = service.authorize_session_file(
        link.status.session_id,
        _file("first"),
        session_token=browser.session_token,
    )
    completed = service.cancel_browser_file(
        link.status.session_id,
        first.status.intent_id,
        session_token=browser.session_token,
    ).status
    assert completed.session_state == UploadSessionState.open
    assert completed.terminal is True
    assert completed.poll_after_seconds == 0
    assert "remains open" in completed.status
    assert completed.next_action.startswith("Stop polling.")

    service.authorize_session_file(
        link.status.session_id,
        _file("second"),
        session_token=browser.session_token,
    )
    resumed = service.get_status(
        link.status.session_id,
        principal=Principal(subject="owner", client_id="mcp-client"),
    )
    assert resumed.session_state == UploadSessionState.open
    assert resumed.terminal is False
    assert resumed.poll_after_seconds == 2
    catalog.close()


def test_authenticated_status_and_close_require_initiating_principal(
    tmp_path: Path,
) -> None:
    service, catalog, _jobs = _service(tmp_path, authenticated=True)
    owner = Principal(subject="owner", client_id="client-a")
    link = service.create_upload_session(
        principal=owner,
        request_key="c" * 64,
    )

    assert service.get_status(link.status.session_id, principal=owner).session_id == (
        link.status.session_id
    )
    for outsider in (
        Principal(subject="other", client_id="client-a"),
        Principal(subject="owner", client_id="client-b"),
    ):
        with pytest.raises(ApplicationError) as hidden:
            service.get_status(link.status.session_id, principal=outsider)
        assert hidden.value.detail.code == "resource_not_found"
        with pytest.raises(ApplicationError) as close_hidden:
            service.close_upload_session(
                link.status.session_id,
                principal=outsider,
            )
        assert close_hidden.value.detail.code == "resource_not_found"

    closed = service.close_upload_session(link.status.session_id, principal=owner)
    assert closed.session_state == UploadSessionState.closed
    catalog.close()


def test_status_projection_never_opens_write_transaction_or_submits_jobs(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    link = service.create_upload_session(
        principal=Principal(subject="owner"),
        request_key="d" * 64,
    )
    with patch.object(
        catalog,
        "with_upload_transaction",
        side_effect=AssertionError("status attempted a write"),
    ):
        status = service.get_status(
            link.status.session_id,
            principal=Principal(subject="owner"),
        )

    assert status.session_id == link.status.session_id
    assert jobs.calls == []
    catalog.close()


def test_coordinator_recovers_import_and_pre_index_restart_without_status_polling(
    tmp_path: Path,
) -> None:
    source = tmp_path / "autonomous.mp4"
    source.write_bytes(b"video")
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'restart.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    jobs = _RestartingCoordinatorJobs()
    settings = VidXPSettings(
        repository_root=tmp_path,
        trusted_local_import_roots=(tmp_path,),
        max_local_import_bytes=100,
        upload_max_bytes=100,
        upload_session_max_bytes=200,
    )
    service = RemoteUploadService(
        settings=settings,
        catalog=catalog,
        media=SimpleNamespace(resolve_local_source=lambda path: path.resolve()),
        jobs=jobs,
    )
    submitted = service.create_local_ingestion(
        (str(source),),
        principal=Principal(subject="local", client_id="stdio"),
        request_key="f" * 64,
        index_after_import=True,
        index_modalities=("scene",),
    )
    intent_id = submitted.items[0].intent_id

    first_process = IngestionCoordinator(
        catalog=catalog,
        jobs=jobs,
        interval_seconds=1,
    )
    first_process.run_once()
    importing = catalog.get_upload_intent(intent_id)
    assert importing is not None
    assert importing.state == UploadState.processing
    assert importing.job_id is not None
    assert jobs.import_submissions == [importing.job_id]

    with catalog.engine.begin() as connection:
        connection.execute(
            insert(media_table).values(
                media_id="123456781234423481234567890abcde",
                sha256="9" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
    jobs.jobs[importing.job_id] = SimpleNamespace(
        job_id=importing.job_id,
        state=JobState.succeeded,
        result=SimpleNamespace(
            result=SimpleNamespace(media_id="123456781234423481234567890abcde")
        ),
    )
    crashed_process = IngestionCoordinator(
        catalog=catalog,
        jobs=jobs,
        interval_seconds=1,
    )
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        crashed_process.advance(importing)
    registered = catalog.get_upload_intent(intent_id)
    assert registered is not None
    assert registered.state == UploadState.ready
    assert registered.index_job_id is not None

    jobs.index_crashes = False
    restarted_process = IngestionCoordinator(
        catalog=catalog,
        jobs=jobs,
        interval_seconds=1,
    )
    restarted_process.run_once()
    indexing = catalog.get_upload_intent(intent_id)
    assert indexing is not None and indexing.index_job_id is not None
    assert jobs.index_submissions == [indexing.index_job_id]

    del jobs.jobs[indexing.index_job_id]
    jobs.index_failure = ApplicationError(
        "job_backend_unavailable",
        ErrorCategory.unavailable,
        "The durable job backend is unavailable.",
        retryable=True,
    )
    restarted_process.run_once()
    recoverable = catalog.get_upload_intent(intent_id)
    assert recoverable is not None and recoverable.state == UploadState.ready
    assert jobs.index_submissions == [indexing.index_job_id]

    jobs.index_failure = None
    restarted_process.run_once()
    assert jobs.index_submissions == [indexing.index_job_id, indexing.index_job_id]
    jobs.jobs[indexing.index_job_id] = SimpleNamespace(
        job_id=indexing.index_job_id,
        state=JobState.succeeded,
        result=SimpleNamespace(
            result=SimpleNamespace(
                generation_id="223456781234423481234567890abcde",
                snapshot_id="323456781234423481234567890abcde",
            )
        ),
    )
    restarted_process.run_once()
    indexed = catalog.get_upload_intent(intent_id)
    assert indexed is not None and indexed.state == UploadState.indexed

    terminal_id = uuid4().hex
    terminal = indexed.model_copy(
        update={
            "intent_id": terminal_id,
            "request_key": "9" * 64,
            "upload_id": terminal_id,
            "job_id": derived_ingestion_job_id(terminal_id, "import"),
            "state": UploadState.ready,
            "index_job_id": None,
        }
    )
    catalog.create_upload_intent(terminal, quota_limit=1_000)
    jobs.index_failure = ApplicationError(
        "index_request_invalid",
        ErrorCategory.validation,
        "The indexing request is invalid.",
    )
    restarted_process.run_once()
    index_failed = catalog.get_upload_intent(terminal.intent_id)
    assert index_failed is not None and index_failed.state == UploadState.ready
    assert index_failed.media_id == terminal.media_id
    assert index_failed.job_id == terminal.job_id
    assert index_failed.index_job_id is not None
    assert index_failed.failure_code == "index_request_invalid"
    catalog.close()


def test_coordinator_stale_transitions_are_compare_and_set_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stale.mp4"
    source.write_bytes(b"video")
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'stale.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    jobs = _RestartingCoordinatorJobs()
    jobs.index_crashes = False
    service = RemoteUploadService(
        settings=VidXPSettings(
            repository_root=tmp_path,
            trusted_local_import_roots=(tmp_path,),
            max_local_import_bytes=100,
            upload_max_bytes=100,
            upload_session_max_bytes=200,
        ),
        catalog=catalog,
        media=SimpleNamespace(resolve_local_source=lambda path: path.resolve()),
        jobs=jobs,
    )
    intent_id = service.create_local_ingestion(
        (str(source),),
        principal=Principal(subject="local"),
        request_key="7" * 64,
        index_after_import=True,
        index_modalities=("scene",),
    ).items[0].intent_id
    coordinator = service.coordinator
    pending = catalog.get_upload_intent(intent_id)
    assert pending is not None
    stale_processing = coordinator._start_import(pending)
    assert stale_processing.state == UploadState.processing

    media_id = uuid4().hex
    with catalog.engine.begin() as connection:
        connection.execute(
            insert(media_table).values(
                media_id=media_id,
                sha256="7" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            intent_id,
            state=UploadState.ready,
            connection=connection,
            media_id=media_id,
            expected_states={UploadState.processing},
            expected_job_id=stale_processing.job_id,
        )
    )

    after_stale_import_failure = coordinator._fail_import(
        stale_processing,
        ErrorDetail(
            code="stale_import_failure",
            category=ErrorCategory.internal,
            message="A stale import observer failed.",
        ),
    )
    assert after_stale_import_failure.state == UploadState.ready
    assert after_stale_import_failure.media_id == media_id
    assert after_stale_import_failure.failure_code is None

    indexing = coordinator._start_index(after_stale_import_failure)
    assert indexing.index_job_id is not None
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            intent_id,
            state=UploadState.indexed,
            connection=connection,
            expected_states={UploadState.ready},
            expected_index_job_id=indexing.index_job_id,
        )
    )
    after_stale_index_failure = coordinator._fail_index(
        indexing,
        ErrorDetail(
            code="stale_index_failure",
            category=ErrorCategory.internal,
            message="A stale index observer failed.",
        ),
    )
    assert after_stale_index_failure.state == UploadState.indexed
    assert after_stale_index_failure.failure_code is None

    expiring_id = uuid4().hex
    expiring = pending.model_copy(
        update={
            "intent_id": expiring_id,
            "request_key": "8" * 64,
            "upload_id": None,
            "job_id": None,
            "state": UploadState.pending,
        }
    )
    catalog.create_upload_intent(expiring, quota_limit=1_000)
    stale_expiring = coordinator._start_import(expiring)
    assert stale_expiring.job_id is not None
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            expiring_id,
            state=UploadState.expired,
            connection=connection,
            expected_states={UploadState.processing},
            expected_job_id=stale_expiring.job_id,
        )
    )
    jobs.jobs[stale_expiring.job_id] = SimpleNamespace(
        job_id=stale_expiring.job_id,
        state=JobState.failed,
        result=None,
        error=ErrorDetail(
            code="late_import_failure",
            category=ErrorCategory.internal,
            message="The stale job failed after cancellation.",
        ),
    )
    assert coordinator.advance(stale_expiring).state == UploadState.expired
    expired = catalog.get_upload_intent(expiring_id)
    assert expired is not None and expired.failure_code is None

    competing_id = uuid4().hex
    ready = after_stale_index_failure.model_copy(
        update={
            "intent_id": competing_id,
            "request_key": "9" * 64,
            "upload_id": competing_id,
            "job_id": derived_ingestion_job_id(competing_id, "import"),
            "state": UploadState.ready,
            "index_job_id": None,
        }
    )
    catalog.create_upload_intent(ready, quota_limit=1_000)
    first = coordinator._start_index(ready)
    second = coordinator._start_index(ready)
    assert first.index_job_id == second.index_job_id
    assert jobs.index_submissions.count(first.index_job_id) == 1
    catalog.close()


def test_capability_tamper_expiry_and_replay(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    link = service.create_upload_session(
        principal=Principal(subject="owner"),
        request_key="a" * 64,
    )
    header, payload, signature = link.capability.split(".")
    tampered = ".".join(
        (
            header,
            payload,
            ("A" if signature[0] != "A" else "B") + signature[1:],
        )
    )
    with pytest.raises(ApplicationError) as invalid:
        service.exchange_upload_session(
            link.status.session_id,
            capability=tampered,
        )
    assert invalid.value.detail.code == "upload_session_capability_invalid"

    with pytest.raises(ApplicationError) as wrong_session:
        service.exchange_upload_session(
            uuid4().hex,
            capability=link.capability,
        )
    assert wrong_session.value.detail.code == "upload_session_capability_invalid"

    first = service.exchange_upload_session(
        link.status.session_id,
        capability=link.capability,
    )
    replay = service.exchange_upload_session(
        link.status.session_id,
        capability=link.capability,
        current_session=first.session_token,
    )
    assert replay.session_token == first.session_token

    with patch(
        "vidxp.upload_service.utc_now",
        return_value=link.status.expires_at + timedelta(seconds=1),
    ):
        with pytest.raises(ApplicationError) as expired:
            service.exchange_upload_session(
                link.status.session_id,
                capability=link.capability,
            )
    assert expired.value.detail.code == "upload_session_expired"
    catalog.close()


def test_multiple_files_duplicate_keys_and_same_filename(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path, quota=200)
    link, browser = _open_session(service)

    first = service.authorize_session_file(
        link.status.session_id,
        _file("file-a"),
        session_token=browser.session_token,
    )
    replay = service.authorize_session_file(
        link.status.session_id,
        _file("file-a"),
        session_token=browser.session_token,
    )
    second = service.authorize_session_file(
        link.status.session_id,
        _file("file-b"),
        session_token=browser.session_token,
    )

    assert replay.status.intent_id == first.status.intent_id
    assert replay.grant != first.grant
    assert second.status.intent_id != first.status.intent_id
    assert second.status.original_filename == first.status.original_filename
    assert first.grant is not None
    with catalog.engine.connect() as connection:
        stored = catalog.get_upload_session_file(
            link.status.session_id,
            "file-a",
            connection=connection,
        )
    assert stored is not None
    assert first.grant not in stored.model_dump_json()

    with pytest.raises(ApplicationError) as conflict:
        service.authorize_session_file(
            link.status.session_id,
            _file("file-a", filename="different.mp4"),
            session_token=browser.session_token,
        )
    assert conflict.value.detail.code == "upload_client_key_conflict"

    status = service.get_status(
        link.status.session_id,
        principal=Principal(subject="owner"),
    )
    assert status.file_count == 2
    assert {item.client_file_key for item in status.items} == {"file-a", "file-b"}
    catalog.close()


@pytest.mark.parametrize(
    ("service_options", "commands", "error_code"),
    [
        (
            {"maximum_file_bytes": 25, "maximum_session_bytes": 100},
            [_file("too-large", size=26)],
            "upload_file_too_large",
        ),
        (
            {"maximum_files": 1},
            [_file("first"), _file("second")],
            "upload_session_file_limit",
        ),
        (
            {"maximum_file_bytes": 30, "maximum_session_bytes": 30},
            [_file("first", size=20), _file("second", size=20)],
            "upload_session_byte_limit",
        ),
        (
            {"quota": 30},
            [_file("first", size=20), _file("second", size=20)],
            "upload_quota_exceeded",
        ),
    ],
)
def test_upload_session_limits(
    tmp_path: Path,
    service_options: dict,
    commands: list[CreateUploadFileCommand],
    error_code: str,
) -> None:
    service, catalog, _ = _service(tmp_path, **service_options)
    link, browser = _open_session(service)

    for command in commands[:-1]:
        service.authorize_session_file(
            link.status.session_id,
            command,
            session_token=browser.session_token,
        )
    with pytest.raises(ApplicationError) as rejected:
        service.authorize_session_file(
            link.status.session_id,
            commands[-1],
            session_token=browser.session_token,
        )
    assert rejected.value.detail.code == error_code
    catalog.close()


def test_quota_is_reserved_only_after_file_selection(tmp_path: Path) -> None:
    service, catalog, _ = _service(tmp_path, quota=100)
    link, browser = _open_session(service)

    with catalog.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(upload_quota)) == 0

    service.authorize_session_file(
        link.status.session_id,
        _file("selected", size=40),
        session_token=browser.session_token,
    )
    with catalog.engine.connect() as connection:
        assert connection.scalar(select(upload_quota.c.reserved_bytes)) == 40
    catalog.close()


def test_creation_grant_is_per_file_and_one_time(tmp_path: Path) -> None:
    service, catalog, _ = _service(tmp_path, quota=200)
    link, browser = _open_session(service)
    authorization = service.authorize_session_file(
        link.status.session_id,
        _file("file-a", size=60),
        session_token=browser.session_token,
    )
    assert authorization.grant is not None

    with pytest.raises(ApplicationError) as wrong_size:
        service.accept_session_creation(
            authorization.status.intent_id,
            grant=authorization.grant,
            byte_size=61,
        )
    assert wrong_size.value.detail.code == "upload_creation_grant_invalid"

    accepted = service.accept_session_creation(
        authorization.status.intent_id,
        grant=authorization.grant,
        byte_size=60,
    )
    assert accepted.state == UploadState.accepted

    with pytest.raises(ApplicationError) as replayed:
        service.accept_session_creation(
            authorization.status.intent_id,
            grant=authorization.grant,
            byte_size=60,
        )
    assert replayed.value.detail.code == "upload_creation_grant_replayed"
    catalog.close()


def test_resume_probe_timeout_is_reported_as_service_unavailable(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path, quota=200)
    link, browser = _open_session(service)
    authorization = service.authorize_session_file(
        link.status.session_id,
        _file("probe-timeout", size=60),
        session_token=browser.session_token,
    )
    assert authorization.grant is not None
    service.accept_session_creation(
        authorization.status.intent_id,
        grant=authorization.grant,
        byte_size=60,
    )
    probing_service = RemoteUploadService(
        settings=service.settings,
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )

    with patch("vidxp.upload_service.urlopen", side_effect=TimeoutError):
        with pytest.raises(ApplicationError) as unavailable:
            probing_service.browser_session(
                link.status.session_id,
                session_token=browser.session_token,
            )

    assert unavailable.value.detail.code == "remote_upload_unavailable"
    catalog.close()


@pytest.mark.parametrize(
    ("job_state", "expected_code"),
    (
        (JobState.failed, "media_index_failed"),
        (JobState.cancelled, "media_index_cancelled"),
        (JobState.recovery_exhausted, "media_index_recovery_exhausted"),
    ),
)
def test_index_terminal_failure_preserves_registered_media_and_status(
    tmp_path: Path,
    job_state: JobState,
    expected_code: str,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner", client_id="mcp-client")
    link = service.create_upload_session(
        principal=owner,
        request_key="a" * 64,
        index_after_import=True,
        index_modalities=("scene",),
    )
    browser = service.exchange_upload_session(
        link.status.session_id,
        capability=link.capability,
    )
    authorization = service.authorize_session_file(
        link.status.session_id,
        _file("index-failure", size=20),
        session_token=browser.session_token,
    )
    successful = service.authorize_session_file(
        link.status.session_id,
        _file("indexed-sibling", size=20),
        session_token=browser.session_token,
    )
    media_id = uuid4().hex
    successful_media_id = uuid4().hex
    import_job_id = derived_ingestion_job_id(authorization.status.intent_id, "import")
    index_job_id = derived_ingestion_job_id(authorization.status.intent_id, "index")
    successful_import_job_id = derived_ingestion_job_id(
        successful.status.intent_id, "import"
    )
    successful_index_job_id = derived_ingestion_job_id(
        successful.status.intent_id, "index"
    )
    with catalog.engine.begin() as connection:
        connection.execute(
            insert(media_table).values(
                media_id=media_id,
                sha256="6" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
        connection.execute(
            insert(media_table).values(
                media_id=successful_media_id,
                sha256="5" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            authorization.status.intent_id,
            state=UploadState.ready,
            connection=connection,
            upload_id=authorization.status.intent_id,
            job_id=import_job_id,
            media_id=media_id,
            index_job_id=index_job_id,
            expected_states={UploadState.pending},
            expected_job_id=None,
            expected_index_job_id=None,
        )
    )
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            successful.status.intent_id,
            state=UploadState.indexed,
            connection=connection,
            upload_id=successful.status.intent_id,
            job_id=successful_import_job_id,
            media_id=successful_media_id,
            index_job_id=successful_index_job_id,
            expected_states={UploadState.pending},
            expected_job_id=None,
            expected_index_job_id=None,
        )
    )
    jobs.states[index_job_id] = job_state

    service.coordinator.run_once()

    stored = catalog.get_upload_intent(authorization.status.intent_id)
    assert stored is not None
    assert stored.state == UploadState.ready
    assert stored.media_id == media_id
    assert stored.job_id == import_job_id
    assert stored.index_job_id == index_job_id
    assert stored.failure_code == expected_code

    status = service.get_status(link.status.session_id, principal=owner)
    items = {item.client_file_key: item for item in status.items}
    item = items["index-failure"]
    assert item.phase == "index_failed"
    assert item.media_id == media_id
    assert item.error is not None and item.error.code == expected_code
    assert item.terminal is True
    assert item.poll_after_seconds == 0
    assert item.searchable is False
    assert "start_indexing" in item.next_action
    assert "do not upload" in item.next_action
    assert items["indexed-sibling"].phase == "indexed"
    assert items["indexed-sibling"].searchable is True
    assert status.aggregate_state == "partial_index_failure"
    assert status.ready_file_count == 2
    assert status.searchable_file_count == 1
    assert status.failed_file_count == 0
    assert status.index_failed_file_count == 1
    assert status.terminal is True
    assert status.poll_after_seconds == 0
    catalog.close()


def _failed_index_upload(
    root: Path,
    jobs: _RestartingCoordinatorJobs,
    *,
    intent_count: int = 1,
) -> tuple[RemoteUploadService, SQLCatalog, Principal, str, str]:
    catalog = SQLCatalog(
        f"sqlite:///{(root / 'index-retry.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    service = RemoteUploadService(
        settings=VidXPSettings(
            repository_root=root,
            upload_handoff_public_url="https://upload.example/upload-handoff",
            upload_handoff_secret="h" * 32,
            upload_max_bytes=100,
            upload_quota_bytes=100,
            upload_session_max_bytes=100,
            max_local_import_bytes=100,
            http_max_small_upload_bytes=100,
        ),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )
    owner = Principal(subject="owner", client_id="mcp-client")
    session = service.create_upload_session(
        principal=owner,
        request_key="d" * 64,
        index_after_import=True,
        index_modalities=("scene",),
    )
    browser = service.exchange_upload_session(
        session.status.session_id,
        capability=session.capability,
    )
    authorizations = [
        service.authorize_session_file(
            session.status.session_id,
            _file(f"retry-index-{number}", size=20),
            session_token=browser.session_token,
        )
        for number in range(intent_count)
    ]
    media_id = uuid4().hex
    with catalog.engine.begin() as connection:
        connection.execute(
            insert(media_table).values(
                media_id=media_id,
                sha256="4" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
    def fail_indexes(connection) -> None:
        for authorization in authorizations:
            intent_id = authorization.status.intent_id
            catalog.update_upload(
                intent_id,
                state=UploadState.ready,
                connection=connection,
                upload_id=intent_id,
                job_id=derived_ingestion_job_id(intent_id, "import"),
                media_id=media_id,
                index_job_id=derived_ingestion_job_id(intent_id, "index"),
                failure_code="media_index_failed",
                failure_message="Automatic indexing failed.",
                expected_states={UploadState.pending},
                expected_job_id=None,
                expected_index_job_id=None,
            )

    catalog.with_upload_transaction(fail_indexes)
    return service, catalog, owner, session.status.session_id, media_id


def test_start_indexing_relinks_failed_upload_and_projects_success(
    tmp_path: Path,
) -> None:
    jobs = _RestartingCoordinatorJobs()
    jobs.index_crashes = False
    service, catalog, owner, session_id, media_id = _failed_index_upload(
        tmp_path,
        jobs,
    )
    retry_job_id = uuid4().hex

    service.start_indexing(
        CreateIndexCommand(media_id=media_id, modalities=("scene",)),
        job_id=retry_job_id,
    )

    with catalog.engine.connect() as connection:
        linked = catalog.failed_index_uploads_for_media(
            media_id,
            connection=connection,
        )
    assert linked == ()
    retrying = next(
        intent for _, intent in catalog.list_upload_session_files(session_id)
    )
    assert retrying.index_job_id == retry_job_id
    assert retrying.failure_code is None
    jobs.jobs[retry_job_id] = SimpleNamespace(
        job_id=retry_job_id,
        state=JobState.succeeded,
        result=SimpleNamespace(
            result=SimpleNamespace(
                generation_id="223456781234423481234567890abcde",
                snapshot_id="323456781234423481234567890abcde",
            )
        ),
    )

    service.coordinator.run_once()
    status = service.get_status(session_id, principal=owner)

    assert status.items[0].phase == "indexed"
    assert status.items[0].searchable is True
    assert status.items[0].generation_id == "223456781234423481234567890abcde"
    assert status.items[0].snapshot_id == "323456781234423481234567890abcde"
    catalog.close()


@pytest.mark.parametrize(
    ("job_state", "expected_phase"),
    ((JobState.succeeded, "indexed"), (JobState.failed, "index_failed")),
)
def test_shared_media_index_retry_updates_every_upload_intent(
    tmp_path: Path,
    job_state: JobState,
    expected_phase: str,
) -> None:
    jobs = _RestartingCoordinatorJobs()
    jobs.index_crashes = False
    service, catalog, owner, session_id, media_id = _failed_index_upload(
        tmp_path,
        jobs,
        intent_count=2,
    )
    retry_job_id = uuid4().hex

    service.start_indexing(
        CreateIndexCommand(media_id=media_id, modalities=("scene",)),
        job_id=retry_job_id,
    )
    jobs.jobs[retry_job_id] = SimpleNamespace(
        job_id=retry_job_id,
        state=job_state,
        result=(
            SimpleNamespace(
                result=SimpleNamespace(
                    generation_id="223456781234423481234567890abcde",
                    snapshot_id="323456781234423481234567890abcde",
                )
            )
            if job_state == JobState.succeeded
            else None
        ),
        error=(
            ErrorDetail(
                code="shared_index_failed",
                category=ErrorCategory.internal,
                message="The shared retry failed.",
            )
            if job_state == JobState.failed
            else None
        ),
    )

    service.coordinator.run_once()
    status = service.get_status(session_id, principal=owner)

    assert len(status.items) == 2
    assert {item.index_job_id for item in status.items} == {retry_job_id}
    assert {item.phase for item in status.items} == {expected_phase}
    catalog.close()


def test_index_retry_recovery_resubmits_exact_persisted_command(
    tmp_path: Path,
) -> None:
    jobs = _RestartingCoordinatorJobs()
    service, catalog, _, session_id, media_id = _failed_index_upload(tmp_path, jobs)
    retry_job_id = uuid4().hex
    command = CreateIndexCommand(
        media_id=media_id,
        modalities=("speech", "scene"),
        frame_stride=7,
        scene_sample_fps=2.5,
        capability_options={
            "scene": {"batch_size": 3},
            "speech": {"language": "ur"},
        },
    )

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        service.start_indexing(command, job_id=retry_job_id)

    linked = catalog.list_upload_session_files(session_id)[0][1]
    assert linked.index_job_id == retry_job_id
    assert CreateIndexCommand.model_validate(linked.index_command) == command

    jobs.index_crashes = False
    service.coordinator.run_once()

    assert jobs.index_submissions == [retry_job_id]
    assert jobs.index_commands == [command]
    catalog.close()


def test_start_indexing_submission_failure_replaces_stale_index_error(
    tmp_path: Path,
) -> None:
    jobs = _RestartingCoordinatorJobs()
    jobs.index_crashes = False
    jobs.index_failure = ApplicationError(
        "index_retry_invalid",
        ErrorCategory.validation,
        "The retry request is invalid.",
    )
    service, catalog, owner, session_id, media_id = _failed_index_upload(
        tmp_path,
        jobs,
    )
    retry_job_id = uuid4().hex

    with pytest.raises(ApplicationError) as failed:
        service.start_indexing(
            CreateIndexCommand(media_id=media_id, modalities=("scene",)),
            job_id=retry_job_id,
        )

    assert failed.value.detail.code == "index_retry_invalid"
    status = service.get_status(session_id, principal=owner)
    assert status.items[0].phase == "index_failed"
    assert status.items[0].index_job_id == retry_job_id
    assert status.items[0].error is not None
    assert status.items[0].error.code == "index_retry_invalid"
    assert status.items[0].searchable is False
    catalog.close()


def test_start_indexing_keeps_ordinary_non_upload_media_supported(
    tmp_path: Path,
) -> None:
    jobs = _RestartingCoordinatorJobs()
    jobs.index_crashes = False
    catalog = SQLCatalog(
        f"sqlite:///{(tmp_path / 'ordinary-index.sqlite3').resolve().as_posix()}",
        initialize=True,
    )
    service = RemoteUploadService(
        settings=VidXPSettings(repository_root=tmp_path),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
    )
    job_id = uuid4().hex
    media_id = uuid4().hex

    job = service.start_indexing(
        CreateIndexCommand(media_id=media_id, modalities=("scene",)),
        job_id=job_id,
    )

    assert job.job_id == job_id
    assert jobs.index_submissions == [job_id]
    assert catalog.active_ingestions() == ()
    catalog.close()


def test_sibling_success_failure_and_cancellation_are_independent(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path, quota=300)
    link, browser = _open_session(service)
    authorizations = [
        service.authorize_session_file(
            link.status.session_id,
            _file(key, filename=f"{key}.mp4", size=20),
            session_token=browser.session_token,
        )
        for key in ("ready", "failed", "cancelled")
    ]
    ready, failed, cancelled = authorizations
    assert ready.grant and failed.grant

    accepted_ready = service.accept_session_creation(
        ready.status.intent_id,
        grant=ready.grant,
        byte_size=20,
    )
    ready_job = service.complete_tus_transfer(
        intent_id=ready.status.intent_id,
        upload_id=accepted_ready.upload_id or "",
        byte_size=20,
        offset=20,
    )
    accepted_failed = service.accept_session_creation(
        failed.status.intent_id,
        grant=failed.grant,
        byte_size=20,
    )
    failed_job = service.complete_tus_transfer(
        intent_id=failed.status.intent_id,
        upload_id=accepted_failed.upload_id or "",
        byte_size=20,
        offset=20,
    )

    def finish_states(connection) -> None:
        ready_media_id = uuid4().hex
        connection.execute(
            insert(media_table).values(
                media_id=ready_media_id,
                sha256="f" * 64,
                created_at=utc_now().isoformat(),
                payload={},
            )
        )
        catalog.update_upload(
            ready.status.intent_id,
            state=UploadState.ready,
            job_id=ready_job,
            media_id=ready_media_id,
            connection=connection,
        )
        catalog.update_upload(
            failed.status.intent_id,
            state=UploadState.failed,
            job_id=failed_job,
            connection=connection,
        )

    catalog.with_upload_transaction(finish_states)
    service.cancel_browser_file(
        link.status.session_id,
        cancelled.status.intent_id,
        session_token=browser.session_token,
    )

    status = service.get_status(
        link.status.session_id,
        principal=Principal(subject="owner"),
    )
    by_key = {item.client_file_key: item for item in status.items}
    assert by_key["ready"].state == UploadState.ready
    assert by_key["failed"].state == UploadState.failed
    assert by_key["cancelled"].state == UploadState.expired
    assert status.aggregate_state == "partial_failure"
    assert status.ready_file_count == 1
    assert status.failed_file_count == 2
    assert len(jobs.calls) == 2
    catalog.close()


def test_upload_status_projects_processing_failed_and_ready_actions(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    session, browser = _open_session(service)
    authorized = service.authorize_session_file(
        session.status.session_id,
        _file("status-file"),
        session_token=browser.session_token,
    )
    with catalog.engine.connect() as connection:
        link = catalog.get_upload_session_file(
            session.status.session_id,
            "status-file",
            connection=connection,
        )
        intent = catalog.get_upload_intent(
            authorized.status.intent_id,
            connection=connection,
        )
    assert link is not None and intent is not None
    upload_id = uuid4().hex
    job_id = uuid4().hex

    processing = service.file_status(
        link,
        intent.model_copy(
            update={
                "state": UploadState.processing,
                "upload_id": upload_id,
                "job_id": job_id,
            }
        ),
    )
    assert processing.job_id is not None
    assert processing.phase == "importing"
    assert "ingestion status" in processing.next_action

    failed = service.file_status(
        link,
        intent.model_copy(
            update={
                "state": UploadState.failed,
                "upload_id": upload_id,
                "job_id": job_id,
            }
        ),
    )
    assert failed.phase == "failed"
    assert "structured error" in failed.next_action

    ready = service.file_status(
        link,
        intent.model_copy(
            update={
                "state": UploadState.ready,
                "upload_id": None,
                "job_id": job_id,
                "media_id": uuid4().hex,
            }
        ),
    )
    assert ready.media_id is not None
    assert "start_indexing" in ready.next_action
    catalog.close()


def test_upload_quota_is_reserved_and_released_atomically(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner")
    first = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )

    with pytest.raises(ApplicationError) as exceeded:
        service.create_intent(
            _command(),
            principal=owner,
            request_key="b" * 64,
        )
    assert exceeded.value.detail.code == "upload_quota_exceeded"

    service.accept_creation(
        first.intent_id,
        principal=owner,
        byte_size=60,
    )
    service.record_terminated(
        catalog.get_upload_intent(first.intent_id).upload_id  # type: ignore[union-attr]
    )
    second = service.create_intent(
        _command(),
        principal=owner,
        request_key="b" * 64,
    )
    assert second.byte_size == 60
    catalog.close()


def test_duplicate_finish_enqueues_one_import_job(tmp_path: Path) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None

    first = service.complete_tus_transfer(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    second = service.complete_tus_transfer(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )

    assert first == second == accepted.upload_id
    assert jobs.calls == [(accepted.upload_id, accepted.upload_id)]
    catalog.close()


def test_creation_hook_retry_replays_only_before_tusd_materializes_upload(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )

    first = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    replay = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )

    assert first.upload_id == replay.upload_id
    assert first.upload_id is not None
    service.settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    (service.settings.quarantine_root / f"{first.upload_id}.info").write_text(
        "{}",
        encoding="utf-8",
    )
    with pytest.raises(ApplicationError) as duplicate:
        service.accept_creation(
            intent.intent_id,
            principal=owner,
            byte_size=60,
        )
    assert duplicate.value.detail.code == "upload_already_created"
    catalog.close()


def test_termination_reserves_state_before_finish_can_enqueue(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None

    service.authorize_termination(
        accepted.upload_id,
        cleanup_token=None,
    )

    with pytest.raises(ApplicationError) as rejected:
        service.complete_tus_transfer(
            intent_id=intent.intent_id,
            upload_id=accepted.upload_id,
            byte_size=60,
            offset=60,
        )
    assert rejected.value.detail.code == "upload_completion_invalid"
    assert jobs.calls == []
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None and stored.state == UploadState.expired
    catalog.close()


def test_expired_creation_persists_state_and_releases_quota(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    now = utc_now()
    record = UploadIntentRecord(
        intent_id=uuid4().hex,
        request_key="a" * 64,
        original_filename="sample.mp4",
        byte_size=60,
        declared_mime_type="video/mp4",
        state=UploadState.pending,
        created_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
    )
    catalog.create_upload_intent(record, quota_limit=100)

    with pytest.raises(ApplicationError) as expired:
        service.accept_creation(
            record.intent_id,
            principal=Principal(subject="owner"),
            byte_size=60,
        )

    assert expired.value.detail.code == "upload_intent_expired"
    stored = catalog.get_upload_intent(record.intent_id)
    assert stored is not None and stored.state == UploadState.expired
    service.create_intent(
        _command(),
        principal=Principal(subject="owner"),
        request_key="b" * 64,
    )
    catalog.close()


def test_split_tus_probe_expires_intent_but_retains_incomplete_tus_resource(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    now = utc_now()
    upload_id = "2" * 32
    record = UploadIntentRecord(
        intent_id=uuid4().hex,
        request_key="a" * 64,
        original_filename="sample.mp4",
        byte_size=60,
        declared_mime_type="video/mp4",
        state=UploadState.accepted,
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
        upload_id=upload_id,
    )
    catalog.create_upload_intent(record, quota_limit=100)
    api_quarantine = tmp_path / "api-has-no-quarantine"
    service = RemoteUploadService(
        settings=service.settings.model_copy(
            update={"upload_quarantine_root": api_quarantine}
        ),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
        tusd_upload_probe=lambda probed_id: TusUploadProbe(
            upload_id=probed_id,
            length=60,
            offset=10,
        ),
    )
    result = service.reconcile()

    assert result["expired"] == 1
    assert result["cleaned"] == 0
    assert not api_quarantine.exists()
    stored = catalog.get_upload_intent(record.intent_id)
    assert stored is not None and stored.state == UploadState.expired
    assert stored.upload_id is None
    assert catalog.cleanup_uploads() == ()
    service.create_intent(
        _command(),
        principal=Principal(subject="owner"),
        request_key="b" * 64,
    )
    catalog.close()


def test_split_tus_probe_recovers_missed_finish_without_quarantine(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None
    api_quarantine = tmp_path / "api-has-no-quarantine"
    service = RemoteUploadService(
        settings=service.settings.model_copy(
            update={"upload_quarantine_root": api_quarantine}
        ),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
        tusd_upload_probe=lambda upload_id: TusUploadProbe(
            upload_id=upload_id,
            length=60,
            offset=60,
        ),
    )

    result = service.reconcile()

    assert result["recovered"] == 1
    assert not api_quarantine.exists()
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None and stored.state == UploadState.processing
    assert stored.job_id == accepted.upload_id
    catalog.close()


def test_missing_tus_upload_expires_but_unavailable_tusd_does_not(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path, quota=200)
    now = utc_now()
    records = tuple(
        UploadIntentRecord(
            intent_id=uuid4().hex,
            request_key=key * 64,
            original_filename=f"{key}.mp4",
            byte_size=60,
            declared_mime_type="video/mp4",
            state=UploadState.accepted,
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
            upload_id=key * 32,
        )
        for key in ("a", "b")
    )
    for record in records:
        catalog.create_upload_intent(record, quota_limit=200)

    def probe(upload_id: str) -> TusUploadProbe | None:
        if upload_id == records[1].upload_id:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "The upload service is temporarily unavailable.",
            )
        return None

    service = RemoteUploadService(
        settings=service.settings.model_copy(
            update={
                "upload_quarantine_root": tmp_path / "not-mounted",
                "upload_internal_endpoint": None,
            }
        ),
        catalog=catalog,
        media=_Media(),
        jobs=jobs,
        tusd_upload_probe=probe,
    )

    result = service.reconcile()

    missing = catalog.get_upload_intent(records[0].intent_id)
    unavailable = catalog.get_upload_intent(records[1].intent_id)
    assert result["expired"] == 1
    assert result["errors"] == 1
    assert missing is not None and missing.state == UploadState.expired
    assert unavailable is not None and unavailable.state == UploadState.accepted
    catalog.close()


def test_concurrent_idempotency_winner_must_match_request(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    original_create = catalog.create_upload_intent

    def lose_race(record, *, quota_limit):
        winner = record.model_copy(
            update={
                "intent_id": uuid4().hex,
                "original_filename": "other.mp4",
            }
        )
        original_create(winner, quota_limit=quota_limit)
        raise IntegrityError("insert upload", {}, RuntimeError("duplicate"))

    with patch.object(catalog, "create_upload_intent", side_effect=lose_race):
        with pytest.raises(ApplicationError) as conflict:
            service.create_intent(
                _command(),
                principal=Principal(subject="owner"),
                request_key="a" * 64,
            )

    assert conflict.value.detail.code == "idempotency_key_reused"
    catalog.close()


def test_terminal_import_job_releases_processing_upload(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None
    job_id = service.complete_tus_transfer(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    jobs.states[job_id] = JobState.cancelled

    result = service.reconcile()

    assert result["advanced"] == 1
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None and stored.state == UploadState.failed
    assert stored.media_id is None
    assert stored.failure_code == "media_import_failed"
    catalog.close()


def test_late_import_callback_cannot_import_terminal_failed_upload(
    tmp_path: Path,
) -> None:
    service, catalog, jobs = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None
    job_id = service.complete_tus_transfer(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    catalog.with_upload_transaction(
        lambda connection: catalog.update_upload(
            intent.intent_id,
            state=UploadState.failed,
            connection=connection,
            failure_code="media_import_failed",
            failure_message="The durable media import failed.",
            expected_states={UploadState.processing},
            expected_job_id=job_id,
        )
    )
    service.settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    (service.settings.quarantine_root / accepted.upload_id).write_bytes(b"x" * 60)
    importer = Mock()
    service.media = SimpleNamespace(import_quarantined=importer)

    with pytest.raises(ApplicationError) as rejected:
        service.import_completed(accepted.upload_id)

    assert rejected.value.detail.code == "upload_not_ready"
    importer.assert_not_called()
    with catalog.engine.connect() as connection:
        assert (
            connection.execute(
                select(func.count()).select_from(media_table)
            ).scalar_one()
            == 0
        )
    assert jobs.calls == [(accepted.upload_id, accepted.upload_id)]
    catalog.close()


def test_cleanup_reconciles_tusd_not_found_and_clears_capability_url(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner")
    intent = service.create_intent(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    accepted = service.accept_creation(
        intent.intent_id,
        principal=owner,
        byte_size=60,
    )
    assert accepted.upload_id is not None
    service.authorize_termination(
        accepted.upload_id,
        cleanup_token=None,
    )
    missing = HTTPError(
        url="http://localhost/uploads/missing",
        code=404,
        msg="not found",
        hdrs=None,
        fp=None,
    )

    with patch("vidxp.upload_service.urlopen", side_effect=missing):
        result = service.reconcile()

    assert result["cleaned"] == 1
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None
    assert stored.state == UploadState.expired
    assert stored.upload_id is None
    catalog.close()
