from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from filelock import FileLock, Timeout
from pydantic import ValidationError

from vidxp.core.contracts import (
    IndexConfig,
    IndexSchemaError,
)
from vidxp.core.manifest import MANIFEST_FILE, sha256_file, write_json_atomic
from vidxp.core.generations import CompletedGenerationManifest
from vidxp.core.snapshots import (
    ActiveSnapshotPointer,
    GenerationReference,
    IndexSnapshot,
)
from vidxp.index_state import (
    IndexNotReadyError,
    IndexingInProgressError,
    snapshot_status,
)
from vidxp.repository_layout import RepositoryLayout


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LocalSnapshotRepository:
    """Durable snapshot metadata for one local index repository.

    The repository is intended for a local filesystem with working advisory
    file locks and atomic same-filesystem replacement semantics.
    """

    def __init__(self, indexes: str | Path):
        index_path = Path(indexes)
        self.layout = RepositoryLayout(root=index_path.parent)
        if index_path != self.layout.indexes:
            raise ValueError(
                "LocalSnapshotRepository requires a RepositoryLayout indexes "
                "directory."
            )

    @property
    def indexes(self) -> Path:
        return self.layout.indexes

    @property
    def store(self) -> Path:
        return self.layout.index_store

    @property
    def generations(self) -> Path:
        return self.layout.generations

    @property
    def snapshots(self) -> Path:
        return self.layout.snapshots

    @property
    def active_pointer(self) -> Path:
        return self.layout.active_snapshot

    @property
    def lease_path(self) -> Path:
        return self.layout.index_lease

    def ensure_directories(self) -> None:
        for path in (
            self.indexes,
            self.store,
            self.generations,
            self.snapshots,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def lease(self) -> Iterator[None]:
        self.ensure_directories()
        lock = FileLock(self.lease_path)
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise IndexingInProgressError(
                f"An index mutation is already active for {self.indexes}."
            ) from exc
        try:
            yield
        finally:
            lock.release()

    def mutation_in_progress(self) -> bool:
        self.ensure_directories()
        lock = FileLock(self.lease_path)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return True
        lock.release()
        return False

    def new_generation_id(self) -> str:
        return uuid4().hex

    def generation_directory(self, generation_id: str) -> Path:
        return self.generations / generation_id

    def _snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshots / f"{snapshot_id}.json"

    def read_active(self, *, required: bool = False) -> IndexSnapshot | None:
        resolved = self._read_active(required=required)
        return None if resolved is None else resolved[1]

    def _read_active(
        self,
        *,
        required: bool = False,
    ) -> tuple[ActiveSnapshotPointer, IndexSnapshot] | None:
        if not self.active_pointer.is_file():
            if required:
                raise IndexNotReadyError(
                    "No active index snapshot was found. Index media first."
                )
            return None
        try:
            pointer = ActiveSnapshotPointer.model_validate_json(
                self.active_pointer.read_text(encoding="utf-8")
            )
            snapshot = self.read_snapshot(
                pointer.snapshot_id,
                expected_sha256=pointer.snapshot_sha256,
            )
            return pointer, snapshot
        except IndexSchemaError:
            raise
        except (OSError, ValueError, ValidationError) as exc:
            raise IndexSchemaError(
                "The active snapshot metadata is invalid."
            ) from exc

    def read_snapshot(
        self,
        snapshot_id: str,
        *,
        expected_sha256: str | None = None,
    ) -> IndexSnapshot:
        snapshot_path = self._snapshot_path(snapshot_id)
        if not snapshot_path.is_file():
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} is missing."
            )
        if (
            expected_sha256 is not None
            and sha256_file(snapshot_path) != expected_sha256
        ):
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} failed integrity validation."
            )
        try:
            snapshot = IndexSnapshot.model_validate_json(
                snapshot_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise IndexSchemaError(
                f"Index snapshot {snapshot_id} is invalid."
            ) from exc
        if snapshot.snapshot_id != snapshot_id:
            raise IndexSchemaError(
                "The snapshot filename and document identifier differ."
            )
        self._validate_generations(snapshot)
        return snapshot

    def _validate_generations(self, snapshot: IndexSnapshot) -> None:
        for media_id, reference in snapshot.generations.items():
            if reference.config_fingerprint != snapshot.config_fingerprint:
                raise IndexSchemaError(
                    f"Generation metadata for {media_id!r} has a "
                    "different index profile."
                )
            self.validate_generation(reference)

    def validate_generation(
        self,
        reference: GenerationReference,
    ) -> CompletedGenerationManifest:
        manifest_path = (
            self.generation_directory(reference.generation_id) / MANIFEST_FILE
        )
        if not manifest_path.is_file():
            raise IndexSchemaError(
                f"Manifest for generation {reference.generation_id} is missing."
            )
        if sha256_file(manifest_path) != reference.manifest_sha256:
            raise IndexSchemaError(
                f"Manifest for generation {reference.generation_id} "
                "failed integrity validation."
            )
        try:
            manifest = CompletedGenerationManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise IndexSchemaError(
                f"Manifest for generation {reference.generation_id} is invalid."
            ) from exc
        if (
            manifest.generation_id != reference.generation_id
            or manifest.config_fingerprint != reference.config_fingerprint
        ):
            raise IndexSchemaError(
                f"Manifest for generation {reference.generation_id} "
                "does not describe a completed compatible generation."
            )
        source = manifest.inputs.get(reference.media_id)
        if source is None or source.sha256 != reference.input_sha256:
            raise IndexSchemaError(
                f"Input checksum for generation {reference.generation_id} "
                "does not match its snapshot reference."
            )
        if (
            manifest.store_size_bytes_at_commit
            != reference.store_size_bytes_at_commit
        ):
            raise IndexSchemaError(
                f"Index size for generation {reference.generation_id} "
                "does not match its snapshot reference."
            )
        if set(manifest.completed_videos) != {reference.media_id}:
            raise IndexSchemaError(
                f"Generation {reference.generation_id} is not a complete "
                "single-media generation."
            )
        if tuple(manifest.configuration["enabled_modalities"]) != (
            reference.modalities
        ) or dict(manifest.record_counts) != dict(reference.record_counts):
            raise IndexSchemaError(
                f"Modalities for generation {reference.generation_id} "
                "do not match its snapshot reference."
            )
        return manifest

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
        self.validate_generation(reference)
        return reference

    def publish_generation(
        self,
        reference: GenerationReference,
        config: IndexConfig,
    ) -> IndexSnapshot:
        active = self.require_compatible_profile(
            media_id=reference.media_id,
            config_fingerprint=reference.config_fingerprint,
        )
        generations = dict(active.generations) if active is not None else {}
        other_media = set(generations) - {reference.media_id}
        generations[reference.media_id] = reference
        configuration = (
            dict(active.configuration)
            if active is not None and other_media
            else self.snapshot_configuration(config)
        )
        return self._publish(
            generations=generations,
            config_fingerprint=reference.config_fingerprint,
            configuration=configuration,
        )

    def require_compatible_profile(
        self,
        *,
        media_id: str,
        config_fingerprint: str,
    ) -> IndexSnapshot | None:
        active = self.read_active()
        if active is None:
            return None
        other_media = set(active.generations) - {media_id}
        if (
            other_media
            and active.config_fingerprint != config_fingerprint
        ):
            raise IndexSchemaError(
                "All media in one active snapshot must use the same index "
                "profile. Re-index or clear the other media first."
            )
        return active

    def remove(self, media_id: str) -> bool:
        active = self.read_active()
        if active is None or media_id not in active.generations:
            return False
        generations = dict(active.generations)
        del generations[media_id]
        self._publish(
            generations=generations,
            config_fingerprint=active.config_fingerprint,
            configuration=dict(active.configuration),
        )
        return True

    def clear(self) -> bool:
        active = self.read_active()
        if active is None or not active.generations:
            return False
        self._publish(
            generations={},
            config_fingerprint=active.config_fingerprint,
            configuration=dict(active.configuration),
        )
        return True

    def _publish(
        self,
        *,
        generations: Mapping[str, GenerationReference],
        config_fingerprint: str,
        configuration: dict[str, Any],
    ) -> IndexSnapshot:
        snapshot = IndexSnapshot(
            snapshot_id=uuid4().hex,
            created_at=_utc_now(),
            config_fingerprint=config_fingerprint,
            configuration=configuration,
            generations=dict(generations),
        )
        snapshot_path = self._snapshot_path(snapshot.snapshot_id)
        if snapshot_path.exists():
            raise FileExistsError(
                f"Snapshot {snapshot.snapshot_id} already exists."
            )
        write_json_atomic(
            snapshot_path,
            snapshot.model_dump(mode="json"),
        )
        pointer = ActiveSnapshotPointer(
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=sha256_file(snapshot_path),
            updated_at=_utc_now(),
        )
        write_json_atomic(
            self.active_pointer,
            pointer.model_dump(mode="json"),
        )
        return snapshot

    @staticmethod
    def snapshot_configuration(config: IndexConfig) -> dict[str, Any]:
        payload = asdict(config)
        for key in (
            "video_id",
            "output_root",
            "storage_directory",
            "generation_directory",
            "generation_id",
            "snapshot_id",
            "snapshot_sha256",
        ):
            payload.pop(key, None)
        payload["enabled_modalities"] = list(config.enabled_modalities)
        payload["collection_names"] = dict(config.collection_names)
        payload["capability_options"] = {
            name: dict(values)
            for name, values in config.capability_options.items()
        }
        return payload

    def active_config(
        self,
        *,
        device: str,
    ) -> tuple[IndexConfig, IndexSnapshot]:
        resolved = self._read_active(required=True)
        assert resolved is not None
        pointer, snapshot = resolved
        if not snapshot.generations:
            raise IndexNotReadyError(
                "The active index snapshot contains no media."
            )
        return (
            self._config_for_snapshot(
                snapshot,
                snapshot_sha256=pointer.snapshot_sha256,
                device=device,
            ),
            snapshot,
        )

    def config_for_snapshot(
        self,
        snapshot_id: str,
        *,
        snapshot_sha256: str,
        device: str,
    ) -> IndexConfig:
        snapshot = self.read_snapshot(
            snapshot_id,
            expected_sha256=snapshot_sha256,
        )
        if not snapshot.generations:
            raise IndexNotReadyError(
                "The requested index snapshot contains no media."
            )
        return self._config_for_snapshot(
            snapshot,
            snapshot_sha256=snapshot_sha256,
            device=device,
        )

    def _config_for_snapshot(
        self,
        snapshot: IndexSnapshot,
        *,
        snapshot_sha256: str,
        device: str,
    ) -> IndexConfig:
        stored = dict(snapshot.configuration)
        stored["enabled_modalities"] = tuple(stored["enabled_modalities"])
        stored["collection_names"] = dict(stored["collection_names"])
        stored.update(
            {
                "storage_directory": str(self.store),
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_sha256": snapshot_sha256,
                "device": device,
            }
        )
        try:
            config = IndexConfig(**stored)
        except (TypeError, ValueError) as exc:
            raise IndexSchemaError(
                "The snapshot configuration is invalid."
            ) from exc
        if config.fingerprint() != snapshot.config_fingerprint:
            raise IndexSchemaError(
                "The snapshot configuration fingerprint is invalid."
            )
        return config

    def status(self) -> dict[str, Any] | None:
        resolved = self._read_active()
        if resolved is None:
            return None
        return snapshot_status(resolved[1])
