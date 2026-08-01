from __future__ import annotations

from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import jwt
import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from vidxp.application_models import (
    ApplicationError,
    CreateUploadFileCommand,
    CreateUploadIntentCommand,
    JobState,
    Principal,
)
from vidxp.core.media import utc_now
from vidxp.core.uploads import UploadIntentRecord, UploadSessionState, UploadState
from vidxp.infrastructure.sql_catalog import SQLCatalog
from vidxp.infrastructure.sql_tables import media as media_table
from vidxp.infrastructure.sql_tables import upload_intents, upload_quota
from vidxp.settings import VidXPSettings
from vidxp.upload_service import RemoteUploadService


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
        return SimpleNamespace(state=self.states[job_id])


def _service(
    root: Path,
    *,
    quota: int = 100,
    maximum_file_bytes: int = 100,
    maximum_files: int = 3,
    maximum_session_bytes: int = 200,
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
    )
    return (
        RemoteUploadService(
            settings=settings,
            catalog=catalog,
            media=_Media(),
            jobs=jobs,
            tusd_upload_exists=lambda upload_id: (
                settings.quarantine_root / f"{upload_id}.info"
            ).exists(),
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
    ready_job = service.complete_upload(
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
    failed_job = service.complete_upload(
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
    assert "get_job" in processing.next_action

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
    assert "failure" in failed.next_action

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

    first = service.complete_upload(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    second = service.complete_upload(
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
        service.complete_upload(
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


def test_active_resumable_upload_is_not_expired(tmp_path: Path) -> None:
    service, catalog, _ = _service(tmp_path)
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
    service.settings.quarantine_root.mkdir(parents=True, exist_ok=True)
    (service.settings.quarantine_root / f"{upload_id}.info").write_text(
        '{"MetaData":{"intent_id":"'
        + record.intent_id
        + '"},"Size":60,"Offset":10}',
        encoding="utf-8",
    )
    result = service.reconcile()

    assert result["expired"] == 0
    stored = catalog.get_upload_intent(record.intent_id)
    assert stored is not None and stored.state == UploadState.accepted
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
    job_id = service.complete_upload(
        intent_id=intent.intent_id,
        upload_id=accepted.upload_id,
        byte_size=60,
        offset=60,
    )
    jobs.states[job_id] = JobState.cancelled

    result = service.reconcile()

    assert result["failed"] == 1
    stored = catalog.get_upload_intent(intent.intent_id)
    assert stored is not None and stored.state == UploadState.failed
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
