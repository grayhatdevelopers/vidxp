from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str
    relevant_media_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.text.strip():
            raise ValueError("Retrieval queries require an ID and text.")
        if not self.relevant_media_ids:
            raise ValueError("Retrieval queries require relevant media IDs.")


@dataclass(frozen=True)
class TemporalQuery:
    query_id: str
    media_id: str
    text: str
    intervals: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.media_id.strip():
            raise ValueError("Temporal queries require query and media IDs.")
        if not self.text.strip() or not self.intervals:
            raise ValueError("Temporal queries require text and intervals.")
        if any(start < 0 or end <= start for start, end in self.intervals):
            raise ValueError("Temporal query intervals must be positive ranges.")


def retrieval_metrics(
    queries: Sequence[RetrievalQuery],
    rankings: Mapping[str, Sequence[str]],
) -> dict[str, float | int]:
    """Score text-to-media rankings without changing dataset semantics."""

    ranks: list[int] = []
    average_precision_at_10: list[float] = []
    for query in queries:
        ranking = rankings.get(query.query_id)
        if ranking is None:
            raise ValueError(f"Missing ranking for query {query.query_id!r}.")
        if len(ranking) != len(set(ranking)):
            raise ValueError(
                f"Ranking for query {query.query_id!r} contains duplicates."
            )
        relevant = set(query.relevant_media_ids)
        try:
            rank = next(
                index
                for index, media_id in enumerate(ranking, start=1)
                if media_id in relevant
            )
        except StopIteration:
            rank = len(ranking) + 1
        ranks.append(rank)
        hits = 0
        precision_sum = 0.0
        for index, media_id in enumerate(ranking[:10], start=1):
            if media_id in relevant:
                hits += 1
                precision_sum += hits / index
        average_precision_at_10.append(
            precision_sum / min(len(relevant), 10)
        )

    if not ranks:
        raise ValueError("At least one retrieval query is required.")
    return {
        "query_count": len(ranks),
        "recall_at_1": mean(rank <= 1 for rank in ranks),
        "recall_at_5": mean(rank <= 5 for rank in ranks),
        "recall_at_10": mean(rank <= 10 for rank in ranks),
        "recall_at_50": mean(rank <= 50 for rank in ranks),
        "median_rank": float(median(ranks)),
        "mean_rank": mean(ranks),
        "map_at_10": mean(average_precision_at_10),
    }


def _interval_iou(
    start: float,
    end: float,
    target_start: float,
    target_end: float,
) -> float:
    intersection = max(0.0, min(end, target_end) - max(start, target_start))
    union = max(end, target_end) - min(start, target_start)
    return intersection / union if union > 0 else 0.0


def temporal_retrieval_metrics(
    queries: Sequence[TemporalQuery],
    predictions: Mapping[str, Sequence[tuple[float, float]]],
) -> dict[str, float | int]:
    """Score ranked intervals with standard temporal-IoU recall diagnostics."""

    best_at: dict[int, list[float]] = {1: [], 5: []}
    for query in queries:
        ranked = predictions.get(query.query_id)
        if ranked is None:
            raise ValueError(f"Missing predictions for query {query.query_id!r}.")
        for cutoff in best_at:
            candidates = ranked[:cutoff]
            best_at[cutoff].append(
                max(
                    (
                        _interval_iou(start, end, target_start, target_end)
                        for start, end in candidates
                        for target_start, target_end in query.intervals
                    ),
                    default=0.0,
                )
            )

    if not queries:
        raise ValueError("At least one temporal query is required.")
    metrics: dict[str, float | int] = {
        "query_count": len(queries),
        "mean_iou_at_1": mean(best_at[1]),
    }
    for cutoff, values in best_at.items():
        for threshold in (0.3, 0.5, 0.7):
            label = str(threshold).replace(".", "_")
            metrics[f"recall_at_{cutoff}_tiou_{label}"] = mean(
                value >= threshold for value in values
            )
    return metrics
