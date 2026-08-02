from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from vidxp.application_models import (
    ActorOverlayProfile,
    Artifact,
    CreateSnippetCommand,
    SnippetProfile,
)
from vidxp.core.artifacts import (
    artifact_file_identity,
    ArtifactIntegrityError,
    ArtifactKind,
    ArtifactRecord,
    ArtifactState,
)
from vidxp.core.media import InvalidMediaError, utc_now
from vidxp.execution import ExecutionContext, execution_context
from vidxp.media_service import MediaService
from vidxp.ports import (
    ActorRendererPort,
    ArtifactCatalogPort,
    ArtifactStorePort,
    FrameRendererPort,
    LocalFileResource,
    MediaProbePort,
    SnippetRendererPort,
)


class ArtifactUnavailableError(FileNotFoundError):
    """Raised when an artifact is missing, expired, or fails integrity checks."""


class ArtifactNotFoundError(ArtifactUnavailableError):
    """Raised when no registered artifact exists for an identifier."""


class ArtifactNotReadyError(ArtifactUnavailableError):
    """Raised when a registered artifact cannot currently be delivered."""


class ArtifactRequestError(ValueError):
    """Raised when an artifact request is invalid for its source media."""


class InvalidArtifactError(RuntimeError):
    """Raised when rendered output is not valid media."""


ARTIFACT_RENDER_CONTRACT_VERSION = 1


def artifact_result(record: ArtifactRecord) -> Artifact:
    return Artifact(
        schema_version=record.schema_version,
        artifact_id=record.artifact_id,
        media_id=record.media_id,
        generation_id=record.generation_id,
        job_id=record.job_id,
        kind=record.kind,
        profile=record.profile,
        mime_type=record.mime_type,
        byte_size=record.byte_size,
        sha256=record.sha256,
        state=record.state,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _request_key(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class ArtifactQueryService:
    """Read-only artifact boundary shared by worker and control-plane profiles."""

    def __init__(
        self,
        *,
        catalog: ArtifactCatalogPort,
        store: ArtifactStorePort,
    ) -> None:
        self.catalog = catalog
        self.store = store

    def get(self, artifact_id: str) -> Artifact:
        return artifact_result(self.require_record(artifact_id))

    def require_record(self, artifact_id: str) -> ArtifactRecord:
        record = self.catalog.get_artifact(artifact_id)
        if record is None:
            raise ArtifactNotFoundError("The artifact was not found.")
        self._require_ready(record)
        return record

    @staticmethod
    def _require_ready(record: ArtifactRecord) -> None:
        if record.state != ArtifactState.ready or (
            record.expires_at is not None and record.expires_at <= utc_now()
        ):
            raise ArtifactNotReadyError("The artifact is not ready.")

    def content(self, artifact_id: str) -> LocalFileResource:
        record = self.require_record(artifact_id)
        path = self._verified_content(record)
        try:
            filename, _extension = artifact_file_identity(
                kind=record.kind,
                artifact_id=record.artifact_id,
                mime_type=record.mime_type,
            )
        except ValueError as exc:
            raise ArtifactNotReadyError(
                "The artifact media type cannot be delivered."
            ) from exc
        return LocalFileResource(
            path=path,
            filename=filename,
            mime_type=record.mime_type,
            byte_size=record.byte_size,
            etag=record.sha256,
        )

    def _verified_content(self, record: ArtifactRecord) -> Path:
        try:
            return self.store.verify(
                record.storage_key,
                sha256=record.sha256,
                byte_size=record.byte_size,
            )
        except (FileNotFoundError, PermissionError) as exc:
            raise ArtifactNotReadyError("The artifact content is unavailable.") from exc
        except RuntimeError as exc:
            raise ArtifactIntegrityError(
                "The artifact failed its integrity check."
            ) from exc


class ArtifactService(ArtifactQueryService):
    def __init__(
        self,
        *,
        catalog: ArtifactCatalogPort,
        store: ArtifactStorePort,
        media: MediaService,
        probe: MediaProbePort,
        actor_renderer: ActorRendererPort,
        snippet_renderer: SnippetRendererPort,
        max_snippet_duration_seconds: float,
        frame_renderer: FrameRendererPort | None = None,
    ) -> None:
        super().__init__(catalog=catalog, store=store)
        self.media = media
        self.probe = probe
        self.actor_renderer = actor_renderer
        self.snippet_renderer = snippet_renderer
        self.frame_renderer = frame_renderer
        self.max_snippet_duration_seconds = max_snippet_duration_seconds

    def create_actor_overlay(
        self,
        *,
        media_id: str,
        generation_id: str,
        cluster_id: str,
        detections: list[dict],
        profile: ActorOverlayProfile,
        job_id: str | None = None,
        execution: ExecutionContext | None = None,
    ) -> Artifact:
        active_execution = execution_context(execution)
        active_execution.checkpoint()
        source = self.media.content(media_id)
        request_key = _request_key(
            {
                "kind": ArtifactKind.actor_overlay,
                "render_contract_version": ARTIFACT_RENDER_CONTRACT_VERSION,
                "media_sha256": source.etag,
                "generation_id": generation_id,
                "cluster_id": cluster_id,
                "profile": profile,
            }
        )
        return self._create(
            media_id=media_id,
            generation_id=generation_id,
            kind=ArtifactKind.actor_overlay,
            profile=profile.value,
            request_key=request_key,
            suffix=".mp4",
            expected_mime_type="video/mp4",
            job_id=job_id,
            execution=active_execution,
            render=lambda destination: self.actor_renderer.render(
                source.path,
                destination,
                cluster_id,
                detections,
                cancellation=active_execution.cancellation,
                progress=active_execution.progress,
            ),
        )

    def create_snippet(
        self,
        command: CreateSnippetCommand,
        *,
        job_id: str | None = None,
        execution: ExecutionContext | None = None,
        artifact_operation_id: str | None = None,
    ) -> Artifact:
        active_execution = execution_context(execution)
        active_execution.checkpoint()
        media_record = self.media.require_record(command.media_id)
        duration = command.end_seconds - command.start_seconds
        if command.end_seconds > media_record.duration_seconds:
            raise ArtifactRequestError(
                "The snippet interval exceeds the media duration."
            )
        if duration > self.max_snippet_duration_seconds:
            raise ArtifactRequestError(
                "The snippet exceeds the configured duration limit."
            )
        source = self.media.content(command.media_id)
        compatible = command.profile == SnippetProfile.compatible_mp4
        request_key = _request_key(
            {
                "kind": ArtifactKind.snippet,
                "render_contract_version": ARTIFACT_RENDER_CONTRACT_VERSION,
                "media_sha256": source.etag,
                "start_seconds": command.start_seconds,
                "end_seconds": command.end_seconds,
                "profile": command.profile,
            }
        )
        return self._create(
            media_id=command.media_id,
            generation_id=None,
            kind=ArtifactKind.snippet,
            profile=command.profile.value,
            request_key=request_key,
            suffix=".mp4" if compatible else ".mkv",
            expected_mime_type=("video/mp4" if compatible else "video/x-matroska"),
            job_id=job_id,
            execution=active_execution,
            artifact_operation_id=artifact_operation_id,
            render=lambda destination: self.snippet_renderer.render(
                source.path,
                destination,
                start_seconds=command.start_seconds,
                end_seconds=command.end_seconds,
                compatible_mp4=compatible,
                cancellation=active_execution.cancellation,
                progress=active_execution.progress,
            ),
        )

    def create_evidence_frame(
        self,
        *,
        media_id: str,
        generation_id: str,
        evidence_id: str,
        timestamp_seconds: float,
        frame_index: int | None,
        job_id: str | None = None,
        execution: ExecutionContext | None = None,
        artifact_operation_id: str | None = None,
    ) -> tuple[Artifact, int, int]:
        active_execution = execution_context(execution)
        active_execution.checkpoint()
        media_record = self.media.require_record(media_id)
        if timestamp_seconds < 0 or timestamp_seconds > media_record.duration_seconds:
            raise ArtifactRequestError(
                "The evidence frame timestamp is outside the media duration."
            )
        source = self.media.content(media_id)
        if self.frame_renderer is None:
            raise ArtifactRequestError("Evidence frame rendering is not configured.")
        renderer = self.frame_renderer
        request_key = _request_key(
            {
                "kind": ArtifactKind.evidence_frame,
                "render_contract_version": ARTIFACT_RENDER_CONTRACT_VERSION,
                "media_sha256": source.etag,
                "generation_id": generation_id,
                "evidence_id": evidence_id,
                "timestamp_seconds": timestamp_seconds,
                "frame_index": frame_index,
                "profile": "png",
            }
        )
        artifact = self._create(
            media_id=media_id,
            generation_id=generation_id,
            kind=ArtifactKind.evidence_frame,
            profile="png",
            request_key=request_key,
            suffix=".png",
            expected_mime_type="image/png",
            job_id=job_id,
            execution=active_execution,
            artifact_operation_id=artifact_operation_id,
            render=lambda destination: renderer.render(
                source.path,
                destination,
                timestamp_seconds=timestamp_seconds,
                frame_index=frame_index,
                cancellation=active_execution.cancellation,
                progress=active_execution.progress,
            ),
            validate=self._validate_png,
            artifact_label="evidence frame",
        )
        content = self.content(artifact.artifact_id)
        width, height = self.png_dimensions(content.path)
        return artifact, width, height

    def _create(
        self,
        *,
        media_id: str,
        generation_id: str | None,
        kind: ArtifactKind,
        profile: str,
        request_key: str,
        suffix: str,
        expected_mime_type: str,
        job_id: str | None,
        execution: ExecutionContext,
        render: Callable[[Path], None],
        artifact_operation_id: str | None = None,
        validate: Callable[[Path, str], str] | None = None,
        artifact_label: str = "video artifact",
    ) -> Artifact:
        execution.checkpoint()
        cached = self.catalog.get_artifact_by_request(request_key)
        if cached is not None:
            try:
                self._require_ready(cached)
                self._verified_content(cached)
            except (ArtifactIntegrityError, ArtifactUnavailableError):
                self.catalog.invalidate_artifact_request(
                    request_key,
                    cached.artifact_id,
                )
            else:
                execution.report(
                    {
                        "stage": "complete",
                        "message": "Reused the existing ready artifact.",
                        "current": 1,
                        "total": 1,
                    }
                )
                return artifact_result(cached)

        artifact_id = artifact_operation_id or execution.operation_id or uuid4().hex
        staged = self.store.stage(artifact_id, suffix=suffix)
        stored = self.store.recover(artifact_id, suffix=suffix)
        try:
            if stored is None:
                execution.report(
                    {
                        "stage": "rendering",
                        "message": f"Rendering the requested {artifact_label}.",
                    }
                )
                render(staged.path)
                execution.checkpoint()
                artifact_path = staged.path
            else:
                execution.report(
                    {
                        "stage": "recovering",
                        "message": f"Recovering the published {artifact_label}.",
                    }
                )
                artifact_path = stored.local_path
            execution.report(
                {
                    "stage": "validating",
                    "message": f"Validating the rendered {artifact_label}.",
                }
            )
            detected_mime_type = (
                validate(artifact_path, expected_mime_type)
                if validate is not None
                else self._validate_video(artifact_path, expected_mime_type)
            )
            execution.checkpoint()
            if stored is None:
                execution.report(
                    {
                        "stage": "publishing",
                        "message": f"Publishing the validated {artifact_label}.",
                    }
                )
                stored = self.store.publish(staged)
            execution.checkpoint()
            record = ArtifactRecord(
                artifact_id=artifact_id,
                media_id=media_id,
                generation_id=generation_id,
                request_key=request_key,
                kind=kind,
                profile=profile,
                mime_type=detected_mime_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                storage_key=stored.storage_key,
                job_id=job_id,
                state=ArtifactState.ready,
                created_at=utc_now(),
            )
            authoritative = self.catalog.put_artifact(record)
            if authoritative.artifact_id != artifact_id:
                self._delete_quietly(stored.storage_key)
            stored = None
        except BaseException:
            if stored is not None:
                self._delete_quietly(stored.storage_key)
            raise
        finally:
            self.store.discard(staged)
        execution.report(
            {
                "stage": "complete",
                "message": f"The {artifact_label} is ready.",
                "current": 1,
                "total": 1,
            }
        )
        return artifact_result(authoritative)

    def _validate_video(self, path: Path, expected_mime_type: str) -> str:
        try:
            probed = self.probe.probe(path)
        except InvalidMediaError as exc:
            raise InvalidArtifactError(
                "The rendered artifact is not valid video."
            ) from exc
        if probed.detected_mime_type != expected_mime_type:
            raise InvalidArtifactError(
                "The rendered artifact does not match its output profile."
            )
        return probed.detected_mime_type

    @classmethod
    def _validate_png(cls, path: Path, expected_mime_type: str) -> str:
        if expected_mime_type != "image/png":
            raise InvalidArtifactError("The evidence image profile is invalid.")
        cls.png_dimensions(path)
        return expected_mime_type

    @staticmethod
    def png_dimensions(path: Path) -> tuple[int, int]:
        try:
            with path.open("rb") as stream:
                header = stream.read(24)
        except OSError as exc:
            raise InvalidArtifactError("The evidence image is unavailable.") from exc
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
        ):
            raise InvalidArtifactError(
                "The rendered evidence artifact is not a valid PNG image."
            )
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0:
            raise InvalidArtifactError(
                "The rendered evidence image has invalid dimensions."
            )
        return width, height

    def _delete_quietly(self, storage_key: str) -> None:
        try:
            self.store.delete(storage_key)
        except OSError:
            pass
