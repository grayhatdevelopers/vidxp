from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, Sequence

from vidxp.core.video import FrameSample


@dataclass(frozen=True)
class VideoClip:
    samples: tuple[FrameSample, ...]
    start: float
    end: float

    @property
    def sample_count(self) -> int:
        return len(self.samples)


@dataclass
class ClipStreamAccumulator:
    clip_frames: int
    boundaries: Sequence[float] | None = None
    pending: list = field(default_factory=list)
    _next_boundary: int = 0

    def add(self, samples: Iterable[FrameSample]) -> Iterator[VideoClip]:
        for sample in samples:
            self.pending.append(sample)
            crossed = (
                self.boundaries is not None
                and self._next_boundary < len(self.boundaries)
                and sample.timestamp >= self.boundaries[self._next_boundary]
            )
            if crossed:
                self._next_boundary += 1
            if crossed or len(self.pending) >= self.clip_frames:
                yield self._flush()

    def finalize(self) -> VideoClip | None:
        return self._flush() if self.pending else None

    def _flush(self) -> VideoClip:
        samples = tuple(self.pending)
        self.pending.clear()
        return VideoClip(
            samples=samples,
            start=samples[0].timestamp,
            end=samples[-1].timestamp,
        )
