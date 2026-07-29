from __future__ import annotations

from dataclasses import dataclass
from math import floor
from pathlib import Path
from typing import Iterator, Sequence

from vidxp.core.contracts import CancellationToken
from vidxp.core.indexing_common import ProgressCallback


@dataclass(frozen=True)
class VideoInfo:
    fps: float
    frame_count: int
    duration: float
    width: int
    height: int


@dataclass
class FrameStreamStats:
    frames_advanced: int = 0
    frames_materialized: int = 0


@dataclass(frozen=True)
class FrameSample:
    frame_index: int
    timestamp: float
    frame: object


@dataclass(frozen=True)
class FrameSampling:
    frame_stride: int | None = None
    source_fps: float | None = None
    target_fps: float | None = None

    def __post_init__(self) -> None:
        frame_based = self.frame_stride is not None
        time_based = self.source_fps is not None or self.target_fps is not None
        if frame_based == time_based:
            raise ValueError(
                "Frame sampling requires either a stride or a source/target FPS pair."
            )
        if frame_based and self.frame_stride <= 0:
            raise ValueError("frame_stride must be greater than zero.")
        if time_based and (
            self.source_fps is None
            or self.target_fps is None
            or self.source_fps <= 0
            or self.target_fps <= 0
        ):
            raise ValueError("source_fps and target_fps must be greater than zero.")

    def includes(self, frame_index: int, timestamp: float) -> bool:
        if self.frame_stride is not None:
            return frame_index % self.frame_stride == 0
        assert self.source_fps is not None
        assert self.target_fps is not None
        if self.source_fps <= self.target_fps or frame_index == 0:
            return True
        previous_timestamp = (frame_index - 1) / self.source_fps
        return floor(timestamp * self.target_fps) > floor(
            previous_timestamp * self.target_fps
        )

    def next_sample_timestamp(
        self,
        frame_index: int,
        *,
        frame_count: int,
        duration: float,
    ) -> float:
        if self.source_fps is None:
            raise ValueError(
                "Next sample timestamps require time-based sampling."
            )
        for next_index in range(frame_index + 1, max(0, frame_count)):
            timestamp = next_index / self.source_fps
            if self.includes(next_index, timestamp):
                return min(duration, timestamp)
        return duration


def probe_video(path: str | Path) -> VideoInfo:
    import cv2

    video = cv2.VideoCapture(str(path))
    try:
        fps = float(video.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            raise ValueError("The selected video has an invalid frame rate.")
        frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        return VideoInfo(
            fps=fps,
            frame_count=frame_count,
            duration=frame_count / fps,
            width=int(video.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(video.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
    finally:
        video.release()


def iter_frame_batches(
    path: str | Path,
    *,
    frame_stride: int | None = None,
    frame_strides: Sequence[int] | None = None,
    samplings: Sequence[FrameSampling] | None = None,
    batch_size: int,
    cancellation: CancellationToken,
    stats: FrameStreamStats | None = None,
) -> Iterator[list[FrameSample]]:
    import cv2

    strides = tuple(dict.fromkeys(frame_strides or ()))
    if frame_stride is not None:
        strides = tuple(dict.fromkeys((frame_stride, *strides)))
    if any(stride <= 0 for stride in strides):
        raise ValueError("frame strides must be greater than zero.")
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero.")

    video = cv2.VideoCapture(str(path))
    fps = float(video.get(cv2.CAP_PROP_FPS))
    if fps <= 0:
        video.release()
        raise ValueError("The selected video has an invalid frame rate.")
    active_samplings = tuple(samplings or ()) + tuple(
        FrameSampling(frame_stride=stride) for stride in strides
    )
    if not active_samplings:
        video.release()
        raise ValueError("At least one frame sampling schedule is required.")

    stream_stats = stats or FrameStreamStats()
    batch: list[FrameSample] = []
    frame_index = 0
    try:
        while True:
            timestamp = frame_index / fps
            sampled = any(
                sampling.includes(frame_index, timestamp)
                for sampling in active_samplings
            )
            if sampled:
                retrieved, frame = video.read()
            else:
                retrieved = video.grab()
                frame = None
            if not retrieved:
                break
            stream_stats.frames_advanced += 1
            if sampled:
                stream_stats.frames_materialized += 1
                batch.append(
                    FrameSample(
                        frame_index=frame_index,
                        timestamp=timestamp,
                        frame=frame,
                    )
                )
                if len(batch) == batch_size:
                    cancellation.raise_if_cancelled()
                    yield batch
                    batch = []
            frame_index += 1
        if batch:
            cancellation.raise_if_cancelled()
            yield batch
    finally:
        video.release()


def render_actor_video(
    input_path: str | Path,
    output_path: str | Path,
    cluster_id: str,
    detections: list[dict],
    *,
    cancellation: CancellationToken | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    import cv2

    active_cancellation = cancellation or CancellationToken()
    source_path = Path(input_path)
    destination = Path(output_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Video not found: {source_path}")
    if source_path.resolve() == destination.resolve():
        raise ValueError("Actor result output must differ from the input video.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    source = cv2.VideoCapture(str(source_path))
    if not source.isOpened():
        source.release()
        raise RuntimeError(f"Could not open actor source video: {source_path}")
    fps = float(source.get(cv2.CAP_PROP_FPS))
    width = int(source.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(source.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(source.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or width <= 0 or height <= 0:
        source.release()
        raise RuntimeError(
            "Actor source video has invalid FPS or frame dimensions."
        )

    writer = None
    for codec in ("avc1", "mp4v"):
        candidate = cv2.VideoWriter(
            str(destination),
            cv2.VideoWriter_fourcc(*codec),
            fps,
            (width, height),
        )
        if candidate.isOpened():
            writer = candidate
            break
        candidate.release()
    if writer is None:
        source.release()
        raise RuntimeError(f"Could not open actor result video: {destination}")

    frame_targets = {
        int(item["frame_index"]): tuple(item["bbox"])
        for item in detections
    }

    frames_written = 0
    try:
        frame_index = 0
        while True:
            active_cancellation.raise_if_cancelled()
            retrieved, frame = source.read()
            if not retrieved:
                break
            if frame_index in frame_targets:
                top, right, bottom, left = frame_targets[frame_index]
                color = (0, 255, 0)
                thickness = max(2, int(height / 200))
                font_scale = max(0.5, height / 1000)
                cv2.rectangle(
                    frame,
                    (left, top),
                    (right, bottom),
                    color,
                    thickness,
                )
                cv2.putText(
                    frame,
                    f"Actor {cluster_id}",
                    (left, top - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    color,
                    thickness,
                )
            writer.write(frame)
            frame_index += 1
            frames_written += 1
            if progress is not None and frames_written % 30 == 0:
                progress(
                    {
                        "state": "rendering",
                        "stage": "rendering",
                        "message": "Rendering the actor overlay.",
                        "current": frames_written,
                        "total": frame_count if frame_count > 0 else None,
                    }
                )
    finally:
        source.release()
        writer.release()

    active_cancellation.raise_if_cancelled()
    if progress is not None and frames_written:
        progress(
            {
                "state": "rendering",
                "stage": "rendering",
                "message": "Rendered the actor overlay.",
                "current": frames_written,
                "total": frame_count if frame_count > 0 else frames_written,
            }
        )

    if (
        frames_written == 0
        or not destination.is_file()
        or destination.stat().st_size == 0
    ):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Actor result video was not created: {destination}")
