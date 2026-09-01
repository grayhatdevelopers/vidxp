from __future__ import annotations

import hashlib
import json

from vidxp.application_models import (
    FusedMoment,
    FusedSearchResult,
    FusionProfile,
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
            "temporal_anchor_rrf_v1",
            query,
            ",".join(modalities),
            media_id or "*",
            *atomic_query_ids,
        )
    )
    return "fused:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _connected_components(
    hits: tuple[SearchHit, ...],
) -> list[list[SearchHit]]:
    ordered = sorted(
        hits,
        key=lambda hit: (
            hit.media_id,
            hit.start,
            hit.end,
            hit.modality,
            hit.rank,
            hit.source_id,
        ),
    )
    components: list[list[SearchHit]] = []
    current: list[SearchHit] = []
    current_media: str | None = None
    current_end = 0.0
    for hit in ordered:
        if not current or hit.media_id != current_media or hit.start > current_end:
            if current:
                components.append(current)
            current = [hit]
            current_media = hit.media_id
            current_end = hit.end
        else:
            current.append(hit)
            current_end = max(current_end, hit.end)
    if current:
        components.append(current)
    return components


def _score(hits: list[SearchHit]) -> float:
    best_ranks: dict[str, int] = {}
    for hit in hits:
        best_ranks[hit.modality] = min(
            hit.rank,
            best_ranks.get(hit.modality, hit.rank),
        )
    return sum(1.0 / (RRF_RANK_CONSTANT + rank) for rank in best_ranks.values())


def _overlaps(
    hit: SearchHit,
    *,
    media_id: str,
    start: float,
    end: float,
) -> bool:
    return hit.media_id == media_id and hit.start <= end and hit.end >= start


def _hit_identity(hit: SearchHit) -> tuple[str, str, str]:
    return hit.generation_id, hit.modality, hit.source_id


def _candidate_sort_key(candidate: dict) -> tuple:
    return (
        -candidate["score"],
        -candidate["anchor_hit_count"],
        candidate["anchor_best_rank"],
        candidate["media_id"],
        candidate["start"],
        candidate["end"],
        tuple(_hit_identity(hit) for hit in candidate["hits"]),
    )


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
    candidates_by_support: dict[tuple[tuple[str, str, str], ...], dict] = {}
    for result in ordered_results:
        for anchor_hits in _connected_components(result.hits):
            anchor_start = min(hit.start for hit in anchor_hits)
            anchor_end = max(hit.end for hit in anchor_hits)
            supporting_hits = [
                hit
                for hit in flattened
                if _overlaps(
                    hit,
                    media_id=anchor_hits[0].media_id,
                    start=anchor_start,
                    end=anchor_end,
                )
            ]
            ordered_hits = tuple(
                sorted(
                    supporting_hits,
                    key=lambda hit: (
                        hit.modality,
                        hit.rank,
                        hit.source_id,
                    ),
                )
            )
            candidate = {
                "score": _score(supporting_hits),
                "anchor_hit_count": len(anchor_hits),
                "anchor_best_rank": min(hit.rank for hit in anchor_hits),
                "media_id": anchor_hits[0].media_id,
                "start": anchor_start,
                "end": anchor_end,
                "modalities": tuple(
                    sorted({hit.modality for hit in supporting_hits})
                ),
                "hits": ordered_hits,
            }
            support_key = tuple(_hit_identity(hit) for hit in ordered_hits)
            existing = candidates_by_support.get(support_key)
            if existing is None or _candidate_sort_key(
                candidate
            ) < _candidate_sort_key(existing):
                candidates_by_support[support_key] = candidate

    candidates = list(candidates_by_support.values())
    candidates.sort(key=_candidate_sort_key)
    moments = []
    for rank, candidate in enumerate(candidates[:top_k], start=1):
        public_candidate = {
            key: value
            for key, value in candidate.items()
            if key not in {"anchor_hit_count", "anchor_best_rank"}
        }
        moments.append(
            FusedMoment(
                rank=rank,
                moment_id=_moment_id(
                    snapshot_id=snapshot_id,
                    media_id=candidate["media_id"],
                    start=candidate["start"],
                    end=candidate["end"],
                    hits=candidate["hits"],
                ),
                **public_candidate,
            )
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
        moments=tuple(moments),
        fusion=FusionProvenance(
            profile=FusionProfile.temporal_anchor,
            overlap_rule="anchored_intervals",
            requested_modalities=requested_modalities,
            searched_modalities=searched_modalities,
        ),
    )
