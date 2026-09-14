from __future__ import annotations

import hashlib
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

from jwt import PyJWTError

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from vidxp.application_models import (
    ApplicationError,
    CreateIndexCommand,
    CreateUploadFileCommand,
    CreateUploadIntentCommand,
    ErrorCategory,
    ErrorDetail,
    Job,
    MediaUploadSessionStatus,
    MediaUploadStatus,
    Principal,
    UploadIntent,
)
from vidxp.core.media import QuarantinedMedia, utc_now, validate_display_filename
from vidxp.core.uploads import (
    UploadIntentRecord,
    UploadSessionFileRecord,
    UploadSessionRecord,
    UploadSessionState,
    UploadState,
    UploadTransferBackend,
)
from vidxp.capability_security import (
    decode_capability,
    encode_capability,
    repository_binding,
)
from vidxp.infrastructure.sql_catalog import (
    SQLCatalog,
    UploadQuotaExceededError,
)
from vidxp.media_service import MediaService
from vidxp.ingestion_coordinator import IngestionCoordinator
from vidxp.settings import ApplicationMode, HttpAuthMode, VidXPSettings
from vidxp.upload_status import UploadStatusProjector

LOGGER = logging.getLogger(__name__)
_CREATION_GRANT_TTL_SECONDS = 5 * 60


@dataclass(frozen=True)
class UploadSessionLink:
    status: MediaUploadSessionStatus
    capability: str


@dataclass(frozen=True)
class UploadBrowserSession:
    status: MediaUploadSessionStatus
    session_token: str
    session_expires_at: datetime
    creation_url: str
    resume_urls: dict[str, str]


@dataclass(frozen=True)
class UploadFileAuthorization:
    status: MediaUploadStatus
    grant: str | None
    grant_expires_at: datetime | None
    resume_url: str | None


@dataclass(frozen=True)
class TusUploadProbe:
    """Authoritative resumable-upload state returned by tusd HEAD."""

    upload_id: str
    length: int
    offset: int


@dataclass(frozen=True)
class _UploadSessionRequest:
    purpose: str
    inputs: tuple[str, ...] | None
    index_after_import: bool
    index_modalities: tuple[str, ...]
    initiating_subject: str
    initiating_client_id: str | None
    repository_binding: str


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    """Durable orchestration for browser and local-path media ingestion."""

    def __init__(
        self,
        *,
        settings: VidXPSettings,
        catalog: SQLCatalog,
        media: MediaService | None,
        jobs: Any | None = None,
        tusd_upload_probe: Callable[[str], TusUploadProbe | None] | None = None,
        default_index_modalities: tuple[str, ...] = (),
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.media = media
        self.jobs = jobs
        self._tusd_upload_probe = tusd_upload_probe
        self.default_index_modalities = default_index_modalities
        self.status = UploadStatusProjector(catalog=catalog, jobs=jobs)
        self.coordinator = IngestionCoordinator(
            catalog=catalog,
            jobs=jobs,
            interval_seconds=min(
                2.0,
                float(settings.upload_recovery_interval_seconds),
            ),
        )

    @property
    def transfer_backend(self) -> UploadTransferBackend:
        return (
            UploadTransferBackend.tus
            if self.settings.upload_public_endpoint is not None
            else UploadTransferBackend.multipart
        )

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
            expires_at=now + timedelta(seconds=self.settings.upload_intent_ttl_seconds),
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

    def create_upload_session(
        self,
        *,
        principal: Principal,
        request_key: str,
        index_after_import: bool = True,
        index_modalities: tuple[str, ...] | None = None,
    ) -> UploadSessionLink:
        self._require_session_configured()
        selected_modalities = (
            self.default_index_modalities
            if index_modalities is None
            else index_modalities
        )
        effective_index_after_import = index_after_import and bool(selected_modalities)
        expected = self._upload_session_request(
            purpose="media-upload",
            inputs=None,
            principal=principal,
            index_after_import=effective_index_after_import,
            index_modalities=selected_modalities,
        )
        if existing := self._upload_session_replay(request_key, expected=expected):
            return self._session_link(existing)
        now = utc_now()
        transfer_backend = self.transfer_backend
        maximum_file_bytes = (
            self.settings.upload_max_bytes
            if transfer_backend == UploadTransferBackend.tus
            else min(
                self.settings.max_local_import_bytes,
                self.settings.http_max_small_upload_bytes,
            )
        )
        record = UploadSessionRecord(
            session_id=uuid4().hex,
            request_key=request_key,
            selector=secrets.token_hex(16),
            capability_digest="0" * 64,
            initiating_subject=principal.subject,
            initiating_client_id=principal.client_id,
            repository_binding=self._repository_binding(),
            state=UploadSessionState.open,
            maximum_files=self.settings.upload_session_max_files,
            maximum_file_bytes=maximum_file_bytes,
            maximum_aggregate_bytes=min(
                self.settings.upload_session_max_bytes,
                maximum_file_bytes * self.settings.upload_session_max_files,
            ),
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.settings.upload_session_ttl_seconds),
            transfer_backend=transfer_backend,
            index_after_import=effective_index_after_import,
            index_modalities=selected_modalities,
        )
        capability = self._session_capability(record)
        record = record.model_copy(
            update={"capability_digest": _token_digest(capability)}
        )
        try:
            self.catalog.create_upload_session(record)
        except IntegrityError:
            replay = self._upload_session_replay(request_key, expected=expected)
            if replay is None:
                raise
            record = replay
        return self._session_link(record)

    def _upload_session_request(
        self,
        *,
        purpose: str,
        inputs: tuple[str, ...] | None,
        principal: Principal,
        index_after_import: bool,
        index_modalities: tuple[str, ...],
    ) -> _UploadSessionRequest:
        return _UploadSessionRequest(
            purpose=purpose,
            inputs=inputs,
            index_after_import=index_after_import,
            index_modalities=index_modalities,
            initiating_subject=principal.subject,
            initiating_client_id=principal.client_id,
            repository_binding=self._repository_binding(),
        )

    def _upload_session_replay(
        self,
        request_key: str,
        *,
        expected: _UploadSessionRequest,
    ) -> UploadSessionRecord | None:
        existing = self.catalog.get_upload_session_by_request(request_key)
        if existing is None:
            return None
        stored_inputs = (
            tuple(
                intent.source_path or ""
                for _, intent in self.catalog.list_upload_session_files(
                    existing.session_id
                )
            )
            if expected.inputs is not None
            else None
        )
        if (
            existing.purpose != expected.purpose
            or stored_inputs != expected.inputs
            or existing.index_after_import != expected.index_after_import
            or existing.index_modalities != expected.index_modalities
            or existing.initiating_subject != expected.initiating_subject
            or existing.initiating_client_id != expected.initiating_client_id
            or existing.repository_binding != expected.repository_binding
        ):
            raise ApplicationError(
                "idempotency_key_reused",
                ErrorCategory.validation,
                "The idempotency key was reused for a different upload session.",
            )
        return existing

    def _session_link(self, record: UploadSessionRecord) -> UploadSessionLink:
        capability = self._session_capability(record)
        if not compare_digest(
            record.capability_digest,
            _token_digest(capability),
        ):
            raise RuntimeError("The stored upload session capability is invalid.")
        return UploadSessionLink(
            status=self._session_status(record),
            capability=capability,
        )

    def file_status(
        self,
        link: UploadSessionFileRecord,
        intent: UploadIntentRecord,
        *,
        session: UploadSessionRecord | None = None,
    ) -> MediaUploadStatus:
        active_session = session or self.catalog.get_upload_session(link.session_id)
        if active_session is None:
            raise RuntimeError("The upload session status is unavailable.")
        return self.status.file(link, intent, session=active_session)

    def _session_status(
        self,
        record: UploadSessionRecord,
        *,
        children: tuple[
            tuple[UploadSessionFileRecord, UploadIntentRecord],
            ...,
        ]
        | None = None,
    ) -> MediaUploadSessionStatus:
        return self.status.session(record, children=children)

    def create_local_ingestion(
        self,
        paths: tuple[str, ...],
        *,
        principal: Principal,
        request_key: str,
        index_after_import: bool = True,
        index_modalities: tuple[str, ...] | None = None,
    ) -> MediaUploadSessionStatus:
        if self.settings.mode != ApplicationMode.local:
            raise ApplicationError(
                "local_ingestion_unavailable",
                ErrorCategory.unavailable,
                "Local-path ingestion is available only to a local VidXP runtime.",
            )
        if self.media is None or self.jobs is None:
            raise ApplicationError(
                "local_ingestion_unavailable",
                ErrorCategory.unavailable,
                "Local media ingestion is not configured.",
            )
        selected = (
            self.default_index_modalities
            if index_modalities is None
            else index_modalities
        )
        expected = self._upload_session_request(
            purpose="local-media-ingestion",
            inputs=tuple(str(Path(value).expanduser().resolve()) for value in paths),
            principal=principal,
            index_after_import=index_after_import and bool(selected),
            index_modalities=selected,
        )
        if existing := self._upload_session_replay(request_key, expected=expected):
            return self._session_status(existing)
        now = utc_now()
        session = UploadSessionRecord(
            session_id=uuid4().hex,
            request_key=request_key,
            selector=secrets.token_hex(16),
            capability_digest=secrets.token_hex(32),
            initiating_subject=principal.subject,
            initiating_client_id=principal.client_id,
            repository_binding=self._repository_binding(),
            purpose="local-media-ingestion",
            state=UploadSessionState.closed,
            maximum_files=10,
            maximum_file_bytes=self.settings.max_local_import_bytes,
            maximum_aggregate_bytes=min(
                self.settings.upload_session_max_bytes,
                self.settings.max_local_import_bytes * 10,
            ),
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.settings.upload_session_ttl_seconds),
            transfer_backend=UploadTransferBackend.local_path,
            index_after_import=index_after_import and bool(selected),
            index_modalities=selected,
        )
        children: list[tuple[UploadSessionFileRecord, UploadIntentRecord]] = []
        for position, raw_path in enumerate(paths, start=1):
            client_file_key = f"local-{position:02d}"
            fallback_filename = f"input-{position}.bin"
            selected_filename = Path(raw_path).name.strip() or fallback_filename
            try:
                validate_display_filename(selected_filename)
            except ValueError:
                selected_filename = fallback_filename
            try:
                source = self.media.resolve_local_source(Path(raw_path))
                byte_size = source.stat().st_size
                if byte_size <= 0:
                    raise ApplicationError(
                        "media_empty",
                        ErrorCategory.validation,
                        "The local media file is empty.",
                    )
                if byte_size > session.maximum_file_bytes:
                    raise ApplicationError(
                        "media_too_large",
                        ErrorCategory.resource_limit,
                        "The local media file exceeds the configured import limit.",
                    )
                original_filename = source.name
                source_path = str(source)
                failure = None
            except ApplicationError as exc:
                byte_size = 0
                original_filename = selected_filename
                source_path = str(Path(raw_path).expanduser().resolve())
                failure = exc.detail
            except Exception:
                byte_size = 0
                original_filename = selected_filename
                source_path = str(Path(raw_path).expanduser().resolve())
                failure = ErrorDetail(
                    code="local_media_unavailable",
                    category=ErrorCategory.validation,
                    message=(
                        "The local media path is unavailable or outside the "
                        "configured import boundary."
                    ),
                )
            intent = UploadIntentRecord(
                intent_id=uuid4().hex,
                request_key=self._session_file_request_key(
                    session.session_id,
                    client_file_key,
                ),
                original_filename=original_filename,
                byte_size=byte_size,
                state=(
                    UploadState.failed if failure is not None else UploadState.pending
                ),
                created_at=now,
                expires_at=session.expires_at,
                transfer_backend=UploadTransferBackend.local_path,
                index_after_import=index_after_import and bool(selected),
                index_modalities=selected,
                source_path=source_path,
                failure_code=(failure.code if failure is not None else None),
                failure_message=(failure.message if failure is not None else None),
            )
            link = UploadSessionFileRecord(
                session_id=session.session_id,
                client_file_key=client_file_key,
                intent_id=intent.intent_id,
                created_at=now,
            )
            children.append((link, intent))

        def persist_batch(connection: Connection) -> None:
            self.catalog.create_upload_session_in_transaction(
                session,
                connection=connection,
            )
            for link, intent in children:
                self.catalog.create_upload_session_file(
                    link,
                    intent,
                    quota_limit=max(
                        self.settings.upload_quota_bytes,
                        session.maximum_aggregate_bytes,
                    ),
                    connection=connection,
                )

        try:
            self.catalog.with_upload_transaction(persist_batch)
        except IntegrityError:
            replay = self._upload_session_replay(request_key, expected=expected)
            if replay is None:
                raise
            return self._session_status(replay)
        self.coordinator.wake()
        return self._session_status(session, children=tuple(children))

    def get_status(
        self,
        session_id: str,
        *,
        principal: Principal,
    ) -> MediaUploadSessionStatus:
        record = self.catalog.get_upload_session(session_id)
        if record is None or record.repository_binding != self._repository_binding():
            raise ApplicationError(
                "resource_not_found",
                ErrorCategory.not_found,
                "The requested upload session was not found.",
            )
        self._require_principal_ownership(record, principal)
        return self._session_status(record)

    def exchange_upload_session(
        self,
        session_id: str,
        *,
        capability: str,
        current_session: str | None = None,
    ) -> UploadBrowserSession:
        now = utc_now()

        def exchange(connection: Connection) -> tuple[str, UploadSessionRecord]:
            record = self._session_from_capability(
                capability,
                connection=connection,
                for_update=True,
            )
            if record is None or record.session_id != session_id:
                raise self._invalid_handoff()
            self._validate_upload_session(record, now=now)
            if (
                current_session is not None
                and record.browser_session_digest is not None
                and compare_digest(
                    _token_digest(current_session),
                    record.browser_session_digest,
                )
            ):
                return current_session, record
            session_token = secrets.token_urlsafe(32)
            self.catalog.update_upload_session(
                record.session_id,
                browser_session_digest=_token_digest(session_token),
                connection=connection,
            )
            return session_token, record.model_copy(
                update={"browser_session_digest": _token_digest(session_token)}
            )

        token, record = self.catalog.with_upload_transaction(exchange)
        return self._browser_session(record, token)

    def browser_session(
        self,
        session_id: str,
        *,
        session_token: str | None,
    ) -> UploadBrowserSession:
        record = self._require_browser_session(
            session_id,
            session_token=session_token,
        )
        return self._browser_session(record, session_token or "")

    def _browser_session(
        self,
        record: UploadSessionRecord,
        session_token: str,
    ) -> UploadBrowserSession:
        children = self.catalog.list_upload_session_files(record.session_id)
        resume_urls = {}
        if record.transfer_backend == UploadTransferBackend.tus:
            assert self.settings.upload_public_endpoint is not None
            creation_url = self.settings.upload_public_endpoint
            for link, intent in children:
                if (
                    intent.state == UploadState.accepted
                    and intent.upload_id is not None
                    and self._tusd_creation_exists(intent)
                ):
                    resume_urls[link.client_file_key] = (
                        self.settings.upload_public_endpoint + intent.upload_id
                    )
        else:
            assert self.settings.upload_handoff_public_url is not None
            creation_url = (
                f"{self.settings.upload_handoff_public_url}/{record.session_id}/files"
            )
        return UploadBrowserSession(
            status=self._session_status(record, children=children),
            session_token=session_token,
            session_expires_at=record.expires_at,
            creation_url=creation_url,
            resume_urls=resume_urls,
        )

    def authorize_session_file(
        self,
        session_id: str,
        command: CreateUploadFileCommand,
        *,
        session_token: str | None,
    ) -> UploadFileAuthorization:
        now = utc_now()

        def authorize(connection: Connection) -> UploadFileAuthorization:
            record = self.catalog.get_upload_session(
                session_id,
                connection=connection,
                for_update=True,
            )
            self._validate_browser_session(
                record,
                session_token=session_token,
                now=now,
            )
            assert record is not None
            if command.byte_size > record.maximum_file_bytes:
                raise ApplicationError(
                    "upload_file_too_large",
                    ErrorCategory.resource_limit,
                    "The selected file exceeds this transfer backend's per-file limit.",
                )
            existing = self.catalog.get_upload_session_file(
                session_id,
                command.client_file_key,
                connection=connection,
                for_update=True,
            )
            if existing is not None:
                intent = self.catalog.get_upload_intent(
                    existing.intent_id,
                    connection=connection,
                    for_update=True,
                )
                assert intent is not None
                self._require_same_file(intent, command)
                return self._authorize_existing_file(
                    record,
                    existing,
                    intent,
                    now=now,
                    connection=connection,
                )
            children = self.catalog.list_upload_session_files(
                session_id,
                connection=connection,
            )
            if len(children) >= record.maximum_files:
                raise ApplicationError(
                    "upload_session_file_limit",
                    ErrorCategory.resource_limit,
                    "This upload session reached its configured file-count limit.",
                )
            aggregate_bytes = sum(intent.byte_size for _, intent in children)
            if aggregate_bytes + command.byte_size > record.maximum_aggregate_bytes:
                raise ApplicationError(
                    "upload_session_byte_limit",
                    ErrorCategory.resource_limit,
                    "The selected file would exceed the upload session aggregate limit.",
                )
            if record.state != UploadSessionState.open:
                raise ApplicationError(
                    "upload_session_closed",
                    ErrorCategory.conflict,
                    "This upload session is closed to new files.",
                )
            intent = UploadIntentRecord(
                intent_id=uuid4().hex,
                request_key=self._session_file_request_key(
                    session_id,
                    command.client_file_key,
                ),
                original_filename=command.original_filename,
                byte_size=command.byte_size,
                declared_mime_type=command.declared_mime_type,
                state=UploadState.pending,
                created_at=now,
                expires_at=min(
                    record.expires_at,
                    now + timedelta(seconds=self.settings.upload_intent_ttl_seconds),
                    ),
                transfer_backend=record.transfer_backend,
                index_after_import=record.index_after_import,
                index_modalities=record.index_modalities,
            )
            tus_transfer = record.transfer_backend == UploadTransferBackend.tus
            grant = secrets.token_urlsafe(48) if tus_transfer else None
            grant_expires_at = (
                min(
                intent.expires_at,
                now + timedelta(seconds=_CREATION_GRANT_TTL_SECONDS),
            )
                if tus_transfer
                else None
            )
            link = UploadSessionFileRecord(
                session_id=session_id,
                client_file_key=command.client_file_key,
                intent_id=intent.intent_id,
                created_at=now,
                creation_grant_digest=(
                    _token_digest(grant) if grant is not None else None
                ),
                creation_grant_expires_at=grant_expires_at,
            )
            try:
                self.catalog.create_upload_session_file(
                    link,
                    intent,
                    quota_limit=self.settings.upload_quota_bytes,
                    connection=connection,
                )
            except UploadQuotaExceededError as exc:
                raise ApplicationError(
                    "upload_quota_exceeded",
                    ErrorCategory.resource_limit,
                    "The repository upload quota would be exceeded.",
                ) from exc
            new_count = len(children) + 1
            new_bytes = aggregate_bytes + command.byte_size
            if (
                new_count >= record.maximum_files
                or new_bytes >= record.maximum_aggregate_bytes
            ):
                self.catalog.update_upload_session(
                    session_id,
                    state=UploadSessionState.closed,
                    connection=connection,
                )
            return UploadFileAuthorization(
                status=self.file_status(link, intent, session=record),
                grant=grant,
                grant_expires_at=grant_expires_at,
                resume_url=None,
            )

        return self.catalog.with_upload_transaction(authorize)

    def _authorize_existing_file(
        self,
        session: UploadSessionRecord,
        link: UploadSessionFileRecord,
        intent: UploadIntentRecord,
        *,
        now: datetime,
        connection: Connection,
    ) -> UploadFileAuthorization:
        resume_url = None
        needs_grant = (
            session.transfer_backend == UploadTransferBackend.tus
            and intent.state == UploadState.pending
        )
        if (
            session.transfer_backend == UploadTransferBackend.tus
            and intent.state == UploadState.accepted
            and intent.upload_id is not None
        ):
            if self._tusd_creation_exists(intent):
                assert self.settings.upload_public_endpoint is not None
                resume_url = self.settings.upload_public_endpoint + intent.upload_id
            else:
                needs_grant = True
        grant = None
        grant_expires_at = None
        if needs_grant:
            grant = secrets.token_urlsafe(48)
            grant_expires_at = min(
                session.expires_at,
                now + timedelta(seconds=_CREATION_GRANT_TTL_SECONDS),
            )
            self.catalog.set_upload_session_file_grant(
                session.session_id,
                link.client_file_key,
                digest=_token_digest(grant),
                expires_at=grant_expires_at,
                connection=connection,
            )
        return UploadFileAuthorization(
            status=self.file_status(link, intent, session=session),
            grant=grant,
            grant_expires_at=grant_expires_at,
            resume_url=resume_url,
        )

    def close_upload_session(
        self,
        session_id: str,
        *,
        principal: Principal,
    ) -> MediaUploadSessionStatus:
        now = utc_now()

        def close(connection: Connection) -> UploadSessionRecord:
            record = self.catalog.get_upload_session(
                session_id,
                connection=connection,
                for_update=True,
            )
            if (
                record is None
                or record.repository_binding != self._repository_binding()
            ):
                raise ApplicationError(
                    "resource_not_found",
                    ErrorCategory.not_found,
                    "The requested upload session was not found.",
                )
            self._require_principal_ownership(record, principal)
            state = (
                UploadSessionState.expired
                if record.expires_at <= now
                else UploadSessionState.closed
            )
            self.catalog.update_upload_session(
                session_id,
                state=state,
                connection=connection,
            )
            return record.model_copy(update={"state": state})

        return self._session_status(self.catalog.with_upload_transaction(close))

    def close_browser_session(
        self,
        session_id: str,
        *,
        session_token: str | None,
    ) -> MediaUploadSessionStatus:
        now = utc_now()

        def close(connection: Connection) -> UploadSessionRecord:
            record = self.catalog.get_upload_session(
                session_id,
                connection=connection,
                for_update=True,
            )
            self._validate_browser_session(
                record,
                session_token=session_token,
                now=now,
            )
            assert record is not None
            self.catalog.update_upload_session(
                session_id,
                state=UploadSessionState.closed,
                connection=connection,
            )
            return record.model_copy(update={"state": UploadSessionState.closed})

        return self._session_status(self.catalog.with_upload_transaction(close))

    def cancel_browser_file(
        self,
        session_id: str,
        intent_id: str,
        *,
        session_token: str | None,
    ) -> MediaUploadSessionStatus:
        now = utc_now()

        def inspect(connection: Connection) -> UploadIntentRecord:
            session = self.catalog.get_upload_session(
                session_id,
                connection=connection,
                for_update=True,
            )
            self._validate_browser_session(
                session,
                session_token=session_token,
                now=now,
            )
            link = self.catalog.get_upload_session_file_by_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if link is None or intent is None or link.session_id != session_id:
                raise ApplicationError(
                    "upload_not_found",
                    ErrorCategory.not_found,
                    "The requested session file was not found.",
                )
            if intent.state in {
                UploadState.processing,
                UploadState.ready,
                UploadState.indexed,
            }:
                raise ApplicationError(
                    "upload_cancellation_forbidden",
                    ErrorCategory.conflict,
                    "Processing or ready files cannot be cancelled.",
                )
            return intent

        intent = self.catalog.with_upload_transaction(inspect)
        if intent.upload_id is None:
            self._expire_intent(intent.intent_id)
        else:
            self._cleanup_upload(intent.upload_id)
        return self.browser_session(
            session_id,
            session_token=session_token,
        )

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

    def accept_session_creation(
        self,
        intent_id: str,
        *,
        grant: str,
        byte_size: int,
    ) -> UploadIntentRecord:
        now = utc_now()

        def accept(connection: Connection) -> UploadIntentRecord | None:
            link = self.catalog.get_upload_session_file_by_creation_grant(
                _token_digest(grant),
                connection=connection,
                for_update=True,
            )
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            session = (
                self.catalog.get_upload_session(
                    link.session_id,
                    connection=connection,
                    for_update=True,
                )
                if link is not None
                else None
            )
            self._verify_creation_grant(
                link,
                session,
                intent,
                grant=grant,
                expected_intent_id=intent_id,
                byte_size=byte_size,
                now=now,
            )
            assert link is not None and intent is not None
            if intent.state != UploadState.pending and self._tusd_creation_exists(
                intent
            ):
                raise ApplicationError(
                    "upload_creation_grant_replayed",
                    ErrorCategory.conflict,
                    "This file's tus upload already exists; resume its URL.",
                )
            accepted = self._accept_creation_in_transaction(
                intent_id,
                byte_size=byte_size,
                now=now,
                connection=connection,
            )
            self.catalog.consume_upload_session_file_grant(
                link.session_id,
                link.client_file_key,
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
        if intent.transfer_backend != UploadTransferBackend.tus:
            return self._quarantine_path(intent.upload_id).is_file()
        return self._probe_tus_upload(intent.upload_id) is not None

    def _probe_tus_upload(self, upload_id: str) -> TusUploadProbe | None:
        if self._tusd_upload_probe is not None:
            return self._tusd_upload_probe(upload_id)
        endpoint = self.settings.upload_internal_endpoint
        if endpoint is None:
            raise RuntimeError("Remote upload probing is not configured.")
        request = Request(endpoint + upload_id, method="HEAD")
        request.add_header("Tus-Resumable", "1.0.0")
        try:
            with urlopen(request, timeout=5) as response:
                if response.status != 200:
                    raise ApplicationError(
                        "remote_upload_unavailable",
                        ErrorCategory.unavailable,
                        "The upload service returned an unexpected probe response.",
                    )
                length = self._parse_tus_position(response.headers.get("Upload-Length"))
                offset = self._parse_tus_position(response.headers.get("Upload-Offset"))
                if length is None or offset is None or offset > length:
                    raise ApplicationError(
                        "remote_upload_probe_invalid",
                        ErrorCategory.unavailable,
                        "The upload service returned invalid resumable-upload state.",
                    )
                return TusUploadProbe(
                    upload_id=upload_id,
                    length=length,
                    offset=offset,
                )
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "The upload service could not verify the resumable upload.",
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "The upload service is temporarily unavailable.",
            ) from exc

    @staticmethod
    def _parse_tus_position(value: str | None) -> int | None:
        if (
            value is None
            or len(value) > 20
            or not value.isascii()
            or not value.isdigit()
        ):
            return None
        return int(value)

    @staticmethod
    def _validate_tus_probe(
        intent: UploadIntentRecord,
        probe: TusUploadProbe,
    ) -> None:
        if probe.upload_id != intent.upload_id or probe.length != intent.byte_size:
            raise ApplicationError(
                "remote_upload_probe_mismatch",
                ErrorCategory.unavailable,
                "The resumable upload does not match its durable upload intent.",
            )

    def complete_multipart_file(
        self,
        session_id: str,
        intent_id: str,
        *,
        staged_path: Path,
        original_filename: str,
        declared_mime_type: str | None,
        byte_size: int,
        session_token: str | None,
    ) -> MediaUploadSessionStatus:
        """Claim one immutable staged body without touching a winning sibling."""

        attempt_path = staged_path.resolve()
        quarantine_root = self.settings.quarantine_root.resolve()
        if (
            attempt_path.parent != quarantine_root
            or not attempt_path.is_file()
            or attempt_path.is_symlink()
        ):
            staged_path.unlink(missing_ok=True)
            raise ApplicationError(
                "upload_staging_invalid",
                ErrorCategory.validation,
                "The multipart staging object is invalid.",
            )
        attempt_id = attempt_path.name
        if self._quarantine_path(attempt_id) != attempt_path:
            staged_path.unlink(missing_ok=True)
            raise ApplicationError(
                "upload_staging_invalid",
                ErrorCategory.validation,
                "The multipart staging object is invalid.",
            )
        actual_size = attempt_path.stat().st_size
        content_sha256 = _file_sha256(attempt_path)

        def claim(connection: Connection) -> tuple[bool, UploadIntentRecord]:
            session = self.catalog.get_upload_session(
                session_id,
                connection=connection,
                for_update=True,
            )
            self._validate_browser_session(
                session,
                session_token=session_token,
                now=utc_now(),
            )
            assert session is not None
            if session.transfer_backend != UploadTransferBackend.multipart:
                raise ApplicationError(
                    "upload_backend_mismatch",
                    ErrorCategory.conflict,
                    "This upload session requires the resumable tus backend.",
                )
            link = self.catalog.get_upload_session_file_by_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            intent = self.catalog.get_upload_intent(
                intent_id,
                connection=connection,
                for_update=True,
            )
            if link is None or intent is None or link.session_id != session_id:
                raise ApplicationError(
                    "upload_not_found",
                    ErrorCategory.not_found,
                    "The requested session file was not found.",
                )
            if (
                intent.original_filename != original_filename
                or intent.byte_size != byte_size
                or intent.byte_size != actual_size
                or intent.declared_mime_type != declared_mime_type
            ):
                raise ApplicationError(
                    "upload_metadata_mismatch",
                    ErrorCategory.validation,
                    "The multipart body does not match its selected file metadata.",
                )
            if intent.state in {
                UploadState.accepted,
                UploadState.processing,
                UploadState.ready,
                UploadState.indexed,
            }:
                return False, intent
            if intent.state != UploadState.pending:
                raise ApplicationError(
                    "upload_intent_consumed",
                    ErrorCategory.conflict,
                    "This multipart file cannot be submitted again.",
                )
            self.catalog.update_upload(
                intent.intent_id,
                state=UploadState.accepted,
                connection=connection,
                upload_id=attempt_id,
                content_sha256=content_sha256,
                expected_states={UploadState.pending},
            )
            return True, intent.model_copy(
                update={
                    "state": UploadState.accepted,
                    "upload_id": attempt_id,
                    "content_sha256": content_sha256,
                }
            )

        claimed = False
        try:
            claimed, _intent = self.catalog.with_upload_transaction(claim)
        finally:
            if not claimed:
                attempt_path.unlink(missing_ok=True)
        self.coordinator.wake()
        return self.browser_session(
            session_id,
            session_token=session_token,
        ).status

    def complete_tus_transfer(
        self,
        *,
        intent_id: str,
        upload_id: str,
        byte_size: int,
        offset: int,
    ) -> str:
        if self.jobs is None:
            raise RuntimeError("Upload job submission is not configured.")

        return self.coordinator.complete_tus_transfer(
            intent_id=intent_id,
            upload_id=upload_id,
            byte_size=byte_size,
            offset=offset,
        )

    def start_indexing(
        self,
        command: CreateIndexCommand,
        *,
        job_id: str,
    ) -> Job:
        """Submit indexing and relink any failed upload projection atomically."""

        if self.jobs is None:
            raise RuntimeError("Index job submission is not configured.")

        persisted_command = command.model_dump(mode="json")

        def relink(connection: Connection) -> tuple[str, ...]:
            linked: list[str] = []
            for record in self.catalog.failed_index_uploads_for_media(
                command.media_id,
                connection=connection,
            ):
                if self.catalog.update_upload(
                    record.intent_id,
                    state=UploadState.ready,
                    connection=connection,
                    index_job_id=job_id,
                    index_command=persisted_command,
                    clear_failure=True,
                    expected_states={UploadState.ready},
                    expected_index_job_id=record.index_job_id,
                ):
                    linked.append(record.intent_id)
            return tuple(linked)

        linked_intents = self.catalog.with_upload_transaction(relink)
        try:
            job = self.jobs.submit_index(command, job_id=job_id)
        except ApplicationError as exc:
            if linked_intents and not IngestionCoordinator.is_retryable(exc.detail):
                self._record_index_retry_failure(
                    linked_intents,
                    job_id=job_id,
                    detail=exc.detail,
                )
            raise
        finally:
            if linked_intents:
                self.coordinator.wake()
        return job

    def _record_index_retry_failure(
        self,
        intent_ids: tuple[str, ...],
        *,
        job_id: str,
        detail: ErrorDetail,
    ) -> None:
        def fail(connection: Connection) -> None:
            for intent_id in intent_ids:
                self.catalog.update_upload(
                    intent_id,
                    state=UploadState.ready,
                    connection=connection,
                    failure_code=detail.code,
                    failure_message=detail.message,
                    expected_states={UploadState.ready},
                    expected_index_job_id=job_id,
                )

        self.catalog.with_upload_transaction(fail)

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
        if (
            record.state in {UploadState.ready, UploadState.indexed}
            and record.media_id is not None
        ):
            return self.media.get(record.media_id)
        if record.state != UploadState.processing:
            raise ApplicationError(
                "upload_not_ready",
                ErrorCategory.conflict,
                "The upload is not ready for import.",
            )
        path = self._quarantine_path(upload_id)
        try:
            actual_size = path.stat().st_size
        except OSError as exc:
            raise ApplicationError(
                "upload_content_unavailable",
                ErrorCategory.unavailable,
                "The accepted upload content is unavailable from quarantine.",
            ) from exc
        if actual_size != record.byte_size:
            raise ApplicationError(
                "upload_content_size_mismatch",
                ErrorCategory.validation,
                "The accepted upload size no longer matches its durable intent.",
            )
        if (
            record.content_sha256 is not None
            and _file_sha256(path) != record.content_sha256
        ):
            raise ApplicationError(
                "upload_content_digest_mismatch",
                ErrorCategory.validation,
                "The accepted upload digest no longer matches its durable intent.",
            )
        request_key = hashlib.sha256(
            f"vidxp-upload-import-v1\0{record.intent_id}".encode()
        ).hexdigest()
        return self.media.import_quarantined(
            QuarantinedMedia(
                path=path,
                original_filename=record.original_filename,
                declared_mime_type=record.declared_mime_type,
            ),
            request_key=request_key,
        )

    def reconcile(self) -> dict[str, int]:
        recovered = 0
        coordinated = self.coordinator.run_once()
        errors = coordinated["errors"]
        advanced = coordinated["advanced"]
        tus_probes: dict[str, TusUploadProbe | None] = {}
        failed_tus_probes: set[str] = set()
        for record in self.catalog.recoverable_uploads():
            try:
                if (
                    record.upload_id is None
                    or record.transfer_backend != UploadTransferBackend.tus
                ):
                    continue
                probe = self._probe_tus_upload(record.upload_id)
                tus_probes[record.upload_id] = probe
                if probe is None:
                    continue
                self._validate_tus_probe(record, probe)
                if probe.offset == probe.length:
                    self.complete_tus_transfer(
                        intent_id=record.intent_id,
                        upload_id=record.upload_id,
                        byte_size=probe.length,
                        offset=probe.offset,
                    )
                    recovered += 1
            except Exception:
                errors += 1
                if record.upload_id is not None:
                    failed_tus_probes.add(record.upload_id)
                LOGGER.exception(
                    "Upload recovery failed for intent %s.",
                    record.intent_id,
                )
        expired = 0
        now = utc_now()
        for record in self.catalog.expired_uploads(now=now):
            try:
                tus_upload_missing = False
                if (
                    record.state == UploadState.accepted
                    and record.upload_id is not None
                ):
                    if record.transfer_backend == UploadTransferBackend.tus:
                        if record.upload_id in failed_tus_probes:
                            continue
                        probe = tus_probes.get(record.upload_id)
                        if record.upload_id not in tus_probes:
                            probe = self._probe_tus_upload(record.upload_id)
                            tus_probes[record.upload_id] = probe
                        if probe is not None:
                            self._validate_tus_probe(record, probe)
                            # Expire VidXP's intent and release its reservation, but
                            # leave the still-present tus resource for explicit
                            # operator cleanup. Deleting it here would race a client
                            # that resumed after the HEAD observation.
                            self._expire_intent(
                                record.intent_id,
                                clear_upload_id=True,
                            )
                            expired += 1
                            continue
                        tus_upload_missing = True
                    elif (
                        record.transfer_backend == UploadTransferBackend.multipart
                        and self._local_upload_is_active(record.upload_id, now=now)
                    ):
                        continue
                upload_id = self._expire_intent(record.intent_id)
                if upload_id is not None:
                    if tus_upload_missing:
                        self.record_terminated(upload_id)
                    else:
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
            "advanced": advanced,
            "recovered": recovered,
            "expired": expired,
            "cleaned": cleaned,
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
                UploadState.indexed,
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
                record.state
                if record.state in {UploadState.ready, UploadState.indexed}
                else UploadState.expired
            )
            self.catalog.update_upload(
                record.intent_id,
                state=state,
                connection=connection,
                clear_upload_id=True,
            )

        self.catalog.with_upload_transaction(terminate)

    def _expire_intent(
        self,
        intent_id: str,
        *,
        clear_upload_id: bool = False,
    ) -> str | None:
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
                clear_upload_id=clear_upload_id,
            )
            return None if clear_upload_id else record.upload_id

        return self.catalog.with_upload_transaction(expire)

    def _local_upload_is_active(self, upload_id: str, *, now) -> bool:
        cutoff = now.timestamp() - self.settings.upload_intent_ttl_seconds
        try:
            path = self._quarantine_path(upload_id)
            return (
                path.is_file()
                and not path.is_symlink()
                and path.stat().st_mtime > cutoff
            )
        except OSError:
            return False

    def _quarantine_path(self, name: str) -> Path:
        if (
            not name
            or len(name) > 260
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz"
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
        record = self.catalog.get_upload_intent_by_upload_id(upload_id)
        if record is not None:
            if record.transfer_backend == UploadTransferBackend.tus:
                self._terminate_upload(upload_id)
            elif record.transfer_backend == UploadTransferBackend.multipart:
                self._quarantine_path(upload_id).unlink(missing_ok=True)
        self.record_terminated(upload_id)

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
            if self._tusd_creation_exists(record):
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
                "remote_upload_session_unavailable",
                ErrorCategory.unavailable,
                "Browser upload sessions are not configured.",
            )
        return secret.get_secret_value()

    def _repository_binding(self) -> str:
        return repository_binding(self.settings.repository_root)

    def _session_capability(self, record: UploadSessionRecord) -> str:
        return encode_capability(
            {
                "aud": "vidxp-upload-session",
                "sub": record.session_id,
                "jti": record.selector,
                "purpose": record.purpose,
                "repository": record.repository_binding,
                "iat": int(record.created_at.timestamp()),
                "exp": int(record.expires_at.timestamp()),
            },
            secret=self._secret(),
        )

    def _session_from_capability(
        self,
        capability: str,
        *,
        connection: Connection,
        for_update: bool,
    ) -> UploadSessionRecord | None:
        try:
            claims = decode_capability(
                capability,
                secret=self._secret(),
                audience="vidxp-upload-session",
                verify_exp=False,
                required_claims=("jti", "purpose", "repository"),
            )
        except PyJWTError as exc:
            raise self._invalid_handoff() from exc
        selector = claims.get("jti")
        if (
            not isinstance(selector, str)
            or len(selector) != 32
            or any(character not in "0123456789abcdef" for character in selector)
        ):
            raise self._invalid_handoff()
        record = self.catalog.get_upload_session_by_selector(
            selector,
            connection=connection,
            for_update=for_update,
        )
        if (
            record is None
            or claims.get("sub") != record.session_id
            or claims.get("purpose") != record.purpose
            or claims.get("repository") != record.repository_binding
            or claims.get("iat") != int(record.created_at.timestamp())
            or claims.get("exp") != int(record.expires_at.timestamp())
            or not compare_digest(
                record.capability_digest,
                _token_digest(capability),
            )
        ):
            raise self._invalid_handoff()
        return record

    def _validate_upload_session(
        self,
        record: UploadSessionRecord,
        *,
        now: datetime,
    ) -> None:
        if (
            record.repository_binding != self._repository_binding()
            or record.purpose != "media-upload"
        ):
            raise self._invalid_handoff()
        if record.expires_at <= now or record.state == UploadSessionState.expired:
            raise ApplicationError(
                "upload_session_expired",
                ErrorCategory.authentication,
                "The upload session expired. Create a new media upload session.",
            )

    def _require_principal_ownership(
        self,
        record: UploadSessionRecord,
        principal: Principal,
    ) -> None:
        if self.settings.http_auth_mode == HttpAuthMode.none:
            return
        if (
            record.initiating_subject != principal.subject
            or (
                record.initiating_client_id is not None
                and record.initiating_client_id != principal.client_id
            )
        ):
            raise ApplicationError(
                "resource_not_found",
                ErrorCategory.not_found,
                "The requested upload session was not found.",
            )

    def _require_browser_session(
        self,
        session_id: str,
        *,
        session_token: str | None,
    ) -> UploadSessionRecord:
        record = self.catalog.get_upload_session(session_id)
        self._validate_browser_session(
            record,
            session_token=session_token,
            now=utc_now(),
        )
        assert record is not None
        return record

    def _validate_browser_session(
        self,
        record: UploadSessionRecord | None,
        *,
        session_token: str | None,
        now: datetime,
    ) -> None:
        if (
            record is None
            or session_token is None
            or record.browser_session_digest is None
            or record.repository_binding != self._repository_binding()
            or not compare_digest(
                record.browser_session_digest,
                _token_digest(session_token),
            )
        ):
            raise ApplicationError(
                "upload_browser_session_invalid",
                ErrorCategory.authentication,
                "The browser upload session is missing or invalid. Reopen "
                "the complete capability link.",
            )
        self._validate_upload_session(record, now=now)

    def _verify_creation_grant(
        self,
        link: UploadSessionFileRecord | None,
        session: UploadSessionRecord | None,
        intent: UploadIntentRecord | None,
        *,
        grant: str,
        expected_intent_id: str,
        byte_size: int,
        now: datetime,
    ) -> None:
        if (
            link is None
            or session is None
            or intent is None
            or link.intent_id != expected_intent_id
            or intent.intent_id != expected_intent_id
            or link.session_id != session.session_id
            or session.repository_binding != self._repository_binding()
            or intent.byte_size != byte_size
            or link.creation_grant_digest is None
            or link.creation_grant_expires_at is None
            or not compare_digest(
                link.creation_grant_digest,
                _token_digest(grant),
            )
        ):
            raise self._invalid_creation_grant()
        if (
            link.creation_grant_expires_at <= now
            or intent.expires_at <= now
            or session.expires_at <= now
        ):
            raise ApplicationError(
                "upload_creation_grant_expired",
                ErrorCategory.authentication,
                "The upload creation grant expired. Retry this file from "
                "the browser page.",
            )
        if link.creation_grant_consumed_at is not None:
            raise ApplicationError(
                "upload_creation_grant_replayed",
                ErrorCategory.conflict,
                "The upload creation grant was already used. Resume the "
                "existing file from its browser page.",
            )

    @staticmethod
    def _invalid_handoff() -> ApplicationError:
        return ApplicationError(
            "upload_session_capability_invalid",
            ErrorCategory.authentication,
            "The upload session capability is invalid for this repository.",
        )

    @staticmethod
    def _invalid_creation_grant() -> ApplicationError:
        return ApplicationError(
            "upload_creation_grant_invalid",
            ErrorCategory.authentication,
            "The upload creation grant is invalid for this file, size, or repository.",
        )

    def _require_configured(self) -> None:
        if (
            self.settings.mode == ApplicationMode.server
            and self.settings.upload_public_endpoint is None
        ):
            raise ApplicationError(
                "remote_upload_unavailable",
                ErrorCategory.unavailable,
                "Remote resumable uploads are not configured.",
            )

    def _require_session_configured(self) -> None:
        self._require_configured()
        if (
            self.settings.upload_handoff_public_url is None
            or self.settings.upload_handoff_secret is None
        ):
            raise ApplicationError(
                "remote_upload_session_unavailable",
                ErrorCategory.unavailable,
                "Browser upload sessions are not configured.",
            )

    @staticmethod
    def _session_file_request_key(
        session_id: str,
        client_file_key: str,
    ) -> str:
        return hashlib.sha256(
            f"vidxp-upload-session-file-v1\0{session_id}\0{client_file_key}".encode()
        ).hexdigest()

    @staticmethod
    def _require_same_file(
        record: UploadIntentRecord,
        command: CreateUploadFileCommand,
    ) -> None:
        if (
            record.original_filename != command.original_filename
            or record.byte_size != command.byte_size
            or record.declared_mime_type != command.declared_mime_type
        ):
            raise ApplicationError(
                "upload_client_key_conflict",
                ErrorCategory.conflict,
                "This client file key is already bound to different metadata.",
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
