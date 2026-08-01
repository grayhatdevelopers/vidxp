from __future__ import annotations

from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, Table, insert, select, update

from vidxp.infrastructure.sql_catalog import SQLCatalog
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

CATALOG_SCHEMA_VERSION = 4
_local_metadata = MetaData()
catalog_metadata = Table(
    "catalog_metadata",
    _local_metadata,
    Column("schema_version", Integer, nullable=False),
)


class LocalCatalog(SQLCatalog):
    """Repository-scoped SQLite catalog using the shared SQL adapter."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            f"sqlite:///{database.resolve().as_posix()}",
            initialize=False,
        )
        metadata.create_all(
            self.engine,
            tables=(
                media,
                artifacts,
                artifact_requests,
                media_import_requests,
                upload_intents,
                upload_sessions,
                upload_session_files,
                upload_quota,
            ),
        )
        _local_metadata.create_all(self.engine)
        with self.transaction() as connection:
            version = connection.execute(
                select(catalog_metadata.c.schema_version)
            ).scalar_one_or_none()
            if version is None:
                connection.execute(
                    insert(catalog_metadata).values(
                        schema_version=CATALOG_SCHEMA_VERSION
                    )
                )
            elif version in {1, 2, 3}:
                connection.execute(
                    update(catalog_metadata).values(
                        schema_version=CATALOG_SCHEMA_VERSION
                    )
                )
            elif version != CATALOG_SCHEMA_VERSION:
                raise RuntimeError(
                    "The repository catalog schema is incompatible."
                )
