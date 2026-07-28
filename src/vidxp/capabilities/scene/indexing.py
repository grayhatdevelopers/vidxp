from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vidxp.capabilities.scene.config import scene_config
from vidxp.capabilities.scene.models import SceneModel, get_scene_model
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    batched,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.core.storage import IndexStorage
from vidxp.runtime import ModelRuntime


@dataclass
class SceneIndexState:
    provider: SceneModel
    stored_frames: int = 0


def encode_scene_batch(samples, provider: SceneModel):
    import torch
    from PIL import Image

    inputs = provider.processor(
        images=[Image.fromarray(sample.frame) for sample in samples],
        return_tensors="pt",
    )
    inputs = {name: value.to(provider.device) for name, value in inputs.items()}
    with torch.inference_mode():
        features = provider.model.get_image_features(**inputs)
        features = torch.nn.functional.normalize(features, dim=-1)
    return features.cpu().numpy().tolist()


def scene_records(
    samples,
    vectors,
    info,
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    for sample, vector in zip(samples, vectors):
        end = min(
            info.duration,
            sample.timestamp + config.frame_stride / info.fps,
        )
        if end <= sample.timestamp:
            end = sample.timestamp + 1 / info.fps
        source_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "scene",
            f"f{sample.frame_index:012d}",
        )
        records.append(
            StorageRecord(
                source_id=source_id,
                embedding=vector,
                metadata={
                    **config.record_identity("scene", source_id),
                    "frame_index": sample.frame_index,
                    "timestamp": sample.timestamp,
                    "start": sample.timestamp,
                    "end": end,
                    "fps": info.fps,
                    "duration": info.duration,
                },
            )
        )
    return records


def process_scene_samples(
    samples,
    *,
    state: SceneIndexState,
    info,
    config: IndexConfig,
    storage: IndexStorage,
    cancellation: CancellationToken,
) -> None:
    settings = scene_config(config)
    for group in batched(samples, settings.batch_size):
        cancellation.raise_if_cancelled()
        vectors = encode_scene_batch(
            group,
            state.provider,
        )
        state.stored_frames += storage.upsert(
            "scene",
            scene_records(group, vectors, info, config),
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )


class SceneVisualProcessor:
    def batch_size(self, config: IndexConfig) -> int:
        return scene_config(config).batch_size

    def prepare(
        self,
        config: IndexConfig,
        runtime: ModelRuntime,
        progress: ProgressCallback | None,
    ) -> SceneIndexState:
        settings = scene_config(config)
        report_progress(
            progress,
            "preparing_scene_model",
            f"Preparing scene model: SigLIP2 {settings.model}.",
        )
        provider = get_scene_model(
            runtime,
            settings.model,
            settings.revision,
        )
        return SceneIndexState(provider)

    def process(
        self,
        samples,
        *,
        state: SceneIndexState,
        info,
        config: IndexConfig,
        storage: IndexStorage,
        cancellation: CancellationToken,
    ) -> None:
        process_scene_samples(
            samples,
            state=state,
            info=info,
            config=config,
            storage=storage,
            cancellation=cancellation,
        )

    def finalize(
        self,
        state: SceneIndexState,
        *,
        config: IndexConfig,
        storage: IndexStorage,
    ) -> tuple[dict[str, Any], int]:
        return {"scene_frames": state.stored_frames}, state.stored_frames


VISUAL_PROCESSOR = SceneVisualProcessor()
