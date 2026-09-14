"""End-to-end retrieval evaluation over VidXP's public search path.

The dataset benchmarks (DiDeMo, HiREST) score a single modality against an
official evaluator through the benchmark-ready core. This harness is
complementary: it drives the same public ``search`` operation that real
clients call and reports, in one place, whether relevant moments are found,
how accurate their timestamps are, how well results are ranked across
modalities, whether delivered evidence supports the result, and the
operational cost (latency, failures, and degraded evidence).

The evaluator is dependency-injected. A caller supplies a ``SearchFn`` that
maps an :class:`EvaluationCase` to a public ``FusedSearchResult``; in
production that closure calls ``VidXPApplication.search`` (see
:func:`application_search_fn`), while tests pass a deterministic fake. All
metric helpers are pure functions so the scoring is exercised without models.

Scores in the returned report inherit VidXP's ordering-only meaning: they rank
results within one response and are not probabilities.
"""

from __future__ import annotations

import statistics
from math import log2
from time import perf_counter
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vidxp.application_models import (
    ApplicationError,
    EvidenceDeliveryState,
    FusedSearchResult,
    IndexSnapshotReference,
    InitialEvidenceDeliveryPolicy,
    SearchCommand,
)


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GroundTruthInterval(EvalModel):
    """A relevant time span for a query, in one media item."""

    media_id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def _validate_interval(self) -> "GroundTruthInterval":
        if self.end <= self.start:
            raise ValueError("Ground-truth end must be greater than start.")
        return self


class EvaluationCase(EvalModel):
    """One labeled query and the moments that should be retrieved for it."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    media_id: str | None = Field(
        default=None,
        description="Optional single-media scope passed through to search.",
    )
    modalities: tuple[str, ...] = ()
    relevant: tuple[GroundTruthInterval, ...] = Field(min_length=1)


class EvaluationDataset(EvalModel):
    name: str = Field(min_length=1)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_case_ids(self) -> "EvaluationDataset":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case ids must be unique.")
        return self


class CaseResult(EvalModel):
    """Per-case outcome; failed cases carry an error and no metrics."""

    case_id: str
    status: str = Field(description="ok, no_result, or failed.")
    returned_moments: int = 0
    top1_iou: float = 0.0
    best_iou: float = 0.0
    first_relevant_rank: int | None = None
    contributing_modalities: tuple[str, ...] = ()
    evidence_delivered: bool = False
    evidence_supported: bool | None = None
    evidence_degraded: bool = False
    latency_ms: float = 0.0
    error_code: str | None = None


class AggregateMetrics(EvalModel):
    evaluated_cases: int
    failed_cases: int
    no_result_cases: int
    recall_at_k: dict[int, float] = Field(default_factory=dict)
    mean_top1_iou: float = 0.0
    mean_reciprocal_rank: float = 0.0
    ndcg_at_k: dict[int, float] = Field(default_factory=dict)
    modality_contribution: dict[str, int] = Field(default_factory=dict)
    evidence_support_rate: float | None = None
    latency_ms_mean: float = 0.0
    latency_ms_p50: float = 0.0
    latency_ms_p95: float = 0.0


class EndToEndEvaluationReport(EvalModel):
    dataset_name: str
    total_cases: int
    relevance_iou: float
    k_values: tuple[int, ...]
    score_meaning: str = Field(
        default="ordering_only",
        description="Search scores rank within one response; not a probability.",
    )
    aggregate: AggregateMetrics
    cases: tuple[CaseResult, ...]


SearchFn = Callable[[EvaluationCase], FusedSearchResult]


def temporal_iou(
    a_start: float,
    a_end: float,
    b_start: float,
    b_end: float,
) -> float:
    """Intersection-over-union of two time intervals; 0.0 when disjoint."""

    intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    if intersection <= 0.0:
        return 0.0
    union = (a_end - a_start) + (b_end - b_start) - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def best_interval_iou(
    *,
    media_id: str,
    start: float,
    end: float,
    relevant: tuple[GroundTruthInterval, ...],
) -> float:
    """Best temporal IoU of one span against same-media ground truth."""

    return max(
        (
            temporal_iou(start, end, gt.start, gt.end)
            for gt in relevant
            if gt.media_id == media_id
        ),
        default=0.0,
    )


def _dcg(gains: list[float]) -> float:
    return sum(gain / log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def ndcg_at_k(gains: list[float], k: int) -> float:
    """Normalized DCG over graded (IoU) gains for the first ``k`` results.

    Normalizes against the same retrieved gains sorted descending, so the
    metric reflects how well the ranking ordered the moments it returned.
    """

    top = gains[:k]
    ideal = _dcg(sorted(top, reverse=True))
    if ideal <= 0.0:
        return 0.0
    return _dcg(top) / ideal


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _evidence_outcome(
    result: FusedSearchResult,
    case: EvaluationCase,
    *,
    relevance_iou: float,
) -> tuple[bool, bool | None, bool]:
    """Return (delivered, supported, degraded) for a case's evidence."""

    delivery = result.evidence_delivery
    if delivery is None or not delivery.items:
        return False, None, False
    supported = False
    degraded = False
    for item in delivery.items:
        if item.state != EvidenceDeliveryState.ready:
            degraded = True
        if item.range is None:
            continue
        iou = best_interval_iou(
            media_id=item.media_id,
            start=item.range.source_start_seconds,
            end=item.range.source_end_seconds,
            relevant=case.relevant,
        )
        if iou >= relevance_iou:
            supported = True
    return True, supported, degraded


def evaluate_case(
    case: EvaluationCase,
    result: FusedSearchResult,
    *,
    relevance_iou: float,
    latency_ms: float,
) -> tuple[CaseResult, list[float]]:
    """Score one already-executed case; also return the per-moment IoU gains."""

    moments = tuple(sorted(result.moments, key=lambda moment: moment.rank))
    gains = [
        best_interval_iou(
            media_id=moment.media_id,
            start=moment.start,
            end=moment.end,
            relevant=case.relevant,
        )
        for moment in moments
    ]
    first_relevant_rank: int | None = None
    contributing: tuple[str, ...] = ()
    for position, (moment, gain) in enumerate(zip(moments, gains), start=1):
        if gain >= relevance_iou:
            first_relevant_rank = position
            contributing = tuple(moment.modalities)
            break
    delivered, supported, degraded = _evidence_outcome(
        result,
        case,
        relevance_iou=relevance_iou,
    )
    status = "ok" if moments else "no_result"
    case_result = CaseResult(
        case_id=case.case_id,
        status=status,
        returned_moments=len(moments),
        top1_iou=gains[0] if gains else 0.0,
        best_iou=max(gains, default=0.0),
        first_relevant_rank=first_relevant_rank,
        contributing_modalities=contributing,
        evidence_delivered=delivered,
        evidence_supported=supported,
        evidence_degraded=degraded,
        latency_ms=latency_ms,
    )
    return case_result, gains


def evaluate_end_to_end(
    dataset: EvaluationDataset,
    search_fn: SearchFn,
    *,
    relevance_iou: float = 0.5,
    k_values: tuple[int, ...] = (1, 5, 10),
) -> EndToEndEvaluationReport:
    """Run every case through ``search_fn`` and aggregate the five measures.

    A case whose search raises is recorded as failed and excluded from the
    quality metrics; the run continues so one broken query never hides the
    rest. An :class:`ApplicationError` keeps its ``code``; any other exception
    is recorded under its class name.
    """

    if not 0.0 < relevance_iou <= 1.0:
        raise ValueError("relevance_iou must be within (0, 1].")
    if not k_values or any(k <= 0 for k in k_values):
        raise ValueError("k_values must be positive.")
    ordered_k = tuple(sorted(set(k_values)))

    case_results: list[CaseResult] = []
    gains_by_case: list[list[float]] = []
    latencies: list[float] = []

    for case in dataset.cases:
        started = perf_counter()
        try:
            result = search_fn(case)
        except Exception as error:
            # A reliability harness must survive one broken query and still
            # report it, so every search failure is recorded and the run
            # continues. Metric computation below is outside this guard, so a
            # bug in the harness itself still surfaces.
            latency_ms = (perf_counter() - started) * 1000.0
            latencies.append(latency_ms)
            error_code = (
                error.code
                if isinstance(error, ApplicationError)
                else type(error).__name__
            )
            case_results.append(
                CaseResult(
                    case_id=case.case_id,
                    status="failed",
                    latency_ms=latency_ms,
                    error_code=error_code,
                )
            )
            gains_by_case.append([])
            continue
        latency_ms = (perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        case_result, gains = evaluate_case(
            case,
            result,
            relevance_iou=relevance_iou,
            latency_ms=latency_ms,
        )
        case_results.append(case_result)
        gains_by_case.append(gains)

    aggregate = _aggregate(
        case_results,
        gains_by_case,
        latencies,
        relevance_iou=relevance_iou,
        k_values=ordered_k,
    )
    return EndToEndEvaluationReport(
        dataset_name=dataset.name,
        total_cases=len(dataset.cases),
        relevance_iou=relevance_iou,
        k_values=ordered_k,
        aggregate=aggregate,
        cases=tuple(case_results),
    )


def _aggregate(
    case_results: list[CaseResult],
    gains_by_case: list[list[float]],
    latencies: list[float],
    *,
    relevance_iou: float,
    k_values: tuple[int, ...],
) -> AggregateMetrics:
    scored = [
        (result, gains)
        for result, gains in zip(case_results, gains_by_case)
        if result.status != "failed"
    ]
    evaluated = len(scored)
    failed = sum(1 for result in case_results if result.status == "failed")
    no_result = sum(1 for result in case_results if result.status == "no_result")

    recall_at_k: dict[int, float] = {}
    ndcg_at_k_values: dict[int, float] = {}
    modality_contribution: dict[str, int] = {}
    reciprocal_ranks: list[float] = []
    top1_ious: list[float] = []
    evidence_flags: list[bool] = []

    for result, gains in scored:
        top1_ious.append(gains[0] if gains else 0.0)
        rank = result.first_relevant_rank
        reciprocal_ranks.append(1.0 / rank if rank is not None else 0.0)
        for modality in result.contributing_modalities:
            modality_contribution[modality] = (
                modality_contribution.get(modality, 0) + 1
            )
        if result.evidence_delivered and result.evidence_supported is not None:
            evidence_flags.append(result.evidence_supported)

    for k in k_values:
        hits = sum(
            1
            for _, gains in scored
            if any(gain >= relevance_iou for gain in gains[:k])
        )
        recall_at_k[k] = hits / evaluated if evaluated else 0.0
        ndcg_scores = [ndcg_at_k(gains, k) for _, gains in scored]
        ndcg_at_k_values[k] = (
            statistics.fmean(ndcg_scores) if ndcg_scores else 0.0
        )

    return AggregateMetrics(
        evaluated_cases=evaluated,
        failed_cases=failed,
        no_result_cases=no_result,
        recall_at_k=recall_at_k,
        mean_top1_iou=statistics.fmean(top1_ious) if top1_ious else 0.0,
        mean_reciprocal_rank=(
            statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0
        ),
        ndcg_at_k=ndcg_at_k_values,
        modality_contribution=modality_contribution,
        evidence_support_rate=(
            statistics.fmean(1.0 if flag else 0.0 for flag in evidence_flags)
            if evidence_flags
            else None
        ),
        latency_ms_mean=statistics.fmean(latencies) if latencies else 0.0,
        latency_ms_p50=_percentile(latencies, 0.50),
        latency_ms_p95=_percentile(latencies, 0.95),
    )


def application_search_fn(
    search: Callable[..., FusedSearchResult],
    *,
    snapshot: IndexSnapshotReference | None = None,
    top_k: int = 10,
    evidence_delivery: InitialEvidenceDeliveryPolicy | None = None,
) -> SearchFn:
    """Bind VidXP's public ``search`` operation into a ``SearchFn``.

    ``search`` is normally ``VidXPApplication.search``. Each case is turned
    into the same ``SearchCommand`` a real client would send, so the harness
    measures the public product path rather than an internal shortcut.
    """

    def run(case: EvaluationCase) -> FusedSearchResult:
        command = SearchCommand(
            query=case.query,
            modalities=case.modalities,
            media_id=case.media_id,
            top_k=top_k,
            evidence_delivery=evidence_delivery,
        )
        if snapshot is None:
            return search(command)
        return search(command, snapshot=snapshot)

    return run
