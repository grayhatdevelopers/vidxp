from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vidxp.capabilities.actor.config import actor_config
from vidxp.capabilities.actor.models import ActorModels, get_actor_models
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    batched,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback
from vidxp.core.video import FrameSampling
from vidxp.ports import IndexStore, ModelRuntimePort


@dataclass
class ActorIndexState:
    models: ActorModels
    known_encodings: list[Any] = field(default_factory=list)
    known_ids: list[str] = field(default_factory=list)
    histories: dict[str, list[Any]] = field(default_factory=dict)
    cluster_sizes: dict[str, int] = field(default_factory=dict)
    cluster_ranges: dict[str, tuple[float, float]] = field(
        default_factory=dict
    )
    processed_frames: int = 0


def _best_face_match(known_encodings, encoding, threshold):
    import numpy as np

    if not known_encodings:
        return None
    similarities = np.asarray(known_encodings) @ encoding
    match = int(similarities.argmax())
    return match if similarities[match] >= threshold else None


def _actor_cluster_id(
    config: IndexConfig,
    local_cluster_id: str | int,
) -> str:
    if config.video_id is None:
        raise ValueError(
            "IndexConfig.video_id is required for actor cluster identity."
        )
    return stable_source_id(
        config.run_id,
        config.video_id,
        "actor-cluster",
        local_cluster_id,
        generation_id=config.generation_id,
    )


def _actor_records(
    detections,
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    for detection in detections:
        source_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "actor",
            detection["detection_id"],
            generation_id=config.generation_id,
        )
        top, right, bottom, left = detection["bbox"]
        records.append(
            StorageRecord(
                source_id=source_id,
                embedding=[0.0],
                metadata={
                    **config.record_identity("actor", source_id),
                    "detection_id": detection["detection_id"],
                    "cluster_id": detection["cluster_id"],
                    "frame_index": detection["frame_index"],
                    "timestamp": detection["timestamp"],
                    "bbox_top": top,
                    "bbox_right": right,
                    "bbox_bottom": bottom,
                    "bbox_left": left,
                },
            )
        )
    return records


def _actor_cluster_records(
    cluster_sizes: dict[str, int],
    cluster_ranges: dict[str, tuple[float, float]],
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    for cluster_id in sorted(cluster_sizes):
        size = cluster_sizes[cluster_id]
        first_timestamp, last_timestamp = cluster_ranges[cluster_id]
        source_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "actor-cluster-summary",
            cluster_id,
            generation_id=config.generation_id,
        )
        records.append(
            StorageRecord(
                source_id=source_id,
                embedding=[0.0],
                metadata={
                    **config.record_identity("actor", source_id),
                    "record_kind": "cluster_summary",
                    "summary_cluster_id": cluster_id,
                    "detection_count": size,
                    "first_timestamp": first_timestamp,
                    "last_timestamp": last_timestamp,
                },
            )
        )
    return records


def process_actor_samples(
    samples,
    *,
    state: ActorIndexState,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
) -> None:
    import cv2
    import numpy as np

    settings = actor_config(config)
    for group in batched(samples, settings.batch_size):
        cancellation.raise_if_cancelled()
        detections = []
        for sample in group:
            cancellation.raise_if_cancelled()
            frame = cv2.cvtColor(sample.frame, cv2.COLOR_RGB2BGR)
            height, width = frame.shape[:2]
            state.models.detector.setInputSize((width, height))
            state.models.detector.setScoreThreshold(
                settings.detection_threshold
            )
            _, faces = state.models.detector.detect(frame)
            for ordinal, face in enumerate(faces if faces is not None else ()):
                aligned = state.models.recognizer.alignCrop(frame, face)
                encoding = (
                    state.models.recognizer.feature(aligned)
                    .flatten()
                    .astype("float32")
                )
                norm = float(np.linalg.norm(encoding))
                if norm == 0:
                    continue
                encoding /= norm
                match = _best_face_match(
                    state.known_encodings,
                    encoding,
                    settings.match_threshold,
                )
                if match is None:
                    cluster_id = _actor_cluster_id(
                        config,
                        len(state.known_ids) + 1,
                    )
                    state.known_ids.append(cluster_id)
                    state.known_encodings.append(encoding)
                    state.histories[cluster_id] = [encoding]
                else:
                    cluster_id = state.known_ids[match]
                    history = state.histories[cluster_id]
                    history.append(encoding)
                    if len(history) > 5:
                        history.pop(0)
                    centroid = np.mean(history, axis=0)
                    state.known_encodings[match] = (
                        centroid / np.linalg.norm(centroid)
                    )
                state.cluster_sizes[cluster_id] = (
                    state.cluster_sizes.get(cluster_id, 0) + 1
                )
                previous_range = state.cluster_ranges.get(cluster_id)
                timestamp = float(sample.timestamp)
                state.cluster_ranges[cluster_id] = (
                    timestamp
                    if previous_range is None
                    else min(previous_range[0], timestamp),
                    timestamp
                    if previous_range is None
                    else max(previous_range[1], timestamp),
                )
                detections.append(
                    {
                        "detection_id": (
                            f"d{sample.frame_index:012d}-{ordinal:04d}"
                        ),
                        "cluster_id": cluster_id,
                        "frame_index": sample.frame_index,
                        "timestamp": sample.timestamp,
                        "bbox": (
                            max(0, int(face[1])),
                            min(width, int(face[0] + face[2])),
                            min(height, int(face[1] + face[3])),
                            max(0, int(face[0])),
                        ),
                    }
                )
            state.processed_frames += 1
        storage.upsert(
            "actor",
            _actor_records(detections, config),
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )


def finalize_actor_index(
    state: ActorIndexState,
    *,
    config: IndexConfig,
    storage: IndexStore,
) -> tuple[int, int]:
    settings = actor_config(config)
    rejected = [
        cluster_id
        for cluster_id, size in state.cluster_sizes.items()
        if size < settings.minimum_detections
    ]
    for cluster_id in rejected:
        storage.delete_records(
            "actor",
            video_id=str(config.video_id),
            filters={"cluster_id": cluster_id},
        )
    retained = {
        cluster_id: size
        for cluster_id, size in state.cluster_sizes.items()
        if size >= settings.minimum_detections
    }
    storage.upsert(
        "actor",
        _actor_cluster_records(
            retained,
            {
                cluster_id: state.cluster_ranges[cluster_id]
                for cluster_id in retained
            },
            config,
        ),
        batch_size=config.storage_batch_size,
        cancellation=CancellationToken(),
    )
    return sum(retained.values()), len(retained)


class ActorVisualProcessor:
    def sampling(self, config: IndexConfig, info) -> FrameSampling:
        return FrameSampling(frame_stride=config.frame_stride)

    def batch_size(self, config: IndexConfig) -> int:
        return actor_config(config).batch_size

    def prepare(
        self,
        config: IndexConfig,
        runtime: ModelRuntimePort,
        progress: ProgressCallback | None,
    ) -> ActorIndexState:
        return ActorIndexState(models=get_actor_models(runtime))

    def process(
        self,
        samples,
        *,
        state: ActorIndexState,
        info,
        config: IndexConfig,
        storage: IndexStore,
        cancellation: CancellationToken,
    ) -> None:
        process_actor_samples(
            samples,
            state=state,
            config=config,
            storage=storage,
            cancellation=cancellation,
        )

    def finalize(
        self,
        state: ActorIndexState,
        *,
        config: IndexConfig,
        storage: IndexStore,
    ) -> tuple[dict[str, Any], int]:
        detections, clusters = finalize_actor_index(
            state,
            config=config,
            storage=storage,
        )
        return (
            {
                "actor_frames": state.processed_frames,
                "actor_detections": detections,
                "actor_clusters": clusters,
            },
            state.processed_frames,
        )


VISUAL_PROCESSOR = ActorVisualProcessor()
