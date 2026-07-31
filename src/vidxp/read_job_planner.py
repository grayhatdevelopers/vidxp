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
    CapabilityRole,
)
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.core.snapshots import IndexSnapshot
from vidxp.infrastructure.local_index import LocalIndexReader
from vidxp.repository_layout import RepositoryLayout


class LocalReadJobPlanner:
    """Bind durable reads to one immutable snapshot without loading models."""

    def __init__(
        self,
        *,
        layout: RepositoryLayout,
        registry: CapabilityRegistry,
        index: LocalIndexReader | None = None,
    ) -> None:
        self.layout = layout
        self.registry = registry
        self.index = index or LocalIndexReader(layout)

    def _active(self):
        config, snapshot = self.index.active_snapshot(
            self.layout.indexes,
            device="cpu",
        )
        if config.snapshot_id is None or config.snapshot_sha256 is None:
            raise RuntimeError(
                "The active index did not provide an immutable reference."
            )
        return (
            config,
            IndexSnapshotReference(
                snapshot_id=config.snapshot_id,
                snapshot_sha256=config.snapshot_sha256,
            ),
            snapshot,
        )

    def _select_capabilities(
        self,
        requested: tuple[str, ...],
        *,
        indexed: tuple[str, ...],
        role: CapabilityRole,
        operation: str,
    ) -> tuple[str, ...]:
        explicit = bool(requested)
        selected = self.registry.validate_names(requested) if explicit else indexed
        absent = tuple(name for name in selected if name not in indexed)
        if absent:
            raise CapabilityRequestError(
                f"{operation} capabilities are not present in the active index: "
                + ", ".join(absent)
                + ".",
                field="modalities",
                reason="capability_not_indexed",
                requested=absent,
                available=tuple(
                    name for name in indexed if role in self.registry.get(name).roles
                ),
                indexed=indexed,
                next_action=(
                    "Choose an indexed capability returned by get_workspace or "
                    "index the media with the requested capability."
                ),
            )
        supported = tuple(
            name for name in selected if role in self.registry.get(name).roles
        )
        unsupported = tuple(name for name in selected if name not in supported)
        if explicit and unsupported:
            available = tuple(
                name for name in indexed if role in self.registry.get(name).roles
            )
            raise CapabilityRequestError(
                f"{operation} does not support these indexed capabilities: "
                + ", ".join(unsupported)
                + ".",
                field="modalities",
                reason="capability_role_unsupported",
                requested=unsupported,
                available=available,
                indexed=indexed,
                next_action=(
                    f"Choose a {role.value} capability returned by get_workspace."
                ),
            )
        if not supported:
            raise CapabilityRequestError(
                f"The active index has no {role.value} capabilities.",
                field="modalities",
                reason="capability_role_unavailable",
                indexed=indexed,
                next_action=(
                    f"Index media with a capability whose role is {role.value}."
                ),
            )
        return supported

    @staticmethod
    def _require_media(
        media_id: str | None,
        snapshot: IndexSnapshot,
    ) -> None:
        if media_id is None or media_id in snapshot.generations:
            return
        raise CapabilityRequestError(
            "The selected media item is not present in the active index snapshot.",
            field="media_id",
            reason="media_not_indexed",
            requested=(media_id,),
            available=tuple(sorted(snapshot.generations)[:100]),
            next_action=(
                "Choose indexed media returned by get_workspace or index this "
                "media item first."
            ),
        )

    @application_boundary
    def plan_search(self, command: SearchCommand) -> SearchJobRequest:
        config, reference, snapshot = self._active()
        selected = self._select_capabilities(
            command.modalities,
            indexed=config.enabled_modalities,
            role=CapabilityRole.searchable,
            operation="Search",
        )
        self._require_media(command.media_id, snapshot)
        return SearchJobRequest(
            command=command.model_copy(update={"modalities": selected}),
            snapshot=reference,
        )

    @application_boundary
    def plan_query(self, command: QueryVideoCommand) -> QueryJobRequest:
        config, reference, snapshot = self._active()
        selected = self._select_capabilities(
            command.modalities,
            indexed=config.enabled_modalities,
            role=CapabilityRole.queryable,
            operation="Query",
        )
        self._require_media(command.media_id, snapshot)
        return QueryJobRequest(
            command=command.model_copy(update={"modalities": selected}),
            snapshot=reference,
        )

    @application_boundary
    def plan_actor_overlay(
        self,
        command: CreateActorOverlayCommand,
    ) -> ActorOverlayJobRequest:
        config, reference, _snapshot = self._active()
        selected = self._select_capabilities(
            ("actor",),
            indexed=config.enabled_modalities,
            role=CapabilityRole.renderable,
            operation="Actor overlay",
        )
        assert selected == ("actor",)
        return ActorOverlayJobRequest(
            command=command,
            snapshot=reference,
        )
