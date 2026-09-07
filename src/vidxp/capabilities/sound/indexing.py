from __future__ import annotations

from dataclasses import dataclass
from itertools import chain, islice
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

from vidxp.capabilities.sound.config import sound_config
from vidxp.capabilities.sound.models import get_sound_model
from vidxp.capabilities.sound.specs import (
    PE_A_FRAME_INTERVAL_SECONDS,
    PE_A_FRAME_MODEL,
    PE_A_SAMPLE_RATE,
)
from vidxp.core.contracts import (
    CancellationToken,
    IndexConfig,
    StorageRecord,
    VideoSource,
    stable_source_id,
)
from vidxp.core.indexing_common import ProgressCallback, report_progress
from vidxp.ports import IndexStore, ModelRuntimePort


FINELAP_SAMPLE_RATE = 16_000
PCM_BYTES_PER_SAMPLE = 2
DENSE_INTERVAL_SECONDS = 0.16


@dataclass(frozen=True)
class AudioWindow:
    index: int
    start: float
    end: float
    pcm: bytes
    retained_start: float | None = None
    retained_end: float | None = None

    @property
    def owned_start(self) -> float:
        return self.start if self.retained_start is None else self.retained_start

    @property
    def owned_end(self) -> float:
        return self.end if self.retained_end is None else self.retained_end


def _resampled_pcm_frames(frame: Any, resampler: Any) -> Iterable[bytes]:
    import numpy as np

    converted = resampler.resample(frame)
    if converted is None:
        return
    frames = converted if isinstance(converted, list) else [converted]
    for item in frames:
        values = item.to_ndarray().reshape(-1)
        yield np.asarray(values, dtype="<i2").tobytes()


def _raw_audio_windows(
    input_path: str | Path,
    *,
    window_seconds: float,
    overlap_seconds: float,
    sample_rate: int,
    cancellation: CancellationToken,
) -> Iterable[AudioWindow]:
    import av

    if sample_rate <= 0:
        raise ValueError("sample_rate must be greater than zero.")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero.")
    if overlap_seconds < 0 or overlap_seconds >= window_seconds:
        raise ValueError(
            "overlap_seconds must be nonnegative and smaller than window_seconds."
        )
    samples_per_window = round(window_seconds * sample_rate)
    stride_samples = round((window_seconds - overlap_seconds) * sample_rate)
    bytes_per_window = samples_per_window * PCM_BYTES_PER_SAMPLE
    stride_bytes = stride_samples * PCM_BYTES_PER_SAMPLE
    pending = bytearray()
    buffer_start_samples = 0
    received_samples = 0
    last_full_end_samples = 0
    window_index = 0
    with av.open(str(input_path)) as container:
        if not container.streams.audio:
            return
        max_samples = (
            round(float(container.duration * av.time_base) * sample_rate)
            if container.duration is not None
            else None
        )

        def append_pcm(block: bytes) -> None:
            nonlocal received_samples
            samples = len(block) // PCM_BYTES_PER_SAMPLE
            if max_samples is None:
                accepted = samples
            else:
                accepted = min(samples, max(0, max_samples - received_samples))
            pending.extend(block[: accepted * PCM_BYTES_PER_SAMPLE])
            received_samples += accepted

        stream = container.streams.audio[0]
        resampler = av.AudioResampler(
            format="s16",
            layout="mono",
            rate=sample_rate,
        )
        for frame in container.decode(stream):
            cancellation.raise_if_cancelled()
            for block in _resampled_pcm_frames(frame, resampler):
                append_pcm(block)
                while len(pending) >= bytes_per_window:
                    pcm = bytes(pending[:bytes_per_window])
                    start = buffer_start_samples / sample_rate
                    last_full_end_samples = buffer_start_samples + samples_per_window
                    yield AudioWindow(
                        index=window_index,
                        start=start,
                        end=last_full_end_samples / sample_rate,
                        pcm=pcm,
                    )
                    window_index += 1
                    del pending[:stride_bytes]
                    buffer_start_samples += stride_samples
        for block in _resampled_pcm_frames(None, resampler):
            append_pcm(block)
        sample_count = len(pending) // PCM_BYTES_PER_SAMPLE
        pending_end_samples = buffer_start_samples + sample_count
        if sample_count and pending_end_samples > last_full_end_samples:
            yield AudioWindow(
                index=window_index,
                start=buffer_start_samples / sample_rate,
                end=pending_end_samples / sample_rate,
                pcm=bytes(pending[: sample_count * PCM_BYTES_PER_SAMPLE]),
            )


def _assign_overlap_ownership(
    windows: Iterable[AudioWindow],
) -> Iterable[AudioWindow]:
    """Give overlapping sections one non-duplicated global time range."""
    previous = None
    retained_start = None
    for current in windows:
        if previous is not None:
            boundary = (previous.end + current.start) / 2
            yield AudioWindow(
                previous.index,
                previous.start,
                previous.end,
                previous.pcm,
                retained_start,
                boundary,
            )
            retained_start = boundary
        previous = current
    if previous is not None:
        yield AudioWindow(
            previous.index,
            previous.start,
            previous.end,
            previous.pcm,
            retained_start,
            previous.end,
        )


def iter_audio_windows(
    input_path: str | Path,
    *,
    window_seconds: float,
    cancellation: CancellationToken,
    overlap_seconds: float = 0.0,
    sample_rate: int = FINELAP_SAMPLE_RATE,
) -> Iterable[AudioWindow]:
    yield from _assign_overlap_ownership(
        _raw_audio_windows(
            input_path,
            window_seconds=window_seconds,
            overlap_seconds=overlap_seconds,
            sample_rate=sample_rate,
            cancellation=cancellation,
        )
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
    frame_embeddings: Sequence[Any],
    config: IndexConfig,
    *,
    evidence_window_seconds: float,
) -> list[StorageRecord]:
    records = []
    for window, vectors in zip(
        windows,
        frame_embeddings,
    ):
        for frame_index, vector in enumerate(vectors):
            timestamp = round(
                window.start + frame_index * PE_A_FRAME_INTERVAL_SECONDS,
                6,
            )
            if timestamp >= window.end:
                break
            if not window.owned_start <= timestamp < window.owned_end:
                continue
            frame_end = round(
                min(window.end, timestamp + PE_A_FRAME_INTERVAL_SECONDS),
                6,
            )
            evidence_index = math.floor(timestamp / evidence_window_seconds)
            start = round(evidence_index * evidence_window_seconds, 6)
            end = round(start + evidence_window_seconds, 6)
            source_id = stable_source_id(
                config.run_id,
                str(config.video_id),
                "sound",
                f"s{window.index:08d}-f{frame_index:05d}",
                generation_id=config.generation_id,
            )
            records.append(
                StorageRecord(
                    source_id=source_id,
                    embedding=vector.tolist(),
                    metadata={
                        **config.record_identity("sound", source_id),
                        "representation": "frame",
                        "section_index": window.index,
                        "frame_index": frame_index,
                        "evidence_index": evidence_index,
                        "timestamp": timestamp,
                        "frame_end": frame_end,
                        "start": start,
                        "end": end,
                        "duration": evidence_window_seconds,
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
        window_seconds=settings.inference_window_seconds,
        overlap_seconds=settings.inference_overlap_seconds,
        sample_rate=PE_A_SAMPLE_RATE,
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
        return {"sound_sections": 0, "sound_frames": 0}
    report_progress(
        progress,
        "preparing_sound_model",
        f"Preparing sound model: {PE_A_FRAME_MODEL.model_id}.",
    )
    provider = get_sound_model(runtime)
    report_progress(
        progress,
        "sound_indexing",
        "Indexing sound frames in bounded overlapping sections.",
        0,
        None,
    )
    stored_sections = 0
    stored_frames = 0
    for group in chain((first_group,), groups):
        cancellation.raise_if_cancelled()
        frame_embeddings = provider.encode_audio(
            [window.pcm for window in group]
        )
        records = sound_records(
            group,
            frame_embeddings,
            config,
            evidence_window_seconds=settings.evidence_window_seconds,
        )
        storage.upsert(
            "sound",
            records,
            batch_size=config.storage_batch_size,
            cancellation=cancellation,
        )
        stored_sections += len(group)
        stored_frames += len(records)
        report_progress(
            progress,
            "sound_indexing",
            "Indexing sound frames in bounded overlapping sections.",
            stored_sections,
            None,
        )
    return {
        "sound_sections": stored_sections,
        "sound_frames": stored_frames,
    }
