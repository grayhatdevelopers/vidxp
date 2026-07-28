from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from vidxp.core.contracts import CancellationToken, IndexConfig
from vidxp.core.indexing_common import ProgressCallback


class IndexBackend(Protocol):
    """Infrastructure operations needed by the application layer."""

    def status(self, index_directory: Path) -> dict[str, Any] | None: ...

    def active_config(
        self,
        index_directory: Path,
        *,
        device: str,
    ) -> tuple[IndexConfig, dict[str, Any]]: ...

    def create(
        self,
        path: Path,
        *,
        config: IndexConfig,
        progress: ProgressCallback | None,
        cancellation: CancellationToken | None,
        source_name: str | None,
    ) -> dict[str, Any]: ...

    def indexing_in_progress(self, config: IndexConfig) -> bool: ...

    def clear(self, config: IndexConfig) -> None: ...
