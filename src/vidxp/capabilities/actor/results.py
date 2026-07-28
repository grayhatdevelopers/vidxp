from __future__ import annotations

from pathlib import Path

from vidxp.capabilities.actor.schemas import (
    ActorClusterSummary,
    ActorDetection,
    ActorRenderResult,
)
from vidxp.core.contracts import IndexConfig
from vidxp.ports import IndexStore
from vidxp.core.video import render_actor_video


class ActorClusterNotFoundError(LookupError):
    """Raised when an actor cluster has no retained detections."""


def actor_clusters(
    config: IndexConfig,
    *,
    storage: IndexStore,
) -> tuple[ActorClusterSummary, ...]:
    if config.video_id is None:
        raise ValueError("IndexConfig.video_id is required for actor results.")
    records = storage.records(
        "actor",
        video_id=config.video_id,
    )

    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(str(record["cluster_id"]), []).append(record)
    return tuple(
        ActorClusterSummary(
            cluster_id=cluster_id,
            video_id=config.video_id,
            detection_count=len(cluster_records),
            first_timestamp=min(
                float(record["timestamp"]) for record in cluster_records
            ),
            last_timestamp=max(
                float(record["timestamp"]) for record in cluster_records
            ),
        )
        for cluster_id, cluster_records in sorted(grouped.items())
    )


def actor_detections(
    config: IndexConfig,
    cluster_id: str,
    *,
    storage: IndexStore,
) -> list[ActorDetection]:
    if config.video_id is None:
        raise ValueError("IndexConfig.video_id is required for actor results.")
    records = storage.records(
        "actor",
        video_id=config.video_id,
        filters={"cluster_id": cluster_id},
    )

    detections = [
        ActorDetection(
            **{
                key: value
                for key, value in record.items()
                if not key.startswith("bbox_")
            },
            bbox=(
                int(record["bbox_top"]),
                int(record["bbox_right"]),
                int(record["bbox_bottom"]),
                int(record["bbox_left"]),
            ),
        )
        for record in records
    ]
    if not detections:
        raise ActorClusterNotFoundError(
            f"Actor cluster {cluster_id} was not found in the completed index."
        )
    return sorted(
        detections,
        key=lambda item: (item.frame_index, item.detection_id),
    )


def render_actor_result(
    config: IndexConfig,
    cluster_id: str,
    input_path: str | Path,
    output_path: str | Path,
    *,
    storage: IndexStore,
) -> ActorRenderResult:
    detections = actor_detections(config, cluster_id, storage=storage)
    destination = Path(output_path)
    render_actor_video(input_path, destination, cluster_id, detections)
    return ActorRenderResult(
        output_path=destination,
        detection_count=len(detections),
    )
