from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.core.contracts import CancellationToken, IndexConfig
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.runner import (
    index_video,
    indexing_in_progress,
    local_config_from_status,
)
from vidxp.core.storage import IndexStorage
from vidxp.index_state import read_index_status, require_ready_index
from vidxp.runtime import ModelRuntime


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
        return index_video(
            str(path),
            progress_callback=progress,
            source_name=source_name,
            config=config,
            cancellation=cancellation,
            registry=self.registry,
            runtime=self.runtime,
        )

    def indexing_in_progress(self, config: IndexConfig) -> bool:
        return indexing_in_progress(config)

    def clear(self, config: IndexConfig) -> None:
        with IndexStorage(config) as storage:
            storage.clear()
