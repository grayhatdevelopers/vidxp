from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Engine,
    and_,
    create_engine,
    delete,
    event,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from vidxp.core.artifacts import ArtifactRecord, ArtifactState
from vidxp.core.media import MediaRecord, MediaState, utc_now
from vidxp.core.uploads import (
    UploadIntentRecord,
    UploadSessionFileRecord,
    UploadSessionRecord,
    UploadSessionState,
    UploadState,
    UploadTransferBackend,
)
from vidxp.infrastructure.sql_tables import (
    artifact_requests,
    artifacts,
    media,
    media_import_requests,
    metadata,
    upload_intents,
    upload_quota,
    upload_session_files,
    upload_sessions,
)

_RESERVED_UPLOAD_STATES = {
    UploadState.pending.value,
    UploadState.accepted.value,
    UploadState.processing.value,
    UploadState.failed.value,
}
_REPLACEABLE_MEDIA_STATES = {MediaState.pending, MediaState.failed}
_UPLOAD_QUOTA_ID = "1"
_EXPECTED_VALUE_UNSET = object()


class UploadQuotaExceededError(RuntimeError):
    """Raised when an atomic upload reservation exceeds its quota."""


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("The relational catalog payload is invalid.")
    return value


def _record(model: Any, value: Any) -> Any:
    return model.model_validate(_payload(value), strict=False)


def _escape_like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _media_payload_text(key: str):
    return media.c.payload[key].as_string()


def _media_list_conditions(
    *,
    dialect_name: str,
    filename: str | None,
    state: MediaState | None,
) -> tuple[Any, ...]:
    conditions: list[Any] = []
    if state is not None:
        conditions.append(_media_payload_text("state") == state.value)
    if filename is not None:
        filename_text = _media_payload_text("original_filename")
        if dialect_name == "postgresql":
            filename_text = filename_text.collate("pg_unicode_fast")
        conditions.append(
            func.casefold(filename_text).like(
                _escape_like_pattern(filename.casefold()),
                escape="\\",
            )
        )
    return tuple(conditions)


def _upload_record(row: Any) -> UploadIntentRecord:
    return UploadIntentRecord(
        intent_id=row.intent_id,
        request_key=row.request_key,
        original_filename=row.original_filename,
        byte_size=row.byte_size,
        declared_mime_type=row.declared_mime_type,
        state=row.state,
        created_at=datetime.fromisoformat(row.created_at),
        expires_at=datetime.fromisoformat(row.expires_at),
        upload_id=row.upload_id,
        job_id=row.job_id,
        media_id=row.media_id,
        transfer_backend=UploadTransferBackend(row.transfer_backend),
        index_after_import=bool(row.index_after_import),
        index_modalities=tuple(row.index_modalities or ()),
        index_job_id=row.index_job_id,
        index_command=row.index_command,
        source_path=row.source_path,
        content_sha256=row.content_sha256,
        failure_code=row.failure_code,
        failure_message=row.failure_message,
    )


def _upload_session_record(row: Any) -> UploadSessionRecord:
    return UploadSessionRecord(
        session_id=row.session_id,
        request_key=row.request_key,
        selector=row.selector,
        capability_digest=row.capability_digest,
        initiating_subject=row.initiating_subject,
        initiating_client_id=row.initiating_client_id,
        repository_binding=row.repository_binding,
        purpose=row.purpose,
        state=row.state,
        maximum_files=row.maximum_files,
        maximum_file_bytes=row.maximum_file_bytes,
        maximum_aggregate_bytes=row.maximum_aggregate_bytes,
        created_at=datetime.fromisoformat(row.created_at),
        expires_at=datetime.fromisoformat(row.expires_at),
        browser_session_digest=row.browser_session_digest,
        transfer_backend=UploadTransferBackend(row.transfer_backend),
        index_after_import=bool(row.index_after_import),
        index_modalities=tuple(row.index_modalities or ()),
    )


def _upload_session_file_record(row: Any) -> UploadSessionFileRecord:
    return UploadSessionFileRecord(
        session_id=row.session_id,
        client_file_key=row.client_file_key,
        intent_id=row.intent_id,
        created_at=datetime.fromisoformat(row.created_at),
        creation_grant_digest=row.creation_grant_digest,
        creation_grant_expires_at=(
            datetime.fromisoformat(row.creation_grant_expires_at)
            if row.creation_grant_expires_at is not None
            else None
        ),
        creation_grant_consumed_at=(
            datetime.fromisoformat(row.creation_grant_consumed_at)
            if row.creation_grant_consumed_at is not None
            else None
        ),
    )


class SQLCatalog:
    """SQLAlchemy Core catalog shared by SQLite and PostgreSQL adapters."""

    def __init__(
        self,
        database_url: str,
        *,
        initialize: bool = False,
        engine: Engine | None = None,
    ) -> None:
        sqlite = database_url.startswith("sqlite:")
        self.engine = engine or create_engine(
            database_url,
            pool_pre_ping=True,
            **(
                {
                    "poolclass": NullPool,
                    "connect_args": {"timeout": 30},
                }
                if sqlite
                else {}
            ),
        )
        self._owns_engine = engine is None
        if sqlite and engine is None:
            event.listen(self.engine, "connect", self._configure_sqlite)
        if initialize:
            metadata.create_all(self.engine)

    def close(self) -> None:
        if self._owns_engine:
            self.engine.dispose()

    def health(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(select(1)).scalar_one()

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with self._write_transaction() as connection:
            yield connection

    @staticmethod
    def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.create_function(
            "casefold",
            1,
            str.casefold,
            deterministic=True,
        )
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 30000")
        finally:
            cursor.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[Connection]:
        with self.engine.connect() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
            else:
                with connection.begin():
                    yield connection

    def get_media(self, media_id: str) -> MediaRecord | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(media.c.payload).where(media.c.media_id == media_id)
            ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(MediaRecord, payload)
        )

    def get_media_by_checksum(self, sha256: str) -> MediaRecord | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(media.c.payload).where(media.c.sha256 == sha256)
            ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(MediaRecord, payload)
        )

    def put_media(self, record: MediaRecord) -> MediaRecord:
        values = {
            "media_id": record.media_id,
            "sha256": record.sha256,
            "created_at": record.created_at.isoformat(),
            "payload": record.model_dump(mode="json"),
        }
        with self._write_transaction() as connection:
            existing = self._media_by_id(connection, record.media_id)
            if existing is not None:
                if existing != record:
                    raise FileExistsError(
                        f"Media {record.media_id} already has another record."
                    )
                return existing
            checksum_match = self._media_by_checksum(connection, record.sha256)
            if checksum_match is not None:
                return checksum_match
            try:
                with connection.begin_nested():
                    connection.execute(insert(media).values(**values))
            except IntegrityError:
                authoritative = self._media_by_checksum(
                    connection,
                    record.sha256,
                )
                if authoritative is not None:
                    return authoritative
                existing = self._media_by_id(connection, record.media_id)
                if existing == record:
                    return existing
                raise
        return record

    def replace_media(self, record: MediaRecord) -> MediaRecord:
        with self._write_transaction() as connection:
            existing = self._media_by_id(connection, record.media_id)
            if existing is None:
                raise FileNotFoundError(
                    f"Media {record.media_id} is not cataloged."
                )
            if existing.sha256 != record.sha256:
                raise FileExistsError(
                    f"Media {record.media_id} already has another record."
                )
            if existing == record:
                return existing
            if existing.state not in _REPLACEABLE_MEDIA_STATES:
                raise FileExistsError(
                    f"Media {record.media_id} already has another record."
                )
            connection.execute(
                update(media)
                .where(media.c.media_id == record.media_id)
                .values(payload=record.model_dump(mode="json"))
            )
        return record

    @staticmethod
    def _media_by_id(
        connection: Connection,
        media_id: str,
    ) -> MediaRecord | None:
        payload = connection.execute(
            select(media.c.payload).where(media.c.media_id == media_id)
        ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(MediaRecord, payload)
        )

    @staticmethod
    def _media_by_checksum(
        connection: Connection,
        sha256: str,
    ) -> MediaRecord | None:
        payload = connection.execute(
            select(media.c.payload).where(media.c.sha256 == sha256)
        ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(MediaRecord, payload)
        )

    def list_media(
        self,
        *,
        limit: int,
        offset: int = 0,
        filename: str | None = None,
        state: MediaState | None = None,
    ) -> tuple[MediaRecord, ...]:
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset nonnegative")
        conditions = _media_list_conditions(
            dialect_name=self.engine.dialect.name,
            filename=filename,
            state=state,
        )
        query = select(media.c.payload).order_by(media.c.created_at, media.c.media_id)
        if conditions:
            query = query.where(and_(*conditions))
        with self.engine.connect() as connection:
            payloads = connection.execute(
                query.limit(limit).offset(offset)
            ).scalars()
            return tuple(
                _record(MediaRecord, payload)
                for payload in payloads
            )

    def count_media(
        self,
        *,
        filename: str | None = None,
        state: MediaState | None = None,
    ) -> int:
        conditions = _media_list_conditions(
            dialect_name=self.engine.dialect.name,
            filename=filename,
            state=state,
        )
        query = select(func.count()).select_from(media)
        if conditions:
            query = query.where(and_(*conditions))
        with self.engine.connect() as connection:
            return int(connection.execute(query).scalar_one())

    def reserve_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
    ) -> MediaRecord | None:
        with self._write_transaction() as connection:
            row = connection.execute(
                select(
                    media_import_requests.c.request_fingerprint,
                    media_import_requests.c.media_id,
                )
                .where(media_import_requests.c.request_key == request_key)
                .with_for_update()
            ).one_or_none()
            if row is None:
                try:
                    with connection.begin_nested():
                        connection.execute(
                            insert(media_import_requests).values(
                                request_key=request_key,
                                request_fingerprint=request_fingerprint,
                            )
                        )
                    return None
                except IntegrityError:
                    row = connection.execute(
                        select(
                            media_import_requests.c.request_fingerprint,
                            media_import_requests.c.media_id,
                        )
                        .where(
                            media_import_requests.c.request_key == request_key
                        )
                        .with_for_update()
                    ).one()
            if row.request_fingerprint != request_fingerprint:
                raise FileExistsError(
                    "The media import key is bound to different content."
                )
            if row.media_id is None:
                return None
            stored = self._media_by_id(connection, row.media_id)
            if stored is None:
                raise RuntimeError(
                    "A completed media import references missing media."
                )
            return stored

    def complete_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
        record: MediaRecord,
    ) -> None:
        with self._write_transaction() as connection:
            row = connection.execute(
                select(
                    media_import_requests.c.request_fingerprint,
                    media_import_requests.c.media_id,
                )
                .where(media_import_requests.c.request_key == request_key)
                .with_for_update()
            ).one_or_none()
            if row is None or row.request_fingerprint != request_fingerprint:
                raise FileExistsError(
                    "The media import reservation does not match the content."
                )
            if row.media_id is not None and row.media_id != record.media_id:
                raise FileExistsError(
                    "The media import key is already completed."
                )
            connection.execute(
                update(media_import_requests)
                .where(media_import_requests.c.request_key == request_key)
                .values(media_id=record.media_id)
            )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(artifacts.c.payload).where(
                    artifacts.c.artifact_id == artifact_id
                )
            ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(ArtifactRecord, payload)
        )

    def get_artifact_by_request(
        self,
        request_key: str,
    ) -> ArtifactRecord | None:
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(artifacts.c.payload)
                .select_from(
                    artifact_requests.join(
                        artifacts,
                        artifact_requests.c.artifact_id
                        == artifacts.c.artifact_id,
                    )
                )
                .where(artifact_requests.c.request_key == request_key)
            ).scalar_one_or_none()
        if payload is None:
            return None
        record = _record(ArtifactRecord, payload)
        if (
            record.state != ArtifactState.ready
            or (
                record.expires_at is not None
                and record.expires_at <= utc_now()
            )
        ):
            return None
        return record

    def put_artifact(self, record: ArtifactRecord) -> ArtifactRecord:
        with self._write_transaction() as connection:
            request_match = self._artifact_by_request(
                connection,
                record.request_key,
            )
            if request_match is not None:
                if (
                    request_match.state == ArtifactState.ready
                    and (
                        request_match.expires_at is None
                        or request_match.expires_at > utc_now()
                    )
                ):
                    return request_match
                connection.execute(
                    delete(artifact_requests).where(
                        artifact_requests.c.request_key == record.request_key
                    )
                )
            existing = self._artifact_by_id(
                connection,
                record.artifact_id,
            )
            if existing is not None:
                if existing != record:
                    raise FileExistsError(
                        f"Artifact {record.artifact_id} already exists."
                    )
                return existing
            connection.execute(
                insert(artifacts).values(
                    artifact_id=record.artifact_id,
                    media_id=record.media_id,
                    created_at=record.created_at.isoformat(),
                    payload=record.model_dump(mode="json"),
                )
            )
            connection.execute(
                insert(artifact_requests).values(
                    request_key=record.request_key,
                    artifact_id=record.artifact_id,
                )
            )
        return record

    @staticmethod
    def _artifact_by_id(
        connection: Connection,
        artifact_id: str,
    ) -> ArtifactRecord | None:
        payload = connection.execute(
            select(artifacts.c.payload).where(
                artifacts.c.artifact_id == artifact_id
            )
        ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(ArtifactRecord, payload)
        )

    @staticmethod
    def _artifact_by_request(
        connection: Connection,
        request_key: str,
    ) -> ArtifactRecord | None:
        payload = connection.execute(
            select(artifacts.c.payload)
            .select_from(
                artifact_requests.join(
                    artifacts,
                    artifact_requests.c.artifact_id == artifacts.c.artifact_id,
                )
            )
            .where(artifact_requests.c.request_key == request_key)
        ).scalar_one_or_none()
        return (
            None
            if payload is None
            else _record(ArtifactRecord, payload)
        )

    def invalidate_artifact_request(
        self,
        request_key: str,
        artifact_id: str,
    ) -> None:
        with self._write_transaction() as connection:
            connection.execute(
                delete(artifact_requests).where(
                    artifact_requests.c.request_key == request_key,
                    artifact_requests.c.artifact_id == artifact_id,
                )
            )

    def create_upload_intent(
        self,
        record: UploadIntentRecord,
        *,
        quota_limit: int,
    ) -> UploadIntentRecord:
        with self._write_transaction() as connection:
            self.create_upload_intent_in_transaction(
                record,
                quota_limit=quota_limit,
                connection=connection,
            )
        return record

    def create_upload_intent_in_transaction(
        self,
        record: UploadIntentRecord,
        *,
        quota_limit: int,
        connection: Connection,
    ) -> None:
        self._reserve_upload_quota(
            connection,
            byte_size=record.byte_size,
            quota_limit=quota_limit,
        )
        connection.execute(
            insert(upload_intents).values(
                intent_id=record.intent_id,
                request_key=record.request_key,
                original_filename=record.original_filename,
                byte_size=record.byte_size,
                declared_mime_type=record.declared_mime_type,
                state=record.state.value,
                created_at=record.created_at.isoformat(),
                expires_at=record.expires_at.isoformat(),
                upload_id=record.upload_id,
                job_id=record.job_id,
                media_id=record.media_id,
                transfer_backend=record.transfer_backend.value,
                index_after_import=record.index_after_import,
                index_modalities=list(record.index_modalities),
                index_job_id=record.index_job_id,
                index_command=record.index_command,
                source_path=record.source_path,
                content_sha256=record.content_sha256,
                failure_code=record.failure_code,
                failure_message=record.failure_message,
            )
        )

    def create_upload_session(
        self,
        record: UploadSessionRecord,
    ) -> UploadSessionRecord:
        with self._write_transaction() as connection:
            self.create_upload_session_in_transaction(record, connection=connection)
        return record

    def create_upload_session_in_transaction(
        self,
        record: UploadSessionRecord,
        *,
        connection: Connection,
    ) -> None:
        connection.execute(
            insert(upload_sessions).values(
                session_id=record.session_id,
                request_key=record.request_key,
                selector=record.selector,
                capability_digest=record.capability_digest,
                initiating_subject=record.initiating_subject,
                initiating_client_id=record.initiating_client_id,
                repository_binding=record.repository_binding,
                purpose=record.purpose,
                state=record.state.value,
                maximum_files=record.maximum_files,
                maximum_file_bytes=record.maximum_file_bytes,
                maximum_aggregate_bytes=record.maximum_aggregate_bytes,
                created_at=record.created_at.isoformat(),
                expires_at=record.expires_at.isoformat(),
                browser_session_digest=record.browser_session_digest,
                transfer_backend=record.transfer_backend.value,
                index_after_import=record.index_after_import,
                index_modalities=list(record.index_modalities),
            )
        )

    def get_upload_session_by_request(
        self,
        request_key: str,
    ) -> UploadSessionRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(upload_sessions).where(
                    upload_sessions.c.request_key == request_key
                )
            ).one_or_none()
        return None if row is None else _upload_session_record(row)

    def get_upload_session(
        self,
        session_id: str,
        *,
        connection: Connection | None = None,
        for_update: bool = False,
    ) -> UploadSessionRecord | None:
        statement = select(upload_sessions).where(
            upload_sessions.c.session_id == session_id
        )
        if for_update:
            statement = statement.with_for_update()
        if connection is not None:
            row = connection.execute(statement).one_or_none()
        else:
            with self.engine.connect() as active:
                row = active.execute(statement).one_or_none()
        return None if row is None else _upload_session_record(row)

    def get_upload_session_by_selector(
        self,
        selector: str,
        *,
        connection: Connection | None = None,
        for_update: bool = False,
    ) -> UploadSessionRecord | None:
        statement = select(upload_sessions).where(
            upload_sessions.c.selector == selector
        )
        if for_update:
            statement = statement.with_for_update()
        if connection is not None:
            row = connection.execute(statement).one_or_none()
        else:
            with self.engine.connect() as active:
                row = active.execute(statement).one_or_none()
        return None if row is None else _upload_session_record(row)

    def update_upload_session(
        self,
        session_id: str,
        *,
        connection: Connection,
        state: UploadSessionState | None = None,
        browser_session_digest: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if state is not None:
            values["state"] = state.value
        if browser_session_digest is not None:
            values["browser_session_digest"] = browser_session_digest
        if not values:
            return
        result = connection.execute(
            update(upload_sessions)
            .where(upload_sessions.c.session_id == session_id)
            .values(**values)
        )
        if result.rowcount != 1:
            raise RuntimeError("The upload session update was lost.")

    def create_upload_session_file(
        self,
        record: UploadSessionFileRecord,
        intent: UploadIntentRecord,
        *,
        quota_limit: int,
        connection: Connection,
    ) -> None:
        self.create_upload_intent_in_transaction(
            intent,
            quota_limit=quota_limit,
            connection=connection,
        )
        connection.execute(
            insert(upload_session_files).values(
                session_id=record.session_id,
                client_file_key=record.client_file_key,
                intent_id=record.intent_id,
                created_at=record.created_at.isoformat(),
                creation_grant_digest=record.creation_grant_digest,
                creation_grant_expires_at=(
                    record.creation_grant_expires_at.isoformat()
                    if record.creation_grant_expires_at is not None
                    else None
                ),
                creation_grant_consumed_at=(
                    record.creation_grant_consumed_at.isoformat()
                    if record.creation_grant_consumed_at is not None
                    else None
                ),
            )
        )

    def get_upload_session_file(
        self,
        session_id: str,
        client_file_key: str,
        *,
        connection: Connection,
        for_update: bool = False,
    ) -> UploadSessionFileRecord | None:
        statement = select(upload_session_files).where(
            upload_session_files.c.session_id == session_id,
            upload_session_files.c.client_file_key == client_file_key,
        )
        if for_update:
            statement = statement.with_for_update()
        row = connection.execute(statement).one_or_none()
        return None if row is None else _upload_session_file_record(row)

    def get_upload_session_file_by_intent(
        self,
        intent_id: str,
        *,
        connection: Connection,
        for_update: bool = False,
    ) -> UploadSessionFileRecord | None:
        statement = select(upload_session_files).where(
            upload_session_files.c.intent_id == intent_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = connection.execute(statement).one_or_none()
        return None if row is None else _upload_session_file_record(row)

    def get_upload_session_file_by_creation_grant(
        self,
        digest: str,
        *,
        connection: Connection,
        for_update: bool = False,
    ) -> UploadSessionFileRecord | None:
        statement = select(upload_session_files).where(
            upload_session_files.c.creation_grant_digest == digest
        )
        if for_update:
            statement = statement.with_for_update()
        row = connection.execute(statement).one_or_none()
        return None if row is None else _upload_session_file_record(row)

    def list_upload_session_files(
        self,
        session_id: str,
        *,
        connection: Connection | None = None,
    ) -> tuple[tuple[UploadSessionFileRecord, UploadIntentRecord], ...]:
        def read(active: Connection):
            rows = active.execute(
                select(upload_session_files)
                .where(upload_session_files.c.session_id == session_id)
                .order_by(
                    upload_session_files.c.created_at,
                    upload_session_files.c.client_file_key,
                )
            )
            items = []
            for row in rows:
                link = _upload_session_file_record(row)
                intent_row = active.execute(
                    select(upload_intents).where(
                        upload_intents.c.intent_id == link.intent_id
                    )
                ).one()
                items.append((link, _upload_record(intent_row)))
            return tuple(items)

        if connection is not None:
            return read(connection)
        with self.engine.connect() as active:
            return read(active)

    def set_upload_session_file_grant(
        self,
        session_id: str,
        client_file_key: str,
        *,
        digest: str,
        expires_at: datetime,
        connection: Connection,
    ) -> None:
        result = connection.execute(
            update(upload_session_files)
            .where(
                upload_session_files.c.session_id == session_id,
                upload_session_files.c.client_file_key == client_file_key,
            )
            .values(
                creation_grant_digest=digest,
                creation_grant_expires_at=expires_at.isoformat(),
                creation_grant_consumed_at=None,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("The upload creation grant update was lost.")

    def consume_upload_session_file_grant(
        self,
        session_id: str,
        client_file_key: str,
        *,
        consumed_at: datetime,
        connection: Connection,
    ) -> None:
        result = connection.execute(
            update(upload_session_files)
            .where(
                upload_session_files.c.session_id == session_id,
                upload_session_files.c.client_file_key == client_file_key,
            )
            .values(
                creation_grant_consumed_at=consumed_at.isoformat(),
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("The upload session file grant update was lost.")

    @staticmethod
    def _reserve_upload_quota(
        connection: Connection,
        *,
        byte_size: int,
        quota_limit: int,
    ) -> None:
        row = connection.execute(
            select(upload_quota.c.reserved_bytes)
            .where(upload_quota.c.singleton_id == _UPLOAD_QUOTA_ID)
            .with_for_update()
        ).one_or_none()
        if row is None:
            try:
                with connection.begin_nested():
                    connection.execute(
                        insert(upload_quota).values(
                            singleton_id=_UPLOAD_QUOTA_ID,
                            reserved_bytes=0,
                        )
                    )
            except IntegrityError:
                pass
            row = connection.execute(
                select(upload_quota.c.reserved_bytes)
                .where(upload_quota.c.singleton_id == _UPLOAD_QUOTA_ID)
                .with_for_update()
            ).one()
        reserved = int(row.reserved_bytes)
        if reserved + byte_size > quota_limit:
            raise UploadQuotaExceededError
        connection.execute(
            update(upload_quota)
            .where(upload_quota.c.singleton_id == _UPLOAD_QUOTA_ID)
            .values(reserved_bytes=reserved + byte_size)
        )

    def get_upload_intent_by_request(
        self,
        request_key: str,
    ) -> UploadIntentRecord | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(upload_intents).where(
                    upload_intents.c.request_key == request_key
                )
            ).one_or_none()
        return None if row is None else _upload_record(row)

    def get_upload_intent_by_upload_id(
        self,
        upload_id: str,
        *,
        connection: Connection | None = None,
        for_update: bool = False,
    ) -> UploadIntentRecord | None:
        statement = select(upload_intents).where(
            upload_intents.c.upload_id == upload_id
        )
        if for_update:
            statement = statement.with_for_update()
        if connection is not None:
            row = connection.execute(statement).one_or_none()
        else:
            with self.engine.connect() as active:
                row = active.execute(statement).one_or_none()
        return None if row is None else _upload_record(row)

    def get_upload_intent(
        self,
        intent_id: str,
        *,
        connection: Connection | None = None,
        for_update: bool = False,
    ) -> UploadIntentRecord | None:
        statement = select(upload_intents).where(
            upload_intents.c.intent_id == intent_id
        )
        if for_update:
            statement = statement.with_for_update()
        if connection is not None:
            row = connection.execute(statement).one_or_none()
        else:
            with self.engine.connect() as active:
                row = active.execute(statement).one_or_none()
        return None if row is None else _upload_record(row)

    def failed_index_uploads_for_media(
        self,
        media_id: str,
        *,
        connection: Connection,
    ) -> tuple[UploadIntentRecord, ...]:
        rows = connection.execute(
            select(upload_intents)
            .where(
                upload_intents.c.media_id == media_id,
                upload_intents.c.state == UploadState.ready.value,
                upload_intents.c.index_after_import.is_(True),
                upload_intents.c.index_job_id.is_not(None),
                upload_intents.c.failure_code.is_not(None),
            )
            .with_for_update()
        )
        return tuple(_upload_record(row) for row in rows)

    def update_upload(
        self,
        intent_id: str,
        *,
        state: UploadState,
        connection: Connection,
        upload_id: str | None = None,
        job_id: str | None = None,
        media_id: str | None = None,
        index_job_id: str | None = None,
        index_command: dict[str, Any] | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
        content_sha256: str | None = None,
        clear_upload_id: bool = False,
        clear_failure: bool = False,
        expected_states: set[UploadState] | None = None,
        expected_job_id: str | None | object = _EXPECTED_VALUE_UNSET,
        expected_index_job_id: str | None | object = _EXPECTED_VALUE_UNSET,
    ) -> bool:
        current = connection.execute(
            select(
                upload_intents.c.byte_size,
                upload_intents.c.state,
                upload_intents.c.job_id,
                upload_intents.c.index_job_id,
            )
            .where(upload_intents.c.intent_id == intent_id)
            .with_for_update()
        ).one_or_none()
        if current is None:
            raise RuntimeError("The upload intent update was lost.")
        if (
            expected_states is not None
            and UploadState(current.state) not in expected_states
        ):
            return False
        if (
            expected_job_id is not _EXPECTED_VALUE_UNSET
            and current.job_id != expected_job_id
        ):
            return False
        if (
            expected_index_job_id is not _EXPECTED_VALUE_UNSET
            and current.index_job_id != expected_index_job_id
        ):
            return False
        values: dict[str, Any] = {"state": state.value}
        if clear_upload_id:
            values["upload_id"] = None
        elif upload_id is not None:
            values["upload_id"] = upload_id
        if job_id is not None:
            values["job_id"] = job_id
        if media_id is not None:
            values["media_id"] = media_id
        if index_job_id is not None:
            values["index_job_id"] = index_job_id
        if index_command is not None:
            values["index_command"] = index_command
        if clear_failure:
            values["failure_code"] = None
            values["failure_message"] = None
        elif failure_code is not None:
            values["failure_code"] = failure_code
        if failure_message is not None:
            values["failure_message"] = failure_message
        if content_sha256 is not None:
            values["content_sha256"] = content_sha256
        result = connection.execute(
            update(upload_intents)
            .where(upload_intents.c.intent_id == intent_id)
            .values(**values)
        )
        if result.rowcount != 1:
            raise RuntimeError("The upload intent update was lost.")
        if (
            current.state in _RESERVED_UPLOAD_STATES
            and state.value not in _RESERVED_UPLOAD_STATES
        ):
            quota = connection.execute(
                select(upload_quota.c.reserved_bytes)
                .where(
                    upload_quota.c.singleton_id == _UPLOAD_QUOTA_ID,
                )
                .with_for_update()
            ).scalar_one()
            connection.execute(
                update(upload_quota)
                .where(
                    upload_quota.c.singleton_id == _UPLOAD_QUOTA_ID,
                )
                .values(
                    reserved_bytes=max(0, int(quota) - current.byte_size)
                )
            )
        return True

    def expired_uploads(
        self,
        *,
        now: datetime,
    ) -> tuple[UploadIntentRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(upload_intents).where(
                    upload_intents.c.expires_at <= now.isoformat(),
                    upload_intents.c.state.in_(
                        (
                            UploadState.pending.value,
                            UploadState.accepted.value,
                            UploadState.failed.value,
                        )
                    ),
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def recoverable_uploads(self) -> tuple[UploadIntentRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(upload_intents).where(
                    upload_intents.c.state == UploadState.accepted.value
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def cleanup_uploads(self) -> tuple[UploadIntentRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(upload_intents).where(
                    upload_intents.c.upload_id.is_not(None),
                    upload_intents.c.state.in_(
                        (
                            UploadState.ready.value,
                            UploadState.indexed.value,
                            UploadState.expired.value,
                        )
                    ),
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def active_ingestions(self) -> tuple[UploadIntentRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(upload_intents).where(
                    or_(
                        upload_intents.c.state.in_(
                            (
                                UploadState.pending.value,
                                UploadState.processing.value,
                            )
                        ),
                        and_(
                            upload_intents.c.state == UploadState.accepted.value,
                            upload_intents.c.transfer_backend
                            == UploadTransferBackend.multipart.value,
                        ),
                        and_(
                            upload_intents.c.state == UploadState.ready.value,
                            upload_intents.c.index_after_import.is_(True),
                            upload_intents.c.failure_code.is_(None),
                        ),
                    )
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def with_upload_transaction(
        self,
        operation: Callable[[Connection], Any],
    ) -> Any:
        with self._write_transaction() as connection:
            return operation(connection)
