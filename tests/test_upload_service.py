from __future__ import annotations

from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

import jwt
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
        upload_handoff_public_url="https://upload.example/upload-handoff",
        upload_handoff_secret="h" * 32,
        upload_cors_origin_regex=r"^(https://upload\.example)$",
        upload_max_bytes=quota,
        upload_quota_bytes=quota,
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


def test_upload_handoff_is_idempotent_and_bound_to_intent(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    owner = Principal(subject="owner")

    first = service.create_handoff(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )
    replay = service.create_handoff(
        _command(),
        principal=owner,
        request_key="a" * 64,
    )

    assert replay == first
    assert first.status.state == UploadState.pending
    assert first.status.maximum_bytes == 100
    stored = catalog.get_upload_handoff_by_intent(first.status.intent_id)
    assert stored is not None
    assert stored.session_digest is None
    assert first.capability not in stored.model_dump_json()
    claims = jwt.decode(
        first.capability,
        options={"verify_signature": False},
    )
    assert claims["sub"] == first.status.intent_id
    assert claims["aud"] == "vidxp-upload-handoff"
    assert claims["purpose"] == "upload-handoff"
    catalog.close()


def test_handoff_rejects_tamper_wrong_intent_and_expiry(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    handoff = service.create_handoff(
        _command(),
        principal=Principal(subject="owner"),
        request_key="a" * 64,
    )
    header, payload, signature = handoff.capability.split(".")
    tampered = ".".join(
        (
            header,
            payload,
            ("A" if signature[0] != "A" else "B") + signature[1:],
        )
    )

    with pytest.raises(ApplicationError) as invalid:
        service.exchange_handoff(
            handoff.status.intent_id,
            capability=tampered,
        )
    assert invalid.value.detail.code == "upload_handoff_invalid"

    with pytest.raises(ApplicationError) as wrong_intent:
        service.exchange_handoff(
            uuid4().hex,
            capability=handoff.capability,
        )
    assert wrong_intent.value.detail.code == "upload_handoff_invalid"

    with patch(
        "vidxp.upload_service.utc_now",
        return_value=handoff.expires_at + timedelta(seconds=1),
    ):
        with pytest.raises(ApplicationError) as expired:
            service.exchange_handoff(
                handoff.status.intent_id,
                capability=handoff.capability,
            )
    assert expired.value.detail.code == "upload_handoff_expired"
    catalog.close()


def test_browser_session_grant_accepts_five_mib_and_rejects_replay(
    tmp_path: Path,
) -> None:
    size = 5 * 1024 * 1024
    service, catalog, _ = _service(tmp_path, quota=2 * size)
    command = _command(size)
    handoff = service.create_handoff(
        command,
        principal=Principal(subject="owner"),
        request_key="a" * 64,
    )
    session = service.exchange_handoff(
        handoff.status.intent_id,
        capability=handoff.capability,
    )
    grant = service.issue_creation_grant(
        handoff.status.intent_id,
        session_token=session.session_token,
    )
    assert len(grant.token) >= 64
    assert "." not in grant.token

    with pytest.raises(ApplicationError) as wrong_size:
        service.accept_handoff_creation(
            handoff.status.intent_id,
            grant=grant.token,
            byte_size=size + 1,
        )
    assert wrong_size.value.detail.code == "upload_creation_grant_invalid"

    accepted = service.accept_handoff_creation(
        handoff.status.intent_id,
        grant=grant.token,
        byte_size=size,
    )
    assert accepted.state == UploadState.accepted
    consumed = catalog.get_upload_handoff_by_intent(handoff.status.intent_id)
    assert consumed is not None
    assert consumed.creation_grant_digest is not None
    assert consumed.creation_grant_consumed_at is not None
    assert grant.token not in consumed.model_dump_json()
    page = service.browser_session(
        handoff.status.intent_id,
        session_token=session.session_token,
    )
    assert page.resume_url is None

    with pytest.raises(ApplicationError) as replayed:
        service.accept_handoff_creation(
            handoff.status.intent_id,
            grant=grant.token,
            byte_size=size,
        )
    assert replayed.value.detail.code == "upload_handoff_replayed"

    retry_grant = service.issue_creation_grant(
        handoff.status.intent_id,
        session_token=session.session_token,
    )
    retried = service.accept_handoff_creation(
        handoff.status.intent_id,
        grant=retry_grant.token,
        byte_size=size,
    )
    assert retried.upload_id == accepted.upload_id

    info_path = service.settings.quarantine_root / f"{accepted.upload_id}.info"
    info_path.parent.mkdir(parents=True, exist_ok=True)
    info_path.write_text("{}", encoding="utf-8")
    resumed_page = service.browser_session(
        handoff.status.intent_id,
        session_token=session.session_token,
    )
    assert resumed_page.resume_url is not None
    assert resumed_page.resume_url.endswith(accepted.upload_id or "")

    with pytest.raises(ApplicationError) as already_created:
        service.issue_creation_grant(
            handoff.status.intent_id,
            session_token=session.session_token,
        )
    assert already_created.value.detail.code == "upload_handoff_replayed"
    catalog.close()


def test_upload_status_projects_processing_failed_and_ready_actions(
    tmp_path: Path,
) -> None:
    service, catalog, _ = _service(tmp_path)
    created = service.create_intent(
        _command(),
        principal=Principal(subject="owner"),
        request_key="a" * 64,
    )

    processing = service.status(
        created.model_copy(
            update={"state": UploadState.processing, "job_id": uuid4().hex}
        )
    )
    assert processing.job_id is not None
    assert "get_job" in processing.next_action

    failed = service.status(
        created.model_copy(update={"state": UploadState.failed, "job_id": uuid4().hex})
    )
    assert failed.job_id is not None
    assert "get_job" in failed.next_action

    ready = service.status(
        created.model_copy(
            update={
                "state": UploadState.ready,
                "job_id": uuid4().hex,
                "media_id": uuid4().hex,
            }
        )
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
