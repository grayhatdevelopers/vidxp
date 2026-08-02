from __future__ import annotations

import hashlib
import json

from vidxp.application_models import (
    ApplicationError,
    CreateSnippetCommand,
    ErrorCategory,
    ErrorDetail,
    EvidenceArtifact,
    EvidenceBoardCandidate,
    EvidenceBoardJobRequest,
    EvidenceDeliveryItem,
    EvidenceDeliveryMode,
    EvidenceDeliveryPolicy,
    EvidenceDeliveryResult,
    EvidenceDeliveryState,
    EvidenceFrameMatch,
    EvidenceKeyframe,
    EvidenceRangeResolution,
    FusedSearchResult,
    Job,
    JobKind,
    JobState,
    MomentEvidence,
    QueryAnswer,
)
from vidxp.artifact_service import (
    ArtifactService,
    deterministic_artifact_operation_id,
)
from vidxp.core.contracts import IndexCancelledError
from vidxp.execution import ExecutionContext, execution_context
from vidxp.media_service import MediaService


def require_completed_evidence_result(
    job: Job,
) -> FusedSearchResult | QueryAnswer:
    if (
        job.state != JobState.succeeded
        or job.result is None
        or job.kind not in {JobKind.search, JobKind.query}
    ):
        raise ApplicationError(
            "evidence_source_job_not_complete",
            ErrorCategory.conflict,
            "Evidence materialization requires a completed search or query job.",
        )
    return job.result.result


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
    def _search_candidates(
        result: FusedSearchResult,
    ) -> tuple[EvidenceBoardCandidate, ...]:
        candidates: list[EvidenceBoardCandidate] = []
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
                EvidenceBoardCandidate(
                    evidence_id=moment.moment_id,
                    rank=moment.rank,
                    media_id=moment.media_id,
                    generation_id=selected.generation_id,
                    modalities=moment.modalities,
                    start=moment.start,
                    end=moment.end,
                    representative_timestamp=representative,
                    frame_index=frame_index,
                    frame_match=(
                        EvidenceFrameMatch.exact_indexed_frame
                        if frame_index is not None
                        else EvidenceFrameMatch.representative
                    ),
                    score=moment.score,
                    display_text=EvidenceDeliveryService._display_text(
                        selected.metadata
                    ),
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
    def _query_candidates(
        answer: QueryAnswer,
    ) -> tuple[EvidenceBoardCandidate, ...]:
        candidates: list[EvidenceBoardCandidate] = []
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
                EvidenceBoardCandidate(
                    evidence_id=evidence.evidence_id,
                    rank=rank,
                    media_id=evidence.media_id,
                    generation_id=evidence.generation_id,
                    modalities=(evidence.modality,),
                    start=evidence.start,
                    end=evidence.end,
                    representative_timestamp=representative,
                    frame_index=frame_index,
                    frame_match=(
                        EvidenceFrameMatch.exact_indexed_frame
                        if frame_index is not None
                        else EvidenceFrameMatch.representative
                    ),
                    score=score,
                    display_text=evidence.display_text,
                    provenance=provenance,
                )
            )
        return tuple(candidates)

    @staticmethod
    def _display_text(metadata: dict) -> str | None:
        for key in ("display_text", "text", "transcript", "caption", "label"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:512]
        return None

    @classmethod
    def candidates(
        cls,
        result: FusedSearchResult | QueryAnswer,
    ) -> tuple[EvidenceBoardCandidate, ...]:
        return (
            cls._search_candidates(result)
            if isinstance(result, FusedSearchResult)
            else cls._query_candidates(result)
        )

    @classmethod
    def prepare_board_request(
        cls,
        *,
        source_job_id: str,
        evidence_ids: tuple[str, ...] | None,
        start_rank: int,
        result: FusedSearchResult | QueryAnswer,
    ) -> EvidenceBoardJobRequest:
        candidates = cls.candidates(result)
        by_id = {candidate.evidence_id: candidate for candidate in candidates}
        if evidence_ids is None:
            selected = tuple(
                candidate
                for candidate in candidates
                if candidate.rank >= start_rank
            )
        else:
            missing = tuple(
                evidence_id
                for evidence_id in evidence_ids
                if evidence_id not in by_id
            )
            if missing:
                raise ApplicationError(
                    "evidence_not_in_source_job",
                    ErrorCategory.not_found,
                    "One or more evidence IDs do not belong to the completed source job.",
                    details={"evidence_ids": list(missing)},
                )
            selected = tuple(
                sorted(
                    (
                        by_id[evidence_id]
                        for evidence_id in evidence_ids
                        if by_id[evidence_id].rank >= start_rank
                    ),
                    key=lambda candidate: candidate.rank,
                )
            )
        if not selected:
            raise ApplicationError(
                "evidence_board_empty",
                ErrorCategory.validation,
                "The requested source range contains no boardable evidence.",
            )
        source_payload = [candidate.model_dump(mode="json") for candidate in candidates]
        source_fingerprint = hashlib.sha256(
            json.dumps(
                source_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return EvidenceBoardJobRequest(
            source_job_id=source_job_id,
            source_fingerprint=source_fingerprint,
            candidates=selected,
        )

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

    def deliver_selected(
        self,
        result: FusedSearchResult | QueryAnswer,
        evidence_ids: tuple[str, ...],
        policy: EvidenceDeliveryPolicy,
        *,
        execution: ExecutionContext | None = None,
    ) -> EvidenceDeliveryResult:
        """Materialize a bounded selection from a completed retrieval result."""

        candidates = self.candidates(result)
        by_id = {candidate.evidence_id: candidate for candidate in candidates}
        missing = tuple(item for item in evidence_ids if item not in by_id)
        if missing:
            raise ApplicationError(
                "evidence_not_in_source_job",
                ErrorCategory.not_found,
                "One or more evidence IDs do not belong to the completed source job.",
                details={"evidence_ids": list(missing)},
            )
        selected = tuple(by_id[item] for item in evidence_ids)
        return self._deliver(selected, policy, execution=execution)

    def _deliver(
        self,
        candidates: tuple[EvidenceBoardCandidate, ...],
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
                    representative_timestamp=candidate.representative_timestamp,
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
                    artifact_operation_id=deterministic_artifact_operation_id(
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
                        artifact_operation_id=deterministic_artifact_operation_id(
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
    ) -> tuple[EvidenceBoardCandidate, EvidenceRangeResolution]:
        candidates = self.candidates(result)
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
            representative_timestamp=candidate.representative_timestamp,
            media_duration=media.duration_seconds,
            padding_before=padding_before,
            padding_after=padding_after,
            max_duration=self.max_clip_duration_seconds,
        )
