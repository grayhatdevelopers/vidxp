from __future__ import annotations

from dataclasses import dataclass
from itertools import chain, islice
from pathlib import Path
from typing import Any, Iterable, Sequence

from vidxp.capabilities.sound.config import sound_config
from vidxp.capabilities.sound.models import get_sound_model
from vidxp.capabilities.sound.specs import FINELAP_MODEL
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    VideoSource,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.ports import IndexStore, ModelRuntimePort


SAMPLE_RATE = 16_000
PCM_BYTES_PER_SAMPLE = 2
DENSE_INTERVAL_SECONDS = 0.16


@dataclass(frozen=True)
class AudioWindow:
    index: int
    start: float
    end: float
    pcm: bytes


def _resampled_pcm_frames(frame: Any, resampler: Any) -> Iterable[bytes]:
    import numpy as np

    converted = resampler.resample(frame)
    if converted is None:
        return
    frames = converted if isinstance(converted, list) else [converted]
    for item in frames:
        values = item.to_ndarray().reshape(-1)
        yield np.asarray(values, dtype="<i2").tobytes()


def iter_audio_windows(
    input_path: str | Path,
    *,
    window_seconds: float,
    cancellation: CancellationToken,
) -> Iterable[AudioWindow]:
    import av

    samples_per_window = round(window_seconds * SAMPLE_RATE)
    bytes_per_window = samples_per_window * PCM_BYTES_PER_SAMPLE
    pending = bytearray()
    emitted_samples = 0
    window_index = 0
    with av.open(str(input_path)) as container:
        if not container.streams.audio:
            return
        max_samples = (
            round(float(container.duration * av.time_base) * SAMPLE_RATE)
            if container.duration is not None
            else None
        )

        def append_pcm(block: bytes) -> None:
            if max_samples is None:
                pending.extend(block)
                return
            buffered_samples = len(pending) // PCM_BYTES_PER_SAMPLE
            remaining = max_samples - emitted_samples - buffered_samples
            if remaining > 0:
                pending.extend(block[: remaining * PCM_BYTES_PER_SAMPLE])

        stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE,
        )
        for frame in container.decode(stream):
            cancellation.raise_if_cancelled()
            for block in _resampled_pcm_frames(frame, resampler):
                append_pcm(block)
                while len(pending) >= bytes_per_window:
                    pcm = bytes(pending[:bytes_per_window])
                    del pending[:bytes_per_window]
                    start = emitted_samples / SAMPLE_RATE
                    emitted_samples += samples_per_window
                    yield AudioWindow(
                        index=window_index,
                        start=start,
                        end=emitted_samples / SAMPLE_RATE,
                        pcm=pcm,
                    )
                    window_index += 1
        for block in _resampled_pcm_frames(None, resampler):
            append_pcm(block)
        if pending:
            sample_count = len(pending) // PCM_BYTES_PER_SAMPLE
            if sample_count:
                pcm = bytes(pending[: sample_count * PCM_BYTES_PER_SAMPLE])
                start = emitted_samples / SAMPLE_RATE
                yield AudioWindow(
                    index=window_index,
                    start=start,
                    end=(emitted_samples + sample_count) / SAMPLE_RATE,
                    pcm=pcm,
                )


def _window_batches(
    windows: Iterable[AudioWindow],
    batch_size: int,
) -> Iterable[tuple[AudioWindow, ...]]:
    iterator = iter(windows)
    while group := tuple(islice(iterator, batch_size)):
        yield group


def sound_records(
    windows: Sequence[AudioWindow],
    global_embeddings: Any,
    dense_embeddings: Any,
    config: IndexConfig,
) -> list[StorageRecord]:
    records = []
    for window, global_vector, dense_vectors in zip(
        windows,
        global_embeddings,
        dense_embeddings,
    ):
        window_id = stable_source_id(
            config.run_id,
            str(config.video_id),
            "sound",
            f"w{window.index:08d}",
            generation_id=config.generation_id,
        )
        records.append(
            StorageRecord(
                source_id=window_id,
                embedding=global_vector.tolist(),
                metadata={
                    **config.record_identity("sound", window_id),
                    "representation": "window",
                    "window_index": window.index,
                    "start": window.start,
                    "end": window.end,
                    "duration": round(window.end - window.start, 6),
                },
            )
        )
        for activation_index, dense_vector in enumerate(dense_vectors):
            start = round(
                window.start + activation_index * DENSE_INTERVAL_SECONDS,
                6,
            )
            if start >= window.end:
                break
            end = round(
                min(window.end, start + DENSE_INTERVAL_SECONDS),
                6,
            )
            source_id = stable_source_id(
                config.run_id,
                str(config.video_id),
                "sound",
                f"w{window.index:08d}-a{activation_index:04d}",
                generation_id=config.generation_id,
            )
            records.append(
                StorageRecord(
                    source_id=source_id,
                    embedding=dense_vector.tolist(),
                    metadata={
                        **config.record_identity("sound", source_id),
                        "representation": "activation",
                        "window_index": window.index,
                        "activation_index": activation_index,
                        "start": start,
                        "end": end,
                        "duration": round(end - start, 6),
                    },
                )
            )
    return records


def index_sound(
    source: VideoSource,
    *,
    config: IndexConfig,
    storage: IndexStore,
    cancellation: CancellationToken,
    runtime: ModelRuntimePort,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if config.video_id is None:
        raise ValueError("IndexConfig.video_id is required for indexing.")
    if source.path is None:
        raise ValueError("Sound indexing requires a video or audio path.")
    settings = sound_config(config)
    windows = iter_audio_windows(
        source.path,
        window_seconds=settings.window_seconds,
        cancellation=cancellation,
    )
    groups = iter(_window_batches(windows, settings.batch_size))
    first_group = next(groups, None)
    if first_group is None:
        report_progress(
            progress,
            "sound_skipped",
            "No audio samples were found; sound indexing was skipped.",
        )
        return {"sound_windows": 0, "sound_activations": 0}
    report_progress(
        progress,
        "preparing_sound_model",
        f"Preparing sound model: {FINELAP_MODEL.model_id}.",
    )
    provider = get_sound_model(runtime)
    report_progress(
        progress,
        "sound_indexing",
        "Indexing sound windows and dense activations.",
        0,
        None,
    )
    stored_windows = 0
    stored_activations = 0
    for group in chain((first_group,), groups):
        cancellation.raise_if_cancelled()
        global_embeddings, dense_embeddings = provider.encode_audio(
            [window.pcm for window in group]
        )
        records = sound_records(
            group,
            global_embeddings,
            dense_embeddings,
            config,
        )
        storage.upsert(
            "sound",
            records,
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )
        stored_windows += len(group)
        stored_activations += len(records) - len(group)
        report_progress(
            progress,
            "sound_indexing",
            "Indexing sound windows and dense activations.",
            stored_windows,
            None,
        )
    return {
        "sound_windows": stored_windows,
        "sound_activations": stored_activations,
    }
