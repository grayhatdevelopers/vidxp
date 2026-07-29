from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, delete, event, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from vidxp.core.artifacts import ArtifactRecord, ArtifactState
from vidxp.core.media import MediaRecord, utc_now
from vidxp.core.uploads import UploadIntentRecord, UploadState
from vidxp.infrastructure.sql_tables import (
    artifact_requests,
    artifacts,
    media,
    media_import_requests,
    metadata,
    upload_intents,
    upload_quota,
)

_RESERVED_UPLOAD_STATES = {
    UploadState.pending.value,
    UploadState.accepted.value,
    UploadState.processing.value,
    UploadState.failed.value,
}
_UPLOAD_QUOTA_ID = "1"


class UploadQuotaExceededError(RuntimeError):
    """Raised when an atomic upload reservation exceeds its quota."""


def _payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("The relational catalog payload is invalid.")
    return value


def _record(model: Any, value: Any) -> Any:
    return model.model_validate(_payload(value), strict=False)


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
    ) -> tuple[MediaRecord, ...]:
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset nonnegative")
        with self.engine.connect() as connection:
            payloads = connection.execute(
                select(media.c.payload)
                .order_by(media.c.created_at, media.c.media_id)
                .limit(limit)
                .offset(offset)
            ).scalars()
            return tuple(
                _record(MediaRecord, payload)
                for payload in payloads
            )

    def count_media(self) -> int:
        with self.engine.connect() as connection:
            return int(
                connection.execute(
                    select(func.count()).select_from(media)
                ).scalar_one()
            )

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
                )
            )
        return record

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

    def update_upload(
        self,
        intent_id: str,
        *,
        state: UploadState,
        connection: Connection,
        upload_id: str | None = None,
        job_id: str | None = None,
        media_id: str | None = None,
        clear_upload_id: bool = False,
        expected_states: set[UploadState] | None = None,
    ) -> bool:
        current = connection.execute(
            select(
                upload_intents.c.byte_size,
                upload_intents.c.state,
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
        values: dict[str, Any] = {"state": state.value}
        if clear_upload_id:
            values["upload_id"] = None
        elif upload_id is not None:
            values["upload_id"] = upload_id
        if job_id is not None:
            values["job_id"] = job_id
        if media_id is not None:
            values["media_id"] = media_id
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
                            UploadState.expired.value,
                        )
                    ),
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def processing_uploads(self) -> tuple[UploadIntentRecord, ...]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(upload_intents).where(
                    upload_intents.c.state == UploadState.processing.value
                )
            )
            return tuple(_upload_record(row) for row in rows)

    def with_upload_transaction(
        self,
        operation: Callable[[Connection], Any],
    ) -> Any:
        with self._write_transaction() as connection:
            return operation(connection)
