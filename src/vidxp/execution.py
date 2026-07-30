from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from vidxp.core.contracts import CancellationToken
from vidxp.core.indexing_common import ProgressCallback


@dataclass(frozen=True)
class ExecutionContext:
    """Runtime-only progress and cancellation controls for worker operations."""

    job_id: str | None = None
    progress: ProgressCallback | None = None
    cancellation: CancellationToken = field(default_factory=CancellationToken)

    def report(self, event: dict[str, Any]) -> None:
        self.cancellation.raise_if_cancelled()
        if self.progress is not None:
            self.progress(event)

    def checkpoint(self) -> None:
        self.cancellation.raise_if_cancelled()

    @property
    def operation_id(self) -> str | None:
        """Return a filesystem-safe UUID identity without changing the job ID."""

        if self.job_id is None:
            return None
        identifier = UUID(self.job_id)
        if identifier.version == 4:
            return identifier.hex
        digest = hashlib.sha256(self.job_id.encode()).digest()[:16]
        return UUID(bytes=digest, version=4).hex


def execution_context(value: ExecutionContext | None) -> ExecutionContext:
    return value if value is not None else ExecutionContext()
