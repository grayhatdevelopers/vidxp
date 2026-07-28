from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from vidxp.capabilities.contracts import (
    RuntimeCheckBinding,
    module_import_check,
)
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.core.contracts import CancellationToken, IndexConfig
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.runner import (
    index_video,
    indexing_in_progress,
    local_config_from_status,
)
from vidxp.core.manifest import (
    CHECKPOINT_DIRECTORY,
    COMPLETION_FILE,
    FAILURES_FILE,
    MANIFEST_FILE,
    TIMINGS_FILE,
    ManifestStore,
)
from vidxp.core.storage import IndexStorage
from vidxp.index_state import (
    INDEX_STATUS_FILE,
    read_index_status,
    require_ready_index,
)
from vidxp.runtime import ModelRuntime


LOCAL_INDEX_RUNTIME_CHECKS = (
    RuntimeCheckBinding(
        capability="storage",
        check=module_import_check(
            "Chroma storage import",
            "chromadb",
            "PersistentClient",
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


class LocalIndexBackend:
    def __init__(
        self,
        registry: CapabilityRegistry,
        runtime: ModelRuntime,
    ) -> None:
        self.registry = registry
        self.runtime = runtime

    def status(self, index_directory: Path) -> dict[str, Any] | None:
        return read_index_status(index_directory)

    def active_config(
        self,
        index_directory: Path,
        *,
        device: str,
    ) -> tuple[IndexConfig, dict[str, Any]]:
        status = require_ready_index(index_directory)
        config = local_config_from_status(
            status,
            storage_directory=index_directory,
        )
        return replace(config, device=device), status

    def create(
        self,
        path: Path,
        *,
        config: IndexConfig,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
        source_name: str | None,
    ) -> dict[str, Any]:
        with IndexStorage(config) as storage:
            return index_video(
                str(path),
                progress_callback=progress,
                source_name=source_name,
                config=config,
                cancellation=cancellation,
                storage=storage,
                manifest_store=ManifestStore(
                    config,
                    registry=self.registry,
                    runtime=self.runtime,
                ),
                registry=self.registry,
                runtime=self.runtime,
            )

    def indexing_in_progress(self, config: IndexConfig) -> bool:
        return indexing_in_progress(config)

    def open_store(self, config: IndexConfig) -> IndexStorage:
        return IndexStorage(config)

    def clear(self, config: IndexConfig) -> None:
        with IndexStorage(config) as storage:
            storage.clear()
        for name in (
            INDEX_STATUS_FILE,
            MANIFEST_FILE,
            TIMINGS_FILE,
            FAILURES_FILE,
            COMPLETION_FILE,
        ):
            (config.run_directory / name).unlink(missing_ok=True)
        checkpoint_directory = config.run_directory / CHECKPOINT_DIRECTORY
        if checkpoint_directory.is_dir():
            for checkpoint in checkpoint_directory.glob("*.json"):
                checkpoint.unlink()
            try:
                checkpoint_directory.rmdir()
            except OSError:
                pass
