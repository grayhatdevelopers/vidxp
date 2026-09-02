from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


DIWAN_PAPER_URL = "https://proceedings.mlr.press/v203/diwan23a.html"
DIWAN_CONTENT_THRESHOLD = 53.0


@dataclass(frozen=True)
class TemporalShot:
    start: float
    end: float


@dataclass(frozen=True)
class RankedShot:
    rank: int
    start: float
    end: float
    score: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True)
class FusedShot:
    rank: int
    scene_rank: int | None
    start: float
    end: float
    score: float
    best_ranks: tuple[tuple[str, int], ...]
    source_ids: tuple[str, ...]
    evidence: tuple[ProposalEvidence, ...]


@dataclass(frozen=True)
class ProposalEvidence:
    modality: str
    rank: int
    source_id: str
    proposal_overlap_count: int


def rank_shots_from_scene_records(
    shots: Sequence[TemporalShot],
    records: Sequence[dict[str, Any]],
) -> tuple[RankedShot, ...]:
    """Rank disjoint shot proposals by the best contained scene score."""

    ordered = tuple(sorted(shots, key=lambda shot: (shot.start, shot.end)))
    if any(shot.start < 0 or shot.end <= shot.start for shot in ordered):
        raise ValueError("shot proposals require valid positive intervals")
    if any(left.end > right.start for left, right in zip(ordered, ordered[1:])):
        raise ValueError("shot proposals must not overlap")

    scored = []
    for index, shot in enumerate(ordered):
        is_last = index == len(ordered) - 1
        contained = tuple(
            record
            for record in records
            if shot.start <= float(record["start_seconds"])
            and (
                float(record["start_seconds"]) < shot.end
                or (is_last and float(record["start_seconds"]) <= shot.end)
            )
        )
        if not contained:
            continue
        scored.append(
            (
                max(float(record["ordering_score"]) for record in contained),
                shot,
                tuple(str(record["source_id"]) for record in contained),
            )
        )

    scored.sort(key=lambda item: (-item[0], item[1].start, item[1].end))
    return tuple(
        RankedShot(
            rank=rank,
            start=shot.start,
            end=shot.end,
            score=score,
            source_ids=source_ids,
        )
        for rank, (score, shot, source_ids) in enumerate(scored, start=1)
    )


def rank_shots_with_rrf_evidence(
    shots: Sequence[TemporalShot],
    records_by_modality: Mapping[str, Sequence[dict[str, Any]]],
    *,
    scene_ranking: Sequence[RankedShot] = (),
    candidate_top_k: int,
    rank_constant: int = 60,
) -> tuple[FusedShot, ...]:
    """Rank fixed shot boundaries with the best overlapping rank per modality."""

    if candidate_top_k <= 0:
        raise ValueError("candidate_top_k must be positive")
    if rank_constant < 0:
        raise ValueError("rank_constant must not be negative")

    scene_by_interval = {
        (shot.start, shot.end): shot for shot in scene_ranking
    }
    if len(scene_by_interval) != len(scene_ranking):
        raise ValueError("scene ranking contains duplicate shot intervals")
    shot_intervals = {(shot.start, shot.end) for shot in shots}
    if any(interval not in shot_intervals for interval in scene_by_interval):
        raise ValueError("scene ranking contains an unknown shot interval")

    candidates = []
    for shot in shots:
        scene_shot = scene_by_interval.get((shot.start, shot.end))
        best_ranks = {"scene": scene_shot.rank} if scene_shot else {}
        source_ids = list(scene_shot.source_ids) if scene_shot else []
        evidence = []
        for modality, records in records_by_modality.items():
            if modality == "scene":
                continue
            overlapping = tuple(
                record
                for record in records
                if int(record["retrieval_rank"]) <= candidate_top_k
                and min(shot.end, float(record["end_seconds"]))
                > max(shot.start, float(record["start_seconds"]))
            )
            if not overlapping:
                continue
            best = min(overlapping, key=lambda record: int(record["retrieval_rank"]))
            best_ranks[modality] = int(best["retrieval_rank"])
            source_ids.append(str(best["source_id"]))
            evidence.append(
                ProposalEvidence(
                    modality=modality,
                    rank=int(best["retrieval_rank"]),
                    source_id=str(best["source_id"]),
                    proposal_overlap_count=sum(
                        min(candidate.end, float(best["end_seconds"]))
                        > max(candidate.start, float(best["start_seconds"]))
                        for candidate in shots
                    ),
                )
            )
        if not best_ranks:
            continue
        score = sum(
            1.0 / (rank_constant + rank) for rank in best_ranks.values()
        )
        candidates.append(
            (
                score,
                scene_shot,
                shot,
                tuple(sorted(best_ranks.items())),
                source_ids,
                tuple(sorted(evidence, key=lambda item: item.modality)),
            )
        )

    candidates.sort(
        key=lambda item: (
            -item[0],
            item[1].rank if item[1] is not None else candidate_top_k + 1,
            item[2].start,
            item[2].end,
        )
    )
    return tuple(
        FusedShot(
            rank=rank,
            scene_rank=scene_shot.rank if scene_shot else None,
            start=shot.start,
            end=shot.end,
            score=score,
            best_ranks=best_ranks,
            source_ids=tuple(source_ids),
            evidence=evidence,
        )
        for rank, (
            score,
            scene_shot,
            shot,
            best_ranks,
            source_ids,
            evidence,
        ) in enumerate(
            candidates,
            start=1,
        )
    )
