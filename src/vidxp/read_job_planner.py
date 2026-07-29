from __future__ import annotations

from vidxp.application_boundary import application_boundary
from vidxp.application_models import (
    ActorOverlayJobRequest,
    CreateActorOverlayCommand,
    IndexSnapshotReference,
    QueryJobRequest,
    QueryVideoCommand,
    SearchCommand,
    SearchJobRequest,
)
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.infrastructure.local_index import LocalIndexReader
from vidxp.repository_layout import RepositoryLayout


class LocalReadJobPlanner:
    """Bind durable reads to one immutable snapshot without loading models."""

    def __init__(
        self,
        *,
        layout: RepositoryLayout,
        index: LocalIndexReader | None = None,
    ) -> None:
        self.layout = layout
        self.index = index or LocalIndexReader(layout)

    def _active(self):
        config = self.index.active_config(
            self.layout.indexes,
            device="cpu",
        )
        if config.snapshot_id is None or config.snapshot_sha256 is None:
            raise RuntimeError(
                "The active index did not provide an immutable reference."
            )
        return config, IndexSnapshotReference(
            snapshot_id=config.snapshot_id,
            snapshot_sha256=config.snapshot_sha256,
        )

    @staticmethod
    def _require_capability(capability: str, config) -> None:
        if capability not in config.enabled_modalities:
            raise CapabilityRequestError(
                f"The {capability} capability is not present in this index."
            )

    @application_boundary
    def plan_search(self, command: SearchCommand) -> SearchJobRequest:
        config, reference = self._active()
        for capability in command.modalities:
            self._require_capability(capability, config)
        return SearchJobRequest(command=command, snapshot=reference)

    @application_boundary
    def plan_query(self, command: QueryVideoCommand) -> QueryJobRequest:
        config, reference = self._active()
        for capability in command.modalities:
            self._require_capability(capability, config)
        return QueryJobRequest(command=command, snapshot=reference)

    @application_boundary
    def plan_actor_overlay(
        self,
        command: CreateActorOverlayCommand,
    ) -> ActorOverlayJobRequest:
        config, reference = self._active()
        self._require_capability("actor", config)
        return ActorOverlayJobRequest(
            command=command,
            snapshot=reference,
        )
