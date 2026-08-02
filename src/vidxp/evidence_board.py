from __future__ import annotations

import hashlib

from vidxp.application_models import (
    ApplicationError,
    ErrorCategory,
    ErrorDetail,
    EvidenceArtifact,
    EvidenceBoardCandidate,
    EvidenceBoardJobRequest,
    EvidenceBoardPage,
    EvidenceBoardResult,
    EvidenceBoardTile,
    EvidenceDeliveryState,
)
from vidxp.artifact_service import (
    ArtifactService,
    EvidenceBoardPageTileInput,
    deterministic_artifact_operation_id,
)
from vidxp.core.contracts import IndexCancelledError
from vidxp.evidence_delivery import resolve_evidence_range
from vidxp.execution import ExecutionContext, execution_context
from vidxp.media_service import MediaService
from vidxp.settings import VidXPSettings


def plan_evidence_board_pages(
    candidates: tuple[EvidenceBoardCandidate, ...],
    *,
    tiles_per_page: int,
    pages_per_job: int,
) -> tuple[tuple[tuple[EvidenceBoardCandidate, ...], ...], int | None]:
    """Plan ordered, single-media pages without skipping continuation ranks."""

    pages: list[tuple[EvidenceBoardCandidate, ...]] = []
    current: list[EvidenceBoardCandidate] = []
    current_identity: tuple[str, str] | None = None
    for candidate in candidates:
        identity = candidate.media_id, candidate.generation_id
        must_close = bool(current) and (
            identity != current_identity or len(current) >= tiles_per_page
        )
        if must_close:
            pages.append(tuple(current))
            current = []
            current_identity = None
            if len(pages) >= pages_per_job:
                return tuple(pages), candidate.rank
        current.append(candidate)
        current_identity = identity
    if current:
        if len(pages) >= pages_per_job:
            return tuple(pages), current[0].rank
        pages.append(tuple(current))
    return tuple(pages), None


class EvidenceBoardService:
    _COLUMNS = 4
    _TILE_WIDTH = 320
    _TILE_HEIGHT = 180
    _ANNOTATION_HEIGHT = 52

    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        media: MediaService,
        settings: VidXPSettings,
    ) -> None:
        self.artifacts = artifacts
        self.media = media
        self.settings = settings

    def create(
        self,
        request: EvidenceBoardJobRequest,
        *,
        execution: ExecutionContext | None = None,
    ) -> EvidenceBoardResult:
        active = execution_context(execution)
        plans, next_start_rank = plan_evidence_board_pages(
            request.candidates,
            tiles_per_page=self.settings.evidence_board_tiles_per_page,
            pages_per_job=self.settings.evidence_board_pages_per_job,
        )
        pages: list[EvidenceBoardPage] = []
        result_tiles: list[EvidenceBoardTile] = []
        failed_count = 0
        for page_number, plan in enumerate(plans, start=1):
            active.checkpoint()
            media_id = plan[0].media_id
            generation_id = plan[0].generation_id
            media = self.media.require_record(media_id)
            page_inputs: list[EvidenceBoardPageTileInput] = []
            page_tiles: list[EvidenceBoardTile] = []
            for position, candidate in enumerate(plan, start=1):
                tile_id = hashlib.sha256(
                    (
                        f"evidence-board-tile-v1\0{request.source_fingerprint}\0"
                        f"{candidate.evidence_id}"
                    ).encode()
                ).hexdigest()
                errors: list[ErrorDetail] = []
                frame_artifact_id = None
                representative = candidate.representative_timestamp
                try:
                    resolved = resolve_evidence_range(
                        source_start=candidate.start,
                        source_end=candidate.end,
                        representative_timestamp=representative,
                        media_duration=media.duration_seconds,
                        padding_before=0,
                        padding_after=0,
                        max_duration=self.settings.max_snippet_duration_seconds,
                    )
                    representative = resolved.representative_timestamp_seconds
                    frame, _width, _height = self.artifacts.create_evidence_frame(
                        media_id=candidate.media_id,
                        generation_id=candidate.generation_id,
                        evidence_id=candidate.evidence_id,
                        timestamp_seconds=representative,
                        frame_index=candidate.frame_index,
                        job_id=active.job_id,
                        execution=active,
                        artifact_operation_id=deterministic_artifact_operation_id(
                            active.job_id,
                            candidate.evidence_id,
                            "board-frame",
                        ),
                    )
                    frame_artifact_id = frame.artifact_id
                except IndexCancelledError:
                    raise
                except Exception as exc:
                    failed_count += 1
                    errors.append(self._frame_failure(exc))
                annotations = self._annotations(candidate, representative)
                page_inputs.append(
                    EvidenceBoardPageTileInput(
                        evidence_id=candidate.evidence_id,
                        frame_artifact_id=frame_artifact_id,
                        annotation_lines=annotations,
                        placeholder=(
                            None
                            if frame_artifact_id is not None
                            else "Frame unavailable"
                        ),
                    )
                )
                page_tiles.append(
                    EvidenceBoardTile(
                        **candidate.model_copy(
                            update={"representative_timestamp": representative}
                        ).model_dump(),
                        tile_id=tile_id,
                        page_number=page_number,
                        position=position,
                        keyframe_artifact_id=frame_artifact_id,
                        state=(
                            EvidenceDeliveryState.ready
                            if frame_artifact_id is not None
                            else EvidenceDeliveryState.failed
                        ),
                        errors=tuple(errors),
                    )
                )
            active.report(
                {
                    "stage": "composing_board_pages",
                    "message": f"Composing board page {page_number} of {len(plans)}.",
                    "current": page_number - 1,
                    "total": len(plans),
                }
            )
            page_artifact, width, height = self.artifacts.create_evidence_board_page(
                media_id=media_id,
                generation_id=generation_id,
                title=media.original_filename,
                tiles=tuple(page_inputs),
                columns=self._COLUMNS,
                tile_width=self._TILE_WIDTH,
                tile_height=self._TILE_HEIGHT,
                annotation_height=self._ANNOTATION_HEIGHT,
                maximum_bytes=self.settings.mcp_max_resource_bytes,
                job_id=active.job_id,
                execution=active,
                artifact_operation_id=deterministic_artifact_operation_id(
                    active.job_id,
                    ":".join(tile.tile_id for tile in page_tiles),
                    "board-page",
                ),
            )
            pages.append(
                EvidenceBoardPage(
                    page_number=page_number,
                    media_id=media_id,
                    generation_id=generation_id,
                    artifact=EvidenceArtifact(artifact=page_artifact),
                    width=width,
                    height=height,
                    columns=min(self._COLUMNS, len(page_tiles)),
                    rows=(len(page_tiles) + self._COLUMNS - 1) // self._COLUMNS,
                    tile_ids=tuple(tile.tile_id for tile in page_tiles),
                )
            )
            result_tiles.extend(page_tiles)
        active.report(
            {
                "stage": "complete",
                "message": "The evidence board is ready.",
                "current": len(pages),
                "total": len(pages),
            }
        )
        return EvidenceBoardResult(
            source_job_id=request.source_job_id,
            source_fingerprint=request.source_fingerprint,
            requested_count=len(request.candidates),
            rendered_count=len(result_tiles) - failed_count,
            failed_count=failed_count,
            pages=tuple(pages),
            tiles=tuple(result_tiles),
            next_start_rank=next_start_rank,
        )

    @staticmethod
    def _annotations(
        candidate: EvidenceBoardCandidate,
        representative: float,
    ) -> tuple[str, ...]:
        minutes, seconds = divmod(representative, 60)
        timecode = f"{int(minutes):02d}:{seconds:06.3f}"
        lines = [
            f"#{candidate.rank} · {timecode} · {', '.join(candidate.modalities)}",
            candidate.display_text or f"Evidence {candidate.evidence_id[:12]}",
            candidate.frame_match.value.replace("_", " "),
        ]
        return tuple(lines)

    @staticmethod
    def _frame_failure(exc: Exception) -> ErrorDetail:
        if isinstance(exc, ApplicationError):
            return exc.detail
        return ErrorDetail(
            code="evidence_board_frame_failed",
            category=ErrorCategory.unavailable,
            message="The representative frame could not be prepared for this tile.",
            details={"reason": type(exc).__name__},
            retryable=True,
        )
