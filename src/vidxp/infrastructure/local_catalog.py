from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from vidxp.core.artifacts import ArtifactRecord, ArtifactState
from vidxp.core.media import MediaRecord, utc_now


CATALOG_SCHEMA_VERSION = 2


class LocalCatalog:
    """Repository-scoped SQLite catalog for media and generated artifacts."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_metadata (
                    schema_version INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS catalog_metadata_singleton
                    ON catalog_metadata((1));
                CREATE TABLE IF NOT EXISTS media (
                    media_id TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    media_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(media_id) REFERENCES media(media_id)
                );
                CREATE INDEX IF NOT EXISTS artifacts_media_id
                    ON artifacts(media_id);
                CREATE TABLE IF NOT EXISTS artifact_requests (
                    request_key TEXT PRIMARY KEY,
                    artifact_id TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS media_import_requests (
                    request_key TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    media_id TEXT,
                    FOREIGN KEY(media_id) REFERENCES media(media_id)
                );
                """
            )
            row = connection.execute(
                "SELECT schema_version FROM catalog_metadata"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT OR IGNORE INTO catalog_metadata"
                    "(schema_version) VALUES (?)",
                    (CATALOG_SCHEMA_VERSION,),
                )
            elif row[0] == 1:
                connection.execute(
                    "UPDATE catalog_metadata SET schema_version = ?",
                    (CATALOG_SCHEMA_VERSION,),
                )
            elif row[0] != CATALOG_SCHEMA_VERSION:
                raise RuntimeError(
                    "The repository catalog schema is incompatible."
                )

    def get_media(self, media_id: str) -> MediaRecord | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT payload FROM media WHERE media_id = ?",
                (media_id,),
            ).fetchone()
        return None if row is None else MediaRecord.model_validate_json(row[0])

    def get_media_by_checksum(self, sha256: str) -> MediaRecord | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT payload FROM media WHERE sha256 = ?",
                (sha256,),
            ).fetchone()
        return None if row is None else MediaRecord.model_validate_json(row[0])

    def put_media(self, record: MediaRecord) -> MediaRecord:
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            id_row = connection.execute(
                "SELECT payload FROM media WHERE media_id = ?",
                (record.media_id,),
            ).fetchone()
            if id_row is not None:
                authoritative = MediaRecord.model_validate_json(id_row[0])
                if authoritative != record:
                    raise FileExistsError(
                        f"Media {record.media_id} already has another record."
                    )
                return authoritative
            checksum_row = connection.execute(
                "SELECT payload FROM media WHERE sha256 = ?",
                (record.sha256,),
            ).fetchone()
            if checksum_row is not None:
                return MediaRecord.model_validate_json(checksum_row[0])
            connection.execute(
                """
                INSERT INTO media(media_id, sha256, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.media_id,
                    record.sha256,
                    record.created_at.isoformat(),
                    record.model_dump_json(),
                ),
            )
        return record

    def list_media(
        self,
        *,
        limit: int,
        offset: int = 0,
    ) -> tuple[MediaRecord, ...]:
        if limit <= 0 or offset < 0:
            raise ValueError("limit must be positive and offset nonnegative")
        with self._session() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM media
                ORDER BY created_at, media_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return tuple(MediaRecord.model_validate_json(row[0]) for row in rows)

    def count_media(self) -> int:
        with self._session() as connection:
            row = connection.execute("SELECT COUNT(*) FROM media").fetchone()
        return int(row[0])

    def reserve_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
    ) -> MediaRecord | None:
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_fingerprint, media_id
                FROM media_import_requests
                WHERE request_key = ?
                """,
                (request_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO media_import_requests(
                        request_key, request_fingerprint
                    )
                    VALUES (?, ?)
                    """,
                    (request_key, request_fingerprint),
                )
                return None
            stored_fingerprint, media_id = row
            if stored_fingerprint != request_fingerprint:
                raise FileExistsError(
                    "The media import key is bound to different content."
                )
            if media_id is None:
                return None
            media = connection.execute(
                "SELECT payload FROM media WHERE media_id = ?",
                (media_id,),
            ).fetchone()
            if media is None:
                raise RuntimeError(
                    "A completed media import references missing media."
                )
            return MediaRecord.model_validate_json(media[0])

    def complete_media_import(
        self,
        request_key: str,
        request_fingerprint: str,
        record: MediaRecord,
    ) -> None:
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_fingerprint, media_id
                FROM media_import_requests
                WHERE request_key = ?
                """,
                (request_key,),
            ).fetchone()
            if row is None or row[0] != request_fingerprint:
                raise FileExistsError(
                    "The media import reservation does not match the content."
                )
            if row[1] is not None and row[1] != record.media_id:
                raise FileExistsError(
                    "The media import key is already completed."
                )
            connection.execute(
                """
                UPDATE media_import_requests
                SET media_id = ?
                WHERE request_key = ?
                """,
                (record.media_id, request_key),
            )

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT payload FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return (
            None
            if row is None
            else ArtifactRecord.model_validate_json(row[0])
        )

    def get_artifact_by_request(
        self,
        request_key: str,
    ) -> ArtifactRecord | None:
        with self._session() as connection:
            row = connection.execute(
                """
                SELECT artifacts.payload
                FROM artifact_requests
                JOIN artifacts USING (artifact_id)
                WHERE artifact_requests.request_key = ?
                """,
                (request_key,),
            ).fetchone()
        if row is None:
            return None
        record = ArtifactRecord.model_validate_json(row[0])
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
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request_row = connection.execute(
                """
                SELECT artifacts.payload
                FROM artifact_requests
                JOIN artifacts USING (artifact_id)
                WHERE artifact_requests.request_key = ?
                """,
                (record.request_key,),
            ).fetchone()
            if request_row is not None:
                existing = ArtifactRecord.model_validate_json(request_row[0])
                if (
                    existing.state == ArtifactState.ready
                    and (
                        existing.expires_at is None
                        or existing.expires_at > utc_now()
                    )
                ):
                    return existing
                connection.execute(
                    "DELETE FROM artifact_requests WHERE request_key = ?",
                    (record.request_key,),
                )
            row = connection.execute(
                "SELECT payload FROM artifacts WHERE artifact_id = ?",
                (record.artifact_id,),
            ).fetchone()
            if row is not None:
                existing = ArtifactRecord.model_validate_json(row[0])
                if existing != record:
                    raise FileExistsError(
                        f"Artifact {record.artifact_id} already exists."
                    )
                return existing
            connection.execute(
                """
                INSERT INTO artifacts(
                    artifact_id, media_id, created_at, payload
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    record.artifact_id,
                    record.media_id,
                    record.created_at.isoformat(),
                    record.model_dump_json(),
                ),
            )
            connection.execute(
                """
                INSERT INTO artifact_requests(request_key, artifact_id)
                VALUES (?, ?)
                """,
                (record.request_key, record.artifact_id),
            )
        return record

    def invalidate_artifact_request(
        self,
        request_key: str,
        artifact_id: str,
    ) -> None:
        with self._session() as connection:
            connection.execute(
                """
                DELETE FROM artifact_requests
                WHERE request_key = ? AND artifact_id = ?
                """,
                (request_key, artifact_id),
            )
