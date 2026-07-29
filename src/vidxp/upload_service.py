from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from hmac import compare_digest
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from vidxp.application_models import (
    ApplicationError,
    CreateUploadIntentCommand,
    ErrorCategory,
    JobState,
    Principal,
    UploadIntent,
)
from vidxp.core.media import QuarantinedMedia, utc_now
from vidxp.core.uploads import UploadIntentRecord, UploadState
from vidxp.infrastructure.sql_catalog import (
    SQLCatalog,
    UploadQuotaExceededError,
)
from vidxp.media_service import MediaService
from vidxp.settings import VidXPSettings

LOGGER = logging.getLogger(__name__)


def _public_intent(record: UploadIntentRecord) -> UploadIntent:
    return UploadIntent(
        intent_id=record.intent_id,
        original_filename=record.original_filename,
        byte_size=record.byte_size,
        declared_mime_type=record.declared_mime_type,
        state=record.state,
        created_at=record.created_at,
        expires_at=record.expires_at,
        job_id=record.job_id,
        media_id=record.media_id,
    )


class RemoteUploadService:
    """Intent policy and minimal glue around tusd, DBOS, and media import."""

    def __init__(
        self,
        *,
        settings: VidXPSettings,
        catalog: SQLCatalog,
        media: MediaService | None,
        jobs: Any | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.media = media
        self.jobs = jobs

    def create_intent(
        self,
        command: CreateUploadIntentCommand,
        *,
        principal: Principal,
        request_key: str,
    ) -> UploadIntent:
        self._require_configured()
        del principal
        if command.byte_size > self.settings.upload_max_bytes:
            raise ApplicationError(
                "upload_too_large",
                ErrorCategory.resource_limit,
                "The requested upload exceeds the configured limit.",
            )
        if existing := self.catalog.get_upload_intent_by_request(request_key):
            self._require_same_request(existing, command)
            return _public_intent(existing)
        now = utc_now()
        record = UploadIntentRecord(
            intent_id=uuid4().hex,
            request_key=request_key,
            original_filename=command.original_filename,
            byte_size=command.byte_size,
            declared_mime_type=command.declared_mime_type,
            state=UploadState.pending,
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.settings.upload_intent_ttl_seconds),
        )
        try:
            return _public_intent(
                self.catalog.create_upload_intent(
                    record,
                    quota_limit=self.settings.upload_quota_bytes,
                )
            )
        except UploadQuotaExceededError as exc:
            raise ApplicationError(
                "upload_quota_exceeded",
                ErrorCategory.resource_limit,
                "The principal upload quota would be exceeded.",
            ) from exc
        except IntegrityError:
            replay = self.catalog.get_upload_intent_by_request(request_key)
            if replay is not None:
                self._require_same_request(replay, command)
                return _public_intent(replay)
            raise

    def get_intent(
        self,
        intent_id: str,
        *,
        principal: Principal,
    ) -> UploadIntent:
        del principal
        record = self.catalog.get_upload_intent(intent_id)
        if record is None:
            raise ApplicationError(
                "resource_not_found",
                ErrorCategory.not_found,
                "The requested upload intent was not found.",
            )
        return _public_intent(record)

    def upload_url(
        self,
        intent_id: str,
        *,
        principal: Principal,
    ) -> str | None:
        del principal
        record = self.catalog.get_upload_intent(intent_id)
        if record is None:
            raise ApplicationError(
                "resource_not_found",
                ErrorCategory.not_found,
                "The requested upload intent was not found.",
            )
        if record.state != UploadState.accepted or record.upload_id is None:
            return None
        assert self.settings.upload_public_endpoint is not None
        return self.settings.upload_public_endpoint + record.upload_id

    def accept_creation(
        self,
        intent_id: str,
        *,
        principal: Principal,
        byte_size: int,
    ) -> UploadIntentRecord:
        del principal
        now = utc_now()

        def accept(connection: Connection) -> UploadIntentRecord | None:
            record = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if record is None:
                raise ApplicationError(
                    "upload_intent_invalid",
                    ErrorCategory.validation,
                    "The upload intent is invalid.",
                )
            if record.expires_at <= now:
                self.catalog.update_upload(
                    record.intent_id,
                    state=UploadState.expired,
                    connection=connection,
                )
                return None
            if byte_size != record.byte_size:
                raise ApplicationError(
                    "upload_size_mismatch",
                    ErrorCategory.validation,
                    "The upload size does not match its intent.",
                )
            if record.state == UploadState.accepted:
                assert record.upload_id is not None
                if self._quarantine_path(
                    f"{record.upload_id}.info"
                ).exists():
                    raise ApplicationError(
                        "upload_already_created",
                        ErrorCategory.conflict,
                        "The upload was already created; resume its existing URL.",
                    )
                return record
            if record.state != UploadState.pending:
                raise ApplicationError(
                    "upload_intent_consumed",
                    ErrorCategory.conflict,
                    "The upload intent has already been consumed.",
                )
            upload_id = uuid4().hex
            self.catalog.update_upload(
                record.intent_id,
                state=UploadState.accepted,
                connection=connection,
                upload_id=upload_id,
            )
            return record.model_copy(
                update={
                    "state": UploadState.accepted,
                    "upload_id": upload_id,
                }
            )

        accepted = self.catalog.with_upload_transaction(accept)
        if accepted is None:
            raise ApplicationError(
                "upload_intent_expired",
                ErrorCategory.validation,
                "The upload intent has expired.",
            )
        return accepted

    def complete_upload(
        self,
        *,
        intent_id: str,
        upload_id: str,
        byte_size: int,
        offset: int,
    ) -> str:
        if self.jobs is None:
            raise RuntimeError("Upload job submission is not configured.")

        def complete(connection: Connection) -> str:
            record = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if (
                record is None
                or record.upload_id != upload_id
            ):
                raise ApplicationError(
                    "upload_completion_invalid",
                    ErrorCategory.validation,
                    "The completed upload does not match its intent.",
                )
            if byte_size != record.byte_size or offset != record.byte_size:
                raise ApplicationError(
                    "upload_incomplete",
                    ErrorCategory.validation,
                    "The upload is not complete.",
                )
            if record.job_id is not None:
                return record.job_id
            if record.state != UploadState.accepted:
                raise ApplicationError(
                    "upload_completion_invalid",
                    ErrorCategory.conflict,
                    "The upload is not ready for completion.",
                )
            job_id = self.jobs.enqueue_media_import_in_transaction(
                upload_id,
                connection=connection,
                job_id=upload_id,
            )
            self.catalog.update_upload(
                record.intent_id,
                state=UploadState.processing,
                connection=connection,
                job_id=job_id,
            )
            return job_id

        return self.catalog.with_upload_transaction(complete)

    def import_completed(
        self,
        upload_id: str,
    ):
        if self.media is None:
            raise RuntimeError("Media import is not configured in this process.")
        record = self.catalog.get_upload_intent_by_upload_id(upload_id)
        if record is None:
            raise ApplicationError(
                "upload_not_found",
                ErrorCategory.not_found,
                "The completed upload is unavailable.",
            )
        if record.state == UploadState.ready and record.media_id is not None:
            asset = self.media.get(record.media_id)
            self._cleanup_after_import(upload_id)
            return asset
        if record.state not in {
            UploadState.processing,
            UploadState.failed,
        }:
            raise ApplicationError(
                "upload_not_ready",
                ErrorCategory.conflict,
                "The upload is not ready for import.",
            )
        path = self._quarantine_path(upload_id)
        request_key = hashlib.sha256(
            f"vidxp-upload-import-v1\0{record.intent_id}".encode()
        ).hexdigest()
        try:
            asset = self.media.import_quarantined(
                QuarantinedMedia(
                    path=path,
                    original_filename=record.original_filename,
                    declared_mime_type=record.declared_mime_type,
                ),
                request_key=request_key,
            )
        except Exception:
            self.catalog.with_upload_transaction(
                lambda connection: self.catalog.update_upload(
                    record.intent_id,
                    state=UploadState.failed,
                    connection=connection,
                )
            )
            raise
        self.catalog.with_upload_transaction(
            lambda connection: self.catalog.update_upload(
                record.intent_id,
                state=UploadState.ready,
                connection=connection,
                media_id=asset.media_id,
            )
        )
        self._cleanup_after_import(upload_id)
        return asset

    def reconcile(self) -> dict[str, int]:
        recovered = 0
        errors = 0
        for record in self.catalog.recoverable_uploads():
            try:
                if record.upload_id is None:
                    continue
                info = self._read_upload_info(record.upload_id)
                if info is None:
                    continue
                metadata = info.get("MetaData")
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("intent_id") != record.intent_id:
                    continue
                size = info.get("Size")
                offset = info.get("Offset")
                if (
                    isinstance(size, int)
                    and isinstance(offset, int)
                    and size == offset == record.byte_size
                ):
                    self.complete_upload(
                        intent_id=record.intent_id,
                        upload_id=record.upload_id,
                        byte_size=size,
                        offset=offset,
                    )
                    recovered += 1
            except Exception:
                errors += 1
                LOGGER.exception(
                    "Upload recovery failed for intent %s.",
                    record.intent_id,
                )
        failed = 0
        if self.jobs is not None:
            for record in self.catalog.processing_uploads():
                try:
                    assert record.job_id is not None
                    job = self.jobs.get(record.job_id)
                    if job.state in {
                        JobState.failed,
                        JobState.cancelled,
                        JobState.recovery_exhausted,
                    }:
                        changed = self.catalog.with_upload_transaction(
                            lambda connection, item=record: (
                                self.catalog.update_upload(
                                    item.intent_id,
                                    state=UploadState.failed,
                                    connection=connection,
                                    expected_states={
                                        UploadState.processing,
                                    },
                                )
                            )
                        )
                        failed += int(changed)
                except Exception:
                    errors += 1
                    LOGGER.exception(
                        "Upload job reconciliation failed for intent %s.",
                        record.intent_id,
                    )
        expired = 0
        now = utc_now()
        for record in self.catalog.expired_uploads(now=now):
            try:
                if (
                    record.state == UploadState.accepted
                    and record.upload_id is not None
                    and self._upload_is_active(record.upload_id, now=now)
                ):
                    continue
                upload_id = self._expire_intent(record.intent_id)
                if upload_id is not None:
                    self._cleanup_upload(upload_id)
                expired += 1
            except Exception:
                errors += 1
                LOGGER.exception(
                    "Upload expiration failed for intent %s.",
                    record.intent_id,
                )
        cleaned = 0
        for record in self.catalog.cleanup_uploads():
            assert record.upload_id is not None
            try:
                self._cleanup_upload(record.upload_id)
                cleaned += 1
            except Exception:
                errors += 1
                LOGGER.exception(
                    "Upload cleanup failed for intent %s.",
                    record.intent_id,
                )
        return {
            "recovered": recovered,
            "expired": expired,
            "cleaned": cleaned,
            "failed": failed,
            "errors": errors,
        }

    def authorize_termination(
        self,
        upload_id: str,
        *,
        cleanup_token: str | None,
    ) -> None:
        def authorize(connection: Connection) -> None:
            record = self.catalog.get_upload_intent_by_upload_id(
                upload_id,
                connection=connection,
                for_update=True,
            )
            if record is None:
                raise ApplicationError(
                    "upload_not_found",
                    ErrorCategory.not_found,
                    "The upload is unavailable.",
                )
            expected = self.settings.upload_cleanup_token
            internal = (
                expected is not None
                and cleanup_token is not None
                and compare_digest(
                    expected.get_secret_value(),
                    cleanup_token,
                )
            )
            if record.state == UploadState.accepted:
                self.catalog.update_upload(
                    record.intent_id,
                    state=UploadState.expired,
                    connection=connection,
                )
                return
            if internal and record.state in {
                UploadState.ready,
                UploadState.failed,
                UploadState.expired,
            }:
                if record.state == UploadState.failed:
                    self.catalog.update_upload(
                        record.intent_id,
                        state=UploadState.expired,
                        connection=connection,
                    )
                return
            raise ApplicationError(
                "upload_termination_forbidden",
                ErrorCategory.conflict,
                "The upload cannot be terminated in its current state.",
            )

        self.catalog.with_upload_transaction(authorize)

    def record_terminated(self, upload_id: str) -> None:
        def terminate(connection: Connection) -> None:
            record = self.catalog.get_upload_intent_by_upload_id(
                upload_id,
                connection=connection,
                for_update=True,
            )
            if record is None:
                return
            if record.state == UploadState.processing:
                raise ApplicationError(
                    "upload_termination_forbidden",
                    ErrorCategory.conflict,
                    "A processing upload cannot be terminated.",
                )
            state = (
                UploadState.ready
                if record.state == UploadState.ready
                else UploadState.expired
            )
            self.catalog.update_upload(
                record.intent_id,
                state=state,
                connection=connection,
                clear_upload_id=True,
            )

        self.catalog.with_upload_transaction(terminate)

    def _expire_intent(self, intent_id: str) -> str | None:
        def expire(connection: Connection) -> str | None:
            record = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if record is None or record.state not in {
                UploadState.pending,
                UploadState.accepted,
                UploadState.failed,
            }:
                return None
            self.catalog.update_upload(
                intent_id,
                state=UploadState.expired,
                connection=connection,
            )
            return record.upload_id

        return self.catalog.with_upload_transaction(expire)

    def _upload_is_active(self, upload_id: str, *, now) -> bool:
        cutoff = now.timestamp() - self.settings.upload_intent_ttl_seconds
        for suffix in ("", ".info"):
            path = self._quarantine_path(f"{upload_id}{suffix}")
            try:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_mtime > cutoff
                ):
                    return True
            except OSError:
                continue
        return False

    def _read_upload_info(self, upload_id: str) -> dict[str, Any] | None:
        path = self._quarantine_path(f"{upload_id}.info")
        if not path.is_file() or path.is_symlink():
            return None
        try:
            with path.open("rb") as handle:
                payload = handle.read(65_537)
            if len(payload) > 65_536:
                return None
            result = json.loads(payload)
        except (OSError, json.JSONDecodeError):
            return None
        return result if isinstance(result, dict) else None

    def _quarantine_path(self, name: str) -> Path:
        if (
            not name
            or len(name) > 260
            or any(
                character
                not in "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._~-"
                for character in name
            )
        ):
            raise ApplicationError(
                "upload_identifier_invalid",
                ErrorCategory.validation,
                "The upload identifier is invalid.",
            )
        root = self.settings.quarantine_root.resolve()
        path = (root / name).resolve()
        if path.parent != root:
            raise RuntimeError("The upload escaped its quarantine root.")
        return path

    def _terminate_upload(self, upload_id: str) -> None:
        endpoint = self.settings.upload_internal_endpoint
        if endpoint is None:
            return
        request = Request(endpoint + upload_id, method="DELETE")
        request.add_header("Tus-Resumable", "1.0.0")
        if self.settings.upload_cleanup_token is not None:
            request.add_header(
                "X-VidXP-Cleanup-Token",
                self.settings.upload_cleanup_token.get_secret_value(),
            )
        try:
            with urlopen(request, timeout=5) as response:
                if response.status not in {204, 404}:
                    raise RuntimeError("tusd rejected upload cleanup.")
        except HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError("tusd upload cleanup failed.") from exc
        except URLError as exc:
            raise RuntimeError("tusd upload cleanup is unavailable.") from exc

    def _cleanup_upload(self, upload_id: str) -> None:
        self._terminate_upload(upload_id)
        self.record_terminated(upload_id)

    def _cleanup_after_import(self, upload_id: str) -> None:
        try:
            self._cleanup_upload(upload_id)
        except Exception:
            LOGGER.warning(
                "Imported upload %s is awaiting quarantine cleanup.",
                upload_id,
                exc_info=True,
            )

    def _require_configured(self) -> None:
        if self.settings.upload_public_endpoint is None:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "Remote resumable uploads are not configured.",
            )

    @staticmethod
    def _require_same_request(
        record: UploadIntentRecord,
        command: CreateUploadIntentCommand,
    ) -> None:
        if (
            record.original_filename != command.original_filename
            or record.byte_size != command.byte_size
            or record.declared_mime_type != command.declared_mime_type
        ):
            raise ApplicationError(
                "idempotency_key_reused",
                ErrorCategory.validation,
                "The idempotency key was already used for another request.",
            )
