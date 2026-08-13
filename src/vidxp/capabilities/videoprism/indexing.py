from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from vidxp.capabilities.videoprism.config import videoprism_config
from vidxp.capabilities.videoprism.models import (
    VideoPrismModel,
    get_videoprism_model,
    normalize_pooled_output,
)
from vidxp.capabilities.videoprism.specs import VIDEOPRISM_MODEL
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    batched,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.core.video import FrameSample, FrameSampling
from vidxp.ports import IndexStore, ModelRuntimePort


CLIP_FRAMES = 16


@dataclass
class VideoPrismIndexState:
    provider: VideoPrismModel
    pending: list[FrameSample] = field(default_factory=list)
    stored_clips: int = 0
    video_info: Any | None = None


def videoprism_sampling(config: IndexConfig, info) -> FrameSampling:
    return FrameSampling(
        source_fps=info.fps,
        target_fps=videoprism_config(config).sample_fps,
    )


def encode_video_clips(
    clips: Sequence[Sequence[FrameSample]],
    provider: VideoPrismModel,
) -> list[list[float]]:
    import torch

    inputs = provider.processor(
        videos=[[sample.frame for sample in clip] for clip in clips],
        do_sample_frames=False,
        return_tensors="pt",
    )
    inputs = {name: value.to(provider.device) for name, value in inputs.items()}
    with torch.inference_mode():
        features = provider.model.get_video_features(**inputs).pooler_output
        features = normalize_pooled_output(features)
    return features.cpu().numpy().tolist()


def videoprism_records(
    clips: Sequence[Sequence[FrameSample]],
    vectors: Sequence[Sequence[float]],
    info,
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    cadence = 1 / min(info.fps, videoprism_config(config).sample_fps)
    for clip, vector in zip(clips, vectors):
        first, last = clip[0], clip[-1]
        end = min(info.duration, last.timestamp + cadence)
        if end <= first.timestamp:
            end = first.timestamp + 1 / info.fps
        source_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "videoprism",
            f"f{first.frame_index:012d}-f{last.frame_index:012d}",
            generation_id=config.generation_id,
        )
        records.append(
            StorageRecord(
                source_id=source_id,
                embedding=list(vector),
                metadata={
                    **config.record_identity("videoprism", source_id),
                    "frame_index": first.frame_index,
                    "end_frame_index": last.frame_index,
                    "timestamp": first.timestamp,
                    "start": first.timestamp,
                    "end": end,
                    "fps": info.fps,
                    "duration": info.duration,
                    "sample_count": len(clip),
                },
            )
        )
    return records


def _store_clips(
    clips: Sequence[Sequence[FrameSample]],
    *,
    state: VideoPrismIndexState,
    info,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
) -> None:
    settings = videoprism_config(config)
    for group in batched(clips, settings.batch_size):
        cancellation.raise_if_cancelled()
        model_clips = [
            list(clip) + [clip[-1]] * (CLIP_FRAMES - len(clip))
            for clip in group
        ]
        vectors = encode_video_clips(model_clips, state.provider)
        state.stored_clips += storage.upsert(
            "videoprism",
            videoprism_records(group, vectors, info, config),
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )


def process_videoprism_samples(
    samples: Sequence[FrameSample],
    *,
    state: VideoPrismIndexState,
    info,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
) -> None:
    state.video_info = info
    state.pending.extend(samples)
    complete = len(state.pending) // CLIP_FRAMES
    if not complete:
        return
    consumed = complete * CLIP_FRAMES
    clips = [
        state.pending[start : start + CLIP_FRAMES]
        for start in range(0, consumed, CLIP_FRAMES)
    ]
    del state.pending[:consumed]
    _store_clips(
        clips,
        state=state,
        info=info,
        config=config,
        storage=storage,
        cancellation=cancellation,
    )


class VideoPrismVisualProcessor:
    def sampling(self, config: IndexConfig, info) -> FrameSampling:
        return videoprism_sampling(config, info)

    def batch_size(self, config: IndexConfig) -> int:
        return CLIP_FRAMES * videoprism_config(config).batch_size

    def prepare(
        self,
        config: IndexConfig,
        runtime: ModelRuntimePort,
        progress: ProgressCallback | None,
    ) -> VideoPrismIndexState:
        report_progress(
            progress,
            "preparing_videoprism_model",
            f"Preparing VideoPrism {VIDEOPRISM_MODEL.model_id}.",
        )
        return VideoPrismIndexState(get_videoprism_model(runtime))

    def process(
        self,
        samples,
        *,
        state: VideoPrismIndexState,
        info,
        config: IndexConfig,
        storage: IndexStore,
        cancellation: CancellationToken,
    ) -> None:
        process_videoprism_samples(
            samples,
            state=state,
            info=info,
            config=config,
            storage=storage,
            cancellation=cancellation,
        )

    def finalize(
        self,
        state: VideoPrismIndexState,
        *,
        config: IndexConfig,
        storage: IndexStore,
    ) -> tuple[dict[str, Any], int]:
        if state.pending:
            if state.video_info is None:
                raise RuntimeError("VideoPrism indexing is missing video metadata.")
            _store_clips(
                [state.pending],
                state=state,
                info=state.video_info,
                config=config,
                storage=storage,
                cancellation=CancellationToken(),
            )
            state.pending.clear()
        return {"videoprism_clips": state.stored_clips}, state.stored_clips


VISUAL_PROCESSOR = VideoPrismVisualProcessor()
