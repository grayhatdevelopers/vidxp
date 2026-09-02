from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


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
