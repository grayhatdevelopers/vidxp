from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

from vidxp.capabilities.contracts import (
    RuntimeCheckBinding,
    module_import_check,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    IndexSchemaError,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.manifest import MANIFEST_FILE, ManifestStore
from vidxp.core.runner import index_video
from vidxp.core.storage import (
    ChromaClientFactory,
    IndexStorage,
    SnapshotScopedIndexStore,
)
from vidxp.infrastructure.local_snapshots import LocalSnapshotRepository
from vidxp.repository_layout import RepositoryLayout
from vidxp.runtime import ModelRuntime


def _index_runtime_checks(
    chroma_client: str,
) -> tuple[RuntimeCheckBinding, ...]:
    return (
        RuntimeCheckBinding(
            capability="storage",
            check=module_import_check(
                "Chroma storage import",
                "chromadb",
                chroma_client,
            ),
        ),
        RuntimeCheckBinding(
            capability="storage",
            check=module_import_check(
                "Host resource monitor import",
                "psutil",
                "virtual_memory",
            ),
        ),
    )


LOCAL_INDEX_RUNTIME_CHECKS = _index_runtime_checks("PersistentClient")
SERVER_INDEX_RUNTIME_CHECKS = _index_runtime_checks("HttpClient")


class LocalIndexReader:
    """Model-free access to immutable local index snapshots."""

    def __init__(
        self,
        layout: RepositoryLayout,
        *,
        chroma_server_url: str | None = None,
        snapshot_repository: LocalSnapshotRepository | None = None,
    ) -> None:
        self.layout = layout
        self.repository = snapshot_repository or LocalSnapshotRepository(
            layout.indexes
        )
        self.chroma_clients = ChromaClientFactory(chroma_server_url)

    def _require_index_directory(self, index_directory: str | Path) -> None:
        if Path(index_directory) != self.layout.indexes:
            raise ValueError(
                "The index operation is outside the configured repository."
            )

    def active_config(
        self,
        index_directory: Path,
        *,
        device: str,
    ) -> IndexConfig:
        self._require_index_directory(index_directory)
        config, _snapshot = self.repository.active_config(device=device)
        return config

    def config_for_snapshot(
        self,
        index_directory: Path,
        *,
        snapshot_id: str,
        snapshot_sha256: str,
        device: str,
    ) -> IndexConfig:
        self._require_index_directory(index_directory)
        return self.repository.config_for_snapshot(
            snapshot_id,
            snapshot_sha256=snapshot_sha256,
            device=device,
        )

    def open_store(self, config: IndexConfig) -> SnapshotScopedIndexStore:
        if config.snapshot_id is None:
            raise IndexSchemaError("A snapshot ID is required for index reads.")
        if config.snapshot_sha256 is None:
            raise IndexSchemaError(
                "A snapshot checksum is required for index reads."
            )
        snapshot = self.repository.read_snapshot(
            config.snapshot_id,
            expected_sha256=config.snapshot_sha256,
        )
        generation_ids = tuple(
            reference.generation_id
            for reference in snapshot.generations.values()
        )
        storage = self._open_committed_storage(config)
        return SnapshotScopedIndexStore(
            storage,
            generation_ids=generation_ids,
        )

    @classmethod
    def _validate_snapshot_storage(
        cls,
        storage: IndexStorage,
        snapshot,
        base_config: IndexConfig | None = None,
    ) -> None:
        active_config = base_config or storage.config
        for reference in snapshot.generations.values():
            try:
                actual = {
                    modality: storage.count_records(
                        modality,
                        video_id=reference.media_id,
                        generation_ids=(reference.generation_id,),
                    )
                    for modality in active_config.enabled_modalities
                }
            except FileNotFoundError as exc:
                raise IndexSchemaError(
                    "A committed Chroma collection is missing."
                ) from exc
            if actual != dict(reference.record_counts):
                raise IndexSchemaError(
                    "Stored generation record counts do not match the "
                    "authoritative manifest."
                )

    def _open_committed_storage(self, config: IndexConfig) -> IndexStorage:
        try:
            return IndexStorage(
                config,
                create=False,
                client_factory=self.chroma_clients,
            )
        except FileNotFoundError as exc:
            raise IndexSchemaError(
                "The committed Chroma store is missing."
            ) from exc


class LocalIndexBackend(LocalIndexReader):
    def __init__(
        self,
        registry: CapabilityRegistry,
        runtime: ModelRuntime,
        layout: RepositoryLayout,
        *,
        chroma_server_url: str | None = None,
        snapshot_repository: LocalSnapshotRepository | None = None,
    ) -> None:
        super().__init__(
            layout,
            chroma_server_url=chroma_server_url,
            snapshot_repository=snapshot_repository,
        )
        self.registry = registry
        self.runtime = runtime

    def status(self, index_directory: Path) -> dict[str, Any] | None:
        self._require_index_directory(index_directory)
        status = self.repository.status()
        if status is not None and status.get("state") == "ready":
            config, snapshot = self.repository.active_config(
                device=self.runtime.backends.torch_device
            )
            with self._open_committed_storage(config) as storage:
                self._validate_snapshot_storage(storage, snapshot)
        return status

    def create(
        self,
        path: Path,
        *,
        config: IndexConfig,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
        source_name: str | None,
        source_checksum: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_index_directory(config.index_directory)
        repository = self.repository
        media_id = config.video_id
        if media_id is None:
            raise ValueError("A catalog media_id is required for indexing.")
        generation_id = operation_id or repository.new_generation_id()
        generation_directory = repository.generation_directory(generation_id)
        build_config = replace(
            config,
            video_id=media_id,
            storage_directory=repository.store,
            generation_directory=generation_directory,
            generation_id=generation_id,
            snapshot_id=None,
            snapshot_sha256=None,
        )

        with repository.lease():
            active = repository.require_compatible_profile(
                media_id=media_id,
                config_fingerprint=build_config.fingerprint(),
            )
            if active is not None and active.generations:
                active_config, _ = repository.active_config(
                    device=build_config.device
                )
                with self._open_committed_storage(
                    active_config
                ) as active_store:
                    self._validate_snapshot_storage(
                        active_store,
                        active,
                        active_config,
                    )
            if operation_id is not None and active is not None:
                committed = active.generations.get(media_id)
                if (
                    committed is not None
                    and committed.generation_id == operation_id
                ):
                    if (
                        committed.input_sha256 != source_checksum
                        or committed.config_fingerprint
                        != build_config.fingerprint()
                    ):
                        raise IndexSchemaError(
                            "The indexing operation ID is already bound to "
                            "different input or configuration."
                        )
                    return {
                        "media_id": media_id,
                        "generation_id": committed.generation_id,
                        "snapshot_id": active.snapshot_id,
                        "active_media_count": len(active.generations),
                        "record_counts": dict(committed.record_counts),
                    }
            completed_manifest = generation_directory / MANIFEST_FILE
            if operation_id is not None and completed_manifest.is_file():
                completed = None
                try:
                    completed = repository.generation_reference(
                        generation_id=operation_id,
                        media_id=media_id,
                    )
                except IndexSchemaError:
                    try:
                        raw_manifest = json.loads(
                            completed_manifest.read_text(encoding="utf-8")
                        )
                    except (OSError, json.JSONDecodeError):
                        pass
                    else:
                        if raw_manifest.get("state") == "complete":
                            manifest_store = ManifestStore(
                                build_config,
                                registry=self.registry,
                                runtime=self.runtime,
                            )
                            with IndexStorage(
                                build_config,
                                create=False,
                                client_factory=self.chroma_clients,
                            ) as storage:
                                recovered_counts = (
                                    self._validate_generation_records(
                                        storage,
                                        build_config,
                                    )
                                )
                            manifest_store.record_storage_counts(
                                recovered_counts
                            )
                            completed = repository.generation_reference(
                                generation_id=operation_id,
                                media_id=media_id,
                            )
                if completed is not None:
                    if (
                        completed.input_sha256 != source_checksum
                        or completed.config_fingerprint
                        != build_config.fingerprint()
                    ):
                        raise IndexSchemaError(
                            "The indexing operation ID is already bound to "
                            "different input or configuration."
                        )
                    snapshot = repository.publish_generation(
                        completed,
                        build_config,
                    )
                    return {
                        "media_id": media_id,
                        "generation_id": completed.generation_id,
                        "snapshot_id": snapshot.snapshot_id,
                        "active_media_count": len(snapshot.generations),
                        "record_counts": dict(completed.record_counts),
                    }
            self._cleanup_abandoned_generations(
                repository,
                build_config,
                client_factory=self.chroma_clients,
            )
            generation_validated = False
            try:
                manifest_store = ManifestStore(
                    build_config,
                    registry=self.registry,
                    runtime=self.runtime,
                )
                with IndexStorage(
                    build_config,
                    client_factory=self.chroma_clients,
                ) as storage:
                    index_video(
                        str(path),
                        progress_callback=progress,
                        source_name=source_name,
                        checksum=source_checksum,
                        config=build_config,
                        cancellation=cancellation,
                        storage=storage,
                        manifest_store=manifest_store,
                        registry=self.registry,
                        runtime=self.runtime,
                    )
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    record_counts = self._validate_generation_records(
                        storage,
                        build_config,
                    )
                    manifest_store.record_storage_counts(record_counts)
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                reference = repository.generation_reference(
                    generation_id=generation_id,
                    media_id=media_id,
                )
                generation_validated = True
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                snapshot = repository.publish_generation(
                    reference,
                    build_config,
                )
            except BaseException:
                try:
                    self._remove_uncommitted_generation(
                        repository,
                        build_config,
                        preserve_completed=generation_validated,
                    )
                except Exception:
                    # Preserve the primary build/publication failure.
                    pass
                raise

        return {
            "media_id": media_id,
            "generation_id": generation_id,
            "snapshot_id": snapshot.snapshot_id,
            "active_media_count": len(snapshot.generations),
            "record_counts": record_counts,
        }

    @staticmethod
    def _validate_generation_records(
        storage: IndexStorage,
        config: IndexConfig,
        expected_counts: dict[str, int] | None = None,
    ) -> dict[str, int]:
        if config.generation_id is None or config.video_id is None:
            raise IndexSchemaError(
                "Generation and media identity are required before publication."
            )
        counts: dict[str, int] = {}
        for modality in config.enabled_modalities:
            records = storage.records(
                modality,
                generation_ids=(config.generation_id,),
            )
            if any(
                record.get("generation_id") != config.generation_id
                or record.get("video_id") != config.video_id
                or record.get("modality") != modality
                for record in records
            ):
                raise IndexSchemaError(
                    f"Stored {modality} records do not match the generation "
                    "being published."
                )
            counts[modality] = len(records)
        if expected_counts is not None and counts != expected_counts:
            raise IndexSchemaError(
                "Stored generation record counts do not match the "
                "authoritative manifest."
            )
        return counts

    @staticmethod
    def _cleanup_abandoned_generations(
        repository: LocalSnapshotRepository,
        config: IndexConfig,
        *,
        client_factory: ChromaClientFactory | None = None,
    ) -> None:
        clients = client_factory or ChromaClientFactory()
        completed: set[str] = set()
        incomplete: dict[str, Path] = {}
        if repository.generations.is_dir():
            for path in repository.generations.iterdir():
                if (
                    not path.is_dir()
                    or not LocalIndexBackend._is_generation_id(path.name)
                ):
                    continue
                manifest_path = path / MANIFEST_FILE
                try:
                    manifest = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    incomplete[path.name] = path
                    continue
                if (
                    manifest.get("state") == "complete"
                    and manifest.get("generation_id") == path.name
                ):
                    completed.add(path.name)
                else:
                    incomplete[path.name] = path

        cleanup_config = replace(
            config,
            storage_directory=repository.store,
            generation_directory=None,
            video_id=None,
            generation_id=None,
            snapshot_id=None,
            snapshot_sha256=None,
        )
        if clients.remote or repository.store.is_dir():
            try:
                with IndexStorage(
                    cleanup_config,
                    create=False,
                    client_factory=clients,
                ) as storage:
                    stored_ids: set[str] = set()
                    for modality in cleanup_config.enabled_modalities:
                        try:
                            records = storage.records(modality)
                        except FileNotFoundError:
                            continue
                        for record in records:
                            generation_id = record.get("generation_id")
                            if (
                                isinstance(generation_id, str)
                                and LocalIndexBackend._is_generation_id(
                                    generation_id
                                )
                            ):
                                stored_ids.add(generation_id)
                    for generation_id in sorted(
                        set(incomplete) | (stored_ids - completed)
                    ):
                        for modality in cleanup_config.enabled_modalities:
                            try:
                                storage.delete_generation(
                                    generation_id,
                                    modalities=(modality,),
                                )
                            except FileNotFoundError:
                                continue
            except FileNotFoundError:
                pass

        for generation_id, path in incomplete.items():
            resolved = path.resolve()
            if repository.generations.resolve() not in resolved.parents:
                raise RuntimeError(
                    "Refusing to clean a generation outside the repository."
                )
            shutil.rmtree(resolved)

    @staticmethod
    def _is_generation_id(value: str) -> bool:
        try:
            identifier = UUID(hex=value)
        except ValueError:
            return False
        return identifier.version == 4 and identifier.hex == value

    def _remove_uncommitted_generation(
        self,
        repository: LocalSnapshotRepository,
        config: IndexConfig,
        *,
        preserve_completed: bool,
    ) -> None:
        generation_id = config.generation_id
        if generation_id is None:
            return
        generation_directory = config.run_directory.resolve()
        generations_root = repository.generations.resolve()
        if generations_root not in generation_directory.parents:
            raise RuntimeError(
                "Refusing to clean a generation outside the repository."
            )
        manifest_path = generation_directory / MANIFEST_FILE
        state = None
        if manifest_path.is_file():
            try:
                state = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                ).get("state")
            except (OSError, json.JSONDecodeError):
                state = None
        if state == "complete" and preserve_completed:
            return
        try:
            with IndexStorage(
                config,
                client_factory=self.chroma_clients,
            ) as storage:
                storage.delete_generation(generation_id)
        except Exception:
            # Cleanup is best-effort and must never replace the build failure.
            pass
        if generation_directory.is_dir():
            shutil.rmtree(generation_directory)

    def indexing_in_progress(self, config: IndexConfig) -> bool:
        self._require_index_directory(config.index_directory)
        return self.repository.mutation_in_progress()

    def remove(self, config: IndexConfig, media_id: str) -> bool:
        self._require_index_directory(config.index_directory)
        repository = self.repository
        with repository.lease():
            return repository.remove(media_id)

    def clear(self, config: IndexConfig) -> bool:
        self._require_index_directory(config.index_directory)
        repository = self.repository
        with repository.lease():
            return repository.clear()
