from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, Sequence

from vidxp.capabilities.contracts import CapabilityIndexResult
from vidxp.capabilities.registry import CapabilityRegistry
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    VideoSource,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.ports import IndexStore, ModelRuntimePort
from vidxp.core.video import (
    FrameSample,
    FrameSampling,
    FrameStreamStats,
    iter_frame_batches,
    probe_video,
)


class VisualProcessor(Protocol):
    def sampling(self, config: IndexConfig, info: Any) -> FrameSampling: ...

    def batch_size(self, config: IndexConfig) -> int: ...

    def prepare(
        self,
        config: IndexConfig,
        runtime: ModelRuntimePort,
        progress: ProgressCallback | None,
    ) -> Any: ...

    def process(
        self,
        samples: Sequence[FrameSample],
        *,
        state: Any,
        info: Any,
        config: IndexConfig,
        storage: IndexStore,
        cancellation: CancellationToken,
    ) -> None: ...

    def finalize(
        self,
        state: Any,
        *,
        config: IndexConfig,
        storage: IndexStore,
    ) -> tuple[dict[str, Any], int]: ...


@dataclass
class _Participant:
    name: str
    processor: VisualProcessor
    state: Any
    sampling: FrameSampling


def _rgb_samples(samples) -> list[FrameSample]:
    import cv2

    return [
        FrameSample(
            frame_index=sample.frame_index,
            timestamp=sample.timestamp,
            frame=cv2.cvtColor(sample.frame, cv2.COLOR_BGR2RGB),
        )
        for sample in samples
    ]


def _participants(
    names: Sequence[str],
    *,
    info: Any,
    config: IndexConfig,
    registry: CapabilityRegistry,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None,
    timings: dict[str, float],
) -> list[_Participant]:
    participants = []
    for name in names:
        processor = registry.executor(name).index_processor
        if processor is None:
            raise ValueError(
                f"Capability {name!r} does not provide a visual processor."
            )
        started = perf_counter()
        state = processor.prepare(config, runtime, progress)
        timings[name] = perf_counter() - started
        sampling_factory = getattr(type(processor), "sampling", None)
        sampling = (
            FrameSampling(frame_stride=config.frame_stride)
            if sampling_factory is None
            else sampling_factory(processor, config, info)
        )
        if not isinstance(sampling, FrameSampling):
            raise ValueError(
                f"Capability {name!r} returned invalid frame sampling."
            )
        participants.append(_Participant(name, processor, state, sampling))
    return participants


def _is_participant_sample(
    sample: FrameSample,
    participant: _Participant,
) -> bool:
    return participant.sampling.includes(
        sample.frame_index,
        sample.timestamp,
    )


def _expected_sample_count(
    info: Any,
    participants: Sequence[_Participant],
) -> int:
    return sum(
        any(
            participant.sampling.includes(
                frame_index,
                frame_index / info.fps,
            )
            for participant in participants
        )
        for frame_index in range(max(0, info.frame_count))
    )


def _consume_visual_stream(
    source: VideoSource,
    *,
    participants: Sequence[_Participant],
    expected: int,
    info: Any,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    progress: ProgressCallback | None,
    timings: dict[str, float],
) -> FrameStreamStats:
    stream_stats = FrameStreamStats()
    stream = iter(
        iter_frame_batches(
            source.path,
            samplings=tuple(
                participant.sampling for participant in participants
            ),
            batch_size=max(
                participant.processor.batch_size(config)
                for participant in participants
            ),
            cancellation=cancellation,
            stats=stream_stats,
        )
    )
    while True:
        stream_started = perf_counter()
        try:
            samples = next(stream)
        except StopIteration:
            timings["frame_stream"] += perf_counter() - stream_started
            break
        rgb_samples = _rgb_samples(samples)
        timings["frame_stream"] += perf_counter() - stream_started

        for participant in participants:
            participant_samples = [
                sample
                for sample in rgb_samples
                if _is_participant_sample(sample, participant)
            ]
            if not participant_samples:
                continue
            processor_started = perf_counter()
            participant.processor.process(
                participant_samples,
                state=participant.state,
                info=info,
                config=config,
                storage=storage,
                cancellation=cancellation,
            )
            timings[participant.name] += (
                perf_counter() - processor_started
            )

        report_progress(
            progress,
            "visual_indexing",
            "Indexing the shared sampled-frame stream.",
            stream_stats.frames_materialized,
            expected,
        )
    return stream_stats


def _finalize(
    participants: Sequence[_Participant],
    *,
    config: IndexConfig,
    storage: IndexStore,
    timings: dict[str, float],
) -> tuple[dict[str, Any], int]:
    summary: dict[str, Any] = {}
    frame_operations = 0
    for participant in participants:
        started = perf_counter()
        result, operations = participant.processor.finalize(
            participant.state,
            config=config,
            storage=storage,
        )
        timings[participant.name] += perf_counter() - started
        duplicate = set(summary).intersection(result)
        if duplicate:
            raise ValueError(
                "Visual capability summaries contain duplicate keys: "
                + ", ".join(sorted(duplicate))
            )
        summary.update(result)
        frame_operations += operations
    return summary, frame_operations


def index_visuals(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    registry: CapabilityRegistry,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
    modalities: Sequence[str] | None = None,
) -> CapabilityIndexResult:
    if config.video_id is None:
        raise ValueError("IndexConfig.video_id is required for indexing.")
    if source.path is None:
        raise ValueError("Visual indexing requires a video path.")

    selected = tuple(
        config.enabled_modalities if modalities is None else modalities
    )
    if not selected:
        raise ValueError("At least one visual capability must be selected.")

    started = perf_counter()
    info = probe_video(source.path)
    timings = {
        "frame_stream": 0.0,
    }
    participants = _participants(
        selected,
        info=info,
        config=config,
        registry=registry,
        runtime=runtime,
        progress=progress,
        timings=timings,
    )
    expected = _expected_sample_count(info, participants)
    report_progress(
        progress,
        "visual_indexing",
        "Decoding sampled frames for "
        + " and ".join(selected)
        + " indexing.",
        0,
        expected,
    )
    stream_stats = _consume_visual_stream(
        source,
        participants=participants,
        expected=expected,
        info=info,
        config=config,
        storage=storage,
        cancellation=cancellation,
        progress=progress,
        timings=timings,
    )
    capability_summary, frame_operations = _finalize(
        participants,
        config=config,
        storage=storage,
        timings=timings,
    )
    sampled_frames = stream_stats.frames_materialized
    timings["visual_total"] = perf_counter() - started
    return CapabilityIndexResult(
        summary={
            "source_frames_advanced": stream_stats.frames_advanced,
            "sampled_frames": sampled_frames,
            "processed_frames": sampled_frames,
            "frame_operations": frame_operations,
            "duration": info.duration,
            "fps": info.fps,
            **capability_summary,
        },
        timings=timings,
    )


def index_capabilities(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    registry: CapabilityRegistry,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
    modalities: Sequence[str] | None = None,
) -> CapabilityIndexResult:
    return index_visuals(
        source,
        config=config,
        storage=storage,
        cancellation=cancellation,
        registry=registry,
        runtime=runtime,
        progress=progress,
        modalities=modalities,
    )
