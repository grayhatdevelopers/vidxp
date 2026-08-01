from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from hmac import compare_digest
from pathlib import Path
from typing import Any, Callable
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
    MediaUploadStatus,
    Principal,
    UploadIntent,
)
from vidxp.core.media import QuarantinedMedia, utc_now
from vidxp.core.uploads import (
    UploadHandoffRecord,
    UploadIntentRecord,
    UploadState,
)
from vidxp.infrastructure.sql_catalog import (
    SQLCatalog,
    UploadQuotaExceededError,
)
from vidxp.media_service import MediaService
from vidxp.settings import HttpAuthMode, VidXPSettings

LOGGER = logging.getLogger(__name__)
_CREATION_GRANT_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class UploadHandoff:
    status: MediaUploadStatus
    capability: str
    expires_at: datetime


@dataclass(frozen=True)
class UploadBrowserSession:
    status: MediaUploadStatus
    session_token: str
    session_expires_at: datetime
    creation_url: str
    resume_url: str | None


@dataclass(frozen=True)
class UploadCreationGrant:
    token: str
    expires_at: datetime


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


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
        tusd_upload_exists: Callable[[str], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.media = media
        self.jobs = jobs
        self._tusd_upload_exists = tusd_upload_exists

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
                "The deployment upload quota would be exceeded.",
            ) from exc
        except IntegrityError:
            replay = self.catalog.get_upload_intent_by_request(request_key)
            if replay is not None:
                self._require_same_request(replay, command)
                return _public_intent(replay)
            raise

    def create_handoff(
        self,
        command: CreateUploadIntentCommand,
        *,
        principal: Principal,
        request_key: str,
    ) -> UploadHandoff:
        self._require_handoff_configured()
        intent = self.create_intent(
            command,
            principal=principal,
            request_key=request_key,
        )
        record = self.catalog.get_upload_handoff_by_intent(intent.intent_id)
        if record is None:
            now = utc_now()
            record = UploadHandoffRecord(
                selector=secrets.token_hex(16),
                intent_id=intent.intent_id,
                principal_subject=principal.subject,
                principal_client_id=principal.client_id,
                repository_binding=self._repository_binding(),
                byte_size=intent.byte_size,
                created_at=now,
                expires_at=min(
                    intent.expires_at,
                    now + timedelta(seconds=self.settings.upload_handoff_ttl_seconds),
                ),
            )
            try:
                self.catalog.create_upload_handoff(record)
            except IntegrityError:
                replay = self.catalog.get_upload_handoff_by_intent(intent.intent_id)
                if replay is None:
                    raise
                record = replay
        self._require_handoff_binding(record, intent, principal=principal)
        return UploadHandoff(
            status=self.status(intent),
            capability=self._handoff_capability(record),
            expires_at=record.expires_at,
        )

    def status(self, intent: UploadIntent) -> MediaUploadStatus:
        status, next_action = {
            UploadState.pending: (
                "Waiting for the expected video to be selected.",
                "Open the upload page and upload the declared file, then "
                "call get_media_upload again.",
            ),
            UploadState.accepted: (
                "The tus upload is accepted or still transferring.",
                "Resume or finish the upload in the browser, then call "
                "get_media_upload again.",
            ),
            UploadState.processing: (
                "The uploaded video is being validated and imported.",
                "Call get_job with job_id to inspect import progress, and "
                "continue polling get_media_upload.",
            ),
            UploadState.ready: (
                "The video is registered and ready for indexing.",
                "Call start_indexing with media_id.",
            ),
            UploadState.failed: (
                "Validation or durable import failed.",
                "Call get_job with job_id to inspect the failure, then create "
                "a new media upload with a new idempotency key.",
            ),
            UploadState.expired: (
                "The upload intent expired before it became ready.",
                "Call create_media_upload with a new idempotency key.",
            ),
        }[intent.state]
        return MediaUploadStatus(
            intent_id=intent.intent_id,
            state=intent.state,
            original_filename=intent.original_filename,
            byte_size=intent.byte_size,
            declared_mime_type=intent.declared_mime_type,
            maximum_bytes=self.settings.upload_max_bytes,
            expires_at=intent.expires_at,
            job_id=intent.job_id,
            media_id=intent.media_id,
            status=status,
            next_action=next_action,
        )

    def get_status(
        self,
        intent_id: str,
        *,
        principal: Principal,
    ) -> MediaUploadStatus:
        return self.status(self.get_intent(intent_id, principal=principal))

    def exchange_handoff(
        self,
        intent_id: str,
        *,
        capability: str,
        current_session: str | None = None,
    ) -> UploadBrowserSession:
        return self._exchange_browser_session(
            intent_id,
            record_loader=lambda connection: self._handoff_from_capability(
                capability,
                connection=connection,
                for_update=True,
            ),
            current_session=current_session,
        )

    def exchange_authenticated_handoff(
        self,
        intent_id: str,
        *,
        principal: Principal,
        current_session: str | None = None,
    ) -> UploadBrowserSession:
        """Create the browser session after independently verified OIDC auth."""
        if self.settings.http_auth_mode != HttpAuthMode.oidc:
            raise ApplicationError(
                "upload_handoff_identity_unavailable",
                ErrorCategory.unavailable,
                "Identity-bound browser handoffs require OIDC authentication.",
            )
        return self._exchange_browser_session(
            intent_id,
            record_loader=lambda connection: (
                self.catalog.get_upload_handoff_by_intent(
                    intent_id,
                    connection=connection,
                    for_update=True,
                )
            ),
            principal=principal,
            current_session=current_session,
        )

    def _exchange_browser_session(
        self,
        intent_id: str,
        *,
        record_loader: Callable[[Connection], UploadHandoffRecord | None],
        current_session: str | None,
        principal: Principal | None = None,
    ) -> UploadBrowserSession:
        now = utc_now()

        def exchange(connection: Connection) -> tuple[str, UploadIntentRecord]:
            record = record_loader(connection)
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            self._verify_handoff(
                record,
                intent=intent,
                expected_intent_id=intent_id,
                now=now,
            )
            assert record is not None and intent is not None
            if principal is not None:
                self._require_browser_principal(record, principal)
            if intent.state != UploadState.pending:
                raise ApplicationError(
                    "upload_handoff_replayed",
                    ErrorCategory.conflict,
                    "This handoff already started an upload. Reopen the "
                    "existing browser session or inspect its status through "
                    "get_media_upload.",
                )
            if (
                current_session is not None
                and record.session_digest is not None
                and compare_digest(
                    _token_digest(current_session),
                    record.session_digest,
                )
            ):
                return current_session, intent
            session = secrets.token_urlsafe(32)
            self.catalog.set_upload_handoff_session(
                record.selector,
                session_digest=_token_digest(session),
                connection=connection,
            )
            return session, intent

        session, record = self.catalog.with_upload_transaction(exchange)
        intent = _public_intent(record)
        assert self.settings.upload_public_endpoint is not None
        return UploadBrowserSession(
            status=self.status(intent),
            session_token=session,
            session_expires_at=intent.expires_at,
            creation_url=self.settings.upload_public_endpoint,
            resume_url=None,
        )

    def browser_session(
        self,
        intent_id: str,
        *,
        session_token: str | None,
    ) -> UploadBrowserSession:
        record, intent = self._require_browser_session(
            intent_id,
            session_token=session_token,
        )
        del record
        public = _public_intent(intent)
        assert self.settings.upload_public_endpoint is not None
        resume_url = (
            self.settings.upload_public_endpoint + (intent.upload_id or "")
            if self._tusd_creation_exists(intent)
            else None
        )
        return UploadBrowserSession(
            status=self.status(public),
            session_token=session_token or "",
            session_expires_at=intent.expires_at,
            creation_url=self.settings.upload_public_endpoint,
            resume_url=resume_url,
        )

    def issue_creation_grant(
        self,
        intent_id: str,
        *,
        session_token: str | None,
    ) -> UploadCreationGrant:
        now = utc_now()

        def issue(connection: Connection) -> UploadCreationGrant:
            record = self.catalog.get_upload_handoff_by_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            self._validate_browser_session(
                record,
                intent,
                session_token=session_token,
                now=now,
            )
            assert record is not None and intent is not None
            if intent.state != UploadState.pending and self._tusd_creation_exists(
                intent
            ):
                raise ApplicationError(
                    "upload_handoff_replayed",
                    ErrorCategory.conflict,
                    "The tus upload was already created. Resume its existing "
                    "URL from the page status response.",
                )
            expires_at = min(
                intent.expires_at,
                now + timedelta(seconds=_CREATION_GRANT_TTL_SECONDS),
            )
            token = secrets.token_urlsafe(48)
            self.catalog.set_upload_creation_grant(
                record.selector,
                digest=_token_digest(token),
                expires_at=expires_at,
                connection=connection,
            )
            return UploadCreationGrant(token=token, expires_at=expires_at)

        return self.catalog.with_upload_transaction(issue)

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
            return self._accept_creation_in_transaction(
                intent_id,
                byte_size=byte_size,
                now=now,
                connection=connection,
            )

        accepted = self.catalog.with_upload_transaction(accept)
        if accepted is None:
            raise ApplicationError(
                "upload_intent_expired",
                ErrorCategory.validation,
                "The upload intent has expired.",
            )
        return accepted

    def accept_handoff_creation(
        self,
        intent_id: str,
        *,
        grant: str,
        byte_size: int,
    ) -> UploadIntentRecord:
        now = utc_now()

        def accept(connection: Connection) -> UploadIntentRecord | None:
            handoff = self.catalog.get_upload_handoff_by_creation_grant(
                _token_digest(grant),
                connection=connection,
                for_update=True,
            )
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            self._verify_creation_grant(
                handoff,
                intent,
                grant=grant,
                expected_intent_id=intent_id,
                byte_size=byte_size,
                now=now,
            )
            assert intent is not None
            if intent.state != UploadState.pending and self._tusd_creation_exists(
                intent
            ):
                raise ApplicationError(
                    "upload_handoff_replayed",
                    ErrorCategory.conflict,
                    "The tus upload was already created. Resume its existing URL.",
                )
            assert handoff is not None
            accepted = self._accept_creation_in_transaction(
                intent_id,
                byte_size=byte_size,
                now=now,
                connection=connection,
            )
            self.catalog.consume_upload_creation_grant(
                handoff.selector,
                consumed_at=now,
                connection=connection,
            )
            return accepted

        accepted = self.catalog.with_upload_transaction(accept)
        if accepted is None:
            raise ApplicationError(
                "upload_intent_expired",
                ErrorCategory.validation,
                "The upload intent has expired.",
            )
        return accepted

    def _tusd_creation_exists(self, intent: UploadIntentRecord) -> bool:
        if intent.state == UploadState.pending or intent.upload_id is None:
            return False
        if self._tusd_upload_exists is not None:
            return self._tusd_upload_exists(intent.upload_id)

        endpoint = self.settings.upload_internal_endpoint
        if endpoint is None:
            raise RuntimeError("Remote upload probing is not configured.")
        request = Request(endpoint + intent.upload_id, method="HEAD")
        request.add_header("Tus-Resumable", "1.0.0")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status == 200
        except HTTPError as exc:
            if exc.code == 404:
                return False
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "The upload service could not verify the resumable upload.",
            ) from exc
        except URLError as exc:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "The upload service is temporarily unavailable.",
            ) from exc

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

    def _accept_creation_in_transaction(
        self,
        intent_id: str,
        *,
        byte_size: int,
        now: datetime,
        connection: Connection,
    ) -> UploadIntentRecord | None:
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
            if self._quarantine_path(f"{record.upload_id}.info").exists():
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

    def _secret(self) -> str:
        secret = self.settings.upload_handoff_secret
        if secret is None:
            raise ApplicationError(
                "remote_upload_handoff_unavailable",
                ErrorCategory.unavailable,
                "Browser upload handoffs are not configured.",
            )
        return secret.get_secret_value()

    def _repository_binding(self) -> str:
        root = str(self.settings.repository_root.resolve()).replace("\\", "/")
        return hashlib.sha256(
            f"vidxp-upload-repository-v1\0{root}".encode("utf-8")
        ).hexdigest()

    def _handoff_capability(self, record: UploadHandoffRecord) -> str:
        import jwt

        return jwt.encode(
            {
                "iss": "vidxp",
                "aud": "vidxp-upload-handoff",
                "sub": record.intent_id,
                "jti": record.selector,
                "purpose": "upload-handoff",
                "repository": record.repository_binding,
                "size": record.byte_size,
                "iat": int(record.created_at.timestamp()),
                "exp": int(record.expires_at.timestamp()),
            },
            self._secret(),
            algorithm="HS256",
        )

    def _handoff_from_capability(
        self,
        capability: str,
        *,
        connection: Connection,
        for_update: bool,
    ) -> UploadHandoffRecord | None:
        import jwt

        try:
            claims = jwt.decode(
                capability,
                self._secret(),
                algorithms=["HS256"],
                audience="vidxp-upload-handoff",
                issuer="vidxp",
                options={
                    "verify_exp": False,
                    "require": ["sub", "jti", "purpose", "repository", "size", "iat", "exp"],
                },
            )
        except jwt.PyJWTError as exc:
            raise self._invalid_handoff() from exc
        selector = claims.get("jti")
        if (
            not isinstance(selector, str)
            or len(selector) != 32
            or any(character not in "0123456789abcdef" for character in selector)
        ):
            raise self._invalid_handoff()
        record = self.catalog.get_upload_handoff(
            selector,
            connection=connection,
            for_update=for_update,
        )
        if (
            record is None
            or claims.get("sub") != record.intent_id
            or claims.get("purpose") != "upload-handoff"
            or claims.get("repository") != record.repository_binding
            or claims.get("size") != record.byte_size
            or claims.get("iat") != int(record.created_at.timestamp())
            or claims.get("exp") != int(record.expires_at.timestamp())
        ):
            raise self._invalid_handoff()
        return record

    def _verify_handoff(
        self,
        record: UploadHandoffRecord | None,
        *,
        intent: UploadIntentRecord | None,
        expected_intent_id: str,
        now: datetime,
    ) -> None:
        if (
            record is None
            or intent is None
            or record.intent_id != expected_intent_id
            or intent.intent_id != expected_intent_id
            or record.repository_binding != self._repository_binding()
            or record.byte_size != intent.byte_size
        ):
            raise self._invalid_handoff()
        if record.expires_at <= now or intent.expires_at <= now:
            raise ApplicationError(
                "upload_handoff_expired",
                ErrorCategory.authentication,
                "The upload handoff expired. Create a new media upload.",
            )

    def _require_handoff_binding(
        self,
        record: UploadHandoffRecord,
        intent: UploadIntent,
        *,
        principal: Principal,
    ) -> None:
        if (
            record.intent_id != intent.intent_id
            or record.principal_subject != principal.subject
            or record.principal_client_id != principal.client_id
            or record.repository_binding != self._repository_binding()
            or record.byte_size != intent.byte_size
        ):
            raise RuntimeError("The stored upload handoff binding is invalid.")

    @staticmethod
    def _require_browser_principal(
        record: UploadHandoffRecord | None,
        principal: Principal,
    ) -> None:
        if record is None or record.principal_subject != principal.subject:
            raise ApplicationError(
                "upload_handoff_identity_mismatch",
                ErrorCategory.authorization,
                "The authenticated browser user does not own this upload handoff.",
            )

    def _require_browser_session(
        self,
        intent_id: str,
        *,
        session_token: str | None,
    ) -> tuple[UploadHandoffRecord, UploadIntentRecord]:
        record = self.catalog.get_upload_handoff_by_intent(intent_id)
        intent = self.catalog.get_upload_intent(intent_id)
        self._validate_browser_session(
            record,
            intent,
            session_token=session_token,
            now=utc_now(),
        )
        assert record is not None and intent is not None
        return record, intent

    def _validate_browser_session(
        self,
        record: UploadHandoffRecord | None,
        intent: UploadIntentRecord | None,
        *,
        session_token: str | None,
        now: datetime,
    ) -> None:
        if (
            record is None
            or intent is None
            or session_token is None
            or record.session_digest is None
            or record.intent_id != intent.intent_id
            or record.repository_binding != self._repository_binding()
            or record.byte_size != intent.byte_size
            or not compare_digest(
                record.session_digest,
                _token_digest(session_token),
            )
        ):
            raise ApplicationError(
                "upload_handoff_session_invalid",
                ErrorCategory.authentication,
                "The browser upload session is missing or invalid. Reopen "
                "the original handoff URL.",
            )
        if intent.expires_at <= now or intent.state == UploadState.expired:
            raise ApplicationError(
                "upload_handoff_expired",
                ErrorCategory.authentication,
                "The browser upload session expired. Create a new media upload.",
            )

    def _verify_creation_grant(
        self,
        record: UploadHandoffRecord | None,
        intent: UploadIntentRecord | None,
        *,
        grant: str,
        expected_intent_id: str,
        byte_size: int,
        now: datetime,
    ) -> None:
        if (
            record is None
            or intent is None
            or record.session_digest is None
            or record.intent_id != expected_intent_id
            or intent.intent_id != expected_intent_id
            or record.repository_binding != self._repository_binding()
            or record.byte_size != byte_size
            or intent.byte_size != byte_size
            or record.creation_grant_digest is None
            or record.creation_grant_expires_at is None
            or not compare_digest(
                record.creation_grant_digest,
                _token_digest(grant),
            )
        ):
            raise self._invalid_creation_grant()
        if record.creation_grant_expires_at <= now or intent.expires_at <= now:
            raise ApplicationError(
                "upload_creation_grant_expired",
                ErrorCategory.authentication,
                "The upload creation grant expired. Request a new grant from "
                "the browser page.",
            )
        if record.creation_grant_consumed_at is not None:
            raise ApplicationError(
                "upload_handoff_replayed",
                ErrorCategory.conflict,
                "The upload creation grant was already used. Resume the existing "
                "upload from its browser page.",
            )

    @staticmethod
    def _invalid_handoff() -> ApplicationError:
        return ApplicationError(
            "upload_handoff_invalid",
            ErrorCategory.authentication,
            "The upload handoff is invalid for this intent or repository.",
        )

    @staticmethod
    def _invalid_creation_grant() -> ApplicationError:
        return ApplicationError(
            "upload_creation_grant_invalid",
            ErrorCategory.authentication,
            "The upload creation grant is invalid for this intent, size, or "
            "repository.",
        )

    def _require_configured(self) -> None:
        if self.settings.upload_public_endpoint is None:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "Remote resumable uploads are not configured.",
            )

    def _require_handoff_configured(self) -> None:
        self._require_configured()
        if (
            self.settings.upload_handoff_public_url is None
            or self.settings.upload_handoff_secret is None
        ):
            raise ApplicationError(
                "remote_upload_handoff_unavailable",
                ErrorCategory.unavailable,
                "Browser upload handoffs are not configured.",
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
