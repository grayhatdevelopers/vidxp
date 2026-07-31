from __future__ import annotations

from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from vidxp.application_models import (
    ApplicationError,
    CreateUploadIntentCommand,
    JobState,
    Principal,
)
from vidxp.core.media import utc_now
from vidxp.core.uploads import UploadIntentRecord, UploadState
from vidxp.infrastructure.sql_catalog import SQLCatalog
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
        upload_max_bytes=quota,
        upload_quota_bytes=quota,
    )
    return (
        RemoteUploadService(
            settings=settings,
            catalog=catalog,
            media=_Media(),
            jobs=jobs,
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
