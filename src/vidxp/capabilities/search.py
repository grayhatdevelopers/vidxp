from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from vidxp.capabilities.schemas import SearchHit, SearchResult
from vidxp.core.contracts import (
    IndexConfig,
    IndexSchemaError,
)
from vidxp.ports import IndexStore


PUBLIC_SEARCH_METADATA = frozenset(
    {
        "text",
        "phrase_id",
        "frame_index",
        "timestamp",
        "fps",
        "duration",
    }
)


def distance_to_score(raw_distance: float) -> float:
    """Map distance to an ordering score without claiming probability.

    Chroma distances are lower-is-better. Negating the raw distance creates a
    strictly monotonic higher-is-better score while retaining the raw value.
    """

    distance = float(raw_distance)
    if not math.isfinite(distance):
        raise ValueError("Search distance must be finite.")
    return -distance


def stable_query_id(
    query: str,
    modality: str,
    config: IndexConfig,
) -> str:
    identity = "\0".join(
        (
            config.dataset,
            config.split,
            config.run_id,
            modality,
            query,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{modality}:{digest}"


def _to_hits(
    modality: str,
    rows: list[dict[str, Any]],
    required_metadata: frozenset[str],
) -> tuple[SearchHit, ...]:
    ordered = sorted(
        rows,
        key=lambda row: (row["raw_distance"], row["source_id"]),
    )
    hits = []
    for rank, row in enumerate(ordered, start=1):
        metadata = row["metadata"]
        missing = sorted(
            (required_metadata | {"generation_id"}) - metadata.keys()
        )
        if missing:
            raise IndexSchemaError(
                "The saved index predates the benchmark-ready schema and must "
                f"be rebuilt. Missing {modality} metadata: {', '.join(missing)}."
            )
        start = float(metadata["start"])
        end = float(metadata["end"])
        if start < 0 or end <= start:
            raise IndexSchemaError(
                f"Invalid {modality} interval in {row['source_id']}: "
                f"[{start}, {end}]."
            )
        distance = float(row["raw_distance"])
        hits.append(
            SearchHit(
                rank=rank,
                media_id=str(metadata["video_id"]),
                video_id=str(metadata["video_id"]),
                generation_id=str(metadata["generation_id"]),
                start=start,
                end=end,
                score=distance_to_score(distance),
                raw_distance=distance,
                modality=modality,
                source_id=str(row["source_id"]),
                metadata={
                    key: value
                    for key, value in metadata.items()
                    if key in PUBLIC_SEARCH_METADATA
                },
            )
        )
    return tuple(hits)


def search_embeddings(
    query: str,
    modality: str,
    embedding: list[float],
    *,
    config: IndexConfig,
    required_metadata: frozenset[str],
    top_k: int = 10,
    video_id: str | None = None,
    query_id: str | None = None,
    filters: Mapping[str, Any] | None = None,
    storage: IndexStore,
) -> SearchResult:
    query = query.strip()
    if not query:
        raise ValueError("Search query must not be empty.")
    if top_k <= 0:
        raise ValueError("top_k must be greater than zero.")
    if modality not in config.enabled_modalities:
        raise ValueError(
            f"The {modality} modality is not present in this index run."
        )
    rows = storage.query(
        modality,
        embedding,
        top_k=top_k,
        video_id=video_id,
        filters=filters,
    )
    return SearchResult(
        query_id=query_id or stable_query_id(query, modality, config),
        query=query,
        modality=modality,
        hits=_to_hits(modality, rows, required_metadata),
    )


def serialize_predictions(
    results: list[SearchResult],
    path: str | Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    query_ids = [result.query_id for result in results]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("Prediction results contain duplicate query IDs.")
    payload = {
        result.query_id: [hit.to_dict() for hit in result.hits]
        for result in sorted(results, key=lambda item: item.query_id)
    }
    if path is not None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return payload
