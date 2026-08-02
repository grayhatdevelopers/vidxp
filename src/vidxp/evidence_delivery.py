from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from vidxp.application_models import (
    ApplicationError,
    CreateSnippetCommand,
    ErrorCategory,
    ErrorDetail,
    EvidenceArtifact,
    EvidenceDeliveryItem,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryResult,
    EvidenceDeliveryState,
    EvidenceFrameMatch,
    EvidenceKeyframe,
    EvidenceRangeResolution,
    FusedSearchResult,
    MomentEvidence,
    QueryAnswer,
)
from vidxp.artifact_service import ArtifactService
from vidxp.core.contracts import IndexCancelledError
from vidxp.execution import ExecutionContext, execution_context
from vidxp.media_service import MediaService


def resolve_evidence_range(
    *,
    source_start: float,
    source_end: float,
    representative_timestamp: float,
    media_duration: float,
    padding_before: float,
    padding_after: float,
    max_duration: float,
) -> EvidenceRangeResolution:
    if media_duration <= 0 or max_duration <= 0:
        raise ValueError("Media and maximum clip durations must be positive.")
    start = min(max(source_start, 0.0), media_duration)
    end = min(max(source_end, start), media_duration)
    representative = min(max(representative_timestamp, start), end)
    latest_frame_time = max(
        0.0,
        media_duration - min(0.001, media_duration / 2),
    )
    representative = min(representative, latest_frame_time)

    point = end <= start
    effective_before = padding_before
    effective_after = padding_after
    if point and effective_before + effective_after == 0:
        effective_before = min(1.0, max_duration / 2)
        effective_after = min(1.0, max_duration / 2)
    desired_start = (representative if point else start) - effective_before
    desired_end = (representative if point else end) + effective_after
    clip_start = max(0.0, desired_start)
    clip_end = min(media_duration, desired_end)
    source_truncated = end - start > max_duration

    start_clamped = desired_start < 0
    end_clamped = desired_end > media_duration
    if clip_end - clip_start > max_duration:
        half = max_duration / 2
        clip_start = representative - half
        clip_end = clip_start + max_duration
        if clip_start < 0:
            start_clamped = True
            clip_end -= clip_start
            clip_start = 0.0
        if clip_end > media_duration:
            end_clamped = True
            clip_start -= clip_end - media_duration
            clip_end = media_duration
        clip_start = max(0.0, clip_start)

    if clip_end <= clip_start:
        minimum = min(media_duration, max_duration, 0.1)
        clip_start = min(max(0.0, representative - minimum / 2), media_duration)
        clip_end = min(media_duration, clip_start + minimum)
        if clip_end <= clip_start:
            clip_start = max(0.0, media_duration - minimum)
            clip_end = media_duration
    if clip_end <= clip_start:
        raise ValueError("The evidence range cannot produce a positive clip.")

    reference_start = representative if point else start
    reference_end = representative if point else end
    return EvidenceRangeResolution(
        source_start_seconds=start,
        source_end_seconds=end,
        representative_timestamp_seconds=representative,
        clip_start_seconds=clip_start,
        clip_end_seconds=clip_end,
        requested_padding_before_seconds=padding_before,
        requested_padding_after_seconds=padding_after,
        applied_padding_before_seconds=max(0.0, reference_start - clip_start),
        applied_padding_after_seconds=max(0.0, clip_end - reference_end),
        start_clamped=start_clamped,
        end_clamped=end_clamped,
        source_interval_truncated=source_truncated,
    )


@dataclass(frozen=True)
class _Candidate:
    evidence_id: str
    rank: int
    media_id: str
    generation_id: str
    modalities: tuple[str, ...]
    start: float
    end: float
    representative: float
    frame_index: int | None
    frame_match: EvidenceFrameMatch
    score: float | None
    provenance: dict


def _operation_id(job_id: str | None, evidence_id: str, kind: str) -> str:
    digest = hashlib.sha256(
        f"{job_id or 'direct'}\0{evidence_id}\0{kind}".encode()
    ).digest()[:16]
    return UUID(bytes=digest, version=4).hex


def _failure(code: str, label: str, exc: BaseException) -> ErrorDetail:
    if isinstance(exc, ApplicationError):
        return exc.detail
    return ErrorDetail(
        code=code,
        category=ErrorCategory.unavailable,
        message=f"The {label} could not be produced.",
        details={
            "reason": type(exc).__name__,
            "remediation": (
                "Verify that the registered media remains readable and that "
                "the configured FFmpeg runtime is installed, then retry the job."
            ),
        },
        retryable=True,
    )


class EvidenceDeliveryService:
    def __init__(
        self,
        *,
        artifacts: ArtifactService,
        media: MediaService,
        max_clip_duration_seconds: float,
    ) -> None:
        self.artifacts = artifacts
        self.media = media
        self.max_clip_duration_seconds = max_clip_duration_seconds

    @staticmethod
    def _search_candidates(result: FusedSearchResult) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
        for moment in result.moments:
            if moment.moment_id is None:
                continue
            scene = next((hit for hit in moment.hits if hit.modality == "scene"), None)
            selected = scene or moment.hits[0]
            raw_index = selected.metadata.get("frame_index") if scene else None
            frame_index = raw_index if isinstance(raw_index, int) else None
            raw_timestamp = selected.metadata.get("timestamp") if scene else None
            representative = (
                float(raw_timestamp)
                if isinstance(raw_timestamp, (int, float))
                else (selected.start + selected.end) / 2
            )
            candidates.append(
                _Candidate(
                    evidence_id=moment.moment_id,
                    rank=moment.rank,
                    media_id=moment.media_id,
                    generation_id=selected.generation_id,
                    modalities=moment.modalities,
                    start=moment.start,
                    end=moment.end,
                    representative=representative,
                    frame_index=frame_index,
                    frame_match=(
                        EvidenceFrameMatch.exact_indexed_frame
                        if frame_index is not None
                        else EvidenceFrameMatch.representative
                    ),
                    score=moment.score,
                    provenance={
                        "constituent_hits": [
                            {
                                "generation_id": hit.generation_id,
                                "modality": hit.modality,
                                "source_id": hit.source_id,
                                "start": hit.start,
                                "end": hit.end,
                            }
                            for hit in moment.hits
                        ]
                    },
                )
            )
        return tuple(candidates)

    @staticmethod
    def _query_candidates(answer: QueryAnswer) -> tuple[_Candidate, ...]:
        candidates: list[_Candidate] = []
        for rank, evidence in enumerate(answer.evidence, start=1):
            if isinstance(evidence, MomentEvidence):
                raw_index = evidence.hit.metadata.get("frame_index")
                frame_index = (
                    raw_index
                    if evidence.modality == "scene" and isinstance(raw_index, int)
                    else None
                )
                raw_timestamp = evidence.hit.metadata.get("timestamp")
                representative = (
                    float(raw_timestamp)
                    if frame_index is not None
                    and isinstance(raw_timestamp, (int, float))
                    else (evidence.start + evidence.end) / 2
                )
                score = evidence.hit.score
                provenance = {
                    "source_id": evidence.source_id,
                    "kind": evidence.kind,
                }
            else:
                frame_index = None
                representative = (evidence.start + evidence.end) / 2
                score = None
                provenance = {
                    "cluster_id": evidence.cluster_id,
                    "detection_count": evidence.detection_count,
                    "kind": evidence.kind,
                    "identity_semantics": "anonymous_cluster",
                }
            candidates.append(
                _Candidate(
                    evidence_id=evidence.evidence_id,
                    rank=rank,
                    media_id=evidence.media_id,
                    generation_id=evidence.generation_id,
                    modalities=(evidence.modality,),
                    start=evidence.start,
                    end=evidence.end,
                    representative=representative,
                    frame_index=frame_index,
                    frame_match=(
                        EvidenceFrameMatch.exact_indexed_frame
                        if frame_index is not None
                        else EvidenceFrameMatch.representative
                    ),
                    score=score,
                    provenance=provenance,
                )
            )
        return tuple(candidates)

    def deliver_search(
        self,
        result: FusedSearchResult,
        policy: EvidenceDeliveryPolicy,
        *,
        execution: ExecutionContext | None = None,
    ) -> FusedSearchResult:
        delivery = self._deliver(
            self._search_candidates(result), policy, execution=execution
        )
        return result.model_copy(update={"evidence_delivery": delivery})

    def deliver_query(
        self,
        answer: QueryAnswer,
        policy: EvidenceDeliveryPolicy,
        *,
        execution: ExecutionContext | None = None,
    ) -> QueryAnswer:
        delivery = self._deliver(
            self._query_candidates(answer), policy, execution=execution
        )
        return answer.model_copy(update={"evidence_delivery": delivery})

    def _deliver(
        self,
        candidates: tuple[_Candidate, ...],
        policy: EvidenceDeliveryPolicy,
        *,
        execution: ExecutionContext | None,
    ) -> EvidenceDeliveryResult:
        active = execution_context(execution)
        if policy.mode == EvidenceDeliveryMode.none:
            return EvidenceDeliveryResult(policy=policy, items=())
        selected = candidates[: policy.max_items]
        items: list[EvidenceDeliveryItem] = []
        for position, candidate in enumerate(selected, start=1):
            active.report(
                {
                    "stage": "delivering_evidence",
                    "message": f"Preparing evidence {position} of {len(selected)}.",
                    "current": position - 1,
                    "total": len(selected),
                }
            )
            try:
                media = self.media.require_record(candidate.media_id)
                resolved = resolve_evidence_range(
                    source_start=candidate.start,
                    source_end=candidate.end,
                    representative_timestamp=candidate.representative,
                    media_duration=media.duration_seconds,
                    padding_before=policy.padding_before_seconds,
                    padding_after=policy.padding_after_seconds,
                    max_duration=self.max_clip_duration_seconds,
                )
            except IndexCancelledError:
                raise
            except Exception as exc:
                items.append(
                    EvidenceDeliveryItem(
                        evidence_id=candidate.evidence_id,
                        rank=candidate.rank,
                        media_id=candidate.media_id,
                        generation_id=candidate.generation_id,
                        modalities=candidate.modalities,
                        score=candidate.score,
                        provenance=candidate.provenance,
                        state=EvidenceDeliveryState.failed,
                        errors=(
                            _failure(
                                "evidence_range_resolution_failed",
                                "evidence range",
                                exc,
                            ),
                        ),
                    )
                )
                continue
            errors: list[ErrorDetail] = []
            keyframe = None
            clip = None
            try:
                frame, width, height = self.artifacts.create_evidence_frame(
                    media_id=candidate.media_id,
                    generation_id=candidate.generation_id,
                    evidence_id=candidate.evidence_id,
                    timestamp_seconds=resolved.representative_timestamp_seconds,
                    frame_index=candidate.frame_index,
                    job_id=active.job_id,
                    execution=active,
                    artifact_operation_id=_operation_id(
                        active.job_id, candidate.evidence_id, "frame"
                    ),
                )
                keyframe = EvidenceKeyframe(
                    match=candidate.frame_match,
                    timestamp_seconds=resolved.representative_timestamp_seconds,
                    frame_index=candidate.frame_index,
                    width=width,
                    height=height,
                    artifact=EvidenceArtifact(
                        artifact=frame,
                    ),
                )
            except IndexCancelledError:
                raise
            except Exception as exc:
                errors.append(
                    _failure(
                        "evidence_frame_delivery_failed",
                        "evidence frame",
                        exc,
                    )
                )
            if policy.mode == EvidenceDeliveryMode.keyframes_and_clips:
                try:
                    rendered = self.artifacts.create_snippet(
                        CreateSnippetCommand(
                            media_id=candidate.media_id,
                            start_seconds=resolved.clip_start_seconds,
                            end_seconds=resolved.clip_end_seconds,
                            profile=policy.clip_profile,
                        ),
                        job_id=active.job_id,
                        execution=active,
                        artifact_operation_id=_operation_id(
                            active.job_id,
                            candidate.evidence_id,
                            f"clip:{policy.clip_profile.value}",
                        ),
                    )
                    clip = EvidenceArtifact(
                        artifact=rendered,
                    )
                except IndexCancelledError:
                    raise
                except Exception as exc:
                    errors.append(
                        _failure(
                            "evidence_clip_delivery_failed",
                            "evidence clip",
                            exc,
                        )
                    )
            items.append(
                EvidenceDeliveryItem(
                    evidence_id=candidate.evidence_id,
                    rank=candidate.rank,
                    media_id=candidate.media_id,
                    generation_id=candidate.generation_id,
                    modalities=candidate.modalities,
                    score=candidate.score,
                    provenance=candidate.provenance,
                    state=(
                        EvidenceDeliveryState.partial
                        if errors and (keyframe is not None or clip is not None)
                        else EvidenceDeliveryState.failed
                        if errors
                        else EvidenceDeliveryState.ready
                    ),
                    range=resolved,
                    keyframe=keyframe,
                    clip=clip,
                    errors=tuple(errors),
                )
            )
        return EvidenceDeliveryResult(policy=policy, items=tuple(items))

    def resolve_job_evidence(
        self,
        result: FusedSearchResult | QueryAnswer,
        evidence_id: str,
        *,
        padding_before: float,
        padding_after: float,
    ) -> tuple[_Candidate, EvidenceRangeResolution]:
        candidates = (
            self._search_candidates(result)
            if isinstance(result, FusedSearchResult)
            else self._query_candidates(result)
        )
        candidate = next(
            (item for item in candidates if item.evidence_id == evidence_id),
            None,
        )
        if candidate is None:
            raise ApplicationError(
                "evidence_not_in_source_job",
                ErrorCategory.not_found,
                "The evidence ID does not belong to the completed source job.",
            )
        media = self.media.require_record(candidate.media_id)
        return candidate, resolve_evidence_range(
            source_start=candidate.start,
            source_end=candidate.end,
            representative_timestamp=candidate.representative,
            media_duration=media.duration_seconds,
            padding_before=padding_before,
            padding_after=padding_after,
            max_duration=self.max_clip_duration_seconds,
        )
