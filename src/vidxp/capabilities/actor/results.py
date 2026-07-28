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
    records = storage.records("actor")

    grouped: dict[str, list[dict]] = {}
    for record in records:
        grouped.setdefault(str(record["cluster_id"]), []).append(record)
    summaries = []
    for cluster_id, cluster_records in sorted(grouped.items()):
        video_ids = {
            str(record["video_id"])
            for record in cluster_records
        }
        generation_ids = {
            str(record["generation_id"])
            for record in cluster_records
            if record.get("generation_id") is not None
        }
        if len(video_ids) != 1 or len(generation_ids) > 1:
            raise RuntimeError(
                f"Actor cluster {cluster_id!r} spans multiple index identities."
            )
        summaries.append(
            ActorClusterSummary(
                cluster_id=cluster_id,
                video_id=next(iter(video_ids)),
                detection_count=len(cluster_records),
                first_timestamp=min(
                    float(record["timestamp"]) for record in cluster_records
                ),
                last_timestamp=max(
                    float(record["timestamp"]) for record in cluster_records
                ),
            )
        )
    return tuple(summaries)


def actor_detections(
    config: IndexConfig,
    cluster_id: str,
    *,
    storage: IndexStore,
) -> list[ActorDetection]:
    records = storage.records(
        "actor",
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
