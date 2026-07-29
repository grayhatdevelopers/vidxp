from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import Engine, insert, select, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from vidxp.core.contracts import IndexConfig, IndexSchemaError
from vidxp.core.generations import CompletedGenerationManifest
from vidxp.core.manifest import MANIFEST_FILE, sha256_file
from vidxp.core.snapshots import GenerationReference, IndexSnapshot
from vidxp.index_state import (
    IndexNotReadyError,
    IndexingInProgressError,
    snapshot_status,
)
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository
from vidxp.infrastructure.sql_tables import (
    index_generations,
    index_snapshots,
    index_state,
)

_INDEX_STATE_ID = "1"
_INDEX_LOCK_IDENTITY = "vidxp:index"


def _checksum(snapshot: IndexSnapshot) -> str:
    payload = json.dumps(
        snapshot.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SQLSnapshotRepository(LocalSnapshotRepository):
    """PostgreSQL-authoritative snapshots with shared generation manifests."""

    def __init__(
        self,
        indexes: str | Path,
        *,
        engine: Engine,
    ) -> None:
        super().__init__(indexes)
        self.engine = engine

    @contextmanager
    def lease(self) -> Iterator[None]:
        self.ensure_directories()
        with self.engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:identity))"),
                {"identity": _INDEX_LOCK_IDENTITY},
            ).scalar_one()
            if not acquired:
                raise IndexingInProgressError(
                    "An index mutation is already active for this repository."
                )
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:identity))"),
                    {"identity": _INDEX_LOCK_IDENTITY},
                )

    def mutation_in_progress(self) -> bool:
        with self.engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:identity))"),
                {"identity": _INDEX_LOCK_IDENTITY},
            ).scalar_one()
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:identity))"),
                    {"identity": _INDEX_LOCK_IDENTITY},
                )
            return not bool(acquired)

    @staticmethod
    def _ensure_index_state(connection: Connection) -> None:
        if connection.execute(
            select(index_state.c.singleton_id).where(
                index_state.c.singleton_id == _INDEX_STATE_ID
            )
        ).scalar_one_or_none() is not None:
            return
        try:
            with connection.begin_nested():
                connection.execute(
                    insert(index_state).values(singleton_id=_INDEX_STATE_ID)
                )
        except IntegrityError:
            pass

    def read_active(self, *, required: bool = False) -> IndexSnapshot | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(
                    index_state.c.active_snapshot_id,
                    index_state.c.active_snapshot_sha256,
                ).where(index_state.c.singleton_id == _INDEX_STATE_ID)
            ).one_or_none()
            if row is None or row.active_snapshot_id is None:
                if required:
                    raise IndexNotReadyError(
                        "No active index snapshot was found. Index media first."
                    )
                return None
            return self._read_snapshot(
                connection,
                row.active_snapshot_id,
                expected_sha256=row.active_snapshot_sha256,
            )

    def read_snapshot(
        self,
        snapshot_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> IndexSnapshot:
        with self.engine.connect() as connection:
            return self._read_snapshot(
                connection,
                snapshot_id,
                expected_sha256=expected_sha256,
            )

    def _read_snapshot(
        self,
        connection: Connection,
        snapshot_id: str,
        *,
        expected_sha256: str | None,
    ) -> IndexSnapshot:
        row = connection.execute(
            select(
                index_snapshots.c.sha256,
                index_snapshots.c.payload,
            ).where(index_snapshots.c.snapshot_id == snapshot_id)
        ).one_or_none()
        if row is None:
            raise IndexSchemaError(f"Index snapshot {snapshot_id} is missing.")
        if expected_sha256 is not None and row.sha256 != expected_sha256:
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} failed integrity validation."
            )
        try:
            snapshot = IndexSnapshot.model_validate(row.payload, strict=False)
        except ValueError as exc:
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} is invalid."
            ) from exc
        if snapshot.snapshot_id != snapshot_id or _checksum(snapshot) != row.sha256:
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} failed integrity validation."
            )
        self._validate_generations(snapshot)
        return snapshot

    def validate_generation(
        self,
        reference: GenerationReference,
    ):
        with self.engine.connect() as connection:
            payload = connection.execute(
                select(index_generations.c.payload).where(
                    index_generations.c.generation_id
                    == reference.generation_id,
                )
            ).scalar_one_or_none()
        if (
            payload is None
            or GenerationReference.model_validate(payload, strict=False)
            != reference
        ):
            raise IndexSchemaError(
                f"Generation {reference.generation_id} is not published."
            )
        return super().validate_generation(reference)

    def generation_reference(
        self,
        *,
        generation_id: str,
        media_id: str,
    ) -> GenerationReference:
        manifest_path = self.generation_directory(generation_id) / MANIFEST_FILE
        try:
            manifest = CompletedGenerationManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            reference = GenerationReference(
                generation_id=generation_id,
                media_id=media_id,
                manifest_sha256=sha256_file(manifest_path),
                input_sha256=manifest.inputs[media_id].sha256,
                config_fingerprint=manifest.config_fingerprint,
                modalities=tuple(
                    manifest.configuration["enabled_modalities"]
                ),
                record_counts=dict(manifest.record_counts),
                store_size_bytes_at_commit=(
                    manifest.store_size_bytes_at_commit
                ),
            )
        except (OSError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise IndexSchemaError(
                f"Completed generation {generation_id} has an invalid manifest."
            ) from exc
        LocalSnapshotRepository.validate_generation(self, reference)
        return reference

    def publish_generation(
        self,
        reference: GenerationReference,
        config: IndexConfig,
    ) -> IndexSnapshot:
        return self._publish(
            replacement=reference,
            remove_media_id=None,
            config_fingerprint=reference.config_fingerprint,
            configuration=self.snapshot_configuration(config),
        )

    def remove(self, media_id: str) -> bool:
        active = self.read_active()
        if active is None or media_id not in active.generations:
            return False
        self._publish(
            replacement=None,
            remove_media_id=media_id,
            config_fingerprint=active.config_fingerprint,
            configuration=dict(active.configuration),
        )
        return True

    def clear(self) -> bool:
        active = self.read_active()
        if active is None or not active.generations:
            return False
        self._publish(
            replacement=None,
            remove_media_id="*",
            config_fingerprint=active.config_fingerprint,
            configuration=dict(active.configuration),
        )
        return True

    def _publish(
        self,
        *,
        replacement: GenerationReference | None,
        remove_media_id: str | None,
        config_fingerprint: str,
        configuration: dict[str, Any],
    ) -> IndexSnapshot:
        with self.engine.begin() as connection:
            self._ensure_index_state(connection)
            state = connection.execute(
                select(
                    index_state.c.active_snapshot_id,
                    index_state.c.active_snapshot_sha256,
                )
                .where(index_state.c.singleton_id == _INDEX_STATE_ID)
                .with_for_update()
            ).one()
            active = (
                None
                if state.active_snapshot_id is None
                else self._read_snapshot(
                    connection,
                    state.active_snapshot_id,
                    expected_sha256=state.active_snapshot_sha256,
                )
            )
            generations = dict(active.generations) if active is not None else {}
            if remove_media_id == "*":
                generations.clear()
            elif remove_media_id is not None:
                generations.pop(remove_media_id, None)
            if replacement is not None:
                existing = connection.execute(
                    select(index_generations.c.payload).where(
                        index_generations.c.generation_id
                        == replacement.generation_id
                    )
                ).scalar_one_or_none()
                if existing is None:
                    connection.execute(
                        insert(index_generations).values(
                            generation_id=replacement.generation_id,
                            media_id=replacement.media_id,
                            manifest_sha256=replacement.manifest_sha256,
                            payload=replacement.model_dump(mode="json"),
                        )
                    )
                elif (
                    GenerationReference.model_validate(existing, strict=False)
                    != replacement
                ):
                    raise IndexSchemaError(
                        "The generation identity is already published "
                        "with different metadata."
                    )
                other_media = set(generations) - {replacement.media_id}
                if other_media and active is not None:
                    configuration = dict(active.configuration)
                generations[replacement.media_id] = replacement
            snapshot = IndexSnapshot(
                snapshot_id=uuid4().hex,
                created_at=datetime.now(timezone.utc),
                config_fingerprint=config_fingerprint,
                configuration=configuration,
                generations=generations,
            )
            checksum = _checksum(snapshot)
            connection.execute(
                insert(index_snapshots).values(
                    snapshot_id=snapshot.snapshot_id,
                    created_at=snapshot.created_at.isoformat(),
                    sha256=checksum,
                    payload=snapshot.model_dump(mode="json"),
                )
            )
            connection.execute(
                update(index_state)
                .where(index_state.c.singleton_id == _INDEX_STATE_ID)
                .values(
                    active_snapshot_id=snapshot.snapshot_id,
                    active_snapshot_sha256=checksum,
                )
            )
            return snapshot

    def active_config(
        self,
        *,
        device: str,
    ) -> tuple[IndexConfig, IndexSnapshot]:
        snapshot = self.read_active(required=True)
        assert snapshot is not None
        if not snapshot.generations:
            raise IndexNotReadyError(
                "The active index snapshot contains no media."
            )
        return (
            self._config_for_snapshot(
                snapshot,
                snapshot_sha256=_checksum(snapshot),
                device=device,
            ),
            snapshot,
        )

    def status(self) -> dict[str, Any] | None:
        snapshot = self.read_active()
        if snapshot is None:
            return None
        return snapshot_status(snapshot)
