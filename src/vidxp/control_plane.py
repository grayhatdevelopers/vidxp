from __future__ import annotations

from pathlib import Path
from typing import Callable

from vidxp.application_boundary import application_boundary
from vidxp.application_models import (
    Artifact,
    CapabilityInfo,
    CapabilitySummary,
    ComponentReadiness,
    DependencyCheckResult,
    IndexStatus,
    InvalidRequestError,
    ListMediaCommand,
    MediaAsset,
    MediaPage,
    ModelUnavailableError,
    ResourceNotFoundError,
    RuntimeReadiness,
)
from vidxp.artifact_service import ArtifactQueryService
from vidxp.capabilities.contracts import CapabilityRequestError
from vidxp.capability_service import CapabilityService
from vidxp.core.media import QuarantinedMedia
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
    ) -> None:
        self.layout = layout
        self.capabilities = capabilities
        self.media = media
        self.artifacts = artifacts
        self._read_index_status = index_status
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
            ready=(
                all(component.ready for component in components)
                and models.ok
            ),
            runtime=None,
            components=components,
            dependencies=models,
        )
