from __future__ import annotations

import hashlib
import json

from vidxp.application_models import (
    FusedMoment,
    FusedSearchResult,
    FusionProvenance,
    SearchHit,
    SearchResult,
)


RRF_RANK_CONSTANT = 60


def _query_id(
    query: str,
    modalities: tuple[str, ...],
    media_id: str | None,
    atomic_query_ids: tuple[str, ...],
) -> str:
    identity = "\0".join(
        (
            "rrf_v2",
            query,
            ",".join(modalities),
            media_id or "*",
            *atomic_query_ids,
        )
    )
    return "fused:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _hit_priority(hit: SearchHit) -> tuple[object, ...]:
    return (
        hit.rank,
        hit.end - hit.start,
        hit.media_id,
        hit.start,
        hit.end,
        hit.modality,
        hit.source_id,
    )


def _overlaps(anchor: SearchHit, candidate: SearchHit) -> bool:
    return (
        anchor.media_id == candidate.media_id
        and min(anchor.end, candidate.end) > max(anchor.start, candidate.start)
    )


def _rank_anchored_groups(
    hits: tuple[SearchHit, ...],
) -> list[list[SearchHit]]:
    """Keep moments separate and attach only direct cross-modal support."""

    remaining = sorted(hits, key=_hit_priority)
    groups: list[list[SearchHit]] = []
    while remaining:
        anchor = remaining.pop(0)
        best_by_modality: dict[str, tuple[int, SearchHit]] = {}
        for index, candidate in enumerate(remaining):
            if candidate.modality == anchor.modality or not _overlaps(
                anchor,
                candidate,
            ):
                continue
            current = best_by_modality.get(candidate.modality)
            if current is None or _hit_priority(candidate) < _hit_priority(
                current[1]
            ):
                best_by_modality[candidate.modality] = (index, candidate)

        selected = {index for index, _ in best_by_modality.values()}
        groups.append(
            [anchor]
            + [
                candidate
                for _, candidate in sorted(
                    best_by_modality.values(),
                    key=lambda item: _hit_priority(item[1]),
                )
            ]
        )
        remaining = [
            candidate
            for index, candidate in enumerate(remaining)
            if index not in selected
        ]
    return groups


def _score(hits: list[SearchHit]) -> float:
    best_ranks: dict[str, int] = {}
    for hit in hits:
        best_ranks[hit.modality] = min(
            hit.rank,
            best_ranks.get(hit.modality, hit.rank),
        )
    return sum(1.0 / (RRF_RANK_CONSTANT + rank) for rank in best_ranks.values())


def _moment_id(
    *,
    snapshot_id: str | None,
    media_id: str,
    start: float,
    end: float,
    hits: tuple[SearchHit, ...],
) -> str:
    identity = {
        "snapshot_id": snapshot_id or "legacy-unpinned",
        "media_id": media_id,
        "start": start,
        "end": end,
        "hits": [
            {
                "generation_id": hit.generation_id,
                "media_id": hit.media_id,
                "modality": hit.modality,
                "source_id": hit.source_id,
                "start": hit.start,
                "end": hit.end,
            }
            for hit in sorted(
                hits,
                key=lambda item: (
                    item.generation_id,
                    item.media_id,
                    item.modality,
                    item.source_id,
                    item.start,
                    item.end,
                ),
            )
        ],
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def fuse_search_results(
    *,
    query: str,
    requested_modalities: tuple[str, ...],
    results: tuple[SearchResult, ...],
    media_id: str | None = None,
    top_k: int = 10,
    snapshot_id: str | None = None,
) -> FusedSearchResult:
    by_modality = {result.modality: result for result in results}
    if len(by_modality) != len(results):
        raise ValueError("Fusion accepts one result per modality.")
    searched_modalities = tuple(
        modality for modality in requested_modalities if modality in by_modality
    ) + tuple(sorted(set(by_modality) - set(requested_modalities)))
    ordered_results = tuple(by_modality[modality] for modality in searched_modalities)
    flattened = tuple(hit for result in ordered_results for hit in result.hits)
    candidates = []
    for hits in _rank_anchored_groups(flattened):
        ordered_hits = tuple(
            sorted(
                hits,
                key=lambda hit: (
                    hit.modality,
                    hit.rank,
                    hit.source_id,
                ),
            )
        )
        candidates.append(
            {
                "score": _score(hits),
                "media_id": hits[0].media_id,
                "start": min(hit.start for hit in hits),
                "end": max(hit.end for hit in hits),
                "modalities": tuple(sorted({hit.modality for hit in hits})),
                "hits": ordered_hits,
            }
        )
    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["media_id"],
            item["start"],
            item["end"],
            tuple(hit.source_id for hit in item["hits"]),
        )
    )
    moments = tuple(
        FusedMoment(
            rank=rank,
            moment_id=_moment_id(
                snapshot_id=snapshot_id,
                media_id=candidate["media_id"],
                start=candidate["start"],
                end=candidate["end"],
                hits=candidate["hits"],
            ),
            **candidate,
        )
        for rank, candidate in enumerate(candidates[:top_k], start=1)
    )
    return FusedSearchResult(
        query_id=_query_id(
            query,
            searched_modalities,
            media_id,
            tuple(result.query_id for result in ordered_results),
        ),
        query=query,
        modalities=searched_modalities,
        moments=moments,
        fusion=FusionProvenance(
            requested_modalities=requested_modalities,
            searched_modalities=searched_modalities,
        ),
    )
