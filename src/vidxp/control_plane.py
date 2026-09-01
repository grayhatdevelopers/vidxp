from __future__ import annotations

from pathlib import Path
from typing import Callable

from vidxp.application_boundary import application_boundary
from vidxp.application_models import (
    Artifact,
    CapabilityInfo,
    CapabilityRole,
    CapabilitySummary,
    ComponentReadiness,
    CreateIndexCommand,
    DependencyCheckResult,
    IndexStatus,
    Identifier,
    InvalidRequestError,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    ModelUnavailableError,
    ResourceNotFoundError,
    RuntimeReadiness,
    WorkspaceCapability,
    WorkspaceMedia,
    WorkspaceMediaCapability,
    WorkspaceOverview,
)
from vidxp.artifact_service import ArtifactQueryService
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.capability_service import CapabilityService
from vidxp.core.media import QuarantinedMedia
from vidxp.core.snapshots import IndexSnapshot
from vidxp.index_state import INDEX_STATUS_SCHEMA
from vidxp.media_service import MediaService
from vidxp.ports import LocalFileResource
from vidxp.repository_layout import RepositoryLayout


class ControlPlaneApplication:
    """Model-free application facade used by HTTP and future remote adapters."""

    def __init__(
        self,
        *,
        layout: RepositoryLayout,
        capabilities: CapabilityService,
        media: MediaService,
        artifacts: ArtifactQueryService,
        index_status: Callable[[], dict | None],
        model_cache: Path,
        active_snapshot: Callable[[], IndexSnapshot | None] | None = None,
    ) -> None:
        self.layout = layout
        self.capabilities = capabilities
        self.media = media
        self.artifacts = artifacts
        self._read_index_status = index_status
        self._read_active_snapshot = active_snapshot or (lambda: None)
        self.model_cache = model_cache

    @application_boundary
    def import_uploaded_media(
        self,
        *,
        staged_path: Path,
        original_filename: str,
        declared_mime_type: str | None,
        request_key: str | None = None,
    ) -> MediaAsset:
        self.layout.ensure_local_directories()
        return self.media.import_quarantined(
            QuarantinedMedia(
                path=staged_path,
                original_filename=original_filename,
                declared_mime_type=declared_mime_type,
            ),
            request_key=request_key,
        )

    @application_boundary
    def list_capabilities(self) -> tuple[CapabilitySummary, ...]:
        return self.capabilities.list()

    @application_boundary
    def get_capability(self, name: str) -> CapabilityInfo:
        try:
            return self.capabilities.get(name)
        except CapabilityRequestError as exc:
            raise ResourceNotFoundError("capability") from exc

    @application_boundary
    def select_index_modalities(
        self,
        requested: tuple[Identifier, ...] | None,
    ) -> tuple[str, ...]:
        """Resolve an optional capability selection to indexable names."""

        registry = self.capabilities.registry
        indexable = registry.index_names()
        selected = (
            indexable
            if requested is None
            else registry.validate_names(requested)
        )
        unsupported = tuple(
            name for name in selected if name not in indexable
        )
        if unsupported:
            raise CapabilityRequestError(
                "Indexing does not support these capabilities: "
                + ", ".join(unsupported)
                + ".",
                field="modalities",
                reason="capability_not_indexable",
            )
        return selected

    @application_boundary
    def index_status(self) -> IndexStatus:
        stored = self._read_index_status()
        payload = (
            dict(stored)
            if stored is not None
            else {
                "schema_version": INDEX_STATUS_SCHEMA,
                "state": "missing",
                "stage": "status",
                "message": "No local video index was found.",
            }
        )
        return IndexStatus.model_validate(payload)

    @application_boundary
    def get_media(self, media_id: str) -> MediaAsset:
        return self.media.get(media_id)

    @application_boundary
    def list_media(self, command: ListMediaCommand) -> MediaPage:
        try:
            return self.media.list(command)
        except ValueError as exc:
            raise InvalidRequestError() from exc

    @application_boundary
    def workspace(self, command: ListMediaCommand) -> WorkspaceOverview:
        page = self.list_media(command)
        # Workspace actions are repository-level guidance, so compare against
        # repository-wide totals rather than a potentially filtered page total.
        repository_media_total = self.list_media(ListMediaCommand(page_size=1)).total
        index = self.index_status()
        snapshot = self._read_active_snapshot()
        capabilities = self.list_capabilities()
        readiness = self.model_readiness()
        readiness_by_capability = {
            capability.name: all(
                check.ok
                for check in readiness.checks
                if check.capability == capability.name
            )
            for capability in capabilities
            if capability.prepares_models
        }
        projected_capabilities = tuple(
            WorkspaceCapability(
                **capability.model_dump(),
                models_ready=readiness_by_capability.get(capability.name),
            )
            for capability in capabilities
        )
        media = tuple(
            self._workspace_media(
                asset,
                capabilities=capabilities,
                snapshot=snapshot,
            )
            for asset in page.items
        )
        indexed_media = (
            frozenset(snapshot.generations) if snapshot is not None else frozenset()
        )
        indexed_capabilities = (
            {
                name
                for generation in snapshot.generations.values()
                for name in generation.modalities
            }
            if snapshot is not None
            else set()
        )
        active_roles = {
            role
            for capability in capabilities
            if capability.name in indexed_capabilities
            for role in capability.roles
        }
        next_actions = []
        if page.total == 0:
            next_actions.append("register_media")
        if repository_media_total > len(indexed_media) or any(
            item.media_id not in indexed_media for item in page.items
        ):
            next_actions.append("index_media")
        if CapabilityRole.searchable in active_roles:
            next_actions.append("find_moments")
        if CapabilityRole.queryable in active_roles:
            next_actions.append("answer_video")
        return WorkspaceOverview(
            capabilities=projected_capabilities,
            media=media,
            media_total=page.total,
            next_cursor=page.next_cursor,
            index=index,
            next_actions=tuple(next_actions),
        )

    @staticmethod
    def _workspace_media(
        asset: MediaAsset,
        *,
        capabilities: tuple[CapabilitySummary, ...],
        snapshot: IndexSnapshot | None,
    ) -> WorkspaceMedia:
        generation = (
            snapshot.generations.get(asset.media_id) if snapshot is not None else None
        )
        coverage = tuple(
            WorkspaceMediaCapability(
                name=capability.name,
                indexed=(
                    generation is not None and capability.name in generation.modalities
                ),
                record_count=(
                    generation.record_counts.get(capability.name)
                    if generation is not None
                    and capability.name in generation.modalities
                    else None
                ),
                roles=(
                    capability.roles
                    if generation is not None
                    and capability.name in generation.modalities
                    else ()
                ),
                identity_mode=capability.identity_mode,
            )
            for capability in capabilities
        )
        return WorkspaceMedia(
            media_id=asset.media_id,
            original_filename=asset.original_filename,
            duration_seconds=asset.duration_seconds,
            state=asset.state,
            in_active_snapshot=generation is not None,
            capabilities=coverage,
        )

    @application_boundary
    def preflight_index(self, command: CreateIndexCommand) -> None:
        selected = self.capabilities.registry.validate_names(command.modalities)
        indexable = self.capabilities.registry.index_names()
        unsupported = tuple(name for name in selected if name not in indexable)
        if unsupported:
            raise CapabilityRequestError(
                "Indexing does not support these capabilities: "
                + ", ".join(unsupported)
                + ".",
                field="modalities",
                reason="capability_not_indexable",
                requested=unsupported,
                available=indexable,
                next_action="Choose capabilities returned by get_workspace.",
            )
        self.capabilities.registry.validate_options(
            selected,
            command.capability_options,
        )
        self.media.require_record(command.media_id)
        self.require_models(selected)

    @application_boundary
    def open_media_content(self, media_id: str) -> LocalFileResource:
        return self.media.content(media_id)

    @application_boundary
    def get_artifact(self, artifact_id: str) -> Artifact:
        return self.artifacts.get(artifact_id)

    @application_boundary
    def open_artifact_content(self, artifact_id: str) -> LocalFileResource:
        return self.artifacts.content(artifact_id)

    def control_plane_readiness(self) -> tuple[ComponentReadiness, ...]:
        components: list[ComponentReadiness] = []
        try:
            self.media.list(ListMediaCommand(page_size=1))
        except Exception:
            components.append(
                ComponentReadiness(
                    name="catalog",
                    ready=False,
                    message="The media catalog is unavailable.",
                )
            )
        else:
            components.append(
                ComponentReadiness(
                    name="catalog",
                    ready=True,
                    message="The media catalog is available.",
                )
            )
        try:
            self.index_status()
        except Exception:
            components.append(
                ComponentReadiness(
                    name="index",
                    ready=False,
                    message="The index catalog is unavailable.",
                )
            )
        else:
            components.append(
                ComponentReadiness(
                    name="index",
                    ready=True,
                    message="The index catalog is available.",
                )
            )
        return tuple(components)

    def model_readiness(
        self,
        modalities: tuple[str, ...] | None = None,
    ) -> DependencyCheckResult:
        selected = (
            self.capabilities.registry.preparable_names()
            if modalities is None
            else self.capabilities.registry.validate_names(modalities)
        )
        checks = self.capabilities.registry.model_checks(
            selected,
            cache=self.model_cache,
        )
        return DependencyCheckResult(
            ok=all(check.ok for check in checks),
            modalities=selected,
            checks=checks,
        )

    def require_models(self, modalities: tuple[str, ...]) -> None:
        readiness = self.model_readiness(modalities)
        missing = next((check for check in readiness.checks if not check.ok), None)
        if missing is not None:
            raise ModelUnavailableError(missing.capability)

    def runtime_readiness(self) -> RuntimeReadiness:
        components = self.control_plane_readiness()
        models = self.model_readiness()
        return RuntimeReadiness(
            ready=(all(component.ready for component in components) and models.ok),
            runtime=None,
            components=components,
            dependencies=models,
        )
